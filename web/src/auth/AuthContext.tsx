import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { ApiError, apiClient } from '../api/client';
import type { Profile, ProfileUpdate } from '../api/types';
import {
  DEV_OWNER_EMAIL,
  DEV_OWNER_USER_ID,
  buildSupabaseGoogleOAuthUrl,
  extractSupabaseHashToken,
  issueDevJwt,
} from './jwt';

const STORAGE_KEY = 'daily_brief_auth_token';

interface AuthContextValue {
  token: string | null;
  profile: Profile | null;
  loading: boolean;
  error: string | null;
  errorCode: string | null;
  signInWithDevAccount: (claims?: { sub: string; email: string }) => Promise<void>;
  signInWithToken: (rawToken: string) => Promise<void>;
  signInWithGoogle: () => void;
  redeemInviteCode: (code: string) => Promise<Profile>;
  updateProfile: (update: ProfileUpdate) => Promise<Profile>;
  refreshProfile: () => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => {
    if (typeof window !== 'undefined') {
      const hashToken = extractSupabaseHashToken(window.location.hash);
      if (hashToken) {
        window.localStorage.setItem(STORAGE_KEY, hashToken);
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
        return hashToken;
      }
      return window.localStorage.getItem(STORAGE_KEY);
    }
    return null;
  });

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(token));
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  const loadProfileForToken = useCallback(async (activeToken: string) => {
    setLoading(true);
    setError(null);
    setErrorCode(null);
    try {
      const loaded = await apiClient.getMe(activeToken);
      setProfile(loaded);
    } catch (err) {
      setProfile(null);
      if (err instanceof ApiError) {
        setError(err.problem.detail);
        setErrorCode(err.problem.code);
        if (err.problem.status === 401) {
          window.localStorage.removeItem(STORAGE_KEY);
          setToken(null);
        }
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load profile.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!token) {
      setProfile(null);
      setLoading(false);
      return;
    }
    void loadProfileForToken(token);
  }, [token, loadProfileForToken]);

  const signInWithToken = useCallback(async (rawToken: string) => {
    const trimmed = rawToken.trim();
    if (!trimmed) {
      setError('Token is required.');
      return;
    }
    window.localStorage.setItem(STORAGE_KEY, trimmed);
    setToken(trimmed);
  }, []);

  const signInWithDevAccount = useCallback(
    async (claims: { sub: string; email: string } = { sub: DEV_OWNER_USER_ID, email: DEV_OWNER_EMAIL }) => {
      setLoading(true);
      setError(null);
      setErrorCode(null);
      try {
        const jwt = await issueDevJwt(claims);
        window.localStorage.setItem(STORAGE_KEY, jwt);
        setToken(jwt);
      } catch (err) {
        setLoading(false);
        setError(err instanceof Error ? err.message : 'Failed to issue dev token.');
      }
    },
    [],
  );

  const signInWithGoogle = useCallback(() => {
    const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
    if (!supabaseUrl) {
      setError('VITE_SUPABASE_URL is not configured in this environment.');
      return;
    }
    const redirectUrl = buildSupabaseGoogleOAuthUrl(supabaseUrl, window.location.origin);
    window.location.assign(redirectUrl);
  }, []);

  const redeemInviteCode = useCallback(
    async (code: string): Promise<Profile> => {
      if (!token) {
        throw new Error('Not signed in.');
      }
      const updated = await apiClient.redeemInvite(token, { code });
      setProfile(updated);
      return updated;
    },
    [token],
  );

  const updateProfile = useCallback(
    async (update: ProfileUpdate): Promise<Profile> => {
      if (!token) {
        throw new Error('Not signed in.');
      }
      const updated = await apiClient.updateMe(token, update);
      setProfile(updated);
      return updated;
    },
    [token],
  );

  const refreshProfile = useCallback(async () => {
    if (token) {
      await loadProfileForToken(token);
    }
  }, [token, loadProfileForToken]);

  const signOut = useCallback(() => {
    window.localStorage.removeItem(STORAGE_KEY);
    setToken(null);
    setProfile(null);
    setError(null);
    setErrorCode(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token,
        profile,
        loading,
        error,
        errorCode,
        signInWithDevAccount,
        signInWithToken,
        signInWithGoogle,
        redeemInviteCode,
        updateProfile,
        refreshProfile,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
