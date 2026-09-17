import { apiClient } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type { PaymentIntentItem, PaymentReceiptDetail, PaymentReceiptItem } from '../types/index.ts';

export async function createPaymentIntent(
  invoiceIds: number[],
  method: 'VA' | 'QRIS',
  bank?: string
): Promise<PaymentIntentItem> {
  const response = await apiClient.post<PaymentIntentItem>('/finance/payment-intents/', {
    invoice_ids: invoiceIds,
    method,
    ...(bank ? { bank } : {}),
  });
  return response.data;
}

export async function fetchPaymentIntent(id: number): Promise<PaymentIntentItem> {
  const response = await apiClient.get<PaymentIntentItem>(`/finance/payment-intents/${id}/`);
  return response.data;
}

export function getPaymentReceiptCacheKey(studentId: number): string {
  return `educore_parent_payment_receipts_${studentId}`;
}

export async function fetchPaymentsForChild(
  studentId: number,
  options?: { forceRefresh?: boolean }
): Promise<{ data: PaymentReceiptItem[]; fromCache: boolean; lastUpdated: string | null }> {
  const cacheKey = getPaymentReceiptCacheKey(studentId);

  try {
    const response = await apiClient.get<{ results: PaymentReceiptItem[] } | PaymentReceiptItem[]>(
      `/finance/payments/?student_id=${studentId}&status=SETTLED`
    );
    const raw = response.data as any;
    const items: PaymentReceiptItem[] = Array.isArray(raw) ? raw : (raw?.results ?? []);
    await cacheSet(cacheKey, items);
    return {
      data: items,
      fromCache: false,
      lastUpdated: new Date().toISOString(),
    };
  } catch (error) {
    const cached = await cacheGet<PaymentReceiptItem[]>(cacheKey);
    if (cached) {
      return {
        data: cached.value,
        fromCache: true,
        lastUpdated: cached.cachedAt,
      };
    }
    throw error;
  }
}

export async function fetchPaymentReceipt(paymentId: number): Promise<PaymentReceiptDetail> {
  const response = await apiClient.get<PaymentReceiptDetail>(`/finance/payments/${paymentId}/receipt/`);
  return response.data;
}

