import { mockFetch } from './mockApi';
import type {
  AgentSettings,
  AgentSettingsUpdate,
  CeremonyOptions,
  CeremonyVerifyRequest,
  DeliverySlotSet,
  DeliverySlotsReplace,
  HealthResponse,
  ItemMark,
  ItemMarkUpdate,
  ListPasskeysResponse,
  ListReportsResponse,
  ListSessionsResponse,
  ListSubscriptionsResponse,
  ListTopicsResponse,
  Passkey,
  Problem,
  Profile,
  ProfileUpdate,
  RegistrationOptionsRequest,
  RegistrationVerifyRequest,
  Report,
  Subscription,
  SubscriptionCreate,
  SubscriptionUpdate,
  Topic,
  TopicCreate,
  TopicUpdate,
  UserJob,
} from './types';

export class ApiError extends Error {
  public readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.name = 'ApiError';
    this.problem = problem;
  }
}

export function isMockApiEnabled(): boolean {
  const envFlag = import.meta.env.VITE_USE_MOCK_API;
  if (envFlag === 'false') {
    return false;
  }
  return true;
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    skipUnauthorizedHandler?: boolean;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
  };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const init: RequestInit = {
    method: options.method || 'GET',
    credentials: 'same-origin',
    headers,
    ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
  };

  const url = `/api${path}`;
  const fetcher = isMockApiEnabled() ? mockFetch : fetch;
  const response = await fetcher(url, init);

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    let problem: Problem;
    try {
      const parsed = (await response.json()) as Partial<Problem>;
      problem = {
        type: parsed.type || 'about:blank',
        title: parsed.title || response.statusText || 'Error',
        status: parsed.status || response.status,
        detail: parsed.detail || 'Request failed.',
        code: parsed.code || 'unknown_error',
        ...(parsed.errors ? { errors: parsed.errors } : {}),
      };
    } catch {
      problem = {
        type: 'about:blank',
        title: response.statusText || 'Error',
        status: response.status,
        detail: `HTTP ${response.status}`,
        code: 'http_error',
      };
    }

    if (
      response.status === 401 &&
      problem.code !== 'authentication_failed' &&
      !options.skipUnauthorizedHandler &&
      unauthorizedHandler
    ) {
      unauthorizedHandler();
    }

    throw new ApiError(problem);
  }

  return (await response.json()) as T;
}

export const apiClient = {
  getHealth(): Promise<HealthResponse> {
    return request<HealthResponse>('/health', { skipUnauthorizedHandler: true });
  },

  getMe(options?: { skipUnauthorizedHandler?: boolean }): Promise<Profile> {
    return request<Profile>('/v1/me', {
      skipUnauthorizedHandler: options?.skipUnauthorizedHandler,
    });
  },

  updateMe(body: ProfileUpdate): Promise<Profile> {
    return request<Profile>('/v1/me', { method: 'PATCH', body });
  },

  registerOptions(body: RegistrationOptionsRequest): Promise<CeremonyOptions> {
    return request<CeremonyOptions>('/v1/auth/registration/options', {
      method: 'POST',
      body,
      skipUnauthorizedHandler: true,
    });
  },

  registerVerify(body: RegistrationVerifyRequest): Promise<Profile> {
    return request<Profile>('/v1/auth/registration/verify', {
      method: 'POST',
      body,
      skipUnauthorizedHandler: true,
    });
  },

  loginOptions(): Promise<CeremonyOptions> {
    return request<CeremonyOptions>('/v1/auth/login/options', {
      method: 'POST',
      body: {},
      skipUnauthorizedHandler: true,
    });
  },

  loginVerify(body: CeremonyVerifyRequest): Promise<Profile> {
    return request<Profile>('/v1/auth/login/verify', {
      method: 'POST',
      body,
      skipUnauthorizedHandler: true,
    });
  },

  logout(): Promise<void> {
    return request<void>('/v1/auth/logout', {
      method: 'POST',
      skipUnauthorizedHandler: true,
    });
  },

  reauthOptions(): Promise<CeremonyOptions> {
    return request<CeremonyOptions>('/v1/auth/reauth/options', {
      method: 'POST',
    });
  },

  reauthVerify(body: CeremonyVerifyRequest): Promise<void> {
    return request<void>('/v1/auth/reauth/verify', {
      method: 'POST',
      body,
    });
  },

  listPasskeys(): Promise<ListPasskeysResponse> {
    return request<ListPasskeysResponse>('/v1/me/passkeys');
  },

  addPasskeyOptions(): Promise<CeremonyOptions> {
    return request<CeremonyOptions>('/v1/me/passkeys/options', {
      method: 'POST',
    });
  },

  addPasskeyVerify(body: RegistrationVerifyRequest): Promise<Passkey> {
    return request<Passkey>('/v1/me/passkeys/verify', {
      method: 'POST',
      body,
    });
  },

  deletePasskey(passkeyId: string): Promise<void> {
    return request<void>(`/v1/me/passkeys/${encodeURIComponent(passkeyId)}`, {
      method: 'DELETE',
    });
  },

  listSessions(): Promise<ListSessionsResponse> {
    return request<ListSessionsResponse>('/v1/me/sessions');
  },

  deleteSession(sessionId: string): Promise<void> {
    return request<void>(`/v1/me/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    });
  },

  listTopics(): Promise<ListTopicsResponse> {
    return request<ListTopicsResponse>('/v1/topics');
  },

  createTopic(body: TopicCreate): Promise<Topic> {
    return request<Topic>('/v1/topics', { method: 'POST', body });
  },

  updateTopic(topicId: string, body: TopicUpdate): Promise<Topic> {
    return request<Topic>(`/v1/topics/${encodeURIComponent(topicId)}`, {
      method: 'PATCH',
      body,
    });
  },

  deleteTopic(topicId: string): Promise<void> {
    return request<void>(`/v1/topics/${encodeURIComponent(topicId)}`, {
      method: 'DELETE',
    });
  },

  listSubscriptions(): Promise<ListSubscriptionsResponse> {
    return request<ListSubscriptionsResponse>('/v1/subscriptions');
  },

  createSubscription(body: SubscriptionCreate): Promise<Subscription> {
    return request<Subscription>('/v1/subscriptions', { method: 'POST', body });
  },

  updateSubscription(subscriptionId: string, body: SubscriptionUpdate): Promise<Subscription> {
    return request<Subscription>(`/v1/subscriptions/${encodeURIComponent(subscriptionId)}`, {
      method: 'PATCH',
      body,
    });
  },

  deleteSubscription(subscriptionId: string): Promise<void> {
    return request<void>(`/v1/subscriptions/${encodeURIComponent(subscriptionId)}`, {
      method: 'DELETE',
    });
  },

  getDeliverySlots(): Promise<DeliverySlotSet> {
    return request<DeliverySlotSet>('/v1/delivery-slots');
  },

  replaceDeliverySlots(body: DeliverySlotsReplace): Promise<DeliverySlotSet> {
    return request<DeliverySlotSet>('/v1/delivery-slots', { method: 'PUT', body });
  },

  listReports(date?: string): Promise<ListReportsResponse> {
    const query = date ? `?date=${encodeURIComponent(date)}` : '';
    return request<ListReportsResponse>(`/v1/reports${query}`);
  },

  getReport(reportId: string): Promise<Report> {
    return request<Report>(`/v1/reports/${encodeURIComponent(reportId)}`);
  },

  setItemMark(itemId: string, body: ItemMarkUpdate): Promise<ItemMark> {
    return request<ItemMark>(`/v1/items/${encodeURIComponent(itemId)}/mark`, {
      method: 'PUT',
      body,
    });
  },

  getAgentSettings(): Promise<AgentSettings> {
    return request<AgentSettings>('/v1/agent-settings');
  },

  updateAgentSettings(body: AgentSettingsUpdate): Promise<AgentSettings> {
    return request<AgentSettings>('/v1/agent-settings', { method: 'PATCH', body });
  },

  generateNow(): Promise<UserJob> {
    return request<UserJob>('/v1/jobs', { method: 'POST' });
  },

  getJob(jobId: string): Promise<UserJob> {
    return request<UserJob>(`/v1/jobs/${encodeURIComponent(jobId)}`);
  },
};
