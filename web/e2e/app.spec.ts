import { expect, test } from '@playwright/test';
import {
  MOCK_INVITE_TOKEN,
  MOCK_OWNER_RECOVERY_TOKEN,
} from '../src/api/mockApi';
import { getTodayInTimezone } from '../src/utils/date';

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth > doc.clientWidth;
  });
  expect(overflow).toBe(false);
}

async function attachVirtualAuthenticator(page: import('@playwright/test').Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return { cdp, authenticatorId };
}

test('passkey enroll, sign-in, reauth, add and delete passkey with Chromium virtual authenticator', async ({
  page,
}) => {
  const { cdp, authenticatorId } = await attachVirtualAuthenticator(page);

  // 1. Enroll invited user via fragment token
  await page.goto(`/enroll#t=${MOCK_INVITE_TOKEN}`);
  await expect(page.getByRole('heading', { name: 'Set Up Your Passkey' })).toBeVisible();
  expect(page.url()).not.toContain('#t=');
  await expectNoHorizontalOverflow(page);

  await page.getByLabel(/Display Name/i).fill('Mei');
  await page.getByLabel(/Passkey Label/i).fill('Virtual Touch ID');
  await page.getByRole('button', { name: /Create Passkey & Sign In/i }).click();

  await expect(page.getByRole('link', { name: 'Security' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // 2. Navigate to Security page
  await page.getByRole('link', { name: 'Security' }).click();
  await expect(page.getByRole('heading', { name: /Passkeys \(1\/10\)/i })).toBeVisible();
  await expect(page.getByText('Virtual Touch ID')).toBeVisible();
  await expect(page.getByRole('heading', { name: /Active Sessions \(1\)/i })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // 3. Attempt to delete only passkey -> reauth_required -> complete reauth -> 409 last_passkey
  await page.getByRole('button', { name: /Remove passkey Virtual Touch ID/i }).click();
  await expect(page.getByRole('heading', { name: 'Verify Your Passkey' })).toBeVisible();
  await page.getByRole('button', { name: /^Verify passkey$/i }).click();
  await expect(page.getByRole('alert')).toContainText('cannot remove your last active passkey');

  // 4. Add a second passkey on a second virtual authenticator within the 5-minute reauth window
  await cdp.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId });
  await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  await page.getByLabel(/New Passkey Label/i).fill('Backup Virtual Key');
  await page.getByRole('button', { name: /Add passkey on this device/i }).click();
  await expect(page.getByRole('heading', { name: /Passkeys \(2\/10\)/i })).toBeVisible();
  await expect(page.getByText('Backup Virtual Key')).toBeVisible();

  // 5. Delete the current session's passkey -> signs out to SignInPage
  await page.getByRole('button', { name: /Remove passkey Virtual Touch ID/i }).click();
  await expect(page.getByRole('heading', { name: 'Sign In to Daily Brief' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // 6. Sign in with the remaining discoverable passkey
  await page.getByRole('button', { name: 'Sign in with a passkey' }).click();
  await expect(page.getByRole('link', { name: 'Security' })).toBeVisible();
});

test('usable at phone width (375x667) across owner recovery, report, notes, settings, and profile', async ({
  page,
}) => {
  await attachVirtualAuthenticator(page);

  await page.goto(`/enroll#t=${MOCK_OWNER_RECOVERY_TOKEN}`);
  await expect(page.getByRole('heading', { name: 'Set Up Your Passkey' })).toBeVisible();
  expect(page.url()).not.toContain('#t=');

  // Recovery token: leave Display Name blank
  await page.getByRole('button', { name: /Create Passkey & Sign In/i }).click();

  await expect(page.getByRole('heading', { name: 'Stock Market' })).toBeVisible();
  await expect(page.getByLabel('Report Date')).toHaveValue(getTodayInTimezone('Asia/Taipei'));
  await expect(
    page.getByRole('button', { name: /On-demand · 1\/1 unread/i }),
  ).toHaveAttribute('aria-pressed', 'false');
  await expect(
    page.getByRole('button', { name: /Slot 07:00 · 4\/4 unread/i }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    page.getByRole('heading', { name: /Semiconductor index edges higher/i }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Mark item as read and save a plain-text note
  await page.getByRole('checkbox').first().check();
  await page.getByRole('button', { name: 'Add Note' }).first().click();
  await page.getByLabel(/Note for Semiconductor/i).fill('<b>Phone viewport note</b>');
  await page.getByRole('button', { name: 'Save Note' }).click();
  await expect(page.getByTestId('note-display-60000000-0000-4000-8000-000000000001')).toHaveText(
    '<b>Phone viewport note</b>',
  );
  await expectNoHorizontalOverflow(page);

  // Trigger on-demand brief generation ("Generate now"), poll until succeeded, and load new edition
  await page.getByRole('button', { name: 'Generate now' }).click();
  await expect(page.getByRole('button', { name: /On-demand · 6\/6 unread/i })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /On-demand brief update for Stock Market/i }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Navigate to Topics & Slots
  await page.getByRole('link', { name: 'Topics & Slots' }).click();
  await expect(page.getByRole('heading', { name: /Topics & Delivery Slots/i })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Navigate to Profile
  await page.getByRole('link', { name: 'Profile' }).click();
  await expect(page.getByRole('heading', { name: 'Profile Settings' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
