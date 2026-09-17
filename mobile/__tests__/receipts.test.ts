import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  fetchPaymentsForChild,
  fetchPaymentReceipt,
  getPaymentReceiptCacheKey,
} from '../src/services/payments.ts';
import { apiClient } from '../src/services/api.ts';
import { cacheGet, cacheSet, clearCachedData } from '../src/services/storage.ts';
import type { PaymentReceiptItem, PaymentReceiptDetail } from '../src/types/index.ts';

describe('Parent Payment Receipts Service (PAR-009, PAR-015)', () => {
  const mockStudentId = 42;

  const mockReceiptItems: PaymentReceiptItem[] = [
    {
      id: 101,
      foundation_id: 1,
      school: 1,
      student: mockStudentId,
      student_name: 'Ahmad Faiz',
      amount: '500000.00',
      currency: 'IDR',
      method: 'VA',
      channel: 'BCA_VA',
      reference: 'PAY-REF-101',
      status: 'SETTLED',
      fee: '2500.00',
      net: '497500.00',
      receipt_number: 'RCP/2026/0001',
      receipt_pdf_key: 'payment_receipt/101.pdf',
      receipt_download_url: 'https://storage.example.com/receipt/101.pdf',
      paid_at: '2026-09-15T10:00:00Z',
      settled_at: '2026-09-15T10:00:05Z',
      created_at: '2026-09-15T09:59:00Z',
      allocations: [
        {
          id: 201,
          payment: 101,
          invoice: 55,
          invoice_number: 'INV/2026/0055',
          amount: '500000.00',
          currency: 'IDR',
        },
      ],
    },
  ];

  beforeEach(async () => {
    await clearCachedData();
  });

  it('generates consistent student cache key', () => {
    const key = getPaymentReceiptCacheKey(mockStudentId);
    assert.strictEqual(key, 'educore_parent_payment_receipts_42');
  });

  it('fetchPaymentsForChild fetches from API and caches offline (PAR-015)', async () => {
    const originalGet = apiClient.get;
    let requestedUrl = '';

    apiClient.get = (async (url: string) => {
      requestedUrl = url;
      return {
        data: { results: mockReceiptItems },
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const result = await fetchPaymentsForChild(mockStudentId);
      assert.strictEqual(requestedUrl, `/finance/payments/?student_id=${mockStudentId}&status=SETTLED`);
      assert.strictEqual(result.fromCache, false);
      assert.strictEqual(result.data.length, 1);
      assert.strictEqual(result.data[0].receipt_number, 'RCP/2026/0001');
      assert.ok(result.lastUpdated);

      // Verify cached in storage
      const cached = await cacheGet<PaymentReceiptItem[]>(getPaymentReceiptCacheKey(mockStudentId));
      assert.ok(cached);
      assert.strictEqual(cached.value.length, 1);
      assert.strictEqual(cached.value[0].id, 101);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('fetchPaymentsForChild falls back to offline cache on network error (PAR-015)', async () => {
    // Pre-populate cache
    const cacheKey = getPaymentReceiptCacheKey(mockStudentId);
    await cacheSet(cacheKey, mockReceiptItems);

    const originalGet = apiClient.get;
    apiClient.get = (async () => {
      throw new Error('Network error: device is offline');
    }) as any;

    try {
      const result = await fetchPaymentsForChild(mockStudentId);
      assert.strictEqual(result.fromCache, true);
      assert.strictEqual(result.data.length, 1);
      assert.strictEqual(result.data[0].receipt_number, 'RCP/2026/0001');
      assert.ok(result.lastUpdated);
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('fetchPaymentsForChild throws if offline and no cache exists', async () => {
    const originalGet = apiClient.get;
    apiClient.get = (async () => {
      throw new Error('Connection refused');
    }) as any;

    try {
      await assert.rejects(
        async () => {
          await fetchPaymentsForChild(9999);
        },
        /Connection refused/
      );
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('fetchPaymentReceipt retrieves detail and download URL', async () => {
    const originalGet = apiClient.get;
    const mockDetail: PaymentReceiptDetail = {
      payment_id: 101,
      reference: 'PAY-REF-101',
      receipt_number: 'RCP/2026/0001',
      receipt_pdf_key: 'payment_receipt/101.pdf',
      download_url: 'https://storage.example.com/receipt/101.pdf?signed=abc',
      expires_at: '2026-09-17T12:00:00Z',
      amount: '500000.00',
      currency: 'IDR',
      paid_at: '2026-09-15T10:00:00Z',
      status: 'SETTLED',
    };

    apiClient.get = (async (url: string) => {
      assert.strictEqual(url, '/finance/payments/101/receipt/');
      return {
        data: mockDetail,
        status: 200,
        headers: {},
      };
    }) as any;

    try {
      const detail = await fetchPaymentReceipt(101);
      assert.strictEqual(detail.payment_id, 101);
      assert.strictEqual(detail.receipt_number, 'RCP/2026/0001');
      assert.strictEqual(detail.download_url, 'https://storage.example.com/receipt/101.pdf?signed=abc');
    } finally {
      apiClient.get = originalGet;
    }
  });

  it('clearing cached data purges receipt cache on logout (PAR-020)', async () => {
    const cacheKey = getPaymentReceiptCacheKey(mockStudentId);
    await cacheSet(cacheKey, mockReceiptItems);

    let cached = await cacheGet(cacheKey);
    assert.ok(cached);

    await clearCachedData();

    cached = await cacheGet(cacheKey);
    assert.strictEqual(cached, null);
  });
});
