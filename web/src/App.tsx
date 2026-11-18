import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { ProfilePage } from './pages/ProfilePage';
import { RedeemInvitePage } from './pages/RedeemInvitePage';
import { ReportsPage } from './pages/ReportsPage';
import { SettingsPage } from './pages/SettingsPage';
import { SignInPage } from './pages/SignInPage';

export function AppRoutes() {
  const { token, profile, loading, error, errorCode, signOut } = useAuth();

  if (loading) {
    return (
      <Layout>
        <div className="card" role="status">
          <p className="text-muted">Loading session...</p>
        </div>
      </Layout>
    );
  }

  if (!token) {
    return (
      <Layout>
        <SignInPage />
      </Layout>
    );
  }

  if (errorCode === 'account_disabled' || profile?.status === 'disabled') {
    return (
      <Layout>
        <div className="card" role="alert">
          <h1 className="section-title">Account Disabled</h1>
          <p className="text-muted">
            {error || 'This account has been disabled and cannot access Daily Brief.'}
          </p>
          <div>
            <button type="button" className="btn" onClick={signOut}>
              Sign Out
            </button>
          </div>
        </div>
      </Layout>
    );
  }

  if (!profile) {
    return (
      <Layout>
        <div className="card" role="alert">
          <h1 className="section-title">Unable to Load Profile</h1>
          <p className="text-muted">{error || 'Unexpected error communicating with the API.'}</p>
          <div>
            <button type="button" className="btn" onClick={signOut}>
              Back to Sign In
            </button>
          </div>
        </div>
      </Layout>
    );
  }

  if (profile.status === 'pending') {
    return (
      <Layout>
        <RedeemInvitePage />
      </Layout>
    );
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ReportsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
