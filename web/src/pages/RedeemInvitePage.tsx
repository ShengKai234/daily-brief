import React, { useState } from 'react';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';

export function RedeemInvitePage() {
  const { profile, redeemInviteCode } = useAuth();
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = code.trim();
    if (!trimmed) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await redeemInviteCode(trimmed);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to redeem invite code.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Account Pending Approval</h1>
          <p className="page-subtitle">
            Signed in as {profile?.email}. Enter an invite code to activate your account.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Invite error:</strong> {error}
        </div>
      ) : null}

      <section className="card" aria-labelledby="redeem-invite-heading">
        <h2 id="redeem-invite-heading" className="section-title">
          Redeem Invite Code
        </h2>
        <form className="stack" onSubmit={(e) => void handleSubmit(e)}>
          <div className="form-group">
            <label htmlFor="invite-code-input" className="form-label">
              Invite Code
            </label>
            <input
              id="invite-code-input"
              type="text"
              className="input"
              maxLength={64}
              placeholder="WELCOME-2026"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
            <p className="form-hint">
              Enter a valid invite code (up to 64 characters) to unlock topics, delivery slots, and daily reports.
            </p>
          </div>

          <div>
            <button type="submit" className="btn btn-primary" disabled={submitting || !code.trim()}>
              {submitting ? 'Redeeming...' : 'Activate Account'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
