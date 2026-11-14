import React, { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';

export function ProfilePage() {
  const { profile, updateProfile } = useAuth();
  const [displayName, setDisplayName] = useState<string>(profile?.display_name ?? '');
  const [bio, setBio] = useState<string>(profile?.bio ?? '');
  const [timezone, setTimezone] = useState<string>(profile?.timezone ?? 'Asia/Taipei');
  const [locale, setLocale] = useState<string>(profile?.locale ?? 'zh-TW');
  const [avatarUrl, setAvatarUrl] = useState<string>(profile?.avatar_url ?? '');
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  useEffect(() => {
    if (profile) {
      setDisplayName(profile.display_name);
      setBio(profile.bio);
      setTimezone(profile.timezone);
      setLocale(profile.locale);
      setAvatarUrl(profile.avatar_url ?? '');
    }
  }, [profile]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSavedNotice(null);
    try {
      await updateProfile({
        display_name: displayName.trim(),
        bio,
        timezone: timezone.trim(),
        locale: locale.trim(),
        avatar_url: avatarUrl.trim() ? avatarUrl.trim() : null,
      });
      setSavedNotice('Profile updated.');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.problem.detail} (${err.problem.code})`);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to update profile.');
      }
    } finally {
      setSaving(false);
    }
  };

  if (!profile) {
    return null;
  }

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Profile Settings</h1>
          <p className="page-subtitle">
            Manage your display name, biography, time zone, and output language locale.
          </p>
        </div>
      </div>

      {error ? (
        <div className="alert" role="alert">
          <strong>Error:</strong> {error}
        </div>
      ) : null}

      {savedNotice ? (
        <div className="alert" role="status">
          {savedNotice}
        </div>
      ) : null}

      <section className="card" aria-labelledby="account-info-heading">
        <h2 id="account-info-heading" className="section-title">
          Account Identity
        </h2>
        <p className="text-muted">
          Email: <strong>{profile.email}</strong> · Status: <strong>{profile.status}</strong>
        </p>
        <p className="text-subtle text-mono">User ID: {profile.user_id}</p>
      </section>

      <section className="card" aria-labelledby="edit-profile-heading">
        <h2 id="edit-profile-heading" className="section-title">
          Preferences
        </h2>
        <form className="stack" onSubmit={(e) => void handleSubmit(e)}>
          <div className="form-group">
            <label htmlFor="profile-display-name" className="form-label">
              Display Name
            </label>
            <input
              id="profile-display-name"
              type="text"
              className="input"
              maxLength={80}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="profile-bio" className="form-label">
              Bio
            </label>
            <textarea
              id="profile-bio"
              className="textarea"
              maxLength={500}
              value={bio}
              onChange={(e) => setBio(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label htmlFor="profile-timezone" className="form-label">
              Time Zone (IANA)
            </label>
            <input
              id="profile-timezone"
              type="text"
              className="input"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="profile-locale" className="form-label">
              Report Locale (BCP 47, 2–35 chars)
            </label>
            <input
              id="profile-locale"
              type="text"
              className="input"
              minLength={2}
              maxLength={35}
              value={locale}
              onChange={(e) => setLocale(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="profile-avatar-url" className="form-label">
              Avatar URL (optional)
            </label>
            <input
              id="profile-avatar-url"
              type="url"
              className="input"
              maxLength={2048}
              placeholder="https://example.com/avatar.png"
              value={avatarUrl}
              onChange={(e) => setAvatarUrl(e.target.value)}
            />
          </div>

          <div>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save Profile'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
