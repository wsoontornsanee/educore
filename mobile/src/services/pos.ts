/**
 * High-level POS Service for Tablet POS Kiosk (spec/07 §3-§7, spec/12).
 *
 * Handles terminal sessions, incremental delta sync, client-side spend rule
 * validations (limits, blocked categories, time windows, floor limits),
 * rapid online/offline checkout (≤3s WAL-018), and voids.
 */
import { apiClient } from './api.ts';
import { enqueuePosTransaction, generateUUID } from './posOfflineQueue.ts';
import type {
  POSCartItem,
  POSProduct,
  POSReceipt,
  POSSessionData,
  POSSpendRuleCheckResult,
  POSStudent,
} from '../types/index.ts';

// Local storage / memory cache of active POS session
let cachedSession: POSSessionData | null = null;

export const OFFLINE_FLOOR_LIMIT = -50000.0; // WAL-016: Floor limit Rp 50,000

export function getCachedSession(): POSSessionData | null {
  return cachedSession;
}

export function setCachedSession(session: POSSessionData): void {
  cachedSession = session;
}

export function clearCachedSession(): void {
  cachedSession = null;
}

/**
 * Fetch and bootstrap a POS terminal session (spec/07 §8 POST /pos/sessions).
 */
export async function fetchPosSession(terminalId: number): Promise<POSSessionData> {
  const response = await apiClient.post<any>('/pos/sessions/', {
    terminal_id: terminalId,
  });

  const data = response.data;
  const session: POSSessionData = {
    terminal_id: data.terminal?.id || terminalId,
    terminal_name: data.terminal?.name || `Terminal #${terminalId}`,
    merchant_id: data.merchant?.id || 0,
    merchant_name: data.merchant?.name || 'Kantin',
    school_id: data.merchant?.school_id || 0,
    catalog: data.catalog || [],
    roster: data.roster || [],
    sync_cursor: data.cursor,
  };

  cachedSession = session;
  return session;
}

/**
 * Perform incremental sync of roster, balances, and catalog deltas (GET /pos/sync).
 */
export async function syncPosDeltas(terminalId: number, cursor?: string): Promise<{
  rosterDeltas: POSStudent[];
  catalogDeltas: POSProduct[];
  nextCursor?: string;
}> {
  // apiClient takes a finished path: it has no `params` option, so the query is built here.
  const query = `terminal_id=${terminalId}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;

  const response = await apiClient.get<any>(`/pos/sync/?${query}`);
  const data = response.data;

  if (cachedSession) {
    if (data.roster && Array.isArray(data.roster)) {
      const studentMap = new Map(cachedSession.roster.map((s) => [s.id, s]));
      for (const updated of data.roster) {
        studentMap.set(updated.id, { ...studentMap.get(updated.id), ...updated });
      }
      cachedSession.roster = Array.from(studentMap.values());
    }

    if (data.catalog && Array.isArray(data.catalog)) {
      const productMap = new Map(cachedSession.catalog.map((p) => [p.id, p]));
      for (const updated of data.catalog) {
        productMap.set(updated.id, { ...productMap.get(updated.id), ...updated });
      }
      cachedSession.catalog = Array.from(productMap.values());
    }

    if (data.next_cursor) {
      cachedSession.sync_cursor = data.next_cursor;
    }
  }

  return {
    rosterDeltas: data.roster || [],
    catalogDeltas: data.catalog || [],
    nextCursor: data.next_cursor,
  };
}

/**
 * Validate student spend rules (WAL-009 to WAL-013, WAL-016).
 * Returns clear rejection reason if rejected.
 */
export function checkStudentSpendRules(
  student: POSStudent,
  cartItems: POSCartItem[],
  currentTime: Date = new Date()
): POSSpendRuleCheckResult {
  if (!cartItems || cartItems.length === 0) {
    return { allowed: false, reason: 'Keranjang belanja kosong.' };
  }

  const subtotal = cartItems.reduce((sum, item) => sum + item.unit_price * item.qty, 0);
  const currentBalance = Number(student.balance || 0);

  // 1. Time-of-day purchase window check (WAL-011)
  if (student.allowed_window_start && student.allowed_window_end) {
    const hours = currentTime.getHours().toString().padStart(2, '0');
    const minutes = currentTime.getMinutes().toString().padStart(2, '0');
    const nowTimeStr = `${hours}:${minutes}`;

    if (
      nowTimeStr < student.allowed_window_start ||
      nowTimeStr > student.allowed_window_end
    ) {
      return {
        allowed: false,
        reason: `TIME_WINDOW_RESTRICTED: Di luar jam belanja yang diizinkan (${student.allowed_window_start} - ${student.allowed_window_end}).`,
      };
    }
  }

  // 2. Blocked product categories check (WAL-010)
  if (student.blocked_categories && student.blocked_categories.length > 0) {
    const blockedSet = new Set(student.blocked_categories.map((c) => c.toUpperCase()));
    for (const item of cartItems) {
      const cat = (item.product.category || '').toUpperCase();
      if (blockedSet.has(cat)) {
        return {
          allowed: false,
          reason: `BLOCKED_CATEGORY: Kategori "${item.product.category}" dilarang untuk siswa ini.`,
        };
      }
    }
  }

  // 3. Daily spending limit check (WAL-009)
  if (student.daily_limit !== null && student.daily_limit !== undefined) {
    const dailyLimit = Number(student.daily_limit);
    const spentToday = Number(student.spent_today || 0);
    if (spentToday + subtotal > dailyLimit) {
      return {
        allowed: false,
        reason: `LIMIT_EXCEEDED: Batas belanja harian Rp ${dailyLimit.toLocaleString('id-ID')} terlampaui (sudah belanja Rp ${spentToday.toLocaleString('id-ID')}).`,
      };
    }
  }

  // 4. Floor limit check for offline & normal balance (WAL-016)
  if (currentBalance - subtotal < OFFLINE_FLOOR_LIMIT) {
    return {
      allowed: false,
      reason: `FLOOR_LIMIT_EXCEEDED: Saldo tidak mencukupi dan melebihi batas saldo minimum offline (Rp 50.000).`,
    };
  }

  return { allowed: true };
}

/**
 * Execute POS checkout rapidly (≤3s WAL-018) with online-first and offline fallback (WAL-015, WAL-016).
 */
export async function checkoutPOSTransaction(options: {
  terminalId: number;
  student: POSStudent;
  cartItems: POSCartItem[];
  forceOffline?: boolean;
  merchantName?: string;
  terminalName?: string;
  currentTime?: Date;
  /**
   * The offline QR token the student paid with (QRS-022). A token-paid sale is always queued rather
   * than posted live: the batch is where the server ties the sale to the token's single-use nonce.
   */
  qrToken?: string;
}): Promise<POSReceipt> {
  const { terminalId, student, cartItems, currentTime, qrToken } = options;
  const forceOffline = options.forceOffline || !!qrToken;

  // Validate spend rules first
  const ruleCheck = checkStudentSpendRules(student, cartItems, currentTime || new Date());
  if (!ruleCheck.allowed) {
    throw new Error(ruleCheck.reason || 'Transaksi ditolak oleh aturan belanja.');
  }

  const client_transaction_id = `pos-${generateUUID()}`;
  const occurred_at = new Date().toISOString();
  const subtotal = cartItems.reduce((sum, item) => sum + item.unit_price * item.qty, 0);
  const balanceBefore = Number(student.balance || 0);
  const balanceAfter = balanceBefore - subtotal;

  const itemsPayload = cartItems.map((item) => ({
    sku: item.product.sku,
    name: item.product.name,
    qty: item.qty,
    unit_price: String(item.unit_price),
    category: item.product.category,
    nutrition: item.product.nutrition,
    allergens: item.product.allergens,
  }));

  let offlineCreated = false;
  let serverTxId = client_transaction_id;

  if (!forceOffline) {
    try {
      const response = await apiClient.post<any>('/pos/transactions/', {
        terminal_id: terminalId,
        student_id: student.id,
        items: itemsPayload,
        client_transaction_id,
        occurred_at,
      });
      if (response.data && response.data.id) {
        serverTxId = String(response.data.id);
      }
    } catch (networkError: any) {
      // If error is 400 with spend rule rejection from backend, throw immediately
      if (networkError?.response?.status === 400 && networkError?.response?.data?.error) {
        throw new Error(networkError.response.data.error);
      }
      // Otherwise fall back to offline queue
      offlineCreated = true;
    }
  } else {
    offlineCreated = true;
  }

  if (offlineCreated) {
    await enqueuePosTransaction({
      terminal_id: terminalId,
      student_id: student.id,
      student_name: student.full_name,
      items: itemsPayload,
      subtotal,
      total: subtotal,
      occurred_at,
      client_transaction_id,
      qr_token: qrToken,
    });
  }

  // Update local student cached balance and spent_today
  student.balance = balanceAfter;
  student.spent_today = Number(student.spent_today || 0) + subtotal;
  if (cachedSession) {
    const idx = cachedSession.roster.findIndex((s) => s.id === student.id);
    if (idx >= 0) {
      cachedSession.roster[idx] = { ...student };
    }
  }

  const receipt: POSReceipt = {
    transaction_id: serverTxId,
    client_transaction_id,
    student_name: student.full_name,
    student_nis: student.nis || student.nisn,
    items: cartItems.map((i) => ({
      sku: i.product.sku,
      name: i.product.name,
      qty: i.qty,
      unit_price: i.unit_price,
      total: i.unit_price * i.qty,
    })),
    subtotal,
    total: subtotal,
    balance_before: balanceBefore,
    balance_after: balanceAfter,
    occurred_at,
    offline_created: offlineCreated,
    merchant_name: options.merchantName || cachedSession?.merchant_name,
    terminal_name: options.terminalName || cachedSession?.terminal_name,
  };

  return receipt;
}

/**
 * Void a completed POS transaction within the allowable window (WAL-025).
 */
export async function voidPOSTransaction(
  transactionId: string | number,
  reason: string
): Promise<{ success: boolean; message?: string }> {
  const response = await apiClient.post<any>(
    `/pos/transactions/${transactionId}/void/`,
    { reason }
  );
  return { success: true, message: response.data?.status || 'VOIDED' };
}
