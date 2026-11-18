export const DEV_OWNER_USER_ID = '00000000-0000-4000-8000-000000000001';
export const DEV_OWNER_EMAIL = 'owner@example.com';
export const DEV_PENDING_USER_ID = '00000000-0000-4000-8000-000000000002';
export const DEV_PENDING_EMAIL = 'pending@example.com';

export interface DevTokenClaims {
  iss?: string;
  sub: string;
  email: string;
  role?: string;
  aud?: string;
  iat?: number;
  exp?: number;
}

function base64UrlEncodeBytes(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function base64UrlEncodeString(input: string): string {
  const bytes = new TextEncoder().encode(input);
  return base64UrlEncodeBytes(bytes);
}

function base64UrlDecodeString(input: string): string {
  const padded = input.replace(/-/g, '+').replace(/_/g, '/');
  const padLen = (4 - (padded.length % 4)) % 4;
  const binary = atob(padded + '='.repeat(padLen));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder().decode(bytes);
}

/**
 * Issues a standard HS256 JWT signed with the local dev secret for DESIGN 4.5 dev sign-in.
 */
export async function issueDevJwt(
  claims: { sub: string; email: string },
  secret: string = import.meta.env.VITE_DEV_JWT_SECRET || 'change-me-local-only',
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'HS256', typ: 'JWT' };
  const payload: DevTokenClaims = {
    iss: 'daily-brief-local',
    sub: claims.sub,
    email: claims.email,
    role: 'authenticated',
    aud: 'authenticated',
    iat: now,
    exp: now + 86400,
  };

  const encodedHeader = base64UrlEncodeString(JSON.stringify(header));
  const encodedPayload = base64UrlEncodeString(JSON.stringify(payload));
  const signingInput = `${encodedHeader}.${encodedPayload}`;

  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const keyData = new TextEncoder().encode(secret);
    const cryptoKey = await crypto.subtle.importKey(
      'raw',
      keyData,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const sigBuffer = await crypto.subtle.sign(
      'HMAC',
      cryptoKey,
      new TextEncoder().encode(signingInput),
    );
    const encodedSig = base64UrlEncodeBytes(new Uint8Array(sigBuffer));
    return `${signingInput}.${encodedSig}`;
  }

  return `${signingInput}.${base64UrlEncodeString(`dev-sig:${secret}`)}`;
}

export function decodeJwtClaims(token: string): DevTokenClaims | null {
  const parts = token.trim().split('.');
  if (parts.length !== 3) {
    return null;
  }
  try {
    const raw = base64UrlDecodeString(parts[1]);
    const parsed = JSON.parse(raw) as Partial<DevTokenClaims>;
    if (typeof parsed.sub !== 'string' || !parsed.sub) {
      return null;
    }
    return {
      iss: parsed.iss,
      sub: parsed.sub,
      email: typeof parsed.email === 'string' && parsed.email ? parsed.email : DEV_OWNER_EMAIL,
      role: parsed.role,
      aud: parsed.aud,
      iat: parsed.iat,
      exp: parsed.exp,
    };
  } catch {
    return null;
  }
}

export function extractSupabaseHashToken(hash: string): string | null {
  if (!hash || !hash.startsWith('#')) {
    return null;
  }
  const params = new URLSearchParams(hash.slice(1));
  return params.get('access_token');
}

export function buildSupabaseGoogleOAuthUrl(supabaseUrl: string, redirectTo: string): string {
  const base = supabaseUrl.replace(/\/+$/, '');
  const url = new URL(`${base}/auth/v1/authorize`);
  url.searchParams.set('provider', 'google');
  url.searchParams.set('redirect_to', redirectTo);
  return url.toString();
}
