import { apiClient } from './api.ts';
import type { PaymentIntentItem } from '../types/index.ts';

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
