/**
 * Academic Data Service for Parent App (spec/08 §2, PAR-010, PAR-015, ACD-013/014).
 * 
 * Provides API client calls and offline caching for:
 * - Grades & Attainment (published assessments only per PAR-010)
 * - Homework & Submission status
 * - Report Cards & Arrears Gate withholding
 * - Timetable & Daily teacher substitutions
 */
import { api } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type {
  StudentGradesData,
  StudentHomeworkItem,
  StudentReportCardItem,
  StudentTimetableSlotItem,
} from '../types/index.ts';

export interface AcademicGradesResult {
  data: StudentGradesData;
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface AcademicHomeworkResult {
  homework: StudentHomeworkItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface AcademicReportCardsResult {
  reportCards: StudentReportCardItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface AcademicTimetableResult {
  date: string;
  slots: StudentTimetableSlotItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

const CACHE_PREFIX = 'educore_parent_academic';

export async function fetchStudentGrades(studentId: number): Promise<AcademicGradesResult> {
  const cacheKey = `${CACHE_PREFIX}_grades_${studentId}`;
  try {
    const res = await api.get<StudentGradesData>(`/academic/students/${studentId}/grades/`);
    const nowIso = new Date().toISOString();
    await cacheSet(cacheKey, res.data);
    return {
      data: res.data,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<StudentGradesData>(cacheKey);
    if (cached && cached.value) {
      return {
        data: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

export async function fetchStudentHomework(studentId: number): Promise<AcademicHomeworkResult> {
  const cacheKey = `${CACHE_PREFIX}_homework_${studentId}`;
  try {
    const res = await api.get<{ results: StudentHomeworkItem[] }>(`/academic/students/${studentId}/homework/`);
    const nowIso = new Date().toISOString();
    const items = res.data.results || [];
    await cacheSet(cacheKey, items);
    return {
      homework: items,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<StudentHomeworkItem[]>(cacheKey);
    if (cached && cached.value) {
      return {
        homework: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

export async function fetchStudentReportCards(studentId: number): Promise<AcademicReportCardsResult> {
  const cacheKey = `${CACHE_PREFIX}_report_cards_${studentId}`;
  try {
    const res = await api.get<{ results: StudentReportCardItem[] }>(`/academic/students/${studentId}/report-cards/`);
    const nowIso = new Date().toISOString();
    const items = res.data.results || [];
    await cacheSet(cacheKey, items);
    return {
      reportCards: items,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<StudentReportCardItem[]>(cacheKey);
    if (cached && cached.value) {
      return {
        reportCards: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

export async function fetchStudentTimetable(studentId: number, date?: string): Promise<AcademicTimetableResult> {
  const query = date ? `?date=${encodeURIComponent(date)}` : '';
  const cacheKey = `${CACHE_PREFIX}_timetable_${studentId}_${date || 'default'}`;
  try {
    const res = await api.get<{ date: string; slots: StudentTimetableSlotItem[] }>(
      `/academic/students/${studentId}/timetable/${query}`
    );
    const nowIso = new Date().toISOString();
    const slots = res.data.slots || [];
    const payload = { date: res.data.date, slots };
    await cacheSet(cacheKey, payload);
    return {
      date: res.data.date,
      slots,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<{ date: string; slots: StudentTimetableSlotItem[] }>(cacheKey);
    if (cached && cached.value) {
      return {
        date: cached.value.date,
        slots: cached.value.slots,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}
