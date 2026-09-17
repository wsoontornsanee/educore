/**
 * Pure deep-link resolution helpers for push-driven navigation (spec/08 PAR-004).
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { resolveDeepLinkChild, findAttendanceRowIndex } from '../src/services/deepLink.ts';
import type { ChildSummary, AttendanceDayItem } from '../src/types/index.ts';

describe('resolveDeepLinkChild', () => {
  const children: ChildSummary[] = [
    { student_id: 1, full_name: 'Anak Satu', photo_key: '', financial_responsible: true },
    { student_id: 2, full_name: 'Anak Dua', photo_key: '', financial_responsible: false },
  ];

  it('returns the matching child by student_id', () => {
    const result = resolveDeepLinkChild(children, 2);
    assert.strictEqual(result?.student_id, 2);
  });

  it('returns null when no child matches', () => {
    assert.strictEqual(resolveDeepLinkChild(children, 999), null);
  });

  it('returns null when studentId is null or undefined', () => {
    assert.strictEqual(resolveDeepLinkChild(children, null), null);
    assert.strictEqual(resolveDeepLinkChild(children, undefined), null);
  });
});

describe('findAttendanceRowIndex', () => {
  const days: AttendanceDayItem[] = [
    { id: 1, student: 1, date: '2026-09-15', status: 'HADIR', first_in_at: '07:00', first_out_at: null },
    { id: 2, student: 1, date: '2026-09-16', status: 'IZIN', first_in_at: null, first_out_at: null },
  ];

  it('returns the index of the row matching the date', () => {
    assert.strictEqual(findAttendanceRowIndex(days, '2026-09-16'), 1);
  });

  it('returns -1 when no row matches', () => {
    assert.strictEqual(findAttendanceRowIndex(days, '2026-01-01'), -1);
  });

  it('returns -1 when date is null or undefined', () => {
    assert.strictEqual(findAttendanceRowIndex(days, null), -1);
    assert.strictEqual(findAttendanceRowIndex(days, undefined), -1);
  });
});
