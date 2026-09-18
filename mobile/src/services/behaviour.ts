/**
 * Behaviour / Point System Service (spec/09 TCH-008/009).
 *
 * TCH-008: behaviour points recordable in ≤3 taps from mobile.
 * TCH-009: reasons must be from school-configured catalogue (not free-text).
 *
 * API endpoints (spec/09 §5):
 *   GET  /behaviour-reasons          → list of school-configured reasons
 *   POST /behaviour-records          → record a behaviour point
 */
import { api } from './api.ts';
import type { BehaviourReason, BehaviourRecord, BehaviourRecordPayload } from '../types/index.ts';

/**
 * Fetch the school-configured behaviour reason catalogue.
 * TCH-009: reasons have point values (positive and negative);
 * free-text-only records are not permitted.
 */
export async function fetchBehaviourReasons(): Promise<BehaviourReason[]> {
  const res = await api.get<{ results: BehaviourReason[] } | BehaviourReason[]>('/behaviour-reasons/');
  const data = res.data as any;
  return Array.isArray(data) ? data : (data?.results ?? []);
}

/**
 * Record a behaviour point for a student.
 * TCH-016: all writes attributed and audited server-side via recorded_by.
 */
export async function submitBehaviourRecord(
  payload: BehaviourRecordPayload,
): Promise<BehaviourRecord> {
  const res = await api.post<BehaviourRecord>('/behaviour-records/', payload);
  return res.data;
}
