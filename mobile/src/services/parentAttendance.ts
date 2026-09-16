import { apiClient } from './api.ts';
import type { AttendanceDayItem } from '../types/index.ts';

export async function fetchAttendanceForChild(studentId: number): Promise<AttendanceDayItem[]> {
  const response = await apiClient.get<{ results: AttendanceDayItem[] } | AttendanceDayItem[]>(
    `/attendance/daily/?student_id=${studentId}`
  );
  const data = response.data as any;
  return Array.isArray(data) ? data : data.results;
}
