import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { EnrollPage } from './pages/EnrollPage';
import { ProfilePage } from './pages/ProfilePage';
import { ReportsPage } from './pages/ReportsPage';
import { SecurityPage } from './pages/SecurityPage';
import { SettingsPage } from './pages/SettingsPage';
import { SignInPage } from './pages/SignInPage';

export function AppRoutes() {
  const { profile, loading, error, errorCode, signOut } = useAuth();

  if (loading) {
    return (
      <Layout>
        <div className="card" role="status">
          <p className="text-muted">Loading session...</p>
        </div>
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
            <button type="button" className="btn" onClick={() => void signOut()}>
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
        <Routes>
          <Route path="/enroll" element={<EnrollPage />} />
          <Route path="*" element={<SignInPage />} />
        </Routes>
      </Layout>
    );
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ReportsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/security" element={<SecurityPage />} />
        <Route path="/enroll" element={<EnrollPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
