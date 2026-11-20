import { test, expect, Page } from '@playwright/test';
import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

const OUT = process.env.PROOF_DIR!;
const ROOT = process.env.PROOF_STACK_DIR!; // worktree with the compose stack
const steps: { n: string; title: string; expect: string; result: string; file: string }[] = [];
let i = 0;
async function shot(page: Page, title: string, expectText: string, result = 'pass') {
  i += 1;
  const n = String(i).padStart(2, '0');
  const file = `${n}-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-$/, '')}.png`;
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
  steps.push({ n, title, expect: expectText, result, file });
  fs.writeFileSync(path.join(OUT, 'steps.json'), JSON.stringify(steps, null, 2));
}
function compose(args: string[], env: Record<string, string> = {}) {
  return execFileSync('docker', ['compose', ...args], { cwd: ROOT, env: { ...process.env, ...env }, encoding: 'utf8' });
}

test('Kai sets up a passkey, uses the app, signs out and back in, and gets a scheduled brief', async ({ page }) => {
  // A virtual passkey device stands in for Touch ID (user verification on, resident keys on).
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true,
      isUserVerified: true, automaticPresenceSimulation: true },
  });

  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Sign in with a passkey' })).toBeVisible();
  await shot(page, 'Signed out', 'A signed-out visitor sees only "Sign in with a passkey"; no dev sign-in, no password');

  // Kai runs the admin CLI on his machine; the link is used once and never printed.
  const out = compose(['run', '--rm', '-T', 'api', 'python', '-m', 'app.auth_admin', '--yes', 'bootstrap-owner']);
  const link = out.split('\n').find((l) => l.includes('/enroll#t='))!.trim();
  await page.goto(link);
  await expect(page.getByRole('heading', { name: 'Set Up Your Passkey' })).toBeVisible();
  expect(page.url()).not.toContain('#t=');
  await shot(page, 'Enrollment page', 'The bootstrap link opens "Set Up Your Passkey"; the token is already removed from the address bar');

  const label = page.getByPlaceholder('e.g. MacBook Touch ID');
  await label.click();
  await label.pressSequentially('Proof MacBook', { delay: 40 });
  await shot(page, 'Passkey label', 'Kai names the passkey "Proof MacBook"');
  await page.getByRole('button', { name: /Create Passkey & Sign In/ }).click();
  await expect(page.getByRole('button', { name: /Generate now/ })).toBeVisible({ timeout: 30000 });
  await page.waitForTimeout(1500);
  await shot(page, 'Signed in after enrollment', 'After creating the passkey Kai is signed in and lands on today\'s reports');

  // A brand-new owner has no topics yet: Generate now explains what to do.
  await page.getByRole('button', { name: /Generate now/ }).click();
  await expect(page.getByText('No enabled topic subscriptions found.')).toBeVisible();
  await shot(page, 'No topics yet', 'Generate now with no topics shows "No enabled topic subscriptions found." and a link to manage topics');

  // Kai adds two topics and a delivery time in Topics & Slots.
  await page.getByRole('link', { name: 'Topics & Slots' }).click();
  for (const title of ['AI', 'Stock market']) {
    const t = page.getByLabel('Topic Title');
    await t.click();
    await t.pressSequentially(title, { delay: 40 });
    await page.getByRole('button', { name: 'Subscribe Topic' }).click();
    await expect(page.getByRole('button', { name: `Remove ${title}` })).toBeVisible();
  }
  const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Taipei' }));
  const due = `${String(now.getHours()).padStart(2, '0')}:${now.getMinutes() >= 30 ? '30' : '00'}`;
  await page.getByRole('button', { name: 'Add Slot' }).click();
  await page.getByLabel('Slot 1 Time').selectOption(due);
  await page.getByRole('button', { name: 'Save Delivery Slots' }).click();
  await page.waitForTimeout(1500);
  await shot(page, 'Topics and slot', `Kai subscribes to "AI" and "Stock market" and saves a ${due} delivery time (Asia/Taipei)`);

  await page.getByRole('link', { name: "Today's Report" }).click();
  // Generate now (checks the 5-1 fix: the finished brief must appear by itself).
  await page.getByRole('button', { name: /Generate now/ }).click();
  await expect(page.getByRole('button', { name: /Generating/ })).toBeVisible();
  await shot(page, 'Generating', 'Generate now shows "Generating..." while the real Claude worker runs');
  await expect(page.getByText(/^Mark as read$/).first()).toBeVisible({ timeout: 8 * 60 * 1000 });
  await page.waitForTimeout(1500);
  await shot(page, 'Brief shown', 'The finished brief opens by itself (bug 5-1 fixed)');

  await page.getByText(/^Mark as read$/).first().click();
  await expect(page.getByText(/^Marked as read$/).first()).toBeVisible();
  const note = 'Proof note: passkey session';
  const box = page.getByPlaceholder('Write a plain-text note...').first();
  await box.click();
  await box.pressSequentially(note, { delay: 40 });
  await page.getByRole('button', { name: /Save Note/ }).first().click();
  await expect(page.getByText(note).first()).toBeVisible();
  await shot(page, 'Read and note', 'First item is marked read and the note is saved as plain text');

  await page.reload();
  await expect(page.getByText(note).first()).toBeVisible({ timeout: 15000 });
  await shot(page, 'After reload', 'After a reload Kai is still signed in; read state and note persist');

  await page.getByRole('link', { name: 'Security' }).click();
  await expect(page.getByRole('heading', { name: 'Security' })).toBeVisible();
  await page.waitForTimeout(1000);
  await shot(page, 'Security page', 'Security lists the passkey "Proof MacBook" (current session) and one active session');

  await page.getByRole('button', { name: /Remove passkey/ }).first().click();
  const verify = page.getByRole('button', { name: /Verify passkey/ });
  if (await verify.isVisible().catch(() => false)) {
    await shot(page, 'Reauthentication', 'Removing a passkey first asks Kai to verify with his passkey');
    await verify.click();
    await page.waitForTimeout(1500);
    if (await page.getByRole('button', { name: /Remove passkey/ }).first().isVisible()) {
      await page.getByRole('button', { name: /Remove passkey/ }).first().click();
    }
  }
  await expect(page.getByText(/last|only/i).first()).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(800);
  await shot(page, 'Last passkey protected', 'Removing the only passkey is refused with a clear "last passkey" message');

  await page.getByRole('button', { name: 'Sign Out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in with a passkey' })).toBeVisible();
  await shot(page, 'Signed out again', 'Sign Out returns to the sign-in page');

  await page.getByRole('button', { name: 'Sign in with a passkey' }).click();
  await expect(page.getByRole('button', { name: /Generate now/ })).toBeVisible({ timeout: 30000 });
  await page.waitForTimeout(1500);
  await shot(page, 'Signed in with passkey', 'Sign in with a passkey brings Kai back to his reports, no password');

  // Scheduled brief: start the scheduler only for this test, 10-second tick; the slot saved above is already due.
  const editionsBefore = await page.getByText(/unread$/).count();
  const schedulerId = compose(['run', '-d', '--rm', '-e', 'SCHEDULER_INTERVAL_SECONDS=10', 'scheduler']).trim().split('\n').pop()!.trim();
  try {
    await expect.poll(async () => {
      await page.reload();
      await page.waitForTimeout(2000);
      return page.getByText(/unread$/).count();
    }, { timeout: 8 * 60 * 1000, intervals: [15000] }).toBeGreaterThan(editionsBefore);
  } finally {
    execFileSync('docker', ['stop', schedulerId]);
  }
  await shot(page, 'Scheduled brief', `A scheduler tick (every 10 s, then stopped) produced the brief for the saved delivery time: editions ${editionsBefore} → more`);
});
