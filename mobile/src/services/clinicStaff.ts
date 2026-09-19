/**
 * Clinic officer (Petugas UKS) data layer: recent clinic visits, medication stock, student lookup, the health
 * profile shown before treatment, and recording a visit (spec/10 LIF-001..004).
 *
 * Scoping is server-side: the same clinic.read / clinic.write / student_records.read endpoints the web console
 * uses, narrowed to the caller's own schools.
 *
 * Visit notes (complaint, treatment) are medical data. Unlike the parent client (services/clinic.ts), nothing
 * here is written to on-device storage, and there is deliberately no offline queue: a queued visit would put
 * plaintext notes in an unencrypted store (LIF-007). Offline, the screens show an error and the form stays open.
 */
import { apiClient } from './api.ts';
import type {
  ClinicVisitItem, HealthProfileItem, MedicationStockItem, StudentLookupItem,
} from '../types/index.ts';

type Paged<T> = { results: T[] } | T[];

function items<T>(data: Paged<T> | undefined): T[] {
  if (Array.isArray(data)) return data;
  return data?.results ?? [];
}

export interface ClinicVisitsPage {
  visits: ClinicVisitItem[];
  /** Opaque cursor for the next (older) page; null when this was the last one. */
  nextCursor: string | null;
}

/** The server returns `next` as an absolute URL (its own idea of scheme and host); only the cursor is kept. */
export function cursorFromNext(next: string | null | undefined): string | null {
  const match = next ? /[?&]cursor=([^&]+)/.exec(next) : null;
  return match ? match[1] : null;
}

/** Newest first, one server page at a time; pass the previous page's `nextCursor` for the next one. */
export async function fetchClinicVisitsPage(cursor?: string | null): Promise<ClinicVisitsPage> {
  const path = cursor ? `/campus/clinic-visits/?cursor=${cursor}` : '/campus/clinic-visits/';
  const response = await apiClient.get<Paged<ClinicVisitItem> & { next?: string | null }>(path);
  const data = response.data;
  return { visits: items(data), nextCursor: Array.isArray(data) ? null : cursorFromNext(data?.next) };
}

export async function fetchMedicationStock(): Promise<MedicationStockItem[]> {
  const response = await apiClient.get<Paged<MedicationStockItem>>('/campus/medication-stock/');
  return items(response.data);
}

export function isLowStock(item: MedicationStockItem): boolean {
  return item.quantity <= item.reorder_level;
}

/** Expiry is a calendar date; expired means before today, not merely today. */
export function isExpired(item: MedicationStockItem, today: string): boolean {
  return !!item.expiry_date && item.expiry_date < today;
}

/** Items needing attention first (low stock or expired), then by name. Does not mutate its input. */
export function sortStockForAttention(list: MedicationStockItem[], today: string): MedicationStockItem[] {
  const needsAttention = (i: MedicationStockItem) => (isLowStock(i) || isExpired(i, today) ? 0 : 1);
  return [...list].sort((a, b) => needsAttention(a) - needsAttention(b) || a.name.localeCompare(b.name));
}

// ── Student lookup and health profile (LIF-001, LIF-002) ────────────────────────────────────────────────────

export const MIN_SEARCH_LENGTH = 2;

/** Name or NIS/NISN search over the caller's own schools' active students; too-short queries make no request. */
export async function searchStudents(query: string): Promise<StudentLookupItem[]> {
  const q = query.trim();
  if (q.length < MIN_SEARCH_LENGTH) return [];
  const response = await apiClient.get<Paged<any>>(`/students/?status=ACTIVE&q=${encodeURIComponent(q)}`);
  // The endpoint returns the whole student record; only what identifies the child is kept.
  return items(response.data).map((s) => ({
    id: s.id,
    name: s.person?.full_name || s.nis,
    nis: s.nis,
    school: s.school,
    school_name: s.school_name ?? '',
  }));
}

/** Allergies, chronic conditions and medications: shown above the fold before any treatment is recorded. */
export async function fetchStudentHealthProfile(studentId: number): Promise<HealthProfileItem> {
  const response = await apiClient.get<HealthProfileItem>(`/campus/students/${studentId}/health-profile/`);
  return response.data;
}

// ── Recording a visit (LIF-001, LIF-003, LIF-004) ───────────────────────────────────────────────────────────

export type ClinicOutcome = 'RETURNED_TO_CLASS' | 'SENT_HOME' | 'REFERRED';
export const CLINIC_OUTCOMES: ClinicOutcome[] = ['RETURNED_TO_CLASS', 'SENT_HOME', 'REFERRED'];

/** The form as typed: numbers stay strings so a half-typed value is not silently coerced. */
export interface VisitDraft {
  student: StudentLookupItem;
  complaint: string;
  treatment: string;
  temperature: string;
  pulse: string;
  outcome: ClinicOutcome | null;
  medication: MedicationStockItem | null;
  quantity: string;
  consentConfirmed: boolean;
  consentNote: string;
}

export type VisitDraftIssue =
  | 'complaint' | 'outcome' | 'temperature' | 'pulse' | 'quantity' | 'quantity_over_stock' | 'medication_expired';

/** id-ID users type a decimal comma ("37,5"). Empty is fine (vitals are optional); anything else must be a number. */
function parseNumber(text: string): number | null | 'invalid' {
  const trimmed = text.trim().replace(',', '.');
  if (!trimmed) return null;
  return /^\d+(\.\d+)?$/.test(trimmed) ? Number(trimmed) : 'invalid';
}

const TEMPERATURE_RANGE_C = [30, 45] as const; // outside this is a typo, not a patient
const PULSE_RANGE_BPM = [20, 250] as const;

/**
 * What is wrong with the draft before it is sent. Authority stays with the server (school match, stock left,
 * guardian consent, which depends on a standing consent this client cannot see); this only catches what the
 * officer can fix immediately, plus expiry, which the server does not check.
 */
export function validateVisitDraft(draft: VisitDraft, today: string): VisitDraftIssue[] {
  const issues: VisitDraftIssue[] = [];
  if (!draft.complaint.trim()) issues.push('complaint');
  if (!draft.outcome) issues.push('outcome');

  const temperature = parseNumber(draft.temperature);
  if (temperature === 'invalid' || (temperature !== null && (temperature < TEMPERATURE_RANGE_C[0] || temperature > TEMPERATURE_RANGE_C[1]))) {
    issues.push('temperature');
  }
  const pulse = parseNumber(draft.pulse);
  if (pulse === 'invalid' || (pulse !== null && (!Number.isInteger(pulse) || pulse < PULSE_RANGE_BPM[0] || pulse > PULSE_RANGE_BPM[1]))) {
    issues.push('pulse');
  }

  if (draft.medication) {
    const quantity = parseNumber(draft.quantity);
    if (quantity === null || quantity === 'invalid' || !Number.isInteger(quantity) || quantity <= 0) issues.push('quantity');
    else if (quantity > draft.medication.quantity) issues.push('quantity_over_stock');
    if (isExpired(draft.medication, today)) issues.push('medication_expired');
  }
  return issues;
}

/** The request body. Guardian consent fields are only meaningful, and only sent, when medication is given. */
export function buildVisitPayload(draft: VisitDraft): Record<string, unknown> {
  const vitals: Record<string, number> = {};
  const temperature = parseNumber(draft.temperature);
  if (typeof temperature === 'number') vitals.temperature_c = temperature;
  const pulse = parseNumber(draft.pulse);
  if (typeof pulse === 'number') vitals.pulse_bpm = pulse;

  const payload: Record<string, unknown> = {
    student_id: draft.student.id,
    complaint: draft.complaint.trim(),
    treatment: draft.treatment.trim(),
    vitals,
    outcome: draft.outcome,
  };
  if (draft.medication) {
    payload.medication_id = draft.medication.id;
    payload.medication_quantity = Number(draft.quantity.trim());
    payload.guardian_consent_confirmed = draft.consentConfirmed;
    payload.guardian_consent_note = draft.consentNote.trim();
  }
  return payload;
}

/** Stock the officer may hand out for this child: their school's, in date, and not used up; soonest expiry first. */
export function dispensableStock(list: MedicationStockItem[], schoolId: number, today: string): MedicationStockItem[] {
  return list
    .filter((i) => i.school === schoolId && i.quantity > 0 && !isExpired(i, today))
    .sort((a, b) => (a.expiry_date ?? '9999').localeCompare(b.expiry_date ?? '9999') || a.name.localeCompare(b.name));
}

export async function recordClinicVisit(draft: VisitDraft): Promise<ClinicVisitItem> {
  const response = await apiClient.post<ClinicVisitItem>('/campus/clinic-visits/', buildVisitPayload(draft));
  return response.data;
}

export type SubmitFailure =
  /** The server refused it: nothing was saved, and `message` says why (already id-ID). */
  | { kind: 'rejected'; message: string }
  /** No answer, or a server error: the visit may or may not have been saved. Do not blindly resend. */
  | { kind: 'unknown' };

/** DRF answers a validation error as a list, a `{field: [..]}` map or `{detail}`; api.ts only surfaces the last. */
function flattenMessages(data: unknown): string[] {
  if (typeof data === 'string') return [data];
  if (Array.isArray(data)) return data.flatMap(flattenMessages);
  if (data && typeof data === 'object') return Object.values(data).flatMap(flattenMessages);
  return [];
}

/**
 * The create is not idempotent (the server cannot dedupe without keeping the plaintext notes, LIF-007), and it
 * decrements stock. A 4xx is a definite refusal; a timeout or 5xx is not, so the form tells the officer to
 * check the visit list before sending again.
 */
export function classifySubmitFailure(error: unknown): SubmitFailure {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  const status = response?.status;
  if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return { kind: 'rejected', message: flattenMessages(response?.data).join(' ') };
  }
  return { kind: 'unknown' };
}
