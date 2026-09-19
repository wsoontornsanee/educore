import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { fetchChildren } from '../src/services/children.ts';
import { fetchAttendanceForChild } from '../src/services/parentAttendance.ts';
import { fetchInvoicesForChild } from '../src/services/invoices.ts';
import { createPaymentIntent, fetchPaymentIntent } from '../src/services/payments.ts';
import { apiClient } from '../src/services/api.ts';

describe('Parent data services', () => {
  it('fetchChildren calls /me/children/', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/me/children/');
      return { data: [{ student_id: 1, full_name: 'Dewi', photo_key: '', nis: '2026010', nisn: '', class_name: '', school_name: 'SMP Nusantara', financial_responsible: true }], status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchChildren();
      assert.strictEqual(result.length, 1);
      assert.strictEqual(result[0].student_id, 1);
    } finally {
      apiClient.get = original;
    }
  });

  it('fetchAttendanceForChild filters by student_id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/attendance/daily/?student_id=7');
      return { data: { results: [] }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchAttendanceForChild(7);
      assert.deepStrictEqual(result, []);
    } finally {
      apiClient.get = original;
    }
  });

  it('fetchInvoicesForChild filters by student_id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/finance/invoices/?student_id=7');
      return { data: { results: [] }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchInvoicesForChild(7);
      assert.deepStrictEqual(result, []);
    } finally {
      apiClient.get = original;
    }
  });

  it('createPaymentIntent posts invoice_ids and method', async () => {
    const original = apiClient.post;
    apiClient.post = (async (path: string, body: any) => {
      assert.strictEqual(path, '/finance/payment-intents/');
      assert.deepStrictEqual(body.invoice_ids, [5]);
      assert.strictEqual(body.method, 'QRIS');
      return { data: { id: 99, status: 'PENDING' }, status: 201, headers: {} };
    }) as any;
    try {
      const result = await createPaymentIntent([5], 'QRIS');
      assert.strictEqual(result.id, 99);
    } finally {
      apiClient.post = original;
    }
  });

  it('fetchPaymentIntent gets by id', async () => {
    const original = apiClient.get;
    apiClient.get = (async (path: string) => {
      assert.strictEqual(path, '/finance/payment-intents/99/');
      return { data: { id: 99, status: 'SETTLED' }, status: 200, headers: {} };
    }) as any;
    try {
      const result = await fetchPaymentIntent(99);
      assert.strictEqual(result.status, 'SETTLED');
    } finally {
      apiClient.get = original;
    }
  });
});
