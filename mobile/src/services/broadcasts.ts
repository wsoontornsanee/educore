/**
 * Broadcast / Announcement Service for Parent App (spec/08 §2 Messages tab).
 *
 * Fetches school announcements (broadcasts) sent to the selected child's
 * class group(s). Offline-first: falls back to the last successful load
 * when the network is unavailable (PAR-015).
 */
import { api } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type { BroadcastItem } from '../types/index.ts';

const CACHE_PREFIX = 'educore_parent_broadcasts';

export interface BroadcastListResult {
  broadcasts: BroadcastItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

export async function fetchStudentBroadcasts(studentId: number): Promise<BroadcastListResult> {
  const cacheKey = `${CACHE_PREFIX}_${studentId}`;
  try {
    const res = await api.get<{ results: BroadcastItem[] }>(
      `/academic/students/${studentId}/broadcasts/`,
    );
    const nowIso = new Date().toISOString();
    const broadcasts = res.data.results || [];
    await cacheSet(cacheKey, broadcasts);
    return { broadcasts, isOfflineCached: false, lastUpdated: nowIso };
  } catch (error) {
    const cached = await cacheGet<BroadcastItem[]>(cacheKey);
    if (cached && cached.value) {
      return {
        broadcasts: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}
