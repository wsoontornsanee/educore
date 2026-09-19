/**
 * Mobile POS Offline Queue Unit Tests (spec/07 §5 WAL-015, WAL-016).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPosBatchPayload,
  clearAllPosForTesting,
  enqueuePosTransaction,
  getPendingPosCount,
  getPendingPosTransactions,
  syncPendingPosTransactions,
} from '../src/services/posOfflineQueue.ts';
import { apiClient } from '../src/services/api.ts';

describe('POS Offline Queue', () => {
  beforeEach(async () => {
    await clearAllPosForTesting();
  });

  it('enqueues POS transaction with unique UUID and client transaction ID', async () => {
    const items = [
      { sku: 'NASI-01', name: 'Nasi Goreng', qty: 1, unit_price: '15000.00' },
      { sku: 'TEH-01', name: 'Es Teh Manis', qty: 2, unit_price: '5000.00' },
    ];

    const item = await enqueuePosTransaction({
      terminal_id: 10,
      student_id: 201,
      student_name: 'Budi Santoso',
      items,
      subtotal: 25000,
      total: 25000,
    });

    assert.ok(item.id);
    assert.ok(item.client_transaction_id.startsWith('pos-offline-'));
    assert.strictEqual(item.terminal_id, 10);
    assert.strictEqual(item.student_id, 201);
    assert.strictEqual(item.status, 'PENDING');
    assert.strictEqual(item.items.length, 2);

    const pending = await getPendingPosTransactions();
    assert.strictEqual(pending.length, 1);
    assert.strictEqual(pending[0].subtotal, 25000);

    const count = await getPendingPosCount();
    assert.strictEqual(count, 1);
  });

  it('syncs pending transactions in FIFO order to /pos/transactions/batch/', async () => {
    await enqueuePosTransaction({
      terminal_id: 5,
      student_id: 101,
      student_name: 'Siswa A',
      items: [{ sku: 'SNK-01', name: 'Roti', qty: 1, unit_price: '7000.00' }],
      subtotal: 7000,
      total: 7000,
      client_transaction_id: 'tx-offline-1',
    });

    await enqueuePosTransaction({
      terminal_id: 5,
      student_id: 102,
      student_name: 'Siswa B',
      items: [{ sku: 'SNK-02', name: 'Susu', qty: 1, unit_price: '6000.00' }],
      subtotal: 6000,
      total: 6000,
      client_transaction_id: 'tx-offline-2',
    });

    let sentPayload: any = null;
    const originalPost = apiClient.post;
    (apiClient as any).post = async (url: string, data: any) => {
      if (url === '/pos/transactions/batch/') {
        sentPayload = data;
        return {
          data: {
            results: [
              { client_transaction_id: 'tx-offline-1', status: 'COMPLETED' },
              { client_transaction_id: 'tx-offline-2', status: 'RECONCILE_REQUIRED' },
            ],
          },
        };
      }
      return originalPost(url, data);
    };

    try {
      const syncResult = await syncPendingPosTransactions(5);
      assert.strictEqual(syncResult.succeeded, 2);
      assert.strictEqual(syncResult.failed, 0);

      assert.ok(sentPayload);
      assert.strictEqual(sentPayload.terminal_id, 5);
      assert.strictEqual(sentPayload.transactions.length, 2);
      assert.strictEqual(sentPayload.transactions[0].client_transaction_id, 'tx-offline-1');
      assert.strictEqual(sentPayload.transactions[1].client_transaction_id, 'tx-offline-2');

      const remaining = await getPendingPosCount();
      assert.strictEqual(remaining, 0);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('marks items as FAILED on network error without losing records', async () => {
    await enqueuePosTransaction({
      terminal_id: 7,
      student_id: 103,
      student_name: 'Siswa C',
      items: [{ sku: 'SNK-03', name: 'Biskuit', qty: 1, unit_price: '5000.00' }],
      subtotal: 5000,
      total: 5000,
    });

    const originalPost = apiClient.post;
    (apiClient as any).post = async () => {
      throw new Error('Connection refused');
    };

    try {
      const result = await syncPendingPosTransactions(7);
      assert.strictEqual(result.succeeded, 0);
      assert.strictEqual(result.failed, 1);
      assert.ok(result.errors[0].includes('Connection refused'));

      const pending = await getPendingPosTransactions();
      assert.strictEqual(pending.length, 1);
      assert.strictEqual(pending[0].status, 'FAILED');
      assert.strictEqual(pending[0].attempts, 1);
      assert.ok(pending[0].last_error?.includes('Connection refused'));
    } finally {
      (apiClient as any).post = originalPost;
    }
  });
  it('keeps a sale the server refused in the queue as FAILED, and never marks it synced', async () => {
    const items = [{ sku: 'NASI-01', name: 'Nasi Goreng', qty: 1, unit_price: '15000.00' }];
    await enqueuePosTransaction({ terminal_id: 9, student_id: 301, items, subtotal: 15000, total: 15000, client_transaction_id: 'ok-1' });
    await enqueuePosTransaction({
      terminal_id: 9, student_id: 302, items, subtotal: 15000, total: 15000, client_transaction_id: 'bad-1', qr_token: 'p.s',
    });

    const originalPost = apiClient.post;
    (apiClient as any).post = async () => ({
      data: {
        results: [
          { client_transaction_id: 'ok-1', status: 'COMPLETED', reconciled: true },
          { client_transaction_id: 'bad-1', status: 'QR_TOKEN_EXPIRED' },
        ],
      },
    });
    try {
      const result = await syncPendingPosTransactions(9);
      assert.strictEqual(result.succeeded, 1);
      assert.strictEqual(result.reconciled, 1);
      assert.strictEqual(result.failed, 1);
      assert.deepStrictEqual(result.errors, ['QR_TOKEN_EXPIRED']);

      const pending = await getPendingPosTransactions();
      assert.strictEqual(pending.length, 1);
      assert.strictEqual(pending[0].client_transaction_id, 'bad-1');
      assert.strictEqual(pending[0].status, 'FAILED');
      assert.strictEqual(pending[0].last_error, 'QR_TOKEN_EXPIRED');
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('carries the offline QR token through the queue and into the batch payload', async () => {
    const items = [{ sku: 'NASI-01', name: 'Nasi Goreng', qty: 1, unit_price: '15000.00' }];
    await enqueuePosTransaction({
      terminal_id: 7, student_id: 201, items, subtotal: 15000, total: 15000, qr_token: 'payload.sig',
    });
    await enqueuePosTransaction({ terminal_id: 7, student_id: 202, items, subtotal: 15000, total: 15000 });

    const pending = await getPendingPosTransactions();
    assert.strictEqual(pending[0].qr_token, 'payload.sig');
    assert.strictEqual(pending[1].qr_token ?? null, null);

    const { transactions } = buildPosBatchPayload(7, pending);
    assert.strictEqual(transactions[0].qr_token, 'payload.sig');
    assert.ok(!('qr_token' in transactions[1]), 'card/cash sales must not send a token');
  });
});
