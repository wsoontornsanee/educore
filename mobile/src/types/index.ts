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

export interface AttendanceDayItem {
  id: number;
  student: number;
  date: string;
  status: AttendanceStatus | 'TERLAMBAT' | 'DISPEN';
  first_in_at: string | null;
  first_out_at: string | null;
}

export interface InvoiceItem {
  id: number;
  number: string;
  period: string;
  due_date: string;
  total: string;
  paid: string;
  balance_due: string;
  currency: string;
  status: string;
  is_overdue: boolean;
}

export interface PaymentIntentItem {
  id: number;
  invoice: number;
  method: 'VA' | 'QRIS';
  va_bank?: string;
  va_number?: string;
  qris_payload?: string;
  amount: string;
  base_amount: string;
  convenience_fee_amount: string;
  currency: string;
  expires_at: string;
  status: string;
}

export interface PaymentReceiptAllocation {
  id: number;
  payment: number;
  invoice: number;
  invoice_number?: string;
  invoice_line?: number | null;
  amount: string;
  currency: string;
}

export interface PaymentReceiptItem {
  id: number;
  foundation_id: number;
  school: number;
  student: number;
  student_name?: string;
  amount: string;
  currency: string;
  method: string;
  channel: string;
  reference: string;
  external_id?: string | null;
  paid_at?: string | null;
  settled_at?: string | null;
  status: string;
  fee: string;
  net: string;
  receipt_number?: string | null;
  receipt_pdf_key?: string | null;
  receipt_download_url?: string | null;
  allocations: PaymentReceiptAllocation[];
  created_at: string;
}

export interface PaymentReceiptDetail {
  payment_id: number;
  reference: string;
  receipt_number: string;
  receipt_pdf_key: string;
  download_url: string;
  expires_at: string;
  amount: string;
  currency: string;
  paid_at?: string | null;
  status: string;
}

// POS & Canteen Domain Types (spec/07 §3-§6)

export interface POSProductNutrition {
  calories?: number;
  sugar_g?: number;
  is_healthy?: boolean;
}

export interface POSProduct {
  id: number;
  sku: string;
  name: string;
  price: string | number;
  category: string;
  nutrition?: POSProductNutrition;
  allergens?: string[];
  is_active?: boolean;
}

export interface POSStudent {
  id: number;
  full_name: string;
  nisn: string | null;
  nis: string | null;
  card_uid?: string | null;
  photo_url?: string | null;
  balance: string | number;
  daily_limit?: string | number | null;
  spent_today?: string | number;
  blocked_categories?: string[];
  allowed_window_start?: string | null; // e.g. "09:30"
  allowed_window_end?: string | null;   // e.g. "13:30"
}

export interface POSCartItem {
  product: POSProduct;
  qty: number;
  unit_price: number;
}

export interface POSOfflineTransactionItem {
  sku: string;
  name: string;
  qty: number;
  unit_price: string;
  category?: string;
  nutrition?: POSProductNutrition;
  allergens?: string[];
}

export interface POSOfflineTransaction {
  id: string;
  client_transaction_id: string;
  terminal_id: number;
  student_id: number;
  student_name?: string;
  items: POSOfflineTransactionItem[];
  subtotal: number;
  total: number;
  occurred_at: string;
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED';
  attempts: number;
  created_at: string;
  last_error?: string | null;
}

export interface POSSessionData {
  terminal_id: number;
  terminal_name: string;
  merchant_id: number;
  merchant_name: string;
  school_id: number;
  catalog: POSProduct[];
  roster: POSStudent[];
  sync_cursor?: string;
}

export interface POSReceipt {
  transaction_id: string;
  client_transaction_id: string;
  student_name: string;
  student_nis?: string | null;
  items: Array<{
    sku: string;
    name: string;
    qty: number;
    unit_price: number;
    total: number;
  }>;
  subtotal: number;
  total: number;
  balance_before?: number;
  balance_after?: number;
  occurred_at: string;
  offline_created: boolean;
  terminal_name?: string;
  merchant_name?: string;
}

export interface POSSpendRuleCheckResult {
  allowed: boolean;
  reason?: string;
}

export interface NutritionItem {
  sku: string;
  name: string;
  qty: number;
  unit_price: string;
  calories: number;
  sugar_g: string;
  allergens: string[];
  is_healthy: boolean;
  occurred_at: string;
}

export interface DailyNutritionBreakdown {
  date: string;
  total_calories: number;
  total_sugar_g: string;
  items_count: number;
  healthy_count: number;
}

export interface StudentNutritionSummary {
  student_id: number;
  from_date: string;
  to_date: string;
  total_calories: number;
  total_sugar_g: string;
  total_items: number;
  healthy_items_count: number;
  allergens: string[];
  daily_breakdown: DailyNutritionBreakdown[];
  items: NutritionItem[];
}

export type NutritionPeriodFilter = 'TODAY' | 'WEEK' | 'MONTH';

export interface LinkedStudentProfile {
  id: number;
  full_name: string;
  nis: string;
  nisn: string;
  class_name?: string;
  school_name?: string;
  photo_url?: string | null;
}

export type WalletStatusType = 'ACTIVE' | 'FROZEN' | 'BLOCKED';

export interface WalletData {
  id: number;
  foundation_id: number;
  student: number;
  balance: string;
  currency: string;
  status: WalletStatusType;
  daily_limit: string | null;
  created_at: string;
  updated_at: string;
}

export type WalletTransactionType = 'TOPUP' | 'PURCHASE' | 'REFUND' | 'ADJUSTMENT';

export interface WalletTransactionItem {
  id: number;
  foundation_id: number;
  wallet: number;
  type: WalletTransactionType;
  amount: string;
  balance_after: string;
  reference: string;
  occurred_at: string;
  status: string;
}

export interface WalletSpendRule {
  id?: number;
  foundation_id?: number;
  student?: number;
  daily_limit: string | null;
  blocked_categories: string[];
  blocked_products?: number[];
  allowed_window_start?: string | null;
  allowed_window_end?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WalletAutoTopupConfig {
  id?: number;
  wallet?: number;
  is_active: boolean;
  threshold_amount: string;
  topup_amount: string;
  method: 'VA' | 'QRIS';
  bank?: string;
  created_at?: string;
  updated_at?: string;
}

export interface WalletTopupIntentItem {
  id: number;
  method: 'VA' | 'QRIS';
  provider: string;
  amount: string;
  currency: string;
  va_bank: string;
  va_number: string;
  qris_payload: string;
  external_id: string;
  status: 'PENDING' | 'SETTLED' | 'EXPIRED' | 'FAILED';
  expires_at: string;
}

// --- Academic Types (spec/08 §2, PAR-010, ACD-013/014) ---

export interface StudentAssessmentItem {
  id: number;
  title: string;
  type: string;
  type_display: string;
  max_score: string;
  weight: string;
  due_at: string | null;
  score: string | null;
  descriptor: string;
  feedback: string;
  graded_at: string | null;
}

export interface StudentSubjectGradesItem {
  class_subject_id: number;
  class_group_id: number;
  class_group_name: string;
  subject_id: number;
  subject_name: string;
  subject_code: string;
  teacher_name: string;
  term_id: number;
  term_name: string;
  final_grade: string | null;
  final_descriptor: string;
  is_complete: boolean;
  assessments: StudentAssessmentItem[];
}

export interface StudentGradesData {
  student_id: number;
  subjects: StudentSubjectGradesItem[];
}

export type HomeworkStatusType = 'NOT_STARTED' | 'SUBMITTED' | 'LATE' | 'GRADED' | 'RETURNED';

export interface StudentHomeworkItem {
  id: number;
  title: string;
  instructions: string;
  subject_name: string;
  subject_code: string;
  teacher_name: string;
  class_group_name: string;
  assigned_at: string | null;
  due_at: string | null;
  submission_status: HomeworkStatusType;
  submitted_at: string | null;
  score: string | null;
  feedback: string;
  files_count: number;
}

export interface StudentReportCardItem {
  id: number;
  term_id: number;
  term_name: string;
  academic_year_name: string;
  status: string;
  visible: boolean;
  reason?: string;
  version?: number;
  grades_snapshot?: Array<{
    subject: string;
    subject_code: string;
    grade: string | number;
    descriptor?: string;
    narrative?: string;
  }>;
  attendance_snapshot?: {
    hadir?: number;
    sakit?: number;
    izin?: number;
    alpa?: number;
  };
  extracurricular_snapshot?: Array<{
    name: string;
    grade: string;
    description: string;
  }>;
  general_narrative?: string;
  promotion_decision?: string;
  download_url?: string | null;
}

export interface StudentTimetableSlotItem {
  id: number;
  day_of_week: number;
  day_name: string;
  period_no: number;
  start_time: string;
  end_time: string;
  room: string;
  subject_name: string;
  subject_code: string;
  teacher_name: string;
  class_group_name: string;
  is_substituted: boolean;
  substitute_teacher_name?: string | null;
}

export type AcademicSubTab = 'GRADES' | 'HOMEWORK' | 'REPORT_CARDS' | 'TIMETABLE';

export type AbsenceType = 'SAKIT' | 'IZIN';

export type AbsenceRequestStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface AbsenceRequestItem {
  id: number;
  student_id: number;
  student_name: string;
  requested_by_id: number;
  requested_by_name: string;
  type: AbsenceType;
  date_from: string; // YYYY-MM-DD
  date_to: string;   // YYYY-MM-DD
  reason: string;
  attachment_url: string | null;
  status: AbsenceRequestStatus;
  decision_note?: string;
  decided_at?: string | null;
  decided_by_name?: string | null;
  created_at: string;
}

export interface CreateAbsenceRequestPayload {
  student_id: number;
  type: AbsenceType;
  date_from: string;
  date_to: string;
  reason: string;
  attachmentUri?: string | null;
  attachmentName?: string | null;
  attachmentType?: string | null;
  attachmentSize?: number | null;
}
