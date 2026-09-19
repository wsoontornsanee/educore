/**
 * Student Digital Canteen Wallet Service (WAL-001, WAL-005, WAL-008, WAL-009..011, PAR-006..008, PAR-010, PAR-015).
 * 
 * Provides API client calls, offline caching via Storage, and polling utilities
 * for parent wallet management.
 */
import { api } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type {
  WalletAutoTopupConfig,
  WalletData,
  WalletSpendRule,
  WalletTopupIntentItem,
  WalletTransactionItem,
} from '../types/index.ts';

export const TOPUP_PRESETS = [20000, 50000, 100000, 200000] as const;

export const VA_BANKS = ['BCA', 'Mandiri', 'BNI', 'BRI'] as const;
export type VABankCode = (typeof VA_BANKS)[number];

export const CATEGORY_BLOCK_CHOICES = [
  'Minuman Manis',
  'Camilan',
  'Makanan Cepat Saji',
] as const;

export const DAILY_LIMIT_INCREMENT = 5000;

export interface WalletFetchResult {
  wallet: WalletData;
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface TransactionsFetchResult {
  transactions: WalletTransactionItem[];
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface SpendRulesFetchResult {
  rules: WalletSpendRule | null;
  isOfflineCached: boolean;
}

export interface AutoTopupFetchResult {
  config: WalletAutoTopupConfig | null;
  isOfflineCached: boolean;
}

const CACHE_PREFIX = 'educore_parent_wallet';

function toAmount(amount: number | string | null | undefined): number {
  if (amount === null || amount === undefined || amount === '') return 0;
  const num = typeof amount === 'string' ? parseFloat(amount) : amount;
  return isNaN(num) ? 0 : num;
}

/**
 * Format a number or numeric string as Indonesian Rupiah currency. A negative amount reads "-Rp 18.000", the
 * sign before the currency, never "Rp -18.000".
 */
export function formatRupiah(amount: number | string | null | undefined): string {
  const num = Math.round(toAmount(amount));
  const digits = Math.abs(num).toLocaleString('id-ID');
  return num < 0 ? `-Rp ${digits}` : `Rp ${digits}`;
}

/**
 * The ledger stores money leaving a wallet (a purchase) as a negative amount and money entering as a positive
 * one, so the sign is read from the amount itself: "+Rp 50.000" / "-Rp 18.000". Adding a sign by transaction
 * type on top of that is what produced "-Rp -18.000".
 */
export function formatSignedRupiah(amount: number | string | null | undefined): string {
  const num = Math.round(toAmount(amount));
  return num > 0 ? `+${formatRupiah(num)}` : formatRupiah(num);
}

/** True when the amount adds to the balance (drawn green in the history), false for spends and zero. */
export function isCreditAmount(amount: number | string | null | undefined): boolean {
  return Math.round(toAmount(amount)) > 0;
}

/**
 * Fetch wallet balance and status for a student.
 */
export async function fetchWallet(studentId: number): Promise<WalletFetchResult> {
  const cacheKey = `${CACHE_PREFIX}:${studentId}`;
  try {
    const res = await api.get<WalletData>(`/wallets/${studentId}/`);
    const nowIso = new Date().toISOString();
    await cacheSet(cacheKey, res.data);
    return {
      wallet: res.data,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<WalletData>(cacheKey);
    if (cached && cached.value) {
      return {
        wallet: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

/**
 * Fetch itemized transaction history for a student's wallet.
 */
export async function fetchWalletTransactions(
  studentId: number
): Promise<TransactionsFetchResult> {
  const cacheKey = `${CACHE_PREFIX}:tx:${studentId}`;
  try {
    const res = await api.get<any>(`/wallets/${studentId}/transactions/`);
    // DRF may return array or paginated object { results: [...] }
    const items: WalletTransactionItem[] = Array.isArray(res.data)
      ? res.data
      : Array.isArray(res.data?.results)
      ? res.data.results
      : [];
    const nowIso = new Date().toISOString();
    await cacheSet(cacheKey, items);
    return {
      transactions: items,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (error) {
    const cached = await cacheGet<WalletTransactionItem[]>(cacheKey);
    if (cached && cached.value) {
      return {
        transactions: cached.value,
        isOfflineCached: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

/**
 * Fetch spend control rules for a student.
 */
export async function fetchSpendRules(studentId: number): Promise<SpendRulesFetchResult> {
  const cacheKey = `${CACHE_PREFIX}:rules:${studentId}`;
  try {
    const res = await api.get<WalletSpendRule>(`/wallets/${studentId}/rules/`);
    await cacheSet(cacheKey, res.data);
    return {
      rules: res.data,
      isOfflineCached: false,
    };
  } catch (error) {
    const cached = await cacheGet<WalletSpendRule>(cacheKey);
    if (cached && cached.value) {
      return {
        rules: cached.value,
        isOfflineCached: true,
      };
    }
    throw error;
  }
}

/**
 * Update spend rules (daily limit, blocked categories, time windows).
 */
export async function updateSpendRules(
  studentId: number,
  payload: {
    daily_limit: string | null;
    blocked_categories: string[];
    // PUT replaces the whole rule server-side: anything omitted here is reset, so callers that
    // only edit part of the rule must resend the rest (see setQrChargeEnabled).
    blocked_products?: number[];
    allowed_window_start?: string | null;
    allowed_window_end?: string | null;
    qr_charge_enabled?: boolean;
  }
): Promise<WalletSpendRule> {
  const cacheKey = `${CACHE_PREFIX}:rules:${studentId}`;
  const res = await api.put<WalletSpendRule>(`/wallets/${studentId}/rules/`, payload);
  await cacheSet(cacheKey, res.data);
  return res.data;
}

/**
 * Fetch auto-topup configuration for a student's wallet.
 */
export async function fetchAutoTopupConfig(
  studentId: number
): Promise<AutoTopupFetchResult> {
  const cacheKey = `${CACHE_PREFIX}:autotopup:${studentId}`;
  try {
    const res = await api.get<WalletAutoTopupConfig>(
      `/wallets/${studentId}/auto-topup-config/`
    );
    await cacheSet(cacheKey, res.data);
    return {
      config: res.data,
      isOfflineCached: false,
    };
  } catch (error) {
    const cached = await cacheGet<WalletAutoTopupConfig>(cacheKey);
    if (cached && cached.value) {
      return {
        config: cached.value,
        isOfflineCached: true,
      };
    }
    throw error;
  }
}

/**
 * Update auto-topup configuration.
 */
export async function updateAutoTopupConfig(
  studentId: number,
  payload: {
    is_active: boolean;
    threshold_amount: string;
    topup_amount: string;
    method?: 'VA' | 'QRIS';
    bank?: string;
  }
): Promise<WalletAutoTopupConfig> {
  const cacheKey = `${CACHE_PREFIX}:autotopup:${studentId}`;
  const res = await api.put<WalletAutoTopupConfig>(
    `/wallets/${studentId}/auto-topup-config/`,
    payload
  );
  await cacheSet(cacheKey, res.data);
  return res.data;
}

/**
 * Create a new top-up payment intent (VA or QRIS).
 */
export async function createTopupIntent(
  studentId: number,
  payload: {
    method: 'VA' | 'QRIS';
    amount: string;
    bank?: string;
    provider?: string;
  }
): Promise<WalletTopupIntentItem> {
  const res = await api.post<WalletTopupIntentItem>(
    `/wallets/${studentId}/topup-intents/`,
    payload
  );
  return res.data;
}

/**
 * Fetch a specific top-up intent detail by ID (for settlement polling).
 */
export async function fetchTopupIntent(
  studentId: number,
  intentId: number
): Promise<WalletTopupIntentItem> {
  const res = await api.get<WalletTopupIntentItem>(
    `/wallets/${studentId}/topup-intents/${intentId}/`
  );
  return res.data;
}

/**
 * Poll topup intent status every intervalMs until settled, failed, or maxAttempts reached.
 */
export async function pollTopupIntent(
  studentId: number,
  intentId: number,
  options: {
    intervalMs?: number;
    maxAttempts?: number;
    onUpdate?: (intent: WalletTopupIntentItem) => void;
  } = {}
): Promise<WalletTopupIntentItem> {
  const intervalMs = options.intervalMs ?? 3000;
  const maxAttempts = options.maxAttempts ?? 40; // Default ~2 minutes

  let attempts = 0;
  while (attempts < maxAttempts) {
    attempts++;
    const intent = await fetchTopupIntent(studentId, intentId);
    if (options.onUpdate) {
      options.onUpdate(intent);
    }
    if (intent.status === 'SETTLED' || intent.status === 'FAILED' || intent.status === 'EXPIRED') {
      return intent;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  // Return last fetched state if timeout
  return fetchTopupIntent(studentId, intentId);
}
