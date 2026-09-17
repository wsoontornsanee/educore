/**
 * Notification preference service — PAR-013.
 *
 * Backend endpoints:
 *   GET  /api/v1/me/notification-preferences/  -> list of NotificationPrefItem
 *   PUT  /api/v1/me/notification-preferences/  -> upsert one preference by category
 *
 * NTF-013: EMERGENCY cannot be opted out — not included in PARENT_NOTIFICATION_CATEGORIES.
 */
import { apiClient } from './api.ts';
import type { NotificationPrefItem } from '../types/index.ts';

/**
 * Parent-configurable notification categories.
 * EMERGENCY is intentionally omitted (NTF-013: cannot be opted out).
 */
export const PARENT_NOTIFICATION_CATEGORIES: string[] = [
  'ARRIVAL',
  'DEPARTURE',
  'PAYMENT_DUE',
  'PAYMENT_RECEIVED',
  'GRADE_PUBLISHED',
  'REPORT_CARD',
  'HOMEWORK',
  'CANTEEN',
  'ANNOUNCEMENT',
  'WALLET_RECONCILIATION',
];

/**
 * Categories that are non-opt-outable (NTF-013). Shown read-only in the UI.
 */
export const NON_OPTOUT_CATEGORIES: string[] = ['EMERGENCY'];

/**
 * Fetch all notification preferences for the authenticated user.
 */
export async function fetchNotificationPrefs(): Promise<NotificationPrefItem[]> {
  const res = await apiClient.get<NotificationPrefItem[]>('/me/notification-preferences/');
  return res.data;
}

/**
 * Upsert a single notification preference for the authenticated user.
 * The server uses category as the natural key (update_or_create).
 */
export async function saveNotificationPref(pref: NotificationPrefItem): Promise<NotificationPrefItem> {
  const res = await apiClient.put<NotificationPrefItem>('/me/notification-preferences/', pref);
  return res.data;
}

/**
 * Build a map from category -> preference for quick lookup.
 */
export function buildPrefMap(prefs: NotificationPrefItem[]): Record<string, NotificationPrefItem> {
  const map: Record<string, NotificationPrefItem> = {};
  for (const pref of prefs) {
    map[pref.category] = pref;
  }
  return map;
}

/**
 * Return the effective preference for a category, with sensible defaults if
 * the user has not yet explicitly set one.
 */
export function getEffectivePref(
  prefMap: Record<string, NotificationPrefItem>,
  category: string
): NotificationPrefItem {
  return prefMap[category] ?? {
    category,
    channels: [],
    quiet_hours_start: '21:00',
    quiet_hours_end: '06:00',
    enabled: true,
  };
}
