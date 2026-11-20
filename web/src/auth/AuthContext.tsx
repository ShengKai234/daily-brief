import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { ApiError, apiClient, setUnauthorizedHandler } from '../api/client';
import { ERROR_MESSAGES, formatProblemMessage } from '../api/errors';
import type { Profile, ProfileUpdate } from '../api/types';

interface AuthContextValue {
  profile: Profile | null;
  loading: boolean;
  error: string | null;
  errorCode: string | null;
  clearError: () => void;
  setSignedInProfile: (nextProfile: Profile) => void;
  updateProfile: (update: ProfileUpdate) => Promise<Profile>;
  refreshProfile: () => Promise<void>;
  signOut: () => Promise<void>;
  handleSessionRevoked: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  const clearError = useCallback(() => {
    setError(null);
    setErrorCode(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setProfile(null);
      setErrorCode('unauthorized');
      setError(ERROR_MESSAGES.unauthorized);
    });
    return () => {
      setUnauthorizedHandler(null);
    };
  }, []);

  const loadCurrentProfile = useCallback(async (silentOn401: boolean) => {
    setLoading(true);
    if (!silentOn401) {
      setError(null);
      setErrorCode(null);
    }
    try {
      const loaded = await apiClient.getMe({ skipUnauthorizedHandler: true });
      setProfile(loaded);
      setError(null);
      setErrorCode(null);
    } catch (err) {
      setProfile(null);
      if (err instanceof ApiError) {
        if (err.problem.status === 401 && silentOn401) {
          setError(null);
          setErrorCode(null);
        } else {
          setError(formatProblemMessage(err.problem));
          setErrorCode(err.problem.code);
        }
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load profile.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCurrentProfile(true);
  }, [loadCurrentProfile]);

  const setSignedInProfile = useCallback((nextProfile: Profile) => {
    setProfile(nextProfile);
    setError(null);
    setErrorCode(null);
    setLoading(false);
  }, []);

  const updateProfile = useCallback(async (update: ProfileUpdate): Promise<Profile> => {
    const updated = await apiClient.updateMe(update);
    setProfile(updated);
    return updated;
  }, []);

  const refreshProfile = useCallback(async () => {
    await loadCurrentProfile(false);
  }, [loadCurrentProfile]);

  const handleSessionRevoked = useCallback(() => {
    setProfile(null);
    setError(null);
    setErrorCode(null);
  }, []);

  const signOut = useCallback(async () => {
    setError(null);
    setErrorCode(null);
    try {
      await apiClient.logout();
      setProfile(null);
    } catch (err) {
      if (err instanceof ApiError && err.problem.status === 401) {
        setProfile(null);
        return;
      }
      if (err instanceof ApiError) {
        setErrorCode(err.problem.code);
        setError(`Sign out failed: ${formatProblemMessage(err.problem)}`);
      } else {
        setError(
          `Sign out failed: ${err instanceof Error ? err.message : 'Please try again.'}`,
        );
      }
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        profile,
        loading,
        error,
        errorCode,
        clearError,
        setSignedInProfile,
        updateProfile,
        refreshProfile,
        signOut,
        handleSessionRevoked,
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
