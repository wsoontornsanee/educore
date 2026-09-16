/**
 * Agenda and Timetable Attendance Services (spec/09 §3 TCH-001/002/003, ACD-019).
 */
import { apiClient } from './api.ts';
import { enqueueAttendance } from './offlineQueue.ts';
import type { PeriodAttendanceEntry, TimetableSlotItem } from '../types/index.ts';

export interface AgendaResponse {
  date: string;
  agenda: TimetableSlotItem[];
}

export interface AttendanceSubmissionResult {
  success: boolean;
  queuedOffline: boolean;
  data?: any;
  error?: string;
}

export async function fetchTeacherAgenda(dateStr: string): Promise<TimetableSlotItem[]> {
  const response = await apiClient.get<AgendaResponse>(`/teacher/agenda?date=${dateStr}`);
  const items = response.data.agenda || [];
  return items.sort((a, b) => a.period_no - b.period_no);
}

/**
 * Calculates which slot is currently ongoing based on local time string (HH:MM).
 */
export function findCurrentSlot(slots: TimetableSlotItem[], currentTimeStr?: string): TimetableSlotItem | null {
  if (slots.length === 0) return null;

  let nowStr = currentTimeStr;
  if (!nowStr) {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    nowStr = `${hours}:${minutes}`;
  }

  for (const slot of slots) {
    if (slot.start_time <= nowStr && nowStr <= slot.end_time) {
      return slot;
    }
  }

  // If before first slot, return first slot
  if (nowStr < slots[0].start_time) {
    return slots[0];
  }

  // Default to slot nearest to current time
  return null;
}

/**
 * Submits period attendance. If offline or network fails, automatically falls back
 * to the SQLite offline queue.
 */
export async function submitPeriodAttendance(
  slotId: number,
  dateStr: string,
  entries: PeriodAttendanceEntry[],
  forceOffline: boolean = false
): Promise<AttendanceSubmissionResult> {
  // If explicitly offline, enqueue directly
  if (forceOffline) {
    await enqueueAttendance(slotId, dateStr, entries);
    return { success: true, queuedOffline: true };
  }

  // Format exceptions (non-HADIR) per TCH-002
  const exceptions = entries
    .filter((e) => e.status !== 'HADIR')
    .map((e) => ({
      student_id: e.student_id,
      status: e.status,
    }));

  try {
    const response = await apiClient.post(`/timetable/slots/${slotId}/period-attendance/`, {
      date: dateStr,
      exceptions,
    });
    return { success: true, queuedOffline: false, data: response.data };
  } catch (err: any) {
    // Check if network error (no response received or timeout)
    const isNetworkError = !err.response || err.code === 'ECONNABORTED' || err.message === 'Network Error';
    if (isNetworkError) {
      await enqueueAttendance(slotId, dateStr, entries);
      return { success: true, queuedOffline: true };
    }

    // Business logic error from server (e.g. 403 / 400)
    return {
      success: false,
      queuedOffline: false,
      error: err.response?.data?.error || err.message || 'Gagal menyimpan presensi.',
    };
  }
}

/**
 * Accept assigned substitution (ACD-019).
 */
export async function acceptSubstitution(substitutionId: number): Promise<any> {
  const response = await apiClient.post(`/academic/timetable/substitutions/${substitutionId}/accept/`);
  return response.data;
}

/**
 * Decline assigned substitution with mandatory reason (ACD-019).
 */
export async function declineSubstitution(substitutionId: number, reason: string): Promise<any> {
  const response = await apiClient.post(`/academic/timetable/substitutions/${substitutionId}/decline/`, {
    reason,
  });
  return response.data;
}
