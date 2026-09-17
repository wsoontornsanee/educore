/**
 * Pure resolution helpers for push-driven deep links (spec/08 PAR-004).
 * No side effects, no React/RN dependency — directly unit-testable.
 */
import type { AttendanceDayItem, ChildSummary } from '../types/index.ts';

export function resolveDeepLinkChild(
  children: ChildSummary[],
  studentId: number | null | undefined
): ChildSummary | null {
  if (studentId === null || studentId === undefined) return null;
  return children.find((c) => c.student_id === studentId) ?? null;
}

export function findAttendanceRowIndex(
  days: AttendanceDayItem[],
  date: string | null | undefined
): number {
  if (!date) return -1;
  return days.findIndex((d) => d.date === date);
}
