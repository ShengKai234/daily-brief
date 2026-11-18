import React, { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import {
  DEV_OWNER_EMAIL,
  DEV_OWNER_USER_ID,
  DEV_PENDING_EMAIL,
  DEV_PENDING_USER_ID,
} from '../auth/jwt';

export function SignInPage() {
  const { signInWithDevAccount, signInWithGoogle, signInWithToken, error, loading } = useAuth();
  const [customEmail, setCustomEmail] = useState(DEV_OWNER_EMAIL);
  const [customSub, setCustomSub] = useState(DEV_OWNER_USER_ID);
  const [rawJwt, setRawJwt] = useState('');

  const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
  const appEnv = import.meta.env.VITE_APP_ENV || 'local';
  const showDevSignIn = appEnv === 'local' || !supabaseUrl;

  const handleCustomDevSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    await signInWithDevAccount({
      sub: customSub.trim() || DEV_OWNER_USER_ID,
      email: customEmail.trim() || DEV_OWNER_EMAIL,
    });
  };

  const handlePasteJwt = async (e: React.FormEvent) => {
    e.preventDefault();
    await signInWithToken(rawJwt);
  };

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Sign In to Daily Brief</h1>
          <p className="page-subtitle">
            Personal scheduled AI news digest. Sign in to read reports and manage subscriptions.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Sign-in error:</strong> {error}
        </div>
      ) : null}

      <section className="card" aria-labelledby="google-signin-heading">
        <h2 id="google-signin-heading" className="section-title">
          Google Sign-In
        </h2>
        <p className="text-muted">
          Authenticate with Google via Supabase Auth. Restricted to allowlisted emails or invite code holders.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn-primary"
            onClick={signInWithGoogle}
            disabled={loading || !supabaseUrl}
          >
            Sign in with Google
          </button>
          {!supabaseUrl ? (
            <span className="text-subtle">
              (Supabase URL not set in local mode; use Dev Sign-In below)
            </span>
          ) : null}
        </div>
      </section>

      {showDevSignIn ? (
        <section className="card" aria-labelledby="dev-signin-heading">
          <h2 id="dev-signin-heading" className="section-title">
            Local Dev Sign-In (APP_ENV=local)
          </h2>
          <p className="text-muted">
            Issues a local HS256 JWT for the seeded owner account or a pending test account.
          </p>

          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={loading}
              onClick={() =>
                void signInWithDevAccount({
                  sub: DEV_OWNER_USER_ID,
                  email: DEV_OWNER_EMAIL,
                })
              }
            >
              Sign in as Seeded Owner ({DEV_OWNER_EMAIL})
            </button>

            <button
              type="button"
              className="btn"
              disabled={loading}
              onClick={() =>
                void signInWithDevAccount({
                  sub: DEV_PENDING_USER_ID,
                  email: DEV_PENDING_EMAIL,
                })
              }
            >
              Sign in as Pending User ({DEV_PENDING_EMAIL})
            </button>
          </div>

          <hr className="divider" />

          <form className="stack" onSubmit={(e) => void handleCustomDevSignIn(e)}>
            <div className="form-group">
              <label htmlFor="dev-email-input" className="form-label">
                Custom Dev Email
              </label>
              <input
                id="dev-email-input"
                type="email"
                className="input"
                value={customEmail}
                onChange={(e) => setCustomEmail(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="dev-sub-input" className="form-label">
                Custom User UUID (sub)
              </label>
              <input
                id="dev-sub-input"
                type="text"
                className="input"
                value={customSub}
                onChange={(e) => setCustomSub(e.target.value)}
                required
              />
            </div>

            <div>
              <button type="submit" className="btn" disabled={loading}>
                Issue Custom Dev Token
              </button>
            </div>
          </form>

          <hr className="divider" />

          <form className="stack" onSubmit={(e) => void handlePasteJwt(e)}>
            <div className="form-group">
              <label htmlFor="paste-jwt-input" className="form-label">
                Or Paste Bearer JWT
              </label>
              <input
                id="paste-jwt-input"
                type="text"
                className="input"
                placeholder="eyJhbGciOi..."
                value={rawJwt}
                onChange={(e) => setRawJwt(e.target.value)}
              />
            </div>
            <div>
              <button type="submit" className="btn" disabled={loading || !rawJwt.trim()}>
                Use Bearer Token
              </button>
            </div>
          </form>
        </section>
      ) : null}
    </div>
  );
}
