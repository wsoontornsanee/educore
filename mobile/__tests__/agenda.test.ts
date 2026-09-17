/**
 * Agenda Service and Timetable Slots Unit Tests (TCH-001, ACD-019).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  acceptSubstitution,
  declineSubstitution,
  fetchSubstitutionSlot,
  fetchTeacherAgenda,
  fetchTimetableSlot,
  findCurrentSlot,
  submitPeriodAttendance,
} from '../src/services/agenda.ts';
import { apiClient } from '../src/services/api.ts';
import { clearAllForTesting, getPendingCount } from '../src/services/offlineQueue.ts';
import type { TimetableSlotItem } from '../src/types/index.ts';

describe('Agenda and Roll Call Services', () => {
  beforeEach(async () => {
    await clearAllForTesting();
  });

  const mockSlots: TimetableSlotItem[] = [
    {
      id: 1,
      day_of_week: 1,
      period_no: 2,
      start_time: '08:15',
      end_time: '09:00',
      room: 'R-101',
      class_group_name: '7A',
      subject_name: 'Matematika',
      subject_code: 'MTK',
      is_substitution: false,
      attendance_submitted: false,
      student_count: 32,
    },
    {
      id: 2,
      day_of_week: 1,
      period_no: 1,
      start_time: '07:30',
      end_time: '08:15',
      room: 'R-102',
      class_group_name: '7B',
      subject_name: 'IPA Terpadu',
      subject_code: 'IPA',
      is_substitution: true,
      substitution_id: 88,
      substitution_status: 'PENDING',
      original_teacher_name: 'Pak Budi',
      attendance_submitted: true,
      student_count: 30,
    },
  ];

  it('fetches agenda and returns slots sorted by period_no', async () => {
    const originalGet = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/teacher/agenda?date=2026-09-16');
      return {
        data: { date: '2026-09-16', agenda: mockSlots },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const result = await fetchTeacherAgenda('2026-09-16');
      assert.strictEqual(result.length, 2);
      // Period 1 should be first, Period 2 second
      assert.strictEqual(result[0].period_no, 1);
      assert.strictEqual(result[1].period_no, 2);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('findCurrentSlot identifies the active ongoing period based on current time', () => {
    const sorted = [...mockSlots].sort((a, b) => a.period_no - b.period_no);

    // Test at 07:45 (inside Period 1: 07:30 - 08:15)
    const slotAt745 = findCurrentSlot(sorted, '07:45');
    assert.strictEqual(slotAt745?.period_no, 1);

    // Test at 08:30 (inside Period 2: 08:15 - 09:00)
    const slotAt830 = findCurrentSlot(sorted, '08:30');
    assert.strictEqual(slotAt830?.period_no, 2);
  });

  it('submits period attendance online successfully with exceptions only', async () => {
    const originalPost = apiClient.post;
    let postedBody: any = null;

    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/timetable/slots/10/period-attendance/');
      postedBody = body;
      return {
        data: { success: true },
        status: 200,
        headers: {},
      };
    }) as any;

    const entries = [
      { student_id: 1, status: 'HADIR' as const },
      { student_id: 2, status: 'SAKIT' as const },
      { student_id: 3, status: 'ALPA' as const },
    ];

    try {
      const result = await submitPeriodAttendance(10, '2026-09-16', entries);

      assert.strictEqual(result.success, true);
      assert.strictEqual(result.queuedOffline, false);

      assert.strictEqual(postedBody.date, '2026-09-16');
      assert.deepStrictEqual(postedBody.exceptions, [
        { student_id: 2, status: 'SAKIT' },
        { student_id: 3, status: 'ALPA' },
      ]);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('falls back to offline SQLite queue when network error occurs', async () => {
    const originalPost = apiClient.post;
    apiClient.post = (async () => {
      throw new Error('Network Error');
    }) as any;

    const entries = [
      { student_id: 1, status: 'HADIR' as const },
      { student_id: 2, status: 'ALPA' as const },
    ];

    try {
      const result = await submitPeriodAttendance(10, '2026-09-16', entries);

      assert.strictEqual(result.success, true);
      assert.strictEqual(result.queuedOffline, true);

      const pending = await getPendingCount();
      assert.strictEqual(pending, 1);
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('handles substitution accept and decline workflows', async () => {
    const originalPost = apiClient.post;
    let callCount = 0;

    apiClient.post = (async (path: string, body: any) => {
      callCount += 1;
      if (callCount === 1) {
        assert.strictEqual(path, '/academic/timetable/substitutions/88/accept/');
        return { data: { status: 'ACCEPTED' }, status: 200, headers: {} };
      } else {
        assert.strictEqual(path, '/academic/timetable/substitutions/88/decline/');
        assert.strictEqual(body.reason, 'Jadwal dinas luar');
        return { data: { status: 'DECLINED' }, status: 200, headers: {} };
      }
    }) as any;

    try {
      const acceptRes = await acceptSubstitution(88);
      assert.strictEqual(acceptRes.status, 'ACCEPTED');

      const declineRes = await declineSubstitution(88, 'Jadwal dinas luar');
      assert.strictEqual(declineRes.status, 'DECLINED');
    } finally {
      apiClient.post = originalPost;
    }
  });

  it('fetches substitution slot_item and normalizes TimetableSlotItem', async () => {
    const originalGet = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/academic/timetable/substitutions/88/');
      return {
        data: {
          id: 88,
          slot: 2,
          slot_item: {
            id: 2,
            slot_id: 2,
            day_of_week: 1,
            period_no: 1,
            start_time: '07:30',
            end_time: '08:15',
            room: 'R-102',
            class_group_name: '7B',
            subject_name: 'IPA Terpadu',
            subject_code: 'IPA',
            is_substitution: true,
            substitution_id: 88,
            substitution_status: 'PENDING',
            original_teacher_name: 'Pak Budi',
            attendance_submitted: false,
            student_count: 30,
          },
        },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const slot = await fetchSubstitutionSlot(88);
      assert.strictEqual(slot.id, 2);
      assert.strictEqual(slot.substitution_id, 88);
      assert.strictEqual(slot.is_substitution, true);
      assert.strictEqual(slot.class_group_name, '7B');
      assert.strictEqual(slot.subject_name, 'IPA Terpadu');
      assert.strictEqual(slot.original_teacher_name, 'Pak Budi');
      assert.strictEqual(slot.period_no, 1);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('fetches substitution slot with flat payload fallback', async () => {
    const originalGet = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/academic/timetable/substitutions/99/');
      return {
        data: {
          slot_id: 5,
          day_of_week: 2,
          period_no: 3,
          start_time: '09:00:00',
          end_time: '09:45:00',
          room: 'Lab Fisika',
          class_group: '8A',
          subject: 'Fisika',
          substitution_id: 99,
          status: 'PENDING',
          original_teacher_name: 'Bu Nina',
        },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const slot = await fetchSubstitutionSlot(99);
      assert.strictEqual(slot.id, 5);
      assert.strictEqual(slot.substitution_id, 99);
      assert.strictEqual(slot.class_group_name, '8A');
      assert.strictEqual(slot.subject_name, 'Fisika');
      assert.strictEqual(slot.start_time, '09:00');
      assert.strictEqual(slot.end_time, '09:45');
      assert.strictEqual(slot.is_substitution, true);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('fetches timetable slot directly by slotId', async () => {
    const originalGet = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/academic/timetable/slots/1/');
      return {
        data: {
          id: 1,
          slot_item: mockSlots[0],
        },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const slot = await fetchTimetableSlot(1);
      assert.strictEqual(slot.id, 1);
      assert.strictEqual(slot.subject_name, 'Matematika');
    } finally {
      apiClient.get = originalGet;
    }
  });
});
