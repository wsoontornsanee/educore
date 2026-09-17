/**
 * Parent Academic Service Unit Tests (spec/08 §2, PAR-010, PAR-015, ACD-013/014).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  fetchStudentGrades,
  fetchStudentHomework,
  fetchStudentReportCards,
  fetchStudentTimetable,
} from '../src/services/academic.ts';
import { api } from '../src/services/api.ts';
import { clearCachedData } from '../src/services/storage.ts';
import type {
  StudentGradesData,
  StudentHomeworkItem,
  StudentReportCardItem,
  StudentTimetableSlotItem,
} from '../src/types/index.ts';

describe('Parent Academic Service', () => {
  beforeEach(async () => {
    await clearCachedData();
  });

  describe('fetchStudentGrades', () => {
    it('fetches student grades from API and caches the response', async () => {
      const mockData: StudentGradesData = {
        student_id: 101,
        subjects: [
          {
            class_subject_id: 1,
            class_group_id: 10,
            class_group_name: 'X IPA 1',
            subject_id: 5,
            subject_name: 'Matematika',
            subject_code: 'MTK',
            teacher_name: 'Bu Siti Rahayu',
            term_id: 1,
            term_name: 'Semester 1',
            final_grade: '88.50',
            final_descriptor: 'Baik',
            is_complete: true,
            assessments: [
              {
                id: 100,
                title: 'Ulangan Harian 1',
                type: 'SUMMATIVE',
                type_display: 'Sumatif',
                max_score: '100.00',
                weight: '50.00',
                due_at: '2026-08-15T00:00:00Z',
                score: '88.50',
                descriptor: 'Baik',
                feedback: 'Bagus',
                graded_at: '2026-08-16T00:00:00Z',
              },
            ],
          },
        ],
      };

      const originalGet = api.get;
      api.get = (async (path: string) => {
        assert.strictEqual(path, '/academic/students/101/grades/');
        return { data: mockData, status: 200, headers: {} };
      }) as any;

      try {
        const result = await fetchStudentGrades(101);
        assert.strictEqual(result.isOfflineCached, false);
        assert.strictEqual(result.data.student_id, 101);
        assert.strictEqual(result.data.subjects.length, 1);
        assert.strictEqual(result.data.subjects[0].subject_name, 'Matematika');
      } finally {
        api.get = originalGet;
      }
    });

    it('falls back to offline cache when API call fails', async () => {
      const mockData: StudentGradesData = {
        student_id: 102,
        subjects: [],
      };

      const originalGet = api.get;
      // First call succeeds to populate cache
      api.get = (async () => ({ data: mockData, status: 200, headers: {} })) as any;
      await fetchStudentGrades(102);

      // Second call fails with network error
      api.get = (async () => {
        throw new Error('Network Error');
      }) as any;

      try {
        const cachedResult = await fetchStudentGrades(102);
        assert.strictEqual(cachedResult.isOfflineCached, true);
        assert.strictEqual(cachedResult.data.student_id, 102);
        assert.ok(cachedResult.lastUpdated);
      } finally {
        api.get = originalGet;
      }
    });
  });

  describe('fetchStudentHomework', () => {
    it('fetches homework and caches items', async () => {
      const mockItems: StudentHomeworkItem[] = [
        {
          id: 1,
          title: 'PR Aljabar',
          instructions: 'Kerjakan soal 1-10',
          subject_name: 'Matematika',
          subject_code: 'MTK',
          teacher_name: 'Bu Siti Rahayu',
          class_group_name: 'X IPA 1',
          assigned_at: '2026-08-10T00:00:00Z',
          due_at: '2026-08-15T00:00:00Z',
          submission_status: 'SUBMITTED',
          submitted_at: '2026-08-14T10:00:00Z',
          score: '90.00',
          feedback: 'Sangat baik',
          files_count: 1,
        },
      ];

      const originalGet = api.get;
      api.get = (async (path: string) => {
        assert.strictEqual(path, '/academic/students/201/homework/');
        return { data: { results: mockItems }, status: 200, headers: {} };
      }) as any;

      try {
        const result = await fetchStudentHomework(201);
        assert.strictEqual(result.isOfflineCached, false);
        assert.strictEqual(result.homework.length, 1);
        assert.strictEqual(result.homework[0].title, 'PR Aljabar');
        assert.strictEqual(result.homework[0].submission_status, 'SUBMITTED');
      } finally {
        api.get = originalGet;
      }
    });

    it('returns cached homework on network failure', async () => {
      const mockItems: StudentHomeworkItem[] = [
        {
          id: 2,
          title: 'PR Fisika',
          instructions: 'Bab 1',
          subject_name: 'Fisika',
          subject_code: 'FIS',
          teacher_name: 'Pak Budi',
          class_group_name: 'X IPA 1',
          assigned_at: null,
          due_at: null,
          submission_status: 'NOT_STARTED',
          submitted_at: null,
          score: null,
          feedback: '',
          files_count: 0,
        },
      ];

      const originalGet = api.get;
      api.get = (async () => ({ data: { results: mockItems }, status: 200, headers: {} })) as any;
      await fetchStudentHomework(202);

      api.get = (async () => {
        throw new Error('Offline');
      }) as any;

      try {
        const cached = await fetchStudentHomework(202);
        assert.strictEqual(cached.isOfflineCached, true);
        assert.strictEqual(cached.homework.length, 1);
        assert.strictEqual(cached.homework[0].title, 'PR Fisika');
      } finally {
        api.get = originalGet;
      }
    });
  });

  describe('fetchStudentReportCards', () => {
    it('fetches report cards list and reflects arrears gate withholding', async () => {
      const mockReportCards: StudentReportCardItem[] = [
        {
          id: 1,
          term_id: 1,
          term_name: 'Semester 1 (Ganjil)',
          academic_year_name: '2026/2027',
          status: 'PUBLISHED',
          visible: false,
          reason: 'ARREARS',
        },
        {
          id: 2,
          term_id: 2,
          term_name: 'Semester 2 (Genap)',
          academic_year_name: '2025/2026',
          status: 'PUBLISHED',
          visible: true,
          download_url: 'https://cdn.cendekia.sch.id/rapor2.pdf',
        },
      ];

      const originalGet = api.get;
      api.get = (async (path: string) => {
        assert.strictEqual(path, '/academic/students/301/report-cards/');
        return { data: { results: mockReportCards }, status: 200, headers: {} };
      }) as any;

      try {
        const result = await fetchStudentReportCards(301);
        assert.strictEqual(result.isOfflineCached, false);
        assert.strictEqual(result.reportCards.length, 2);
        assert.strictEqual(result.reportCards[0].visible, false);
        assert.strictEqual(result.reportCards[0].reason, 'ARREARS');
        assert.strictEqual(result.reportCards[1].visible, true);
        assert.strictEqual(result.reportCards[1].download_url, 'https://cdn.cendekia.sch.id/rapor2.pdf');
      } finally {
        api.get = originalGet;
      }
    });
  });

  describe('fetchStudentTimetable', () => {
    it('fetches timetable slots with substitutions and date parameter', async () => {
      const mockSlots: StudentTimetableSlotItem[] = [
        {
          id: 1,
          day_of_week: 1,
          day_name: 'Senin',
          period_no: 1,
          start_time: '07:30',
          end_time: '08:50',
          room: 'Lab Komputer',
          subject_name: 'Matematika',
          subject_code: 'MTK',
          teacher_name: 'Bu Siti Rahayu',
          class_group_name: 'X IPA 1',
          is_substituted: true,
          substitute_teacher_name: 'Pak Bambang',
        },
      ];

      const originalGet = api.get;
      api.get = (async (path: string) => {
        assert.strictEqual(path, '/academic/students/401/timetable/?date=2026-09-17');
        return { data: { date: '2026-09-17', slots: mockSlots }, status: 200, headers: {} };
      }) as any;

      try {
        const result = await fetchStudentTimetable(401, '2026-09-17');
        assert.strictEqual(result.isOfflineCached, false);
        assert.strictEqual(result.slots.length, 1);
        assert.strictEqual(result.slots[0].is_substituted, true);
        assert.strictEqual(result.slots[0].substitute_teacher_name, 'Pak Bambang');
      } finally {
        api.get = originalGet;
      }
    });
  });
});
