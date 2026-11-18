import type { WebAuthnCredential } from '../api/types';

export class WebAuthnClientError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WebAuthnClientError';
  }
}

function base64urlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/');
  const padLength = (4 - (padded.length % 4)) % 4;
  const base64 = padded + '='.repeat(padLength);
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function bytesToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

type PublicKeyCredentialWithL3 = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (options: Record<string, unknown>) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (options: Record<string, unknown>) => PublicKeyCredentialRequestOptions;
};

type CredentialWithToJson = PublicKeyCredential & {
  toJSON?: () => WebAuthnCredential;
};

function parseCreationOptions(raw: Record<string, unknown>): PublicKeyCredentialCreationOptions {
  const pkc = PublicKeyCredential as PublicKeyCredentialWithL3;
  if (typeof pkc.parseCreationOptionsFromJSON === 'function') {
    return pkc.parseCreationOptionsFromJSON(raw);
  }
  const user = raw.user as Record<string, unknown>;
  const excludeCredentials = Array.isArray(raw.excludeCredentials)
    ? raw.excludeCredentials.map((item) => {
        const desc = item as Record<string, unknown>;
        return {
          ...desc,
          type: 'public-key' as const,
          id: base64urlToBytes(String(desc.id)) as BufferSource,
        } satisfies PublicKeyCredentialDescriptor;
      })
    : undefined;

  return {
    ...(raw as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64urlToBytes(String(raw.challenge)) as BufferSource,
    user: {
      ...(user as unknown as PublicKeyCredentialUserEntity),
      id: base64urlToBytes(String(user.id)) as BufferSource,
    },
    ...(excludeCredentials ? { excludeCredentials } : {}),
  };
}

function parseRequestOptions(raw: Record<string, unknown>): PublicKeyCredentialRequestOptions {
  const pkc = PublicKeyCredential as PublicKeyCredentialWithL3;
  if (typeof pkc.parseRequestOptionsFromJSON === 'function') {
    return pkc.parseRequestOptionsFromJSON(raw);
  }
  const allowCredentials = Array.isArray(raw.allowCredentials)
    ? raw.allowCredentials.map((item) => {
        const desc = item as Record<string, unknown>;
        return {
          ...desc,
          type: 'public-key' as const,
          id: base64urlToBytes(String(desc.id)) as BufferSource,
        } satisfies PublicKeyCredentialDescriptor;
      })
    : undefined;

  return {
    ...(raw as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64urlToBytes(String(raw.challenge)) as BufferSource,
    ...(allowCredentials ? { allowCredentials } : {}),
  };
}

function serializeCredential(cred: PublicKeyCredential): WebAuthnCredential {
  const withToJson = cred as CredentialWithToJson;
  if (typeof withToJson.toJSON === 'function') {
    return withToJson.toJSON();
  }

  const response = cred.response;
  const serializedResponse: Record<string, unknown> = {
    clientDataJSON: bytesToBase64url(response.clientDataJSON),
  };

  if ('attestationObject' in response) {
    const att = response as AuthenticatorAttestationResponse;
    serializedResponse.attestationObject = bytesToBase64url(att.attestationObject);
    if (typeof att.getTransports === 'function') {
      serializedResponse.transports = att.getTransports();
    }
  } else {
    const assertion = response as AuthenticatorAssertionResponse;
    serializedResponse.authenticatorData = bytesToBase64url(assertion.authenticatorData);
    serializedResponse.signature = bytesToBase64url(assertion.signature);
    if (assertion.userHandle) {
      serializedResponse.userHandle = bytesToBase64url(assertion.userHandle);
    }
  }

  return {
    id: cred.id,
    rawId: bytesToBase64url(cred.rawId),
    type: 'public-key',
    response: serializedResponse,
    authenticatorAttachment: cred.authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults() as Record<string, unknown>,
  };
}

function mapWebAuthnException(err: unknown): WebAuthnClientError {
  if (err instanceof WebAuthnClientError) {
    return err;
  }
  if (err instanceof DOMException) {
    if (err.name === 'NotAllowedError') {
      return new WebAuthnClientError(
        'Passkey prompt was cancelled or timed out. Please try again.',
      );
    }
    if (err.name === 'InvalidStateError') {
      return new WebAuthnClientError(
        'This device already has a passkey registered for this account.',
      );
    }
    if (err.name === 'SecurityError') {
      return new WebAuthnClientError(
        'Passkeys require a secure context (HTTPS or localhost).',
      );
    }
    if (err.name === 'NotSupportedError') {
      return new WebAuthnClientError('This browser does not support passkeys.');
    }
  }
  return new WebAuthnClientError(
    err instanceof Error ? err.message : 'Passkey operation failed.',
  );
}

function assertWebAuthnAvailable(): void {
  if (
    typeof window === 'undefined' ||
    typeof window.PublicKeyCredential === 'undefined' ||
    !navigator.credentials
  ) {
    throw new WebAuthnClientError(
      'Passkeys are not supported in this browser.',
    );
  }
}

export async function createPasskey(
  publicKeyOptions: Record<string, unknown>,
): Promise<WebAuthnCredential> {
  assertWebAuthnAvailable();
  try {
    const publicKey = parseCreationOptions(publicKeyOptions);
    const cred = await navigator.credentials.create({ publicKey });
    if (!(cred instanceof PublicKeyCredential) && (!cred || typeof (cred as PublicKeyCredential).rawId === 'undefined')) {
      throw new WebAuthnClientError('No passkey was created.');
    }
    return serializeCredential(cred as PublicKeyCredential);
  } catch (err) {
    throw mapWebAuthnException(err);
  }
}

export async function getPasskeyAssertion(
  publicKeyOptions: Record<string, unknown>,
): Promise<WebAuthnCredential> {
  assertWebAuthnAvailable();
  try {
    const publicKey = parseRequestOptions(publicKeyOptions);
    const cred = await navigator.credentials.get({ publicKey });
    if (!(cred instanceof PublicKeyCredential) && (!cred || typeof (cred as PublicKeyCredential).rawId === 'undefined')) {
      throw new WebAuthnClientError('No passkey was selected.');
    }
    return serializeCredential(cred as PublicKeyCredential);
  } catch (err) {
    throw mapWebAuthnException(err);
  }
}
