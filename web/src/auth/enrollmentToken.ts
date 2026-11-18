let inMemoryEnrollmentToken: string | null = null;

function parseTokenFromHash(hash: string): string | null {
  if (!hash || hash === '#') {
    return null;
  }
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  const params = new URLSearchParams(raw);
  const token = params.get('t') ?? params.get('token');
  if (!token) {
    return null;
  }
  const trimmed = token.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Extracts an enrollment token from `window.location.hash` into memory and
 * strips the fragment via `history.replaceState` before any other script runs.
 */
export function extractEnrollmentTokenFromLocation(): string | null {
  if (typeof window === 'undefined') {
    return inMemoryEnrollmentToken;
  }
  const parsed = parseTokenFromHash(window.location.hash);
  if (parsed) {
    inMemoryEnrollmentToken = parsed;
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search,
    );
  }
  return inMemoryEnrollmentToken;
}

export function getEnrollmentToken(): string | null {
  return inMemoryEnrollmentToken;
}

export function clearEnrollmentToken(): void {
  inMemoryEnrollmentToken = null;
}

// Run immediately on module evaluation (imported first in main.tsx).
extractEnrollmentTokenFromLocation();
