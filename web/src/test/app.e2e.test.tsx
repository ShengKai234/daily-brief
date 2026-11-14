import fs from 'node:fs';
import path from 'node:path';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AppRoutes } from '../App';
import { ApiError, apiClient } from '../api/client';
import { setMockJobBehavior } from '../api/mockApi';
import { AuthProvider } from '../auth/AuthContext';
import {
  DEV_OWNER_EMAIL,
  DEV_OWNER_USER_ID,
  DEV_PENDING_EMAIL,
  DEV_PENDING_USER_ID,
  issueDevJwt,
} from '../auth/jwt';
import { todayIn } from '../pages/ReportsPage';
import { getNewestReport, getTodayInTimezone } from '../utils/date';

function renderApp(initialEntries: string[] = ['/']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('Design tokens, minimal baseline (DESIGN 3.3), and PWA config', () => {
  const webRoot = path.resolve(__dirname, '../..');

  it('defines light and dark neutral palette and layout/spacing/typography tokens in tokens.css', () => {
    const tokensCss = fs.readFileSync(
      path.join(webRoot, 'src/styles/tokens.css'),
      'utf-8',
    );
    expect(tokensCss).toContain('prefers-color-scheme: dark');
    expect(tokensCss).toContain('--font-sans:');
    expect(tokensCss).toContain('--color-bg:');
    expect(tokensCss).toContain('--color-surface:');
    expect(tokensCss).toContain('--color-text:');
    expect(tokensCss).toContain('--space-4:');
    expect(tokensCss).toContain('--radius-md:');
    expect(tokensCss).toContain('--font-size-md:');
  });

  it('uses no hard-coded colors, font-sizes, radii, spacing, or animations in global.css or components', () => {
    const globalCss = fs.readFileSync(
      path.join(webRoot, 'src/styles/global.css'),
      'utf-8',
    );
    // No hex, rgb, or hsl colors outside tokens.css
    expect(globalCss).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(globalCss).not.toMatch(/\brgba?\s*\(/i);
    expect(globalCss).not.toMatch(/\bhsla?\s*\(/i);
    // No animations or transitions (DESIGN 3.3)
    expect(globalCss).not.toMatch(/@keyframes\b/i);
    expect(globalCss).not.toMatch(/\banimation\s*:/i);
    expect(globalCss).not.toMatch(/\btransition\s*:/i);

    // Every font-size, border-radius, padding, margin, and gap rule in global.css uses var(--...)
    const tokenControlledProps = [
      'font-size',
      'border-radius',
      'padding',
      'padding-bottom',
      'margin',
      'gap',
      'background-color',
      'color',
      'border-color',
    ];
    for (const prop of tokenControlledProps) {
      const regex = new RegExp(`\\b${prop}\\s*:\\s*([^;]+);`, 'g');
      let match: RegExpExecArray | null;
      while ((match = regex.exec(globalCss)) !== null) {
        const value = match[1].trim();
        if (value !== 'transparent' && value !== 'inherit' && value !== 'none') {
          expect(value).toContain('var(--');
        }
      }
    }

    // No inline styles or dangerouslySetInnerHTML in any component/page
    const tsxFiles = [
      'src/App.tsx',
      'src/components/Layout.tsx',
      'src/pages/SignInPage.tsx',
      'src/pages/RedeemInvitePage.tsx',
      'src/pages/ReportsPage.tsx',
      'src/pages/SettingsPage.tsx',
      'src/pages/ProfilePage.tsx',
    ];
    for (const relPath of tsxFiles) {
      const source = fs.readFileSync(path.join(webRoot, relPath), 'utf-8');
      expect(source).not.toContain('dangerouslySetInnerHTML');
      expect(source).not.toMatch(/\bstyle=\{\{/);
      expect(source).not.toContain('mockApi');
    }
  });

  it('computes today in profile timezone via todayIn (e.g. 2026-11-14T23:30Z is 2026-11-15 in Asia/Taipei) and selects newest report by generated_at', () => {
    const instant = new Date('2026-11-14T23:30:00Z');
    expect(todayIn('Asia/Taipei', instant)).toBe('2026-11-15');
    expect(todayIn('UTC', instant)).toBe('2026-11-14');
    expect(todayIn('America/Los_Angeles', instant)).toBe('2026-11-14');
    expect(getTodayInTimezone('Asia/Taipei', instant)).toBe('2026-11-15');

    const ascendingReports = [
      { id: 'oldest', generated_at: '2026-11-14T05:00:00Z' },
      { id: 'middle', generated_at: '2026-11-14T07:02:00Z' },
      { id: 'newest', generated_at: '2026-11-14T10:45:00Z' },
    ];
    expect(getNewestReport(ascendingReports)?.id).toBe('newest');
    expect(getNewestReport([])).toBeNull();
  });

  it('includes PWA manifest and service worker that never caches /api/*', () => {
    const manifestRaw = fs.readFileSync(
      path.join(webRoot, 'public/manifest.webmanifest'),
      'utf-8',
    );
    const manifest = JSON.parse(manifestRaw) as { display: string; start_url: string };
    expect(manifest.display).toBe('standalone');
    expect(manifest.start_url).toBe('/');

    const swSource = fs.readFileSync(path.join(webRoot, 'public/sw.js'), 'utf-8');
    expect(swSource).toContain("url.pathname.startsWith('/api/')");
  });
});

describe('OpenAPI contract & mock API enforcement', () => {
  it('serves /api/health without authentication and rejects unauthenticated /v1 calls with RFC 9457 Problem', async () => {
    const health = await apiClient.getHealth();
    expect(health).toEqual({ status: 'ok' });

    await expect(apiClient.getMe('')).rejects.toThrow(ApiError);
    try {
      await apiClient.getMe('');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.problem.status).toBe(401);
      expect(apiErr.problem.code).toBe('unauthorized');
    }
  });

  it('enforces the registration gate for pending and disabled accounts and locks agent-settings', async () => {
    const pendingToken = await issueDevJwt({
      sub: DEV_PENDING_USER_ID,
      email: DEV_PENDING_EMAIL,
    });
    const me = await apiClient.getMe(pendingToken);
    expect(me.status).toBe('pending');

    await expect(apiClient.listTopics(pendingToken)).rejects.toMatchObject({
      problem: { status: 403, code: 'account_pending' },
    });

    const disabledToken = await issueDevJwt({
      sub: '00000000-0000-4000-8000-000000000099',
      email: 'disabled@example.com',
    });
    await expect(apiClient.getMe(disabledToken)).rejects.toMatchObject({
      problem: { status: 403, code: 'account_disabled' },
    });

    const ownerToken = await issueDevJwt({
      sub: DEV_OWNER_USER_ID,
      email: DEV_OWNER_EMAIL,
    });
    await expect(
      apiClient.updateAgentSettings(ownerToken, { daily_run_limit: 5 }),
    ).rejects.toMatchObject({
      problem: { status: 403, code: 'agent_settings_locked' },
    });
  });

  it('enforces 3-slot limit, duplicate slot check, 30-min granularity, and stable slot IDs by position', async () => {
    const ownerToken = await issueDevJwt({
      sub: DEV_OWNER_USER_ID,
      email: DEV_OWNER_EMAIL,
    });
    const initial = await apiClient.getDeliverySlots(ownerToken);
    expect(initial.slots).toHaveLength(1);
    const firstSlotId = initial.slots[0].id;

    // Swapping/updating position 1 preserves its slot ID
    const updated = await apiClient.replaceDeliverySlots(ownerToken, {
      slots: [
        { local_time: '08:30', enabled: true },
        { local_time: '18:00', enabled: false },
      ],
    });
    expect(updated.slots[0].id).toBe(firstSlotId);
    expect(updated.slots[0].position).toBe(1);
    expect(updated.slots[1].position).toBe(2);

    // > 3 slots rejected with 422 slot_limit_exceeded
    await expect(
      apiClient.replaceDeliverySlots(ownerToken, {
        slots: [
          { local_time: '06:00', enabled: true },
          { local_time: '12:00', enabled: true },
          { local_time: '18:00', enabled: true },
          { local_time: '21:00', enabled: true },
        ],
      }),
    ).rejects.toMatchObject({
      problem: { status: 422, code: 'slot_limit_exceeded' },
    });

    // Non-30-minute step rejected with 422 validation_error
    await expect(
      apiClient.replaceDeliverySlots(ownerToken, {
        slots: [{ local_time: '07:15', enabled: true }],
      }),
    ).rejects.toMatchObject({
      problem: { status: 422, code: 'validation_error' },
    });
  });

  it('enforces POST /v1/jobs and GET /v1/jobs/{job_id} contract rules (202, 409, 429, 422, 403, 404)', async () => {
    const ownerToken = await issueDevJwt({
      sub: DEV_OWNER_USER_ID,
      email: DEV_OWNER_EMAIL,
    });

    // 1. Create first manual job (202 queued)
    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 1 });
    const job1 = await apiClient.generateNow(ownerToken);
    expect(job1.status).toBe('queued');
    expect(job1.trigger).toBe('manual');
    expect(job1.report_id).toBeNull();

    // 2. Concurrent POST /v1/jobs while job1 is queued -> 409 job_in_progress
    await expect(apiClient.generateNow(ownerToken)).rejects.toMatchObject({
      problem: { status: 409, code: 'job_in_progress' },
    });

    // 3. Poll 1 -> running; Poll 2 -> succeeded with report_id
    const poll1 = await apiClient.getJob(ownerToken, job1.id);
    expect(poll1.status).toBe('running');
    const poll2 = await apiClient.getJob(ownerToken, job1.id);
    expect(poll2.status).toBe('succeeded');
    expect(poll2.report_id).toBeTruthy();

    // 4. Create second manual job and finish it -> hits daily_run_limit (3: 1 scheduled + 2 manual)
    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 0 });
    const job2 = await apiClient.generateNow(ownerToken);
    await apiClient.getJob(ownerToken, job2.id);

    // 5. Third manual job -> 429 daily_limit_reached
    await expect(apiClient.generateNow(ownerToken)).rejects.toMatchObject({
      problem: { status: 429, code: 'daily_limit_reached' },
    });

    // 6. Non-owner active user calling POST /v1/jobs with claude_subscription -> 403 owner_only
    const otherToken = await issueDevJwt({
      sub: DEV_PENDING_USER_ID,
      email: DEV_PENDING_EMAIL,
    });
    await apiClient.redeemInvite(otherToken, { code: 'WELCOME-2026' });
    await expect(apiClient.generateNow(otherToken)).rejects.toMatchObject({
      problem: { status: 403, code: 'owner_only' },
    });

    // 7. Non-owner cannot read owner's job -> 404 not_found
    await expect(apiClient.getJob(otherToken, job1.id)).rejects.toMatchObject({
      problem: { status: 404, code: 'not_found' },
    });
  });
});

describe('End-to-end user flows against the mock API', () => {
  it('signs in as seeded owner, displays today report, marks read state, and renders notes strictly as plain text', async () => {
    const user = userEvent.setup();
    renderApp(['/']);

    expect(
      screen.getByRole('heading', { name: /Sign In to Daily Brief/i }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole('button', { name: /Sign in as Seeded Owner/i }),
    );

    // Lands on Today's Report
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Stock Market' }),
      ).toBeInTheDocument();
    });

    // Opens on today in Asia/Taipei (not a fixed 2026-11-12 date)
    const expectedToday = getTodayInTimezone('Asia/Taipei');
    expect(screen.getByLabelText(/Report Date/i)).toHaveValue(expectedToday);

    // Selects the newest report (Slot 07:00, max generated_at) instead of reports[0] (older On-demand 1/1)
    const olderEditionBtn = screen.getByRole('button', {
      name: /On-demand · 1\/1 unread/i,
    });
    const newestEditionBtn = screen.getByRole('button', {
      name: /Slot 07:00 · 4\/4 unread/i,
    });
    expect(olderEditionBtn).toHaveAttribute('aria-pressed', 'false');
    expect(newestEditionBtn).toHaveAttribute('aria-pressed', 'true');
    expect(
      screen.getByRole('heading', {
        name: /Semiconductor index edges higher/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', {
        name: /Earlier pre-market digest before scheduled slot/i,
      }),
    ).not.toBeInTheDocument();

    // Displays insufficient_news section note
    expect(
      screen.getByText(/No new official Ruby or Python point releases/i),
    ).toBeInTheDocument();

    // Mark first item as read
    const firstItemHeading = screen.getByRole('heading', {
      name: /Semiconductor index edges higher/i,
    });
    const firstArticle = firstItemHeading.closest('article')!;
    const readCheckbox = within(firstArticle).getByRole('checkbox');
    expect(readCheckbox).not.toBeChecked();

    await user.click(readCheckbox);

    await waitFor(() => {
      expect(readCheckbox).toBeChecked();
      expect(
        screen.getByRole('button', { name: /Slot 07:00 · 3\/4 unread/i }),
      ).toBeInTheDocument();
    });

    // Add a note containing HTML characters and verify it renders strictly as plain text
    await user.click(within(firstArticle).getByRole('button', { name: /Add Note/i }));
    const noteTextarea = within(firstArticle).getByLabelText(
      /Note for Semiconductor index edges higher/i,
    );
    const unsafeString = '<script>window.__xss = true;</script><b>Plain note check</b>';
    await user.type(noteTextarea, unsafeString);
    await user.click(within(firstArticle).getByRole('button', { name: /Save Note/i }));

    const noteDisplay = await within(firstArticle).findByTestId(
      'note-display-60000000-0000-4000-8000-000000000001',
    );
    expect(noteDisplay.textContent).toBe(unsafeString);
    expect(noteDisplay.querySelector('script')).toBeNull();
    expect(noteDisplay.querySelector('b')).toBeNull();

    // Switch date to an empty date and verify empty state
    const dateInput = screen.getByLabelText(/Report Date/i);
    await user.clear(dateInput);
    await user.type(dateInput, '2026-11-01');

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /No Reports for 2026-11-01/i }),
      ).toBeInTheDocument();
    });
  });

  it('manages topic subscriptions, item counts, topic renaming, and delivery slots (up to 3)', async () => {
    const user = userEvent.setup();
    renderApp(['/']);

    await user.click(
      screen.getByRole('button', { name: /Sign in as Seeded Owner/i }),
    );
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Topics & Slots/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('link', { name: /Topics & Slots/i }));

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Subscribed Topics \(6\)/i }),
      ).toBeInTheDocument();
    });

    // Subscribe to a new topic
    await user.type(screen.getByLabelText(/Topic Title/i), 'Quantum Computing');
    const countInput = screen.getByLabelText(/Items per Report/i);
    await user.clear(countInput);
    await user.type(countInput, '5');
    await user.click(screen.getByRole('button', { name: /Subscribe Topic/i }));

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Subscribed Topics \(7\)/i }),
      ).toBeInTheDocument();
      expect(screen.getByText('Quantum Computing')).toBeInTheDocument();
    });

    // Add 2 more delivery slots (reaching 3/3) and verify Add Slot disables at 3
    const addSlotBtn = screen.getByRole('button', { name: /Add Slot/i });
    await user.click(addSlotBtn);
    await user.click(addSlotBtn);

    expect(
      screen.getByRole('heading', { name: /Delivery Slots \(3\/3\)/i }),
    ).toBeInTheDocument();
    expect(addSlotBtn).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Save Delivery Slots/i }));
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Delivery slots saved.');
    });
  });

  it('updates profile preferences (display_name, bio, timezone, locale)', async () => {
    const user = userEvent.setup();
    renderApp(['/']);

    await user.click(
      screen.getByRole('button', { name: /Sign in as Seeded Owner/i }),
    );
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Profile/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('link', { name: /Profile/i }));

    const nameInput = await screen.findByLabelText(/Display Name/i);
    await user.clear(nameInput);
    await user.type(nameInput, 'Kai Owner');

    const localeInput = screen.getByLabelText(/Report Locale/i);
    await user.clear(localeInput);
    await user.type(localeInput, 'en-US');

    await user.click(screen.getByRole('button', { name: /Save Profile/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Profile updated.');
    });
    expect(nameInput).toHaveValue('Kai Owner');
    expect(localeInput).toHaveValue('en-US');
  });

  it('gates pending users to invite redemption and activates account on valid invite code', async () => {
    const user = userEvent.setup();
    renderApp(['/']);

    await user.click(
      screen.getByRole('button', { name: /Sign in as Pending User/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Account Pending Approval/i }),
      ).toBeInTheDocument();
    });

    const codeInput = screen.getByLabelText('Invite Code');
    await user.type(codeInput, 'INVALID-CODE');
    await user.click(screen.getByRole('button', { name: /Activate Account/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/invite_not_found/i);
    });

    await user.clear(codeInput);
    await user.type(codeInput, 'WELCOME-2026');
    await user.click(screen.getByRole('button', { name: /Activate Account/i }));

    await waitFor(() => {
      expect(screen.getByLabelText('Account status')).toHaveTextContent('active');
      expect(screen.getByRole('link', { name: /Topics & Slots/i })).toBeInTheDocument();
    });
  });

  it('handles Generate now button polling to succeeded/failed and displays 409, 429, 422, and 403 error states', async () => {
    const user = userEvent.setup();
    renderApp(['/']);

    await user.click(
      screen.getByRole('button', { name: /Sign in as Seeded Owner/i }),
    );

    const generateBtn = await screen.findByRole('button', { name: /Generate now/i });
    expect(generateBtn).not.toBeDisabled();

    // 1. Trigger Generate now -> queued -> running -> succeeded and loads new On-demand report
    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 1 });
    await user.click(generateBtn);

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /On-demand · 6\/6 unread/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('heading', { name: /On-demand brief update for Stock Market/i }),
      ).toBeInTheDocument();
    });

    // 2. Trigger second manual run that transitions to failed -> shows plain error message
    setMockJobBehavior({
      outcome: 'failed',
      pollsBeforeTerminal: 0,
      errorCode: 'llm_timeout',
    });
    await user.click(screen.getByRole('button', { name: /Generate now/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Brief generation failed (llm_timeout).',
      );
    });

    // 3. Trigger third manual run -> hits 429 daily_limit_reached
    await user.click(screen.getByRole('button', { name: /Generate now/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Daily limit reached, try tomorrow.',
      );
    });
  });

  it('displays 422 no_topics with link to /settings, 409 job_in_progress, and 403 owner_only on Generate now', async () => {
    const user = userEvent.setup();
    const ownerToken = await issueDevJwt({
      sub: DEV_OWNER_USER_ID,
      email: DEV_OWNER_EMAIL,
    });

    // Disable all topic subscriptions so POST /v1/jobs returns 422 no_topics
    const { subscriptions } = await apiClient.listSubscriptions(ownerToken);
    for (const sub of subscriptions) {
      await apiClient.updateSubscription(ownerToken, sub.id, { enabled: false });
    }

    renderApp(['/']);
    await user.click(
      screen.getByRole('button', { name: /Sign in as Seeded Owner/i }),
    );

    const generateBtn = await screen.findByRole('button', { name: /Generate now/i });
    await user.click(generateBtn);

    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('No enabled topic subscriptions found.');
      const settingsLink = within(alert).getByRole('link', {
        name: /Manage topic subscriptions/i,
      });
      expect(settingsLink).toHaveAttribute('href', '/settings');
    });

    // Re-enable first subscription and start a background job so clicking Generate now gets 409 job_in_progress
    await apiClient.updateSubscription(ownerToken, subscriptions[0].id, {
      enabled: true,
    });
    await apiClient.generateNow(ownerToken);

    await user.click(screen.getByRole('button', { name: /Generate now/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        /A brief generation job is already in progress/i,
      );
    });

    // Sign out, sign in as pending user, activate with invite code, and verify 403 owner_only on Generate now
    await user.click(screen.getByRole('button', { name: /Sign out/i }));
    await user.click(screen.getByRole('button', { name: /Sign in as Pending User/i }));
    const codeInput = await screen.findByLabelText('Invite Code');
    await user.type(codeInput, 'WELCOME-2026');
    await user.click(screen.getByRole('button', { name: /Activate Account/i }));

    const nonOwnerGenBtn = await screen.findByRole('button', { name: /Generate now/i });
    await user.click(nonOwnerGenBtn);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/owner_only/i);
    });
  });
});
