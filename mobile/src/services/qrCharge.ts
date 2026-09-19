/**
 * Canteen QR Charge — guardian app client (spec 18 QRS-010..017, QRS-026, QRS-029).
 *
 * The student scans a cashier's QR (or a static decal), types the amount, and the guardian's
 * spending PIN confirms the debit. This module is the data layer only: API calls, the keypad and
 * amount rules, refusal-message mapping, and the local record of disputed charges. The screens
 * (ParentQrChargeScreen, the wallet dispute sheet) hold no business rules of their own.
 */
import { api } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import { generateUUID } from './offlineQueue.ts';
import { requestOtp } from './parentAuth.ts';
import { updateSpendRules } from './wallet.ts';
import type { WalletSpendRule } from '../types/index.ts';

export const PIN_LENGTH = 6;
/** Rp 999.999.999 — keeps the digit string well inside Number's exact range and the keypad readable. */
export const MAX_AMOUNT_DIGITS = 9;
/** QRS-026: the server refuses disputes older than 7 days; the app hides the action after the same window. */
export const DISPUTE_WINDOW_DAYS = 7;

export interface QrResolveResult {
  type: 'SESSION' | 'STATIC';
  session_id: number | null;
  merchant_name: string;
  terminal_name: string | null;
  payment_point_name: string | null;
  payment_point_location: string | null;
  balance: string;
  currency: string;
  max_amount: string;
  expires_at: string | null;
}

export interface QrChargeResult {
  txn_id: number;
  code: string;
  amount: string;
  balance_after: string;
  occurred_at: string;
}

export interface PinStatus {
  is_set: boolean;
  locked_until: string | null;
  requires_otp_reset: boolean;
}

export interface QrDisputeResult {
  id: number;
  pos_transaction: number;
  status: string;
  reason: string;
}

/** A refusal from the QR / PIN / dispute endpoints, normalised for the screens. */
export interface QrFailure {
  code: string;
  message: string;
  attemptsLeft?: number;
  retryAfterSeconds?: number;
}

/**
 * Refusals after which the same QR can never succeed — the student must ask the cashier for a
 * fresh code (or tap their card). Everything else leaves the entered amount on screen to retry.
 */
const DEAD_QR_CODES = new Set([
  'QR_TOKEN_INVALID',
  'QR_TOKEN_EXPIRED',
  'QR_TOKEN_USED',
  'MERCHANT_FOREIGN_TENANT',
  'QR_MODE_DISABLED_BY_MERCHANT',
  'QR_MODE_DISABLED_BY_GUARDIAN',
  'QR_MODE_REQUIRES_ITEMISED',
  'QR_DECAL_REVOKED',
  'QR_DECAL_EXPIRED',
  'PAYMENT_POINT_CLOSED',
  'WALLET_FROZEN',
  'CURRENCY_MISMATCH',
]);

const PIN_BLOCKING_CODES = new Set(['PIN_LOCKED', 'PIN_RESET_REQUIRED']);

export function isDeadQrFailure(code: string): boolean {
  return DEAD_QR_CODES.has(code);
}

export function isPinBlocking(code: string): boolean {
  return PIN_BLOCKING_CODES.has(code);
}

/** Wrong PIN or a PIN problem: stay on the confirm step and let the guardian retype. */
export function isPinFailure(code: string): boolean {
  return code.startsWith('PIN_') || code === 'OTP_INVALID';
}

export function describeQrError(err: any): QrFailure {
  const data = err?.response?.data;
  if (data && typeof data === 'object' && typeof data.error === 'string') {
    return {
      code: data.error,
      message: typeof data.message === 'string' && data.message ? data.message : data.error,
      attemptsLeft: typeof data.attempts_left === 'number' ? data.attempts_left : undefined,
      retryAfterSeconds: typeof data.retry_after_seconds === 'number' ? data.retry_after_seconds : undefined,
    };
  }
  if (err?.isNetworkError) {
    return { code: 'NETWORK', message: err.message || 'Network Error' };
  }
  return { code: 'UNKNOWN', message: err?.message || 'Unknown error' };
}

/**
 * The QR encodes the signed token and nothing else (QRS-006). Anything with whitespace, empty, or
 * longer than the server's 512-char limit is some other QR (a URL with a space, a wifi code…).
 */
export function normalizeScannedToken(raw: string | null | undefined): string | null {
  const token = (raw ?? '').trim();
  if (!token || token.length > 512 || /\s/.test(token)) return null;
  return token;
}

/** Apply one keypad press to the digit string. `DEL` drops the last digit; leading zeros never stick. */
export function applyKeypadKey(digits: string, key: string): string {
  if (key === 'DEL') return digits.slice(0, -1);
  if (!/^\d{1,2}$/.test(key)) return digits;
  const next = (digits + key).replace(/^0+/, '');
  return next.length > MAX_AMOUNT_DIGITS ? digits : next;
}

export type AmountState = 'EMPTY' | 'OK' | 'ABOVE_CAP' | 'INSUFFICIENT';

export interface AmountEvaluation {
  amount: number;
  state: AmountState;
}

/** Live keypad feedback (QRS-011): the cap and the balance are checked before the guardian PIN is asked. */
export function evaluateAmount(
  digits: string,
  balance: string | number,
  maxAmount: string | number,
): AmountEvaluation {
  const amount = digits ? parseInt(digits, 10) : 0;
  if (amount <= 0) return { amount: 0, state: 'EMPTY' };
  if (amount > Number(maxAmount)) return { amount, state: 'ABOVE_CAP' };
  if (amount > Number(balance)) return { amount, state: 'INSUFFICIENT' };
  return { amount, state: 'OK' };
}

export async function resolveQr(studentId: number, token: string): Promise<QrResolveResult> {
  const res = await api.post<QrResolveResult>('/wallet/qr/resolve/', { student_id: studentId, token });
  return res.data;
}

/**
 * The debit. `idempotencyKey` must be generated once per keypad confirmation and reused if the
 * request is retried after a network drop, so a lost response can never charge twice.
 */
export async function chargeQr(params: {
  studentId: number;
  token: string;
  amount: number;
  pin: string;
  idempotencyKey: string;
}): Promise<QrChargeResult> {
  const res = await api.post<QrChargeResult>('/wallet/qr/charge/', {
    student_id: params.studentId,
    token: params.token,
    amount: params.amount.toFixed(2),
    pin: params.pin,
    idempotency_key: params.idempotencyKey,
  });
  return res.data;
}

export function newIdempotencyKey(): string {
  return `qr-${generateUUID()}`;
}

export function isValidPinFormat(pin: string): boolean {
  return /^\d{6}$/.test(pin);
}

export async function fetchPinStatus(): Promise<PinStatus> {
  const res = await api.get<PinStatus>('/me/pin/');
  return res.data;
}

export async function createPin(pin: string): Promise<PinStatus> {
  const res = await api.post<PinStatus>('/me/pin/', { pin });
  return res.data;
}

export type NewPinProblem = 'PIN_INVALID_FORMAT' | 'PIN_MISMATCH';

/** Client-side check of a new-PIN entry before it is sent; the server still enforces strength (PIN_TOO_WEAK). */
export function checkNewPinEntry(newPin: string, repeat: string): NewPinProblem | null {
  if (!isValidPinFormat(newPin)) return 'PIN_INVALID_FORMAT';
  if (newPin !== repeat) return 'PIN_MISMATCH';
  return null;
}

/** PUT /me/pin/: the current PIN counts against the attempt limit like any other entry. */
export async function changePin(currentPin: string, newPin: string): Promise<PinStatus> {
  const res = await api.put<PinStatus>('/me/pin/', { current_pin: currentPin, new_pin: newPin });
  return res.data;
}

/** Forgotten or locked-out PIN: a fresh OTP goes to the account's own phone; returns the challenge to verify. */
export async function requestPinResetOtp(phoneE164: string): Promise<number> {
  return (await requestOtp(phoneE164)).challenge_id;
}

export async function resetPinWithOtp(challengeId: number, code: string, newPin: string): Promise<PinStatus> {
  const res = await api.post<PinStatus>('/me/pin/reset/', {
    challenge_id: challengeId,
    code,
    new_pin: newPin,
  });
  return res.data;
}

/**
 * QRS-017: the guardian's QR switch. The spend-rule PUT replaces the whole rule, so this resends
 * the saved daily limit, windows and itemised blocks unchanged — flipping the switch must never
 * reset them. Built from the *saved* rule, not the wallet screen's unsaved form state.
 */
export async function setQrChargeEnabled(
  studentId: number,
  saved: WalletSpendRule | null,
  enabled: boolean,
): Promise<WalletSpendRule> {
  return updateSpendRules(studentId, {
    daily_limit: saved?.daily_limit ?? null,
    blocked_categories: saved?.blocked_categories ?? [],
    blocked_products: saved?.blocked_products ?? [],
    allowed_window_start: saved?.allowed_window_start ?? null,
    allowed_window_end: saved?.allowed_window_end ?? null,
    qr_charge_enabled: enabled,
  });
}

// ─── Disputes (QRS-026) ──────────────────────────────────────────────────────

const DISPUTED_KEY_PREFIX = 'educore_parent_qr_disputed';

function disputedKey(studentId: number): string {
  return `${DISPUTED_KEY_PREFIX}:${studentId}`;
}

/** Wallet-transaction ids this device has already disputed (or the server said were). */
export async function getDisputedTransactionIds(studentId: number): Promise<number[]> {
  const cached = await cacheGet<number[]>(disputedKey(studentId));
  return cached?.value ?? [];
}

export async function markTransactionDisputed(studentId: number, walletTransactionId: number): Promise<void> {
  const ids = await getDisputedTransactionIds(studentId);
  if (!ids.includes(walletTransactionId)) {
    await cacheSet(disputedKey(studentId), [...ids, walletTransactionId]);
  }
}

/** A dispute can be opened on a self-entered purchase inside the server's 7-day window. */
export function canDisputeTransaction(
  tx: { type: string; entry_mode?: string; status: string; occurred_at: string },
  now: Date = new Date(),
): boolean {
  if (tx.type !== 'PURCHASE' || tx.entry_mode !== 'SELF_ENTERED' || tx.status !== 'COMPLETED') return false;
  const occurred = new Date(tx.occurred_at).getTime();
  if (Number.isNaN(occurred)) return false;
  return now.getTime() - occurred <= DISPUTE_WINDOW_DAYS * 24 * 60 * 60 * 1000;
}

/**
 * Opens the dispute. `DISPUTE_EXISTS` means it was already opened (from another device, or before a
 * cache clear) — recorded the same way, so the row stops offering the action.
 */
export async function openQrDispute(
  studentId: number,
  walletTransactionId: number,
  reason: string,
): Promise<QrDisputeResult | null> {
  try {
    const res = await api.post<QrDisputeResult>(
      `/wallet/transactions/${walletTransactionId}/dispute/`,
      { reason: reason.trim() },
    );
    await markTransactionDisputed(studentId, walletTransactionId);
    return res.data;
  } catch (err) {
    if (describeQrError(err).code === 'DISPUTE_EXISTS') {
      await markTransactionDisputed(studentId, walletTransactionId);
      return null;
    }
    throw err;
  }
}
