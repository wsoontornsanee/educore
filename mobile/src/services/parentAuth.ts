/**
 * Guardian OTP login service (spec/08 PAR-001).
 */
import { apiClient } from './api.ts';
import { saveTokens, saveUserProfile } from './storage.ts';
import type { AuthResponse, OtpRequestResponse } from '../types/index.ts';

export async function requestOtp(phoneE164: string): Promise<OtpRequestResponse> {
  const response = await apiClient.post<OtpRequestResponse>('/auth/otp/request/', {
    phone_e164: phoneE164,
  });
  return response.data;
}

export async function verifyOtp(challengeId: number, code: string): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/otp/verify/', {
    challenge_id: challengeId,
    code,
  });

  const { access, refresh, user } = response.data;
  await saveTokens(access, refresh);
  await saveUserProfile(user);

  return response.data;
}
