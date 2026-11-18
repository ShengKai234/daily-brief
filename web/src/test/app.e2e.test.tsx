import fs from 'node:fs';
import path from 'node:path';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../App';
import { apiClient } from '../api/client';
import { ERROR_MESSAGES, formatProblemMessage } from '../api/errors';
import {
  MOCK_DEFAULT_PASSKEY_CRED_ID,
  MOCK_EXPIRED_TOKEN,
  MOCK_INVITE_TOKEN,
  MOCK_OWNER_USER_ID,
  MOCK_USED_TOKEN,
  setMockAuthConfig,
  setMockJobBehavior,
  setMockUserStatusForTests,
  signInMockUserForTests,
  signOutMockForTests,
} from '../api/mockApi';
import { AuthProvider } from '../auth/AuthContext';
import {
  clearEnrollmentToken,
  extractEnrollmentTokenFromLocation,
  getEnrollmentToken,
} from '../auth/enrollmentToken';
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

function installFakeBrowserPasskey(credentialId: string = MOCK_DEFAULT_PASSKEY_CRED_ID) {
  class FakePublicKeyCredential {
    public readonly id: string;
    public readonly rawId: ArrayBuffer;
    public readonly type = 'public-key' as const;
    public readonly authenticatorAttachment = 'platform';
    public readonly response: Record<string, unknown>;

    constructor(id: string) {
      this.id = id;
      this.rawId = new Uint8Array([1, 2, 3, 4]).buffer;
      this.response = {
        clientDataJSON: new Uint8Array([5, 6]).buffer,
        attestationObject: new Uint8Array([7, 8]).buffer,
        authenticatorData: new Uint8Array([9, 10]).buffer,
        signature: new Uint8Array([11, 12]).buffer,
        userHandle: new Uint8Array([13, 14]).buffer,
        getTransports: () => ['internal'],
      };
    }

    getClientExtensionResults() {
      return {};
    }

    toJSON() {
      return {
        id: this.id,
        rawId: this.id,
        type: 'public-key',
        response: {
          clientDataJSON: 'BQY',
          attestationObject: 'Bwg',
        },
        authenticatorAttachment: 'platform',
        clientExtensionResults: {},
      };
    }
  }

  Object.defineProperty(window, 'PublicKeyCredential', {
    configurable: true,
    value: FakePublicKeyCredential,
  });

  Object.defineProperty(navigator, 'credentials', {
    configurable: true,
    value: {
      create: vi.fn(async () => new FakePublicKeyCredential(credentialId)),
      get: vi.fn(async () => new FakePublicKeyCredential(credentialId)),
    },
  });
}

describe('Design tokens, minimal baseline (DESIGN 3.3), and PWA config', () => {
  const webRoot = path.resolve(__dirname, '../..');

  it('defines light and dark neutral palette and layout/spacing/typography tokens in tokens.css', () => {
    const tokensCss = fs.readFileSync(path.join(webRoot, 'src/styles/tokens.css'), 'utf-8');
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
    const globalCss = fs.readFileSync(path.join(webRoot, 'src/styles/global.css'), 'utf-8');
    expect(globalCss).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(globalCss).not.toMatch(/\brgba?\s*\(/i);
    expect(globalCss).not.toMatch(/\bhsla?\s*\(/i);
    expect(globalCss).not.toMatch(/@keyframes\b/i);
    expect(globalCss).not.toMatch(/\banimation\s*:/i);
    expect(globalCss).not.toMatch(/\btransition\s*:/i);

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

    const tsxFiles = [
      'src/App.tsx',
      'src/components/Layout.tsx',
      'src/pages/SignInPage.tsx',
      'src/pages/EnrollPage.tsx',
      'src/pages/SecurityPage.tsx',
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

  it('computes today in profile timezone via todayIn and selects newest report by generated_at', () => {
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

  it('provides plain-language messages for every error code in openapi.yaml', () => {
    const expectedCodes = [
      'bad_request',
      'ceremony_invalid',
      'registration_failed',
      'enrollment_token_invalid',
      'enrollment_token_used',
      'enrollment_token_expired',
      'unauthorized',
      'authentication_failed',
      'forbidden',
      'origin_mismatch',
      'account_disabled',
      'admission_disabled',
      'reauth_required',
      'agent_settings_locked',
      'owner_only',
      'not_found',
      'already_subscribed',
      'job_in_progress',
      'lease_lost',
      'topic_exists',
      'credential_exists',
      'passkey_limit',
      'last_passkey',
      'payload_too_large',
      'unsupported_media_type',
      'validation_error',
      'slot_limit_exceeded',
      'duplicate_slot',
      'visibility_not_supported',
      'no_topics',
      'daily_limit_reached',
      'rate_limited',
    ];
    for (const code of expectedCodes) {
      expect(ERROR_MESSAGES[code]).toBeTruthy();
      const msg = formatProblemMessage({
        type: 'about:blank',
        title: 'Error',
        status: 400,
        detail: 'raw',
        code,
      });
      expect(msg.length).toBeGreaterThan(10);
    }
  });
});

describe('OpenAPI contract & mock API enforcement', () => {
  it('serves /api/health without authentication and rejects unauthenticated /v1 calls with RFC 9457 Problem', async () => {
    const health = await apiClient.getHealth();
    expect(health).toEqual({ status: 'ok' });

    await expect(apiClient.getMe()).rejects.toMatchObject({
      problem: { status: 401, code: 'unauthorized' },
    });
  });

  it('enforces enrollment token states, non-owner admission gate, and agent-settings lock', async () => {
    await expect(
      apiClient.registerOptions({ enrollment_token: 'unknown-token-0000000000000000000000000000000' }),
    ).rejects.toMatchObject({
      problem: { status: 400, code: 'enrollment_token_invalid' },
    });

    await expect(
      apiClient.registerOptions({ enrollment_token: MOCK_EXPIRED_TOKEN }),
    ).rejects.toMatchObject({
      problem: { status: 409, code: 'enrollment_token_expired' },
    });

    await expect(
      apiClient.registerOptions({ enrollment_token: MOCK_USED_TOKEN }),
    ).rejects.toMatchObject({
      problem: { status: 409, code: 'enrollment_token_used' },
    });

    setMockAuthConfig({ nonOwnerAdmission: false });
    await expect(
      apiClient.registerOptions({ enrollment_token: MOCK_INVITE_TOKEN }),
    ).rejects.toMatchObject({
      problem: { status: 403, code: 'admission_disabled' },
    });
    setMockAuthConfig({ nonOwnerAdmission: true });

    signInMockUserForTests(MOCK_OWNER_USER_ID);
    await expect(apiClient.updateAgentSettings({ daily_run_limit: 5 })).rejects.toMatchObject({
      problem: { status: 403, code: 'agent_settings_locked' },
    });
  });

  it('enforces 3-slot limit, duplicate slot check, 30-min granularity, and stable slot IDs by position', async () => {
    signInMockUserForTests(MOCK_OWNER_USER_ID);
    const initial = await apiClient.getDeliverySlots();
    expect(initial.slots).toHaveLength(1);
    const firstSlotId = initial.slots[0].id;

    const updated = await apiClient.replaceDeliverySlots({
      slots: [
        { local_time: '08:30', enabled: true },
        { local_time: '18:00', enabled: false },
      ],
    });
    expect(updated.slots[0].id).toBe(firstSlotId);
    expect(updated.slots[0].position).toBe(1);
    expect(updated.slots[1].position).toBe(2);

    await expect(
      apiClient.replaceDeliverySlots({
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

    await expect(
      apiClient.replaceDeliverySlots({
        slots: [{ local_time: '07:15', enabled: true }],
      }),
    ).rejects.toMatchObject({
      problem: { status: 422, code: 'validation_error' },
    });
  });

  it('enforces POST /v1/jobs and GET /v1/jobs/{job_id} contract rules (202, 409, 429, 422, 403, 404)', async () => {
    signInMockUserForTests(MOCK_OWNER_USER_ID);

    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 1 });
    const job1 = await apiClient.generateNow();
    expect(job1.status).toBe('queued');
    expect(job1.trigger).toBe('manual');
    expect(job1.report_id).toBeNull();

    await expect(apiClient.generateNow()).rejects.toMatchObject({
      problem: { status: 409, code: 'job_in_progress' },
    });

    const poll1 = await apiClient.getJob(job1.id);
    expect(poll1.status).toBe('running');
    const poll2 = await apiClient.getJob(job1.id);
    expect(poll2.status).toBe('succeeded');
    expect(poll2.report_id).toBeTruthy();

    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 0 });
    const job2 = await apiClient.generateNow();
    await apiClient.getJob(job2.id);

    await expect(apiClient.generateNow()).rejects.toMatchObject({
      problem: { status: 429, code: 'daily_limit_reached' },
    });
  });
});

describe('Passkey auth, enrollment, security, and 401 handling', () => {
  it('loads blocking enroll-bootstrap.js before any module script in index.html without inline scripts (REVIEWS 9-1)', () => {
    const webRoot = path.resolve(__dirname, '../..');
    const indexHtml = fs.readFileSync(path.join(webRoot, 'index.html'), 'utf-8');
    const bootstrapPos = indexHtml.indexOf('<script src="/enroll-bootstrap.js"></script>');
    const modulePos = indexHtml.indexOf('<script type="module"');
    expect(bootstrapPos).toBeGreaterThan(-1);
    expect(modulePos).toBeGreaterThan(bootstrapPos);
    expect(indexHtml).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/i);

    const bootstrapJs = fs.readFileSync(
      path.join(webRoot, 'public/enroll-bootstrap.js'),
      'utf-8',
    );
    expect(bootstrapJs).toContain('history.replaceState');
    expect(bootstrapJs).not.toContain('localStorage');
    expect(bootstrapJs).not.toContain('sessionStorage');
  });

  it('extracts enrollment token from URL fragment and immediately strips it via history.replaceState', () => {
    clearEnrollmentToken();
    window.history.replaceState(null, '', '/enroll#t=secret-token-value-123');
    const extracted = extractEnrollmentTokenFromLocation();
    expect(extracted).toBe('secret-token-value-123');
    expect(getEnrollmentToken()).toBe('secret-token-value-123');
    expect(window.location.hash).toBe('');
    clearEnrollmentToken();
  });

  it('enrolls an invited user via /enroll#t=..., signs in with passkey, handles reauth_required, and enforces last_passkey', async () => {
    const user = userEvent.setup();
    installFakeBrowserPasskey('new-invitee-cred-1');
    clearEnrollmentToken();
    window.history.replaceState(null, '', `/enroll#t=${MOCK_INVITE_TOKEN}`);

    renderApp(['/enroll']);

    await waitFor(() => {
      expect(window.location.hash).toBe('');
    });
    await user.type(await screen.findByLabelText(/Display Name/i), 'Mei');
    await user.type(screen.getByLabelText(/Passkey Label/i), 'MacBook Touch ID');
    await user.click(screen.getByRole('button', { name: /Create Passkey & Sign In/i }));

    // Signed in as Mei (no reports yet, empty state)
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Security/i })).toBeInTheDocument();
    });

    // Navigate to Security page
    await user.click(screen.getByRole('link', { name: /Security/i }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Passkeys \(1\/10\)/i })).toBeInTheDocument();
    });
    expect(screen.getByText('MacBook Touch ID')).toBeInTheDocument();

    // Try deleting the only passkey -> triggers reauth_required prompt
    await user.click(screen.getByRole('button', { name: /Remove passkey MacBook Touch ID/i }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Verify Your Passkey/i })).toBeInTheDocument();
    });

    // Complete reauth -> delete retries -> 409 last_passkey plain-language error
    await user.click(screen.getByRole('button', { name: /^Verify passkey$/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(ERROR_MESSAGES.last_passkey);
    });

    // Now within 5-min reauth window, add a second passkey
    installFakeBrowserPasskey('new-invitee-cred-2');
    await user.type(screen.getByLabelText(/New Passkey Label/i), 'Backup Key');
    await user.click(screen.getByRole('button', { name: /Add passkey on this device/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Passkeys \(2\/10\)/i })).toBeInTheDocument();
      expect(screen.getByText('Backup Key')).toBeInTheDocument();
    });
  });

  it('transitions to signed-out SignInPage when a session becomes 401 unauthorized', async () => {
    const user = userEvent.setup();
    signInMockUserForTests(MOCK_OWNER_USER_ID);
    renderApp(['/']);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Stock Market' })).toBeInTheDocument();
    });

    // Simulate session revocation on the server
    signOutMockForTests();

    await user.click(screen.getByRole('link', { name: /Security/i }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Sign In to Daily Brief/i })).toBeInTheDocument();
      expect(screen.getByRole('alert')).toHaveTextContent(ERROR_MESSAGES.unauthorized);
    });
  });

  it('shows account_disabled message when a disabled user attempts passkey sign-in', async () => {
    const user = userEvent.setup();
    installFakeBrowserPasskey(MOCK_DEFAULT_PASSKEY_CRED_ID);
    setMockUserStatusForTests(MOCK_OWNER_USER_ID, 'disabled');

    renderApp(['/']);
    await user.click(await screen.findByRole('button', { name: /Sign in with a passkey/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(ERROR_MESSAGES.account_disabled);
    });
  });
});

describe('End-to-end user flows against the mock API', () => {
  it('signs in with passkey as seeded owner, displays today report, marks read state, and renders notes strictly as plain text', async () => {
    const user = userEvent.setup();
    installFakeBrowserPasskey(MOCK_DEFAULT_PASSKEY_CRED_ID);
    renderApp(['/']);

    expect(
      await screen.findByRole('heading', { name: /Sign In to Daily Brief/i }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Sign in with a passkey/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Stock Market' })).toBeInTheDocument();
    });

    const expectedToday = getTodayInTimezone('Asia/Taipei');
    expect(screen.getByLabelText(/Report Date/i)).toHaveValue(expectedToday);

    const olderEditionBtn = screen.getByRole('button', {
      name: /On-demand · 1\/1 unread/i,
    });
    const newestEditionBtn = screen.getByRole('button', {
      name: /Slot 07:00 · 4\/4 unread/i,
    });
    expect(olderEditionBtn).toHaveAttribute('aria-pressed', 'false');
    expect(newestEditionBtn).toHaveAttribute('aria-pressed', 'true');

    const firstItemHeading = screen.getByRole('heading', {
      name: /Semiconductor index edges higher/i,
    });
    const firstArticle = firstItemHeading.closest('article')!;
    const readCheckbox = within(firstArticle).getByRole('checkbox');
    await user.click(readCheckbox);

    await waitFor(() => {
      expect(readCheckbox).toBeChecked();
      expect(screen.getByRole('button', { name: /Slot 07:00 · 3\/4 unread/i })).toBeInTheDocument();
    });

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
  });

  it('manages topic subscriptions, item counts, and delivery slots (up to 3)', async () => {
    const user = userEvent.setup();
    signInMockUserForTests(MOCK_OWNER_USER_ID);
    renderApp(['/']);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Topics & Slots/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('link', { name: /Topics & Slots/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Subscribed Topics \(6\)/i })).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/Topic Title/i), 'Quantum Computing');
    const countInput = screen.getByLabelText(/Items per Report/i);
    await user.clear(countInput);
    await user.type(countInput, '5');
    await user.click(screen.getByRole('button', { name: /Subscribe Topic/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Subscribed Topics \(7\)/i })).toBeInTheDocument();
      expect(screen.getByText('Quantum Computing')).toBeInTheDocument();
    });

    const addSlotBtn = screen.getByRole('button', { name: /Add Slot/i });
    await user.click(addSlotBtn);
    await user.click(addSlotBtn);
    expect(screen.getByRole('heading', { name: /Delivery Slots \(3\/3\)/i })).toBeInTheDocument();
    expect(addSlotBtn).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Save Delivery Slots/i }));
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Delivery slots saved.');
    });
  });

  it('updates profile preferences (display_name, bio, timezone, locale) without email', async () => {
    const user = userEvent.setup();
    signInMockUserForTests(MOCK_OWNER_USER_ID);
    renderApp(['/']);

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
    expect(screen.queryByText(/Email:/i)).not.toBeInTheDocument();
  });

  it('handles Generate now button polling to succeeded/failed and displays 409, 429, and 422 error states', async () => {
    const user = userEvent.setup();
    signInMockUserForTests(MOCK_OWNER_USER_ID);
    renderApp(['/']);

    const generateBtn = await screen.findByRole('button', { name: /Generate now/i });
    setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 1 });
    await user.click(generateBtn);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /On-demand · 6\/6 unread/i })).toBeInTheDocument();
    });

    setMockJobBehavior({
      outcome: 'failed',
      pollsBeforeTerminal: 0,
      errorCode: 'llm_timeout',
    });
    await user.click(screen.getByRole('button', { name: /Generate now/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Brief generation failed (llm_timeout).');
    });

    await user.click(screen.getByRole('button', { name: /Generate now/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Daily limit reached, try tomorrow.');
    });
  });

  it('displays 422 no_topics with link to /settings and 409 job_in_progress on Generate now', async () => {
    const user = userEvent.setup();
    signInMockUserForTests(MOCK_OWNER_USER_ID);

    const { subscriptions } = await apiClient.listSubscriptions();
    for (const sub of subscriptions) {
      await apiClient.updateSubscription(sub.id, { enabled: false });
    }

    renderApp(['/']);
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

    await apiClient.updateSubscription(subscriptions[0].id, { enabled: true });
    await apiClient.generateNow();

    await user.click(screen.getByRole('button', { name: /Generate now/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        /A brief generation job is already in progress/i,
      );
    });
  });

  it('loads and displays the finished brief after Generate now succeeds even when getReport resolves asynchronously after a network delay (REVIEWS 5-1)', async () => {
    const user = userEvent.setup();
    installFakeBrowserPasskey(MOCK_DEFAULT_PASSKEY_CRED_ID);
    renderApp(['/']);

    await user.click(
      await screen.findByRole('button', { name: /Sign in with a passkey/i }),
    );

    const generateBtn = await screen.findByRole('button', { name: /Generate now/i });

    // Switch to an empty date first so the page shows "No Reports for 2026-11-01", matching the proof run
    const dateInput = screen.getByLabelText(/Report Date/i);
    await user.clear(dateInput);
    await user.type(dateInput, '2026-11-01');
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /No Reports for 2026-11-01/i }),
      ).toBeInTheDocument();
    });

    const originalGetReport = apiClient.getReport.bind(apiClient);
    const getReportSpy = vi
      .spyOn(apiClient, 'getReport')
      .mockImplementation(async (reportId) => {
        // Flush any state updates queued before getReport was awaited (simulating browser render while fetch is in flight)
        act(() => {});
        await new Promise((resolve) => window.setTimeout(resolve, 25));
        return originalGetReport(reportId);
      });

    try {
      setMockJobBehavior({ outcome: 'succeeded', pollsBeforeTerminal: 1 });
      await user.click(generateBtn);

      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /On-demand · 6\/6 unread/i }),
        ).toBeInTheDocument();
        expect(
          screen.getByRole('heading', {
            name: /On-demand brief update for Stock Market/i,
          }),
        ).toBeInTheDocument();
      });
    } finally {
      getReportSpy.mockRestore();
    }
  });
});
