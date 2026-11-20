import { useState } from 'react';
import { ApiError, apiClient } from '../api/client';
import { formatProblemMessage } from '../api/errors';
import { useAuth } from '../auth/AuthContext';
import { WebAuthnClientError, getPasskeyAssertion } from '../auth/webauthn';

export function SignInPage() {
  const { setSignedInProfile, error: authError, clearError } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeError = error ?? authError;

  const handlePasskeySignIn = async () => {
    setBusy(true);
    setError(null);
    clearError();
    try {
      const options = await apiClient.loginOptions();
      const credential = await getPasskeyAssertion(options.public_key);
      const profile = await apiClient.loginVerify({
        ceremony_id: options.ceremony_id,
        credential,
      });
      setSignedInProfile(profile);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(formatProblemMessage(err.problem));
      } else if (err instanceof WebAuthnClientError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Sign-in failed. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Sign In to Daily Brief</h1>
          <p className="page-subtitle">
            Personal scheduled AI news digest. Sign in with your passkey to read reports and manage
            subscriptions.
          </p>
        </div>
      </div>

      {activeError ? (
        <div className="alert" role="alert">
          <strong>Sign-in error:</strong> {activeError}
        </div>
      ) : null}

      <section className="card" aria-labelledby="passkey-signin-heading">
        <h2 id="passkey-signin-heading" className="section-title">
          Passkey Sign-In
        </h2>
        <p className="text-muted">
          Use a passkey saved on this device or choose your phone or another device when prompted by
          your browser.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void handlePasskeySignIn()}
            disabled={busy}
            aria-busy={busy}
          >
            {busy ? 'Signing in...' : 'Sign in with a passkey'}
          </button>
        </div>
        <p className="text-subtle">
          New accounts and account recovery require a one-time enrollment link from the
          administrator.
        </p>
      </section>
    </div>
  );
}
