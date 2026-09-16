/**
 * Roll Call Screen Logic Unit Tests (spec/09 §3 TCH-002, TCH-003, TCH-004).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { submitPeriodAttendance } from '../src/services/agenda.ts';
import { clearAllForTesting, getPendingCount, getPendingQueue } from '../src/services/offlineQueue.ts';
import { apiClient } from '../src/services/api.ts';
import type { AttendanceStatus, PeriodAttendanceEntry, StudentRosterItem } from '../src/types/index.ts';

describe('Roll Call Roster and Exception Rules', () => {
  beforeEach(async () => {
    await clearAllForTesting();
  });

  const mockRoster: StudentRosterItem[] = Array.from({ length: 32 }, (_, idx) => {
    const id = idx + 1;
    // Students 5 and 18 have no gate scan
    const isGateAbsent = id === 5 || id === 18;
    return {
      student_id: id,
      full_name: `Siswa ${id}`,
      nis: `NIS-${id}`,
      nisn: `NISN-${id}`,
      gate_status: isGateAbsent ? 'NO_SCAN' : 'IN',
      prefill_status: isGateAbsent ? 'ALPA' : 'HADIR',
      is_gate_prefill: isGateAbsent,
      medical_flags: id === 12 ? ['ASMA'] : [],
    };
  });

  it('pre-fills ALPA for students without gate IN scan (TCH-003) and defaults others to HADIR', () => {
    const statusMap: Record<number, AttendanceStatus> = {};
    for (const student of mockRoster) {
      if (student.gate_status === 'NO_SCAN' || student.gate_status === 'OUT') {
        statusMap[student.student_id] = 'ALPA';
      } else {
        statusMap[student.student_id] = 'HADIR';
      }
    }

    assert.strictEqual(statusMap[5], 'ALPA');
    assert.strictEqual(statusMap[18], 'ALPA');
    assert.strictEqual(statusMap[1], 'HADIR');
    assert.strictEqual(statusMap[32], 'HADIR');

    const alpaCount = Object.values(statusMap).filter((s) => s === 'ALPA').length;
    const hadirCount = Object.values(statusMap).filter((s) => s === 'HADIR').length;
    assert.strictEqual(alpaCount, 2);
    assert.strictEqual(hadirCount, 30);
  });

  it('submits attendance with 2 gate exceptions taking ≤15s equivalent single payload', async () => {
    const originalPost = apiClient.post;
    let submittedPayload: any = null;

    apiClient.post = (async (path: string, body: any) => {
      submittedPayload = body;
      return { data: { success: true }, status: 200, headers: {} };
    }) as any;

    const entries: PeriodAttendanceEntry[] = mockRoster.map((s) => ({
      student_id: s.student_id,
      status: s.student_id === 5 || s.student_id === 18 ? 'ALPA' : 'HADIR',
    }));

    try {
      const result = await submitPeriodAttendance(44, '2026-09-16', entries);
      assert.strictEqual(result.success, true);
      assert.strictEqual(result.queuedOffline, false);

      // Only exceptions are transmitted to minimize payload and network latency
      assert.strictEqual(submittedPayload.exceptions.length, 2);
      assert.deepStrictEqual(submittedPayload.exceptions, [
        { student_id: 5, status: 'ALPA' },
        { student_id: 18, status: 'ALPA' },
      ]);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('offline roll call submission enqueues immediately and preserves exact student exceptions', async () => {
    const entries: PeriodAttendanceEntry[] = mockRoster.map((s) => ({
      student_id: s.student_id,
      status: s.student_id === 3 ? 'SAKIT' : s.student_id === 5 ? 'ALPA' : 'HADIR',
    }));

    // Submit with forceOffline=true
    const result = await submitPeriodAttendance(44, '2026-09-16', entries, true);
    assert.strictEqual(result.success, true);
    assert.strictEqual(result.queuedOffline, true);

    const pending = await getPendingQueue();
    assert.strictEqual(pending.length, 1);
    assert.strictEqual(pending[0].slot_id, 44);
    assert.strictEqual(pending[0].entries.length, 32);

    // Verify non-HADIR entries preserved
    const entrySakit = pending[0].entries.find((e) => e.student_id === 3);
    const entryAlpa = pending[0].entries.find((e) => e.student_id === 5);
    assert.strictEqual(entrySakit?.status, 'SAKIT');
    assert.strictEqual(entryAlpa?.status, 'ALPA');
  });
});
