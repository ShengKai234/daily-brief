import {
  DEV_OWNER_EMAIL,
  DEV_OWNER_USER_ID,
  decodeJwtClaims,
} from '../auth/jwt';
import { getTodayInTimezone } from '../utils/date';
import type {
  AgentSettings,
  DeliverySlot,
  DeliverySlotSet,
  DeliverySlotsReplace,
  ItemMark,
  ItemMarkUpdate,
  Problem,
  Profile,
  ProfileUpdate,
  RedeemInviteRequest,
  Report,
  ReportSection,
  ReportSummary,
  Subscription,
  SubscriptionCreate,
  SubscriptionUpdate,
  Topic,
  TopicCreate,
  TopicUpdate,
  UserJob,
} from './types';

export const DEFAULT_REPORT_DATE = getTodayInTimezone('Asia/Taipei');

export interface MockJobBehavior {
  pollsBeforeTerminal?: number;
  outcome?: 'succeeded' | 'failed';
  errorCode?: string;
}

interface UserStore {
  profile: Profile;
  topics: Topic[];
  subscriptions: Subscription[];
  slots: DeliverySlot[];
  agentSettings: AgentSettings;
  reports: Report[];
  marks: Record<string, ItemMark>;
  jobs: UserJob[];
}

interface MockDatabase {
  users: Record<string, UserStore>;
  allowlistEmails: Set<string>;
  validInviteCodes: Record<string, { status: 'valid' | 'expired' | 'exhausted' }>;
}

const LOCAL_TIME_REGEX = /^([01][0-9]|2[0-3]):(00|30)$/;

function normalizeKey(title: string): string {
  return title.trim().toLowerCase().replace(/\s+/g, '-');
}

function nowIso(): string {
  return '2026-11-12T07:05:00Z';
}

function createInitialOwnerStore(): UserStore {
  const createdAt = '2026-11-01T00:00:00Z';
  const defaultTopicTitles = [
    { id: '10000000-0000-4000-8000-000000000001', title: 'Stock Market' },
    { id: '10000000-0000-4000-8000-000000000002', title: 'AI' },
    { id: '10000000-0000-4000-8000-000000000003', title: 'Information Security' },
    { id: '10000000-0000-4000-8000-000000000004', title: 'Weather' },
    { id: '10000000-0000-4000-8000-000000000005', title: 'Ruby and Python Releases' },
    { id: '10000000-0000-4000-8000-000000000006', title: 'Travel News' },
  ];

  const topics: Topic[] = defaultTopicTitles.map((t) => ({
    id: t.id,
    title: t.title,
    normalized_key: normalizeKey(t.title),
    visibility: 'private',
    created_at: createdAt,
    updated_at: createdAt,
  }));

  const subscriptions: Subscription[] = topics.map((topic, index) => ({
    id: `20000000-0000-4000-8000-00000000000${index + 1}`,
    topic_id: topic.id,
    topic,
    item_count: 3,
    position: index,
    enabled: true,
  }));

  const slots: DeliverySlot[] = [
    {
      id: '30000000-0000-4000-8000-000000000001',
      local_time: '07:00',
      enabled: true,
      position: 1,
    },
  ];

  const initialSections: ReportSection[] = [
    {
      id: '50000000-0000-4000-8000-000000000001',
      topic_id: topics[0].id,
      title: topics[0].title,
      position: 0,
      status: 'ok',
      note: null,
      items: [
        {
          id: '60000000-0000-4000-8000-000000000001',
          rank: 1,
          title: 'Semiconductor index edges higher on cloud capital expenditure outlook',
          summary:
            'Major foundry and chip equipment shares gained in Taipei and Tokyo trading after cloud providers reaffirmed infrastructure budgets for the upcoming quarter.',
          source_name: 'Market Daily',
          source_url: 'https://example.com/news/semiconductor-capex',
          published_at: '2026-11-12T05:30:00Z',
          mark: null,
        },
        {
          id: '60000000-0000-4000-8000-000000000002',
          rank: 2,
          title: 'Central banks hold benchmark policy rates steady amid stable inflation print',
          summary:
            'Policymakers signaled patience on further rate adjustments as core consumer price growth remained within target bands across major economies.',
          source_name: 'Global Econ Wire',
          source_url: 'https://example.com/news/central-bank-rates',
          published_at: '2026-11-12T04:15:00Z',
          mark: null,
        },
      ],
    },
    {
      id: '50000000-0000-4000-8000-000000000002',
      topic_id: topics[1].id,
      title: topics[1].title,
      position: 1,
      status: 'ok',
      note: null,
      items: [
        {
          id: '60000000-0000-4000-8000-000000000003',
          rank: 1,
          title: 'Open-weights reasoning evaluation benchmark adds multi-step tool verification',
          summary:
            'Researchers introduced a reproducible benchmark measuring long-horizon code and schema verification across structured API contracts.',
          source_name: 'AI Research Digest',
          source_url: 'https://example.com/news/reasoning-benchmark',
          published_at: '2026-11-12T06:00:00Z',
          mark: null,
        },
      ],
    },
    {
      id: '50000000-0000-4000-8000-000000000003',
      topic_id: topics[2].id,
      title: topics[2].title,
      position: 2,
      status: 'ok',
      note: null,
      items: [
        {
          id: '60000000-0000-4000-8000-000000000004',
          rank: 1,
          title: 'TLS library maintainers issue patch for certificate chain parsing flaw',
          summary:
            'A maintenance update resolves a denial-of-service vector triggered by malformed client certificates during handshake verification.',
          source_name: 'SecAdvisory',
          source_url: 'https://example.com/news/tls-patch',
          published_at: '2026-11-12T03:40:00Z',
          mark: null,
        },
      ],
    },
    {
      id: '50000000-0000-4000-8000-000000000004',
      topic_id: topics[4].id,
      title: topics[4].title,
      position: 3,
      status: 'insufficient_news',
      note: 'No new official Ruby or Python point releases were published in the last 24 hours.',
      items: [],
    },
  ];

  const ownerTimezone = 'Asia/Taipei';
  const todayDate = getTodayInTimezone(ownerTimezone);

  const olderReport: Report = {
    id: '40000000-0000-4000-8000-000000000000',
    report_date: todayDate,
    slot_id: null,
    generated_at: '2026-11-12T05:00:00Z',
    visibility: 'private',
    sections: [
      {
        id: '50000000-0000-4000-8000-000000000000',
        topic_id: topics[0].id,
        title: topics[0].title,
        position: 0,
        status: 'ok',
        note: null,
        items: [
          {
            id: '60000000-0000-4000-8000-000000000000',
            rank: 1,
            title: 'Earlier pre-market digest before scheduled slot',
            summary:
              'Overnight futures stayed flat ahead of the regular morning session.',
            source_name: 'Pre-Market Wire',
            source_url: 'https://example.com/news/pre-market-digest',
            published_at: '2026-11-12T04:00:00Z',
            mark: null,
          },
        ],
      },
    ],
  };

  const report: Report = {
    id: '40000000-0000-4000-8000-000000000001',
    report_date: todayDate,
    slot_id: slots[0].id,
    generated_at: '2026-11-12T07:02:00Z',
    visibility: 'private',
    sections: initialSections,
  };

  return {
    profile: {
      user_id: DEV_OWNER_USER_ID,
      email: DEV_OWNER_EMAIL,
      display_name: 'Owner',
      avatar_url: null,
      bio: 'Daily Brief owner account',
      timezone: ownerTimezone,
      locale: 'zh-TW',
      status: 'active',
      created_at: createdAt,
      updated_at: createdAt,
    },
    topics,
    subscriptions,
    slots,
    agentSettings: {
      runner: 'claude_subscription',
      model: 'sonnet',
      daily_run_limit: 3,
    },
    reports: [olderReport, report],
    marks: {},
    jobs: [
      {
        id: '70000000-0000-4000-8000-000000000001',
        status: 'succeeded',
        trigger: 'schedule',
        created_at: '2026-11-12T07:00:00Z',
        report_id: report.id,
        error_code: null,
      },
    ],
  };
}

function createInitialDb(): MockDatabase {
  return {
    users: {
      [DEV_OWNER_USER_ID]: createInitialOwnerStore(),
    },
    allowlistEmails: new Set([DEV_OWNER_EMAIL]),
    validInviteCodes: {
      'WELCOME-2026': { status: 'valid' },
      'EXAMPLE-CODE': { status: 'valid' },
      'EXPIRED-CODE': { status: 'expired' },
      'USED-CODE': { status: 'exhausted' },
    },
  };
}

let db: MockDatabase = createInitialDb();
let idCounter = 100;
let mockJobBehavior: MockJobBehavior = {
  pollsBeforeTerminal: 0,
  outcome: 'succeeded',
  errorCode: 'worker_failed',
};
let jobPollCounts: Record<string, number> = {};

function nextUuid(prefix: string = '90000000'): string {
  idCounter += 1;
  const tail = String(idCounter).padStart(12, '0');
  return `${prefix}-0000-4000-8000-${tail}`;
}

export function setMockJobBehavior(behavior: MockJobBehavior): void {
  mockJobBehavior = {
    pollsBeforeTerminal: behavior.pollsBeforeTerminal ?? 0,
    outcome: behavior.outcome ?? 'succeeded',
    errorCode: behavior.errorCode ?? 'worker_failed',
  };
}

export function resetMockApiState(): void {
  db = createInitialDb();
  idCounter = 100;
  mockJobBehavior = {
    pollsBeforeTerminal: 0,
    outcome: 'succeeded',
    errorCode: 'worker_failed',
  };
  jobPollCounts = {};
}

function problemResponse(
  status: number,
  code: string,
  title: string,
  detail: string,
  errors?: { field: string; message: string }[],
): Response {
  const body: Problem = {
    type: 'about:blank',
    title,
    status,
    detail,
    code,
    ...(errors ? { errors } : {}),
  };
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  });
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function getOrCreateUserStore(sub: string, email: string): UserStore {
  const existing = db.users[sub];
  if (existing) {
    return existing;
  }
  const createdAt = nowIso();
  const isDisabled = email.toLowerCase() === 'disabled@example.com';
  const isAllowed = db.allowlistEmails.has(email.toLowerCase());
  const status: Profile['status'] = isDisabled
    ? 'disabled'
    : isAllowed
      ? 'active'
      : 'pending';

  const newStore: UserStore = {
    profile: {
      user_id: sub,
      email,
      display_name: email.split('@')[0] || 'User',
      avatar_url: null,
      bio: '',
      timezone: 'Asia/Taipei',
      locale: 'zh-TW',
      status,
      created_at: createdAt,
      updated_at: createdAt,
    },
    topics: [],
    subscriptions: [],
    slots: [],
    agentSettings: {
      runner: 'claude_subscription',
      model: 'sonnet',
      daily_run_limit: 3,
    },
    reports: [],
    marks: {},
    jobs: [],
  };
  db.users[sub] = newStore;
  return newStore;
}

function buildReportWithMarks(report: Report, marks: Record<string, ItemMark>): Report {
  return {
    ...report,
    sections: report.sections.map((section) => ({
      ...section,
      items: section.items.map((item) => ({
        ...item,
        mark: marks[item.id] ?? null,
      })),
    })),
  };
}

function buildReportSummary(
  report: Report,
  slots: DeliverySlot[],
  marks: Record<string, ItemMark>,
): ReportSummary {
  const slot = slots.find((s) => s.id === report.slot_id) ?? null;
  let itemCount = 0;
  let unreadCount = 0;
  for (const section of report.sections) {
    for (const item of section.items) {
      itemCount += 1;
      const mark = marks[item.id];
      if (!mark || !mark.read_at) {
        unreadCount += 1;
      }
    }
  }
  return {
    id: report.id,
    report_date: report.report_date,
    slot_id: report.slot_id,
    local_time: report.slot_id ? (slot ? slot.local_time : '07:00') : null,
    generated_at: report.generated_at,
    section_count: report.sections.length,
    item_count: itemCount,
    unread_count: unreadCount,
  };
}

async function parseJsonBody<T>(init?: RequestInit): Promise<T | null> {
  if (!init?.body || typeof init.body !== 'string') {
    return null;
  }
  try {
    return JSON.parse(init.body) as T;
  } catch {
    return null;
  }
}

export async function mockFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, 'http://localhost');
  const method = (init?.method || 'GET').toUpperCase();
  const pathname = url.pathname;

  if (pathname === '/api/health' && method === 'GET') {
    return jsonResponse(200, { status: 'ok' });
  }

  if (!pathname.startsWith('/api/v1/')) {
    return problemResponse(404, 'not_found', 'Not Found', `Route ${pathname} not found.`);
  }

  const headers = new Headers(init?.headers);
  const authHeader = headers.get('Authorization') || '';
  if (!authHeader.startsWith('Bearer ')) {
    return problemResponse(
      401,
      'unauthorized',
      'Unauthorized',
      'Missing or invalid Bearer token.',
    );
  }
  const token = authHeader.slice('Bearer '.length).trim();
  const claims = decodeJwtClaims(token);
  if (!claims) {
    return problemResponse(401, 'unauthorized', 'Unauthorized', 'Malformed JWT token.');
  }

  const store = getOrCreateUserStore(claims.sub, claims.email);

  if (store.profile.status === 'disabled') {
    return problemResponse(
      403,
      'account_disabled',
      'Account Disabled',
      'This account has been disabled.',
    );
  }

  const isAllowedForPending =
    (pathname === '/api/v1/me' && method === 'GET') ||
    (pathname === '/api/v1/auth/redeem-invite' && method === 'POST');

  if (store.profile.status === 'pending' && !isAllowedForPending) {
    return problemResponse(
      403,
      'account_pending',
      'Account Pending',
      'Redeem an invite code to activate this account.',
    );
  }

  // GET / PATCH /api/v1/me
  if (pathname === '/api/v1/me') {
    if (method === 'GET') {
      return jsonResponse(200, store.profile);
    }
    if (method === 'PATCH') {
      const body = await parseJsonBody<ProfileUpdate>(init);
      if (!body || Object.keys(body).length === 0) {
        return problemResponse(400, 'bad_request', 'Bad Request', 'Empty update payload.');
      }
      if (body.display_name !== undefined && body.display_name.length > 80) {
        return problemResponse(422, 'validation_error', 'Validation Error', 'Display name max 80 chars.', [
          { field: 'display_name', message: 'Must be at most 80 characters.' },
        ]);
      }
      if (body.bio !== undefined && body.bio.length > 500) {
        return problemResponse(422, 'validation_error', 'Validation Error', 'Bio max 500 chars.', [
          { field: 'bio', message: 'Must be at most 500 characters.' },
        ]);
      }
      if (body.locale !== undefined && (body.locale.length < 2 || body.locale.length > 35)) {
        return problemResponse(
          422,
          'validation_error',
          'Validation Error',
          'Locale must be between 2 and 35 characters.',
          [{ field: 'locale', message: 'Must be 2–35 characters.' }],
        );
      }

      store.profile = {
        ...store.profile,
        ...(body.display_name !== undefined ? { display_name: body.display_name } : {}),
        ...(body.avatar_url !== undefined ? { avatar_url: body.avatar_url } : {}),
        ...(body.bio !== undefined ? { bio: body.bio } : {}),
        ...(body.timezone !== undefined ? { timezone: body.timezone } : {}),
        ...(body.locale !== undefined ? { locale: body.locale } : {}),
        updated_at: nowIso(),
      };
      return jsonResponse(200, store.profile);
    }
  }

  // POST /api/v1/auth/redeem-invite
  if (pathname === '/api/v1/auth/redeem-invite' && method === 'POST') {
    const body = await parseJsonBody<RedeemInviteRequest>(init);
    const code = body?.code?.trim() || '';
    if (!code) {
      return problemResponse(422, 'validation_error', 'Validation Error', 'Invite code is required.');
    }
    if (store.profile.status === 'active') {
      return problemResponse(409, 'already_active', 'Already Active', 'Account is already active.');
    }
    const entry = db.validInviteCodes[code];
    if (!entry) {
      return problemResponse(404, 'invite_not_found', 'Invite Not Found', 'Invite code does not exist.');
    }
    if (entry.status === 'expired') {
      return problemResponse(409, 'invite_expired', 'Invite Expired', 'Invite code has expired.');
    }
    if (entry.status === 'exhausted') {
      return problemResponse(409, 'invite_exhausted', 'Invite Exhausted', 'Invite code has reached max uses.');
    }
    store.profile = {
      ...store.profile,
      status: 'active',
      updated_at: nowIso(),
    };
    return jsonResponse(200, store.profile);
  }

  // GET / POST /api/v1/topics
  if (pathname === '/api/v1/topics') {
    if (method === 'GET') {
      return jsonResponse(200, { topics: store.topics });
    }
    if (method === 'POST') {
      const body = await parseJsonBody<TopicCreate>(init);
      const title = body?.title?.trim() || '';
      if (!title || title.length > 200) {
        return problemResponse(422, 'validation_error', 'Validation Error', 'Title must be 1–200 chars.');
      }
      if (body?.visibility === 'public') {
        return problemResponse(
          422,
          'visibility_not_supported',
          'Visibility Not Supported',
          'Only private topics are supported in the POC.',
        );
      }
      const key = normalizeKey(title);
      if (store.topics.some((t) => t.normalized_key === key)) {
        return problemResponse(409, 'topic_exists', 'Topic Exists', 'A topic with this title already exists.');
      }
      const timestamp = nowIso();
      const created: Topic = {
        id: nextUuid('11000000'),
        title,
        normalized_key: key,
        visibility: 'private',
        created_at: timestamp,
        updated_at: timestamp,
      };
      store.topics.push(created);
      return jsonResponse(201, created);
    }
  }

  // PATCH / DELETE /api/v1/topics/{topic_id}
  const topicMatch = pathname.match(/^\/api\/v1\/topics\/([^/]+)$/);
  if (topicMatch) {
    const topicId = topicMatch[1];
    const index = store.topics.findIndex((t) => t.id === topicId);
    if (index === -1) {
      return problemResponse(404, 'not_found', 'Not Found', 'Topic not found.');
    }
    if (method === 'PATCH') {
      const body = await parseJsonBody<TopicUpdate>(init);
      if (!body || Object.keys(body).length === 0) {
        return problemResponse(400, 'bad_request', 'Bad Request', 'Empty update payload.');
      }
      if (body.visibility === 'public') {
        return problemResponse(
          422,
          'visibility_not_supported',
          'Visibility Not Supported',
          'Only private topics are supported in the POC.',
        );
      }
      if (body.title !== undefined) {
        const trimmed = body.title.trim();
        if (!trimmed || trimmed.length > 200) {
          return problemResponse(422, 'validation_error', 'Validation Error', 'Title must be 1–200 chars.');
        }
        const key = normalizeKey(trimmed);
        if (store.topics.some((t, idx) => idx !== index && t.normalized_key === key)) {
          return problemResponse(409, 'topic_exists', 'Topic Exists', 'Topic already exists.');
        }
        store.topics[index] = {
          ...store.topics[index],
          title: trimmed,
          normalized_key: key,
          updated_at: nowIso(),
        };
        store.subscriptions = store.subscriptions.map((sub) =>
          sub.topic_id === topicId ? { ...sub, topic: store.topics[index] } : sub,
        );
      }
      return jsonResponse(200, store.topics[index]);
    }
    if (method === 'DELETE') {
      store.topics.splice(index, 1);
      store.subscriptions = store.subscriptions
        .filter((s) => s.topic_id !== topicId)
        .map((s, idx) => ({ ...s, position: idx }));
      return new Response(null, { status: 204 });
    }
  }

  // GET / POST /api/v1/subscriptions
  if (pathname === '/api/v1/subscriptions') {
    if (method === 'GET') {
      const ordered = [...store.subscriptions].sort((a, b) => a.position - b.position);
      return jsonResponse(200, { subscriptions: ordered });
    }
    if (method === 'POST') {
      const body = await parseJsonBody<SubscriptionCreate>(init);
      if (!body) {
        return problemResponse(400, 'bad_request', 'Bad Request', 'Missing subscription body.');
      }
      const itemCount = body.item_count ?? 3;
      if (!Number.isInteger(itemCount) || itemCount < 1 || itemCount > 10) {
        return problemResponse(
          422,
          'validation_error',
          'Validation Error',
          'item_count must be between 1 and 10.',
        );
      }

      let topic: Topic | undefined;
      if ('topic_id' in body && body.topic_id) {
        topic = store.topics.find((t) => t.id === body.topic_id);
        if (!topic) {
          return problemResponse(404, 'not_found', 'Not Found', 'Topic not found.');
        }
      } else if ('title' in body && body.title) {
        const trimmed = body.title.trim();
        if (!trimmed || trimmed.length > 200) {
          return problemResponse(422, 'validation_error', 'Validation Error', 'Title must be 1–200 chars.');
        }
        const key = normalizeKey(trimmed);
        topic = store.topics.find((t) => t.normalized_key === key);
        if (!topic) {
          const timestamp = nowIso();
          topic = {
            id: nextUuid('11000000'),
            title: trimmed,
            normalized_key: key,
            visibility: 'private',
            created_at: timestamp,
            updated_at: timestamp,
          };
          store.topics.push(topic);
        }
      } else {
        return problemResponse(422, 'validation_error', 'Validation Error', 'Provide topic_id or title.');
      }

      if (store.subscriptions.some((s) => s.topic_id === topic.id)) {
        return problemResponse(
          409,
          'already_subscribed',
          'Already Subscribed',
          'Already subscribed to this topic.',
        );
      }

      const position = body.position ?? store.subscriptions.length;
      const created: Subscription = {
        id: nextUuid('21000000'),
        topic_id: topic.id,
        topic,
        item_count: itemCount,
        position,
        enabled: body.enabled ?? true,
      };
      store.subscriptions.push(created);
      store.subscriptions.sort((a, b) => a.position - b.position);
      store.subscriptions = store.subscriptions.map((s, idx) => ({ ...s, position: idx }));
      const stored = store.subscriptions.find((s) => s.id === created.id)!;
      return jsonResponse(201, stored);
    }
  }

  // PATCH / DELETE /api/v1/subscriptions/{subscription_id}
  const subMatch = pathname.match(/^\/api\/v1\/subscriptions\/([^/]+)$/);
  if (subMatch) {
    const subId = subMatch[1];
    const index = store.subscriptions.findIndex((s) => s.id === subId);
    if (index === -1) {
      return problemResponse(404, 'not_found', 'Not Found', 'Subscription not found.');
    }
    if (method === 'PATCH') {
      const body = await parseJsonBody<SubscriptionUpdate>(init);
      if (!body || Object.keys(body).length === 0) {
        return problemResponse(400, 'bad_request', 'Bad Request', 'Empty update payload.');
      }
      if (
        body.item_count !== undefined &&
        (!Number.isInteger(body.item_count) || body.item_count < 1 || body.item_count > 10)
      ) {
        return problemResponse(
          422,
          'validation_error',
          'Validation Error',
          'item_count must be between 1 and 10.',
        );
      }

      const current = store.subscriptions[index];
      const updated: Subscription = {
        ...current,
        ...(body.item_count !== undefined ? { item_count: body.item_count } : {}),
        ...(body.enabled !== undefined ? { enabled: body.enabled } : {}),
      };

      if (body.position !== undefined && body.position !== current.position) {
        if (body.position < 0) {
          return problemResponse(422, 'validation_error', 'Validation Error', 'position must be >= 0.');
        }
        const remaining = store.subscriptions.filter((s) => s.id !== subId);
        const targetIndex = Math.min(body.position, remaining.length);
        remaining.splice(targetIndex, 0, updated);
        store.subscriptions = remaining.map((s, idx) => ({ ...s, position: idx }));
      } else {
        store.subscriptions[index] = updated;
      }

      const result = store.subscriptions.find((s) => s.id === subId)!;
      return jsonResponse(200, result);
    }
    if (method === 'DELETE') {
      store.subscriptions.splice(index, 1);
      store.subscriptions = store.subscriptions.map((s, idx) => ({ ...s, position: idx }));
      return new Response(null, { status: 204 });
    }
  }

  // GET / PUT /api/v1/delivery-slots
  if (pathname === '/api/v1/delivery-slots') {
    if (method === 'GET') {
      const payload: DeliverySlotSet = {
        slots: [...store.slots].sort((a, b) => a.position - b.position),
        timezone: store.profile.timezone,
      };
      return jsonResponse(200, payload);
    }
    if (method === 'PUT') {
      const body = await parseJsonBody<DeliverySlotsReplace>(init);
      if (!body || !Array.isArray(body.slots)) {
        return problemResponse(400, 'bad_request', 'Bad Request', 'Invalid slots payload.');
      }
      if (body.slots.length > 3) {
        return problemResponse(
          422,
          'slot_limit_exceeded',
          'Slot Limit Exceeded',
          'At most 3 delivery slots are allowed per user.',
        );
      }
      const seenTimes = new Set<string>();
      for (const slot of body.slots) {
        if (!LOCAL_TIME_REGEX.test(slot.local_time)) {
          return problemResponse(
            422,
            'validation_error',
            'Validation Error',
            'local_time must be HH:MM in 30-minute steps.',
          );
        }
        if (seenTimes.has(slot.local_time)) {
          return problemResponse(
            422,
            'duplicate_slot',
            'Duplicate Slot',
            `Duplicate slot time ${slot.local_time}.`,
          );
        }
        seenTimes.add(slot.local_time);
      }

      // Preserve slot IDs by position per DECISIONS.md
      const previousByPosition = new Map(store.slots.map((s) => [s.position, s.id]));
      store.slots = body.slots.map((slot, idx) => {
        const position = idx + 1;
        return {
          id: previousByPosition.get(position) || nextUuid('31000000'),
          local_time: slot.local_time,
          enabled: slot.enabled,
          position,
        };
      });

      const payload: DeliverySlotSet = {
        slots: store.slots,
        timezone: store.profile.timezone,
      };
      return jsonResponse(200, payload);
    }
  }

  // GET /api/v1/reports?date=...
  if (pathname === '/api/v1/reports' && method === 'GET') {
    const requestedDate =
      url.searchParams.get('date') || getTodayInTimezone(store.profile.timezone);
    const matching = store.reports
      .filter((r) => r.report_date === requestedDate)
      .sort((a, b) => a.generated_at.localeCompare(b.generated_at));
    const summaries = matching.map((r) => buildReportSummary(r, store.slots, store.marks));
    return jsonResponse(200, { reports: summaries });
  }

  // GET /api/v1/reports/{report_id}
  const reportMatch = pathname.match(/^\/api\/v1\/reports\/([^/]+)$/);
  if (reportMatch && method === 'GET') {
    const reportId = reportMatch[1];
    const report = store.reports.find((r) => r.id === reportId);
    if (!report) {
      return problemResponse(404, 'not_found', 'Not Found', 'Report not found.');
    }
    return jsonResponse(200, buildReportWithMarks(report, store.marks));
  }

  // PUT /api/v1/items/{item_id}/mark
  const markMatch = pathname.match(/^\/api\/v1\/items\/([^/]+)\/mark$/);
  if (markMatch && method === 'PUT') {
    const itemId = markMatch[1];
    const itemExists = store.reports.some((r) =>
      r.sections.some((sec) => sec.items.some((it) => it.id === itemId)),
    );
    if (!itemExists) {
      return problemResponse(404, 'not_found', 'Not Found', 'Report item not found.');
    }
    const body = await parseJsonBody<ItemMarkUpdate>(init);
    if (!body || typeof body.read !== 'boolean') {
      return problemResponse(400, 'bad_request', 'Bad Request', 'Invalid mark payload.');
    }
    if (body.note !== null && body.note !== undefined && body.note.length > 2000) {
      return problemResponse(
        422,
        'validation_error',
        'Validation Error',
        'Note must be at most 2000 characters.',
      );
    }
    const timestamp = nowIso();
    const existing = store.marks[itemId];
    const mark: ItemMark = {
      item_id: itemId,
      read_at: body.read ? existing?.read_at || timestamp : null,
      note: body.note && body.note.length > 0 ? body.note : null,
      updated_at: timestamp,
    };
    store.marks[itemId] = mark;
    return jsonResponse(200, mark);
  }

  // GET / PATCH /api/v1/agent-settings
  if (pathname === '/api/v1/agent-settings') {
    if (method === 'GET') {
      return jsonResponse(200, store.agentSettings);
    }
    if (method === 'PATCH') {
      return problemResponse(
        403,
        'agent_settings_locked',
        'Agent Settings Locked',
        'Agent settings are reserved in the POC and cannot be modified.',
      );
    }
  }

  // POST /api/v1/jobs (generateNow)
  if (pathname === '/api/v1/jobs' && method === 'POST') {
    if (
      store.agentSettings.runner === 'claude_subscription' &&
      store.profile.user_id !== DEV_OWNER_USER_ID
    ) {
      return problemResponse(
        403,
        'owner_only',
        'Owner Only',
        'The subscription runner may only process jobs for the configured owner.',
      );
    }

    if (store.jobs.some((j) => j.status === 'queued' || j.status === 'running')) {
      return problemResponse(
        409,
        'job_in_progress',
        'Job In Progress',
        'A brief generation job is already queued or running.',
      );
    }

    const enabledSubs = [...store.subscriptions]
      .filter((s) => s.enabled)
      .sort((a, b) => a.position - b.position);
    if (enabledSubs.length === 0) {
      return problemResponse(
        422,
        'no_topics',
        'No Enabled Topics',
        'Subscribe to and enable at least one topic before generating a brief.',
      );
    }

    if (store.jobs.length >= store.agentSettings.daily_run_limit) {
      return problemResponse(
        429,
        'daily_limit_reached',
        'Daily Limit Reached',
        'Daily run limit reached; try again tomorrow.',
      );
    }

    const createdJob: UserJob = {
      id: nextUuid('71000000'),
      status: 'queued',
      trigger: 'manual',
      created_at: nowIso(),
      report_id: null,
      error_code: null,
    };
    store.jobs.push(createdJob);
    jobPollCounts[createdJob.id] = 0;
    return jsonResponse(202, createdJob);
  }

  // GET /api/v1/jobs/{job_id} (getJob)
  const jobMatch = pathname.match(/^\/api\/v1\/jobs\/([^/]+)$/);
  if (jobMatch && method === 'GET') {
    const jobId = jobMatch[1];
    const jobIndex = store.jobs.findIndex((j) => j.id === jobId);
    if (jobIndex === -1) {
      return problemResponse(404, 'not_found', 'Not Found', 'Job not found.');
    }

    const job = store.jobs[jobIndex];
    if (job.status === 'queued' || job.status === 'running') {
      const count = (jobPollCounts[job.id] ?? 0) + 1;
      jobPollCounts[job.id] = count;
      const requiredPolls = mockJobBehavior.pollsBeforeTerminal ?? 0;

      if (count <= requiredPolls) {
        const runningJob: UserJob = {
          ...job,
          status: 'running',
        };
        store.jobs[jobIndex] = runningJob;
        return jsonResponse(200, runningJob);
      }

      if (mockJobBehavior.outcome === 'failed') {
        const failedJob: UserJob = {
          ...job,
          status: 'failed',
          report_id: null,
          error_code: mockJobBehavior.errorCode || 'worker_failed',
        };
        store.jobs[jobIndex] = failedJob;
        return jsonResponse(200, failedJob);
      }

      const enabledSubs = [...store.subscriptions]
        .filter((s) => s.enabled)
        .sort((a, b) => a.position - b.position);

      const newReportId = nextUuid('41000000');
      const newSections: ReportSection[] = enabledSubs.map((sub, idx) => ({
        id: nextUuid('51000000'),
        topic_id: sub.topic_id,
        title: sub.topic.title,
        position: idx,
        status: 'ok',
        note: null,
        items: [
          {
            id: nextUuid('61000000'),
            rank: 1,
            title: `On-demand brief update for ${sub.topic.title}`,
            summary: `Fresh on-demand summary compiled for ${sub.topic.title} during manual brief run.`,
            source_name: 'On-Demand Wire',
            source_url: 'https://example.com/news/on-demand-update',
            published_at: '2026-11-12T10:40:00Z',
            mark: null,
          },
        ],
      }));

      const latestMs = store.reports.reduce(
        (max, r) => Math.max(max, Date.parse(r.generated_at) || 0),
        Date.parse('2026-11-12T10:44:00Z'),
      );
      const nextGeneratedAt = new Date(latestMs + 60_000)
        .toISOString()
        .replace('.000Z', 'Z');

      const newReport: Report = {
        id: newReportId,
        report_date: getTodayInTimezone(store.profile.timezone),
        slot_id: null,
        generated_at: nextGeneratedAt,
        visibility: 'private',
        sections: newSections,
      };
      store.reports.push(newReport);

      const succeededJob: UserJob = {
        ...job,
        status: 'succeeded',
        report_id: newReportId,
        error_code: null,
      };
      store.jobs[jobIndex] = succeededJob;
      return jsonResponse(200, succeededJob);
    }

    return jsonResponse(200, job);
  }

  return problemResponse(404, 'not_found', 'Not Found', `Unhandled route ${method} ${pathname}`);
}
