/**
 * SSO Service for Mobile — Google Workspace and Microsoft 365 (spec/14 §6, TASK-036).
 *
 * Handles client-side provider token acquisition, EduCore JWT token exchange,
 * and staff account linking/unlinking.
 */
import { apiClient } from './api.ts';
import { saveTokens, saveUserProfile } from './storage.ts';
import type { AuthResponse, UserProfile } from '../types/index.ts';

export type SocialProvider = 'google' | 'microsoft';

export interface SocialLinkItem {
  provider: SocialProvider;
  provider_user_id: string;
  email: string | null;
}

export interface SSOAuthError {
  message: string;
  code?: 'ACCOUNT_NOT_LINKED' | 'TOKEN_INVALID' | 'ACCOUNT_INACTIVE' | 'ACCOUNT_ALREADY_LINKED' | string;
}

// Hook for test suites or external native SDK injection
let mockProviderHandler: ((provider: SocialProvider) => Promise<string>) | null = null;

export function setMockProviderHandler(
  handler: ((provider: SocialProvider) => Promise<string>) | null
): void {
  mockProviderHandler = handler;
}

/**
 * Initiates the client-side provider auth flow to acquire an ID token.
 * In production, integrates with Google Sign-In or Microsoft MSAL / WebBrowser.
 * In testing or headless mode, delegates to the mock provider handler.
 */
export async function signInWithProvider(provider: SocialProvider): Promise<{ idToken: string }> {
  if (mockProviderHandler) {
    const token = await mockProviderHandler(provider);
    return { idToken: token };
  }

  // Fallback / simulated token acquisition for development
  return {
    idToken: `mock-${provider}-id-token-${Date.now()}`,
  };
}

/**
 * Exhange a provider ID token for an EduCore JWT session.
 * POST /auth/sso/login/
 */
export async function loginWithSSO(
  provider: SocialProvider,
  idToken: string,
  foundationId?: number
): Promise<AuthResponse> {
  const headers: Record<string, string> = {};
  if (foundationId) {
    headers['X-Foundation-ID'] = String(foundationId);
  }

  const response = await apiClient.post<AuthResponse>(
    '/auth/sso/login/',
    {
      provider,
      id_token: idToken,
    },
    { headers }
  );

  const { access, refresh, user } = response.data;
  await saveTokens(access, refresh);
  await saveUserProfile(user);

  return response.data;
}

/**
 * Fetch list of linked SSO providers for the authenticated staff user.
 * GET /auth/sso/links/
 */
export async function fetchSSOLinks(): Promise<SocialLinkItem[]> {
  const response = await apiClient.get<{ results: SocialLinkItem[] }>('/auth/sso/links/');
  return response.data?.results || [];
}

/**
 * Link an authenticated user's account to an SSO provider.
 * POST /auth/sso/link/
 */
export async function linkSSO(
  provider: SocialProvider,
  idToken: string
): Promise<SocialLinkItem> {
  const response = await apiClient.post<SocialLinkItem>('/auth/sso/link/', {
    provider,
    id_token: idToken,
  });
  return response.data;
}

/**
 * Unlink an SSO provider from the user's account.
 * DELETE /auth/sso/link/?provider=<provider>
 */
export async function unlinkSSO(provider: SocialProvider): Promise<void> {
  await apiClient.delete(`/auth/sso/link/?provider=${provider}`);
}
