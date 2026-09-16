/**
 * Type definitions for EduCore Mobile
 */

export interface RoleAssignment {
  id: number;
  role: string;
  scope_type: 'FOUNDATION' | 'SCHOOL';
  scope_id: number;
}

export interface UserProfile {
  id: number;
  full_name: string;
  phone_e164: string;
  email: string | null;
  foundation_id: number;
  roles: RoleAssignment[];
}

export interface AuthResponse {
  access: string;
  refresh: string;
  user: UserProfile;
}

export type AttendanceStatus = 'HADIR' | 'SAKIT' | 'IZIN' | 'ALPA';

export type GateScanStatus = 'IN' | 'OUT' | 'NO_SCAN';

export interface StudentRosterItem {
  student_id: number;
  full_name: string;
  nisn: string | null;
  nis: string | null;
  gate_status: GateScanStatus;
  prefill_status: AttendanceStatus;
  is_gate_prefill: boolean;
  medical_flags: string[];
}

export interface TimetableSlotItem {
  id: number;
  day_of_week: number;
  period_no: number;
  start_time: string; // e.g. "07:30"
  end_time: string;   // e.g. "08:15"
  room: string;
  class_group_name: string;
  subject_name: string;
  subject_code: string;
  is_substitution: boolean;
  substitution_id?: number | null;
  substitution_status?: 'PENDING' | 'ACCEPTED' | 'DECLINED' | null;
  original_teacher_name?: string | null;
  attendance_submitted: boolean;
  student_count: number;
  roster?: StudentRosterItem[];
}

export interface PeriodAttendanceEntry {
  student_id: number;
  status: AttendanceStatus;
  notes?: string;
}

export interface OfflineQueueItem {
  id: string;
  idempotency_key: string;
  slot_id: number;
  date: string; // YYYY-MM-DD
  entries: PeriodAttendanceEntry[];
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED';
  attempts: number;
  created_at: string;
  last_error: string | null;
}

export interface SyncBatchResult {
  total: number;
  succeeded: number;
  failed: number;
  results: Array<{
    slot_id: number;
    student_id: number;
    status: string;
    success: boolean;
    error?: string;
  }>;
}

export interface PushTokenPayload {
  token: string;
  platform: 'ios' | 'android' | 'web';
  device_name?: string;
}

export interface ChildSummary {
  student_id: number;
  full_name: string;
  photo_key: string;
  financial_responsible: boolean;
}

export interface OtpRequestResponse {
  challenge_id: number;
}
