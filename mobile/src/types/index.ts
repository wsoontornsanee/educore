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

