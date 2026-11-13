import React from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export function Layout({ children }: { children: React.ReactNode }) {
  const { profile, signOut } = useAuth();

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <header className="app-header">
        <div className="app-header-inner">
          <div className="row">
            <NavLink to="/" className="app-brand">
              Daily Brief
            </NavLink>
            {profile ? (
              <span className="badge" aria-label="Account status">
                {profile.status}
              </span>
            ) : null}
          </div>

          {profile && profile.status === 'active' ? (
            <nav className="app-nav" aria-label="Main navigation">
              <NavLink to="/" end className="nav-link">
                Today&apos;s Report
              </NavLink>
              <NavLink to="/settings" className="nav-link">
                Topics &amp; Slots
              </NavLink>
              <NavLink to="/profile" className="nav-link">
                Profile
              </NavLink>
              <button type="button" className="btn" onClick={signOut}>
                Sign Out
              </button>
            </nav>
          ) : profile ? (
            <div className="app-nav">
              <button type="button" className="btn" onClick={signOut}>
                Sign Out
              </button>
            </div>
          ) : null}
        </div>
      </header>

      <main id="main-content" className="app-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
