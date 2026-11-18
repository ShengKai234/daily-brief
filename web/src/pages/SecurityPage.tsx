import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, apiClient } from '../api/client';
import { formatProblemMessage } from '../api/errors';
import type { Passkey, Session } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { WebAuthnClientError, createPasskey, getPasskeyAssertion } from '../auth/webauthn';

type PendingReauthAction =
  | { type: 'add'; label: string }
  | { type: 'delete'; passkey: Passkey };

function formatTimestamp(iso: string | null): string {
  if (!iso) {
    return 'Never';
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

export function SecurityPage() {
  const { handleSessionRevoked } = useAuth();
  const navigate = useNavigate();

  const [passkeys, setPasskeys] = useState<Passkey[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [newLabel, setNewLabel] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingReauth, setPendingReauth] = useState<PendingReauthAction | null>(null);

  const loadSecurityData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [pkRes, sessRes] = await Promise.all([
        apiClient.listPasskeys(),
        apiClient.listSessions(),
      ]);
      setPasskeys(pkRes.passkeys);
      setSessions(sessRes.sessions);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(formatProblemMessage(err.problem));
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load security settings.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSecurityData();
  }, [loadSecurityData]);

  const executeAddPasskey = async (label: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const options = await apiClient.addPasskeyOptions();
      const credential = await createPasskey(options.public_key);
      const trimmed = label.trim();
      await apiClient.addPasskeyVerify({
        ceremony_id: options.ceremony_id,
        credential,
        ...(trimmed ? { label: trimmed } : {}),
      });
      setNewLabel('');
      setPendingReauth(null);
      await loadSecurityData();
      setNotice('Passkey added.');
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.problem.code === 'reauth_required') {
          setPendingReauth({ type: 'add', label });
        }
        setError(formatProblemMessage(err.problem));
      } else if (err instanceof WebAuthnClientError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to add passkey.');
      }
    } finally {
      setBusy(false);
    }
  };

  const executeDeletePasskey = async (passkey: Passkey) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiClient.deletePasskey(passkey.id);
      setPendingReauth(null);
      if (passkey.is_current) {
        handleSessionRevoked();
        navigate('/', { replace: true });
        return;
      }
      await loadSecurityData();
      setNotice('Passkey removed.');
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.problem.code === 'reauth_required') {
          setPendingReauth({ type: 'delete', passkey });
        }
        setError(formatProblemMessage(err.problem));
      } else {
        setError(err instanceof Error ? err.message : 'Failed to remove passkey.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await executeAddPasskey(newLabel);
  };

  const handleCompleteReauth = async () => {
    const action = pendingReauth;
    if (!action) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const options = await apiClient.reauthOptions();
      const credential = await getPasskeyAssertion(options.public_key);
      await apiClient.reauthVerify({
        ceremony_id: options.ceremony_id,
        credential,
      });
      setPendingReauth(null);
      if (action.type === 'add') {
        await executeAddPasskey(action.label);
      } else {
        await executeDeletePasskey(action.passkey);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(formatProblemMessage(err.problem));
      } else if (err instanceof WebAuthnClientError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Reauthentication failed.');
      }
      setBusy(false);
    }
  };

  const handleRevokeSession = async (session: Session) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiClient.deleteSession(session.id);
      if (session.is_current) {
        handleSessionRevoked();
        navigate('/', { replace: true });
        return;
      }
      await loadSecurityData();
      setNotice('Session revoked.');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(formatProblemMessage(err.problem));
      } else {
        setError(err instanceof Error ? err.message : 'Failed to revoke session.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="card" role="status">
        <p className="text-muted">Loading security settings...</p>
      </div>
    );
  }

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Security</h1>
          <p className="page-subtitle">
            Manage passkeys and active sign-in sessions for your account.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Error:</strong> {error}
        </div>
      ) : null}

      {notice ? (
        <div className="alert" role="status">
          {notice}
        </div>
      ) : null}

      {pendingReauth ? (
        <section className="card" aria-labelledby="reauth-heading" role="region">
          <h2 id="reauth-heading" className="section-title">
            Verify Your Passkey
          </h2>
          <p className="text-muted">
            {pendingReauth.type === 'add'
              ? 'Confirm your current passkey before adding a new passkey.'
              : 'Confirm your current passkey before removing a passkey.'}
          </p>
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleCompleteReauth()}
              disabled={busy}
              aria-busy={busy}
            >
              {busy ? 'Verifying...' : 'Verify passkey'}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setPendingReauth(null);
                setError(null);
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </section>
      ) : null}

      <section className="card" aria-labelledby="passkeys-heading">
        <h2 id="passkeys-heading" className="section-title">
          Passkeys ({passkeys.length}/10)
        </h2>

        {passkeys.length === 0 ? (
          <p className="text-muted">No active passkeys.</p>
        ) : (
          <ul className="stack" aria-label="Registered passkeys">
            {passkeys.map((pk) => (
              <li key={pk.id} className="item-card">
                <div className="row-between">
                  <div className="stack">
                    <div className="row">
                      <strong>{pk.label || 'Passkey'}</strong>
                      {pk.is_current ? <span className="badge">Current session</span> : null}
                      <span className="badge">
                        {pk.backup_state
                          ? 'Synced'
                          : pk.backup_eligible
                            ? 'Sync-capable'
                            : 'Device-bound'}
                      </span>
                    </div>
                    <p className="text-subtle">
                      Added {formatTimestamp(pk.created_at)} · Last used{' '}
                      {formatTimestamp(pk.last_used_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => void executeDeletePasskey(pk)}
                    disabled={busy}
                    aria-label={`Remove passkey ${pk.label || pk.id}`}
                  >
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <hr className="divider" />

        <form className="stack" onSubmit={(e) => void handleAddSubmit(e)}>
          <div className="form-group">
            <label htmlFor="new-passkey-label" className="form-label">
              New Passkey Label (optional)
            </label>
            <input
              id="new-passkey-label"
              type="text"
              className="input"
              maxLength={64}
              placeholder="e.g. Phone or Work Laptop"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              disabled={busy}
            />
          </div>
          <div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={busy}
              aria-busy={busy}
            >
              {busy ? 'Working...' : 'Add passkey on this device'}
            </button>
          </div>
        </form>
      </section>

      <section className="card" aria-labelledby="sessions-heading">
        <h2 id="sessions-heading" className="section-title">
          Active Sessions ({sessions.length})
        </h2>

        {sessions.length === 0 ? (
          <p className="text-muted">No active sessions.</p>
        ) : (
          <ul className="stack" aria-label="Active sessions">
            {sessions.map((sess) => (
              <li key={sess.id} className="item-card">
                <div className="row-between">
                  <div className="stack">
                    <div className="row">
                      <strong>{sess.is_current ? 'This browser session' : 'Signed-in session'}</strong>
                      {sess.is_current ? <span className="badge">Current</span> : null}
                    </div>
                    <p className="text-subtle">
                      Signed in {formatTimestamp(sess.created_at)} · Last seen{' '}
                      {formatTimestamp(sess.last_seen_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => void handleRevokeSession(sess)}
                    disabled={busy}
                    aria-label={
                      sess.is_current
                        ? 'Revoke current session'
                        : `Revoke session ${sess.id}`
                    }
                  >
                    {sess.is_current ? 'Sign out' : 'Revoke'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
