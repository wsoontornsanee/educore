/**
 * The POS kiosk's one place that knows the server's field names (apps/wallet/services.py: pos_session,
 * pos_sync, _roster_payload, _product_dict, _rules_payload). Everything past this file uses the UI types
 * (POSStudent, POSProduct). Pure functions, no I/O.
 *
 * Contract fixtures captured from the real server live in __tests__/fixtures; the backend test
 * apps/wallet/tests/test_pos_mobile_contract.py fails if the server's field names drift from them.
 */
import type { POSProduct, POSSessionData, POSStudent } from '../types/index.ts';

export interface ServerRosterEntry {
  student_id: number;
  name?: string;
  photo_key?: string;
  wallet_balance?: string;
  daily_limit?: string | null;
  wallet_status?: string;
}

export interface ServerProduct {
  sku: string;
  name: string;
  price: string;
  category: string;
  is_active?: boolean;
}

export interface ServerRule {
  student_id: number;
  daily_limit?: string | null;
  blocked_categories?: string[];
  blocked_products?: string[];
  allowed_window_start?: string | null;
  allowed_window_end?: string | null;
}

/** "09:30:00" (Django's time isoformat) to "09:30", the shape the kiosk's window check compares. */
export function toClockMinutes(time: string | null | undefined): string | null {
  return time ? time.slice(0, 5) : null;
}

export function adaptRosterEntry(raw: ServerRosterEntry): POSStudent {
  return {
    id: raw.student_id,
    full_name: raw.name ?? '',
    // The server roster carries neither NIS/NISN nor card UIDs (a backend decision, see Notion), so lookup by
    // those cannot work yet; the operator picks from the roster list.
    nis: null,
    nisn: null,
    card_uid: null,
    photo_url: null,
    balance: raw.wallet_balance ?? '0',
    daily_limit: raw.daily_limit ?? null,
    wallet_status: raw.wallet_status,
  };
}

export function adaptProduct(raw: ServerProduct): POSProduct {
  return { sku: raw.sku, name: raw.name, price: raw.price, category: raw.category, is_active: raw.is_active };
}

/**
 * Spend-rule fields for a student. Only fields the server sent are returned, so merging a rule delta over an
 * existing student never blanks something the delta did not mention. The rule's daily limit wins over the
 * wallet's: check_spend_allowed enforces the rule.
 */
export function adaptRule(rule: ServerRule): Partial<POSStudent> {
  const fields: Partial<POSStudent> = {};
  if (rule.daily_limit !== undefined) fields.daily_limit = rule.daily_limit;
  if (rule.blocked_categories !== undefined) fields.blocked_categories = rule.blocked_categories;
  if (rule.blocked_products !== undefined) fields.blocked_products = rule.blocked_products;
  if (rule.allowed_window_start !== undefined) fields.allowed_window_start = toClockMinutes(rule.allowed_window_start);
  if (rule.allowed_window_end !== undefined) fields.allowed_window_end = toClockMinutes(rule.allowed_window_end);
  return fields;
}

/** Roster entries with their rules applied. A rule for a student not in the roster is ignored. */
export function applyRules(students: POSStudent[], rules: ServerRule[]): POSStudent[] {
  const byStudent = new Map(rules.map((r) => [r.student_id, adaptRule(r)]));
  return students.map((s) => ({ ...s, ...byStudent.get(s.id) }));
}

/** POST /pos/sessions/ response to the kiosk's session. The server sends no terminal or merchant object. */
export function adaptSession(data: any, terminalId: number): POSSessionData {
  return {
    terminal_id: data?.terminal?.id ?? terminalId,
    terminal_name: data?.terminal?.name ?? `Terminal #${terminalId}`,
    merchant_id: data?.merchant?.id ?? 0,
    merchant_name: data?.merchant?.name ?? 'Kantin',
    school_id: data?.merchant?.school_id ?? 0,
    catalog: ((data?.catalog ?? []) as ServerProduct[]).map(adaptProduct),
    roster: applyRules(((data?.roster ?? []) as ServerRosterEntry[]).map(adaptRosterEntry), data?.rules ?? []),
    sync_cursor: data?.cursor,
  };
}

export interface AdaptedDeltas {
  roster: POSStudent[];
  catalog: POSProduct[];
  rules: ServerRule[];
  nextCursor?: string;
}

/** GET /pos/sync/ response: roster_delta, catalog_delta, rules_delta, next_cursor. */
export function adaptDeltas(data: any): AdaptedDeltas {
  return {
    roster: ((data?.roster_delta ?? []) as ServerRosterEntry[]).map(adaptRosterEntry),
    catalog: ((data?.catalog_delta ?? []) as ServerProduct[]).map(adaptProduct),
    rules: (data?.rules_delta ?? []) as ServerRule[],
    nextCursor: data?.next_cursor,
  };
}
