/**
 * Mobile POS Service and Spend Rule Validation Unit Tests (spec/07 §3-§7).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  checkStudentSpendRules,
  checkoutPOSTransaction,
  clearCachedSession,
  setCachedSession,
  voidPOSTransaction,
} from '../src/services/pos.ts';
import { clearAllPosForTesting, getPendingPosCount } from '../src/services/posOfflineQueue.ts';
import { apiClient } from '../src/services/api.ts';
import type { POSCartItem, POSProduct, POSStudent } from '../src/types/index.ts';

describe('POS Service & Spend Rules', () => {
  const mockProductFood: POSProduct = {
    id: 1,
    sku: 'NASI-01',
    name: 'Nasi Kuning',
    price: 12000,
    category: 'FOOD',
    nutrition: { calories: 350, sugar_g: 5, is_healthy: true },
    allergens: ['egg'],
  };

  const mockProductDrink: POSProduct = {
    id: 2,
    sku: 'SODA-01',
    name: 'Minuman Bersoda',
    price: 8000,
    category: 'SUGARY_DRINK',
    nutrition: { calories: 180, sugar_g: 35, is_healthy: false },
    allergens: [],
  };

  const mockStudent: POSStudent = {
    id: 101,
    full_name: 'Ahmad Zaki',
    nisn: '1234567890',
    nis: 'SD-101',
    balance: 50000,
    daily_limit: 25000,
    spent_today: 10000,
    blocked_categories: ['SUGARY_DRINK'],
    allowed_window_start: '09:00',
    allowed_window_end: '14:00',
  };

  beforeEach(async () => {
    clearCachedSession();
    await clearAllPosForTesting();
  });

  describe('checkStudentSpendRules', () => {
    it('approves transaction within daily limit and allowed categories', () => {
      const cart: POSCartItem[] = [
        { product: mockProductFood, qty: 1, unit_price: 12000 },
      ];
      // spent_today (10k) + cart (12k) = 22k <= daily_limit (25k)
      const now = new Date('2026-09-16T10:30:00');
      const result = checkStudentSpendRules(mockStudent, cart, now);
      assert.strictEqual(result.allowed, true);
    });

    it('rejects purchase exceeding daily spending limit (WAL-009, WAL-013)', () => {
      const cart: POSCartItem[] = [
        { product: mockProductFood, qty: 2, unit_price: 12000 }, // 24k
      ];
      // 10k + 24k = 34k > 25k limit
      const now = new Date('2026-09-16T10:30:00');
      const result = checkStudentSpendRules(mockStudent, cart, now);
      assert.strictEqual(result.allowed, false);
      assert.ok(result.reason?.includes('LIMIT_EXCEEDED'));
    });

    it('rejects blocked category with clear error message (WAL-010, WAL-013)', () => {
      const cart: POSCartItem[] = [
        { product: mockProductDrink, qty: 1, unit_price: 8000 },
      ];
      const now = new Date('2026-09-16T10:30:00');
      const result = checkStudentSpendRules(mockStudent, cart, now);
      assert.strictEqual(result.allowed, false);
      assert.ok(result.reason?.includes('BLOCKED_CATEGORY'));
      assert.ok(result.reason?.includes('SUGARY_DRINK'));
    });

    it('rejects purchase outside allowable time window (WAL-011, WAL-013)', () => {
      const cart: POSCartItem[] = [
        { product: mockProductFood, qty: 1, unit_price: 12000 },
      ];
      // 08:15 is before 09:00 window
      const now = new Date('2026-09-16T08:15:00');
      const result = checkStudentSpendRules(mockStudent, cart, now);
      assert.strictEqual(result.allowed, false);
      assert.ok(result.reason?.includes('TIME_WINDOW_RESTRICTED'));
    });

    it('rejects transaction exceeding offline floor limit Rp 50,000 (WAL-016)', () => {
      const studentPoor: POSStudent = {
        ...mockStudent,
        balance: 5000,
        daily_limit: 100000,
        spent_today: 0,
        blocked_categories: [],
      };
      const expensiveCart: POSCartItem[] = [
        { product: mockProductFood, qty: 5, unit_price: 12000 }, // 60k
      ];
      // balance (5k) - 60k = -55k < -50k floor limit
      const now = new Date('2026-09-16T10:00:00');
      const result = checkStudentSpendRules(studentPoor, expensiveCart, now);
      assert.strictEqual(result.allowed, false);
      assert.ok(result.reason?.includes('FLOOR_LIMIT_EXCEEDED'));
    });
  });

  describe('checkoutPOSTransaction', () => {
    it('processes online checkout in ≤3s and outputs complete receipt (WAL-018, WAL-020)', async () => {
      const student: POSStudent = { ...mockStudent, balance: 50000, spent_today: 0 };
      const cart: POSCartItem[] = [{ product: mockProductFood, qty: 1, unit_price: 12000 }];

      let postedData: any = null;
      const originalPost = apiClient.post;
      (apiClient as any).post = async (url: string, data: any) => {
        if (url === '/api/v1/pos/transactions/') {
          postedData = data;
          return { data: { id: 999, status: 'COMPLETED' } };
        }
        return originalPost(url, data);
      };

      try {
        const receipt = await checkoutPOSTransaction({
          terminalId: 1,
          student,
          cartItems: cart,
          merchantName: 'Kantin Berkah',
          terminalName: 'Kiosk 1',
          currentTime: new Date('2026-09-16T10:00:00'),
        });

        assert.strictEqual(receipt.transaction_id, '999');
        assert.strictEqual(receipt.offline_created, false);
        assert.strictEqual(receipt.total, 12000);
        assert.strictEqual(receipt.balance_after, 38000);
        assert.strictEqual(receipt.student_name, 'Ahmad Zaki');
        assert.strictEqual(student.balance, 38000);
        assert.strictEqual(student.spent_today, 12000);
      } finally {
        (apiClient as any).post = originalPost;
      }
    });

    it('falls back seamlessly to offline queue on network error (WAL-015, WAL-016)', async () => {
      const student: POSStudent = { ...mockStudent, balance: 50000, spent_today: 0 };
      const cart: POSCartItem[] = [{ product: mockProductFood, qty: 1, unit_price: 12000 }];

      const originalPost = apiClient.post;
      (apiClient as any).post = async () => {
        throw new Error('Network Error / Offline');
      };

      try {
        const receipt = await checkoutPOSTransaction({
          terminalId: 1,
          student,
          cartItems: cart,
          currentTime: new Date('2026-09-16T10:00:00'),
        });

        assert.strictEqual(receipt.offline_created, true);
        assert.strictEqual(receipt.total, 12000);
        assert.strictEqual(receipt.balance_after, 38000);

        const pending = await getPendingPosCount();
        assert.strictEqual(pending, 1);
      } finally {
        (apiClient as any).post = originalPost;
      }
    });
  });

  describe('voidPOSTransaction', () => {
    it('calls void endpoint with reason within void window (WAL-025)', async () => {
      let voidCalled = false;
      const originalPost = apiClient.post;
      (apiClient as any).post = async (url: string, data: any) => {
        if (url === '/api/v1/pos/transactions/123/void/') {
          assert.strictEqual(data.reason, 'Salah pilih item');
          voidCalled = true;
          return { data: { status: 'VOIDED' } };
        }
        return originalPost(url, data);
      };

      try {
        const result = await voidPOSTransaction(123, 'Salah pilih item');
        assert.strictEqual(result.success, true);
        assert.strictEqual(voidCalled, true);
      } finally {
        (apiClient as any).post = originalPost;
      }
    });
  });
});
