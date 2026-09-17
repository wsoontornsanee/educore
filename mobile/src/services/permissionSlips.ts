/**
 * Permission Slip Service for Parent App (spec/08 §2 Messages tab, PAR-012).
 *
 * - Lists the selected child's class permission slips with the guardian's own
 *   effective response (latest acknowledgement wins on the server).
 * - Signs a digital acknowledgement with a typed signature; the server records
 *   the timestamp (PAR-012).
 * - Offline-first: list falls back to the last successful load (PAR-015);
 *   signing is a write action and is NOT cached — the caller disables it offline.
 */
import { api } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type {
  AcknowledgePermissionSlipPayload,
  PermissionSlipItem,
  PermissionSlipResponse,
} from '../types/index.ts';

const CACHE_PREFIX = 'educore_parent_permission_slips';

export interface PermissionSlipListResult {
  slips: PermissionSlipItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface AcknowledgeResult {
  id: number;
  permission_slip_id: number;
  student_id: number;
  response: 'APPROVED' | 'DECLINED';
  responded_at: string;
  signature: string;
}

export async function fetchStudentPermissionSlips(studentId: number): Promise<PermissionSlipListResult> {
  const cacheKey = `${CACHE_PREFIX}_${studentId}`;
  try {
    const res = await api.get<{ results: PermissionSlipItem[] }>(
      `/academic/students/${studentId}/permission-slips/`,
    );
    const nowIso = new Date().toISOString();
    const slips = res.data.results || [];
    await cacheSet(cacheKey, slips);
    return { slips, isOfflineCached: false, lastUpdated: nowIso };
  } catch (error) {
    const cached = await cacheGet<PermissionSlipItem[]>(cacheKey);
    if (cached && cached.value) {
      return {
        slips: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

export async function acknowledgePermissionSlip(
  slipId: number,
  payload: AcknowledgePermissionSlipPayload,
): Promise<AcknowledgeResult> {
  const res = await api.post<AcknowledgeResult>(
    `/academic/permission-slips/${slipId}/acknowledge/`,
    payload,
  );
  return res.data;
}

/** Local helper mirroring the server's effective-response rule for UI state. */
export function resolveSlipStatus(slip: PermissionSlipItem): PermissionSlipResponse {
  if (slip.my_response) return slip.my_response;
  return 'PENDING';
}
