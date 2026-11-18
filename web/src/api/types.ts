import type { components, operations, paths } from './schema.gen';

export type ApiPaths = paths;
export type ApiOperations = operations;
export type ApiSchemas = components['schemas'];

export type Problem = ApiSchemas['Problem'];
export type Profile = ApiSchemas['Profile'];
export type ProfileUpdate = ApiSchemas['ProfileUpdate'];
export type Topic = ApiSchemas['Topic'];
export type TopicCreate = ApiSchemas['TopicCreate'];
export type TopicUpdate = ApiSchemas['TopicUpdate'];
export type Subscription = ApiSchemas['Subscription'];
export type SubscriptionCreate = ApiSchemas['SubscriptionCreate'];
export type SubscriptionUpdate = ApiSchemas['SubscriptionUpdate'];
export type DeliverySlot = ApiSchemas['DeliverySlot'];
export type DeliverySlotSet = ApiSchemas['DeliverySlotSet'];
export type DeliverySlotsReplace = ApiSchemas['DeliverySlotsReplace'];
export type LocalTime = ApiSchemas['LocalTime'];
export type ReportSummary = ApiSchemas['ReportSummary'];
export type Report = ApiSchemas['Report'];
export type ReportSection = ApiSchemas['ReportSection'];
export type ReportItem = ApiSchemas['ReportItem'];
export type ItemMark = ApiSchemas['ItemMark'];
export type ItemMarkUpdate = ApiSchemas['ItemMarkUpdate'];
export type AgentSettings = ApiSchemas['AgentSettings'];
export type AgentSettingsUpdate = ApiSchemas['AgentSettingsUpdate'];
export type UserJob = ApiSchemas['UserJob'];

export type RegistrationOptionsRequest = ApiSchemas['RegistrationOptionsRequest'];
export type RegistrationVerifyRequest = ApiSchemas['RegistrationVerifyRequest'];
export type CeremonyOptions = ApiSchemas['CeremonyOptions'];
export type CeremonyVerifyRequest = ApiSchemas['CeremonyVerifyRequest'];
export type WebAuthnCredential = ApiSchemas['WebAuthnCredential'];
export type Passkey = ApiSchemas['Passkey'];
export type Session = ApiSchemas['Session'];

export type ListPasskeysResponse =
  operations['listPasskeys']['responses'][200]['content']['application/json'];
export type ListSessionsResponse =
  operations['listSessions']['responses'][200]['content']['application/json'];
export type ListTopicsResponse =
  operations['listTopics']['responses'][200]['content']['application/json'];
export type ListSubscriptionsResponse =
  operations['listSubscriptions']['responses'][200]['content']['application/json'];
export type ListReportsResponse =
  operations['listReports']['responses'][200]['content']['application/json'];
export type HealthResponse =
  operations['getHealth']['responses'][200]['content']['application/json'];
