import type { Problem } from './types';

export const ERROR_MESSAGES: Record<string, string> = {
  bad_request: 'The request could not be understood. Please check your input and try again.',
  ceremony_invalid:
    'This sign-in or registration attempt has expired or is no longer valid. Please try again.',
  registration_failed: 'Could not verify the new passkey. Please try again.',
  enrollment_token_invalid:
    'This enrollment link is invalid or has been revoked. Ask the administrator for a new link.',
  enrollment_token_used: 'This enrollment link has already been used.',
  enrollment_token_expired:
    'This enrollment link has expired. Ask the administrator for a new link.',
  unauthorized: 'Your session has expired or is no longer valid. Please sign in again.',
  authentication_failed: 'Passkey verification failed. Please try again.',
  forbidden: 'You do not have permission to perform this action.',
  origin_mismatch: 'Request origin is not allowed. Please reload the page and try again.',
  account_disabled: 'This account has been disabled and cannot access Daily Brief.',
  admission_disabled:
    'New user registration and non-owner recovery are currently disabled.',
  reauth_required:
    'Please verify your passkey again to continue with this security action.',
  agent_settings_locked: 'Agent settings are locked in this preview.',
  owner_only: 'Only the configured owner can run this action right now.',
  not_found: 'The requested item was not found.',
  already_subscribed: 'You are already subscribed to this topic.',
  job_in_progress: 'A brief is already being generated. Please wait for it to finish.',
  lease_lost: 'The job lease was lost. Please try again.',
  topic_exists: 'A topic with this name already exists.',
  credential_exists: 'This passkey is already registered.',
  passkey_limit:
    'You have reached the maximum of 10 active passkeys. Remove one before adding another.',
  last_passkey:
    'You cannot remove your last active passkey. Add another passkey first.',
  payload_too_large: 'The request is too large to process.',
  unsupported_media_type: 'Unsupported request format.',
  validation_error: 'Please check your input and try again.',
  slot_limit_exceeded: 'You can configure at most 3 delivery slots.',
  duplicate_slot: 'Delivery slot times must be unique.',
  visibility_not_supported: 'Public visibility is not supported yet.',
  no_topics: 'Add at least one enabled topic subscription before generating a brief.',
  daily_limit_reached: 'Daily generation limit reached. Try again tomorrow.',
  rate_limited: 'Too many attempts. Please wait a few minutes and try again.',
};

export function formatProblemMessage(problem: Problem): string {
  const mapped = ERROR_MESSAGES[problem.code];
  if (problem.code === 'validation_error' && problem.errors && problem.errors.length > 0) {
    const details = problem.errors.map((e) => `${e.field}: ${e.message}`).join('; ');
    return `${mapped} (${details})`;
  }
  if (mapped) {
    return mapped;
  }
  return problem.detail || problem.title || 'An unexpected error occurred.';
}
