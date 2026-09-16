/**
 * Authentication service for mobile.
 */
import { apiClient } from './api.ts';
import { clearAuth, getTokens, getUserProfile, saveTokens, saveUserProfile } from './storage.ts';
import type { AuthResponse, UserProfile } from '../types/index.ts';

export async function login(identifier: string, password: string): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/token/', {
    identifier: identifier.trim(),
    password,
  });

  const { access, refresh, user } = response.data;
  await saveTokens(access, refresh);
  await saveUserProfile(user);

  return response.data;
}

export async function logout(): Promise<void> {
  await clearAuth();
}

export async function checkAuth(): Promise<{ authenticated: boolean; user: UserProfile | null }> {
  const { access } = await getTokens();
  if (!access) {
    return { authenticated: false, user: null };
  }
  const cachedUser = await getUserProfile();
  return { authenticated: true, user: cachedUser };
}

export async function fetchMyProfile(): Promise<UserProfile> {
  const response = await apiClient.get<{ user: UserProfile }>('/me');
  const user = response.data.user;
  await saveUserProfile(user);
  return user;
}
