import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, apiClient } from '../api/client';
import { formatProblemMessage } from '../api/errors';
import { useAuth } from '../auth/AuthContext';
import {
  clearEnrollmentToken,
  extractEnrollmentTokenFromLocation,
  getEnrollmentToken,
} from '../auth/enrollmentToken';
import { WebAuthnClientError, createPasskey } from '../auth/webauthn';

export function EnrollPage() {
  const { setSignedInProfile } = useAuth();
  const navigate = useNavigate();

  const [token, setToken] = useState<string | null>(() => getEnrollmentToken());
  const [displayName, setDisplayName] = useState('');
  const [passkeyLabel, setPasskeyLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const extracted = extractEnrollmentTokenFromLocation();
    if (extracted) {
      setToken(extracted);
    }
  }, []);

  const handleEnroll = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) {
      setError('No enrollment token found. Open the full enrollment link from your invitation.');
      return;
    }

    const trimmedName = displayName.trim();
    const trimmedLabel = passkeyLabel.trim();

    setBusy(true);
    setError(null);
    try {
      const options = await apiClient.registerOptions({
        enrollment_token: token,
        ...(trimmedName ? { display_name: trimmedName } : {}),
      });
      const credential = await createPasskey(options.public_key);
      const profile = await apiClient.registerVerify({
        ceremony_id: options.ceremony_id,
        credential,
        ...(trimmedLabel ? { label: trimmedLabel } : {}),
      });
      clearEnrollmentToken();
      setToken(null);
      setSignedInProfile(profile);
      navigate('/', { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(formatProblemMessage(err.problem));
      } else if (err instanceof WebAuthnClientError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Passkey registration failed.');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Set Up Your Passkey</h1>
          <p className="page-subtitle">
            Register a passkey on this device to activate your account or complete recovery.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Enrollment error:</strong> {error}
        </div>
      ) : null}

      {!token ? (
        <section className="card" aria-labelledby="missing-token-heading">
          <h2 id="missing-token-heading" className="section-title">
            Enrollment Link Required
          </h2>
          <p className="text-muted">
            No enrollment token was found in this link. Open the full enrollment link provided by
            the administrator, or sign in if you already have a passkey.
          </p>
          <div>
            <Link to="/" className="btn">
              Back to Sign In
            </Link>
          </div>
        </section>
      ) : (
        <section className="card" aria-labelledby="enroll-form-heading">
          <h2 id="enroll-form-heading" className="section-title">
            Create Passkey
          </h2>
          <form className="stack" onSubmit={(e) => void handleEnroll(e)}>
            <div className="form-group">
              <label htmlFor="enroll-display-name" className="form-label">
                Display Name (for new invite accounts; leave blank for recovery or adding a passkey)
              </label>
              <input
                id="enroll-display-name"
                type="text"
                className="input"
                maxLength={80}
                placeholder="e.g. Mei"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={busy}
              />
            </div>

            <div className="form-group">
              <label htmlFor="enroll-passkey-label" className="form-label">
                Passkey Label (optional)
              </label>
              <input
                id="enroll-passkey-label"
                type="text"
                className="input"
                maxLength={64}
                placeholder="e.g. MacBook Touch ID"
                value={passkeyLabel}
                onChange={(e) => setPasskeyLabel(e.target.value)}
                disabled={busy}
              />
            </div>

            <div className="row">
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy}
                aria-busy={busy}
              >
                {busy ? 'Creating passkey...' : 'Create Passkey & Sign In'}
              </button>
              <Link to="/" className="btn">
                Back to Sign In
              </Link>
            </div>
          </form>
        </section>
      )}
    </div>
  );
}
