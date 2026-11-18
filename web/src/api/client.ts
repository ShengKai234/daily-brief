import { mockFetch } from './mockApi';
import type {
  AgentSettings,
  AgentSettingsUpdate,
  DeliverySlotSet,
  DeliverySlotsReplace,
  HealthResponse,
  ItemMark,
  ItemMarkUpdate,
  ListReportsResponse,
  ListSubscriptionsResponse,
  ListTopicsResponse,
  Problem,
  Profile,
  ProfileUpdate,
  RedeemInviteRequest,
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

async function request<T>(
  path: string,
  options: {
    method?: string;
    token?: string | null;
    body?: unknown;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
  };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const init: RequestInit = {
    method: options.method || 'GET',
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
    throw new ApiError(problem);
  }

  return (await response.json()) as T;
}

export const apiClient = {
  getHealth(): Promise<HealthResponse> {
    return request<HealthResponse>('/health');
  },

  getMe(token: string): Promise<Profile> {
    return request<Profile>('/v1/me', { token });
  },

  updateMe(token: string, body: ProfileUpdate): Promise<Profile> {
    return request<Profile>('/v1/me', { method: 'PATCH', token, body });
  },

  redeemInvite(token: string, body: RedeemInviteRequest): Promise<Profile> {
    return request<Profile>('/v1/auth/redeem-invite', { method: 'POST', token, body });
  },

  listTopics(token: string): Promise<ListTopicsResponse> {
    return request<ListTopicsResponse>('/v1/topics', { token });
  },

  createTopic(token: string, body: TopicCreate): Promise<Topic> {
    return request<Topic>('/v1/topics', { method: 'POST', token, body });
  },

  updateTopic(token: string, topicId: string, body: TopicUpdate): Promise<Topic> {
    return request<Topic>(`/v1/topics/${encodeURIComponent(topicId)}`, {
      method: 'PATCH',
      token,
      body,
    });
  },

  deleteTopic(token: string, topicId: string): Promise<void> {
    return request<void>(`/v1/topics/${encodeURIComponent(topicId)}`, {
      method: 'DELETE',
      token,
    });
  },

  listSubscriptions(token: string): Promise<ListSubscriptionsResponse> {
    return request<ListSubscriptionsResponse>('/v1/subscriptions', { token });
  },

  createSubscription(token: string, body: SubscriptionCreate): Promise<Subscription> {
    return request<Subscription>('/v1/subscriptions', { method: 'POST', token, body });
  },

  updateSubscription(
    token: string,
    subscriptionId: string,
    body: SubscriptionUpdate,
  ): Promise<Subscription> {
    return request<Subscription>(`/v1/subscriptions/${encodeURIComponent(subscriptionId)}`, {
      method: 'PATCH',
      token,
      body,
    });
  },

  deleteSubscription(token: string, subscriptionId: string): Promise<void> {
    return request<void>(`/v1/subscriptions/${encodeURIComponent(subscriptionId)}`, {
      method: 'DELETE',
      token,
    });
  },

  getDeliverySlots(token: string): Promise<DeliverySlotSet> {
    return request<DeliverySlotSet>('/v1/delivery-slots', { token });
  },

  replaceDeliverySlots(token: string, body: DeliverySlotsReplace): Promise<DeliverySlotSet> {
    return request<DeliverySlotSet>('/v1/delivery-slots', { method: 'PUT', token, body });
  },

  listReports(token: string, date?: string): Promise<ListReportsResponse> {
    const query = date ? `?date=${encodeURIComponent(date)}` : '';
    return request<ListReportsResponse>(`/v1/reports${query}`, { token });
  },

  getReport(token: string, reportId: string): Promise<Report> {
    return request<Report>(`/v1/reports/${encodeURIComponent(reportId)}`, { token });
  },

  setItemMark(token: string, itemId: string, body: ItemMarkUpdate): Promise<ItemMark> {
    return request<ItemMark>(`/v1/items/${encodeURIComponent(itemId)}/mark`, {
      method: 'PUT',
      token,
      body,
    });
  },

  getAgentSettings(token: string): Promise<AgentSettings> {
    return request<AgentSettings>('/v1/agent-settings', { token });
  },

  updateAgentSettings(token: string, body: AgentSettingsUpdate): Promise<AgentSettings> {
    return request<AgentSettings>('/v1/agent-settings', { method: 'PATCH', token, body });
  },

  generateNow(token: string): Promise<UserJob> {
    return request<UserJob>('/v1/jobs', { method: 'POST', token });
  },

  getJob(token: string, jobId: string): Promise<UserJob> {
    return request<UserJob>(`/v1/jobs/${encodeURIComponent(jobId)}`, { token });
  },
};
