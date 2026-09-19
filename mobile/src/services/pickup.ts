/**
 * Pickup authorisations (spec/05 ATT-015): a guardian authorises a named person to collect a child for a time
 * window and shows them a QR. Server rules (who counts as a guardian, single use, expiry) live in
 * apps/attendance/pickup.py; this is the client's thin layer.
 *
 * Nothing is cached on the device: the list carries a phone number and a live QR token.
 */
import { apiClient } from './api.ts';
import { WIB_OFFSET_MINUTES } from './localDate.ts';
import type { PickupAuthorizationItem } from '../types/index.ts';

export type WindowPreset = 'TODAY' | 'WEEK' | 'MONTH';

export interface NewPickupAuthorization {
  studentId: number;
  personName: string;
  relation: string;
  phone: string;
  preset: WindowPreset;
  oneTime: boolean;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * The validity window for a preset, starting now. TODAY runs to the last second of today in WIB (a guardian
 * thinks in school days, not in UTC); the others are a fixed number of days from now.
 */
export function windowFor(preset: WindowPreset, now: Date = new Date()): { valid_from: string; valid_to: string } {
  let end: number;
  if (preset === 'TODAY') {
    const wib = new Date(now.getTime() + WIB_OFFSET_MINUTES * 60_000);
    const nextMidnightWib = Date.UTC(wib.getUTCFullYear(), wib.getUTCMonth(), wib.getUTCDate() + 1) - WIB_OFFSET_MINUTES * 60_000;
    end = nextMidnightWib - 1000;
  } else {
    end = now.getTime() + (preset === 'WEEK' ? 7 : 30) * DAY_MS;
  }
  return { valid_from: now.toISOString(), valid_to: new Date(end).toISOString() };
}

export async function fetchPickupAuthorizations(studentId: number): Promise<PickupAuthorizationItem[]> {
  const response = await apiClient.get<PickupAuthorizationItem[]>(`/pickup-authorizations/?student_id=${studentId}`);
  return Array.isArray(response.data) ? response.data : [];
}

export async function createPickupAuthorization(input: NewPickupAuthorization, now: Date = new Date()): Promise<PickupAuthorizationItem> {
  const response = await apiClient.post<PickupAuthorizationItem>('/pickup-authorizations/', {
    student_id: input.studentId,
    person_name: input.personName.trim(),
    relation: input.relation.trim(),
    phone: input.phone.trim(),
    ...windowFor(input.preset, now),
    one_time: input.oneTime,
  });
  return response.data;
}

export async function revokePickupAuthorization(id: number): Promise<PickupAuthorizationItem> {
  const response = await apiClient.post<PickupAuthorizationItem>(`/pickup-authorizations/${id}/revoke/`, {});
  return response.data;
}

/** The server's machine-readable pickup error code (e.g. PICKUP_NOT_GUARDIAN), or null for anything else. */
export function pickupErrorCode(error: unknown): string | null {
  const code = (error as { response?: { data?: { code?: unknown } } })?.response?.data?.code;
  return typeof code === 'string' ? code : null;
}

/** ACTIVE and SCHEDULED can still be shown as a QR and revoked; USED, EXPIRED and REVOKED are history. */
export function isLive(item: PickupAuthorizationItem): boolean {
  return item.status === 'ACTIVE' || item.status === 'SCHEDULED';
}
