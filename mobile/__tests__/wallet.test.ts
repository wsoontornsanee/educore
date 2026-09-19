/**
 * Parent Digital Canteen Wallet Service & Component Unit Tests (spec/07 WAL-001, WAL-005, WAL-008, WAL-009..011, spec/08 PAR-006..008, PAR-010, PAR-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  CATEGORY_BLOCK_CHOICES,
  DAILY_LIMIT_INCREMENT,
  TOPUP_PRESETS,
  VA_BANKS,
  createTopupIntent,
  fetchAutoTopupConfig,
  fetchSpendRules,
  fetchTopupIntent,
  fetchWallet,
  fetchWalletTransactions,
  formatRupiah,
  formatSignedRupiah,
  isCreditAmount,
  pollTopupIntent,
  updateAutoTopupConfig,
  updateSpendRules,
} from '../src/services/wallet.ts';
import { api } from '../src/services/api.ts';
import { cacheGet, cacheSet, clearCachedData } from '../src/services/storage.ts';
import type {
  WalletAutoTopupConfig,
  WalletData,
  WalletSpendRule,
  WalletTopupIntentItem,
  WalletTransactionItem,
} from '../src/types/index.ts';

describe('Parent Digital Canteen Wallet Service', () => {
  beforeEach(async () => {
    await clearCachedData();
  });

  describe('formatRupiah & Constants', () => {
    it('formats numeric values and decimal strings into Indonesian Rupiah', () => {
      assert.strictEqual(formatRupiah(50000), 'Rp 50.000');
      assert.strictEqual(formatRupiah('25000.00'), 'Rp 25.000');
      assert.strictEqual(formatRupiah(1250500), 'Rp 1.250.500');
      assert.strictEqual(formatRupiah(0), 'Rp 0');
      assert.strictEqual(formatRupiah('0.00'), 'Rp 0');
    });

    it('puts the sign of a negative amount before the currency, never inside it', () => {
      assert.strictEqual(formatRupiah('-18000.00'), '-Rp 18.000');
      assert.strictEqual(formatRupiah(-1250500), '-Rp 1.250.500');
      assert.ok(!formatRupiah(-18000).includes('Rp -'));
      assert.strictEqual(formatRupiah('-0.00'), 'Rp 0');
    });

    it('signs a ledger amount by its own sign: the server stores a purchase as a negative number', () => {
      assert.strictEqual(formatSignedRupiah('-18000.00'), '-Rp 18.000');
      assert.strictEqual(formatSignedRupiah('50000.00'), '+Rp 50.000');
      assert.strictEqual(formatSignedRupiah('0.00'), 'Rp 0');
      assert.strictEqual(formatSignedRupiah(null), 'Rp 0');
      // the double negative seen on the simulator: "-" by type on top of a negative amount
      assert.ok(!formatSignedRupiah('-18000.00').includes('--'));
      assert.ok(!formatSignedRupiah('-18000.00').includes('Rp -'));
    });

    it('treats only a positive amount as money in', () => {
      assert.strictEqual(isCreditAmount('50000.00'), true);
      assert.strictEqual(isCreditAmount('-18000.00'), false);
      assert.strictEqual(isCreditAmount('0.00'), false);
      assert.strictEqual(isCreditAmount(undefined), false);
    });

    it('handles null, undefined, and non-numeric inputs gracefully', () => {
      assert.strictEqual(formatRupiah(null), 'Rp 0');
      assert.strictEqual(formatRupiah(undefined), 'Rp 0');
      assert.strictEqual(formatRupiah(''), 'Rp 0');
      assert.strictEqual(formatRupiah('abc'), 'Rp 0');
    });

    it('provides standard top-up presets and bank choices', () => {
      assert.deepStrictEqual(Array.from(TOPUP_PRESETS), [20000, 50000, 100000, 200000]);
      assert.deepStrictEqual(Array.from(VA_BANKS), ['BCA', 'Mandiri', 'BNI', 'BRI']);
      assert.deepStrictEqual(Array.from(CATEGORY_BLOCK_CHOICES), [
        'Minuman Manis',
        'Camilan',
        'Makanan Cepat Saji',
      ]);
      assert.strictEqual(DAILY_LIMIT_INCREMENT, 5000);
    });
  });

  describe('fetchWallet & Offline Caching (PAR-015)', () => {
    const mockWallet: WalletData = {
      id: 1,
      foundation_id: 1,
      student: 101,
      balance: '75000.00',
      currency: 'IDR',
      status: 'ACTIVE',
      daily_limit: '30000.00',
      created_at: '2026-09-01T00:00:00Z',
      updated_at: '2026-09-17T00:00:00Z',
    };

    it('fetches wallet online and caches it locally', async () => {
      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        return { data: mockWallet as unknown as T, status: 200, headers: {} };
      };

      try {
        const res = await fetchWallet(101);
        assert.strictEqual(res.isOfflineCached, false);
        assert.strictEqual(res.wallet.balance, '75000.00');
        assert.strictEqual(res.wallet.status, 'ACTIVE');

        // Verify cached
        const cached = await cacheGet<WalletData>('educore_parent_wallet:101');
        assert.ok(cached !== null);
        assert.strictEqual(cached?.value.balance, '75000.00');
      } finally {
        api.get = originalGet;
      }
    });

    it('falls back to local cache when offline (PAR-015)', async () => {
      // Seed cache
      await cacheSet('educore_parent_wallet:102', {
        ...mockWallet,
        student: 102,
        balance: '40000.00',
      });

      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        throw new Error('Network error: Device is offline');
      };

      try {
        const res = await fetchWallet(102);
        assert.strictEqual(res.isOfflineCached, true);
        assert.strictEqual(res.wallet.balance, '40000.00');
      } finally {
        api.get = originalGet;
      }
    });

    it('rethrows error when offline and no cache exists', async () => {
      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        throw new Error('Network error: Device is offline');
      };

      try {
        await assert.rejects(
          async () => {
            await fetchWallet(999);
          },
          { message: 'Network error: Device is offline' }
        );
      } finally {
        api.get = originalGet;
      }
    });
  });

  describe('fetchWalletTransactions', () => {
    const mockTx: WalletTransactionItem[] = [
      {
        id: 1,
        foundation_id: 1,
        wallet: 1,
        type: 'TOPUP',
        amount: '50000.00',
        balance_after: '50000.00',
        reference: 'Topup VA BCA',
        occurred_at: '2026-09-15T10:00:00Z',
        status: 'SETTLED',
      },
      {
        id: 2,
        foundation_id: 1,
        wallet: 1,
        type: 'PURCHASE',
        amount: '15000.00',
        balance_after: '35000.00',
        reference: 'POS Kiosk Kantin',
        occurred_at: '2026-09-15T12:30:00Z',
        status: 'SETTLED',
      },
    ];

    it('handles paginated response object with results array', async () => {
      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        return { data: { results: mockTx } as unknown as T, status: 200, headers: {} };
      };

      try {
        const res = await fetchWalletTransactions(101);
        assert.strictEqual(res.isOfflineCached, false);
        assert.strictEqual(res.transactions.length, 2);
        assert.strictEqual(res.transactions[0].type, 'TOPUP');
        assert.strictEqual(res.transactions[1].type, 'PURCHASE');
      } finally {
        api.get = originalGet;
      }
    });

    it('handles flat array response', async () => {
      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        return { data: mockTx as unknown as T, status: 200, headers: {} };
      };

      try {
        const res = await fetchWalletTransactions(101);
        assert.strictEqual(res.transactions.length, 2);
      } finally {
        api.get = originalGet;
      }
    });

    it('falls back to cache when network fails', async () => {
      await cacheSet('educore_parent_wallet:tx:103', mockTx);

      const originalGet = api.get;
      api.get = async <T>(_url: string) => {
        throw new Error('Timeout');
      };

      try {
        const res = await fetchWalletTransactions(103);
        assert.strictEqual(res.isOfflineCached, true);
        assert.strictEqual(res.transactions.length, 2);
      } finally {
        api.get = originalGet;
      }
    });
  });

  describe('Spend Rules & Auto-topup mutations', () => {
    it('fetches and updates spend rules', async () => {
      const originalGet = api.get;
      const originalPut = api.put;

      const currentRules: WalletSpendRule = {
        id: 10,
        student: 101,
        daily_limit: '25000.00',
        blocked_categories: ['Minuman Manis'],
        allowed_window_start: '09:30:00',
        allowed_window_end: '13:30:00',
      };

      api.get = async <T>(_url: string) => {
        return { data: currentRules as unknown as T, status: 200, headers: {} };
      };

      let putBodySent: any = null;
      api.put = async <T>(_url: string, body: any) => {
        putBodySent = body;
        return {
          data: { ...currentRules, ...body } as unknown as T,
          status: 200,
          headers: {},
        };
      };

      try {
        const fetched = await fetchSpendRules(101);
        assert.strictEqual(fetched.rules?.daily_limit, '25000.00');
        assert.deepStrictEqual(fetched.rules?.blocked_categories, ['Minuman Manis']);

        const updated = await updateSpendRules(101, {
          daily_limit: '30000.00',
          blocked_categories: ['Minuman Manis', 'Camilan'],
        });

        assert.strictEqual(putBodySent.daily_limit, '30000.00');
        assert.deepStrictEqual(putBodySent.blocked_categories, ['Minuman Manis', 'Camilan']);
        assert.strictEqual(updated.daily_limit, '30000.00');
      } finally {
        api.get = originalGet;
        api.put = originalPut;
      }
    });

    it('fetches and updates auto topup config', async () => {
      const originalGet = api.get;
      const originalPut = api.put;

      const currentConfig: WalletAutoTopupConfig = {
        id: 5,
        wallet: 1,
        is_active: false,
        threshold_amount: '20000.00',
        topup_amount: '50000.00',
        method: 'VA',
        bank: 'BCA',
      };

      api.get = async <T>(_url: string) => {
        return { data: currentConfig as unknown as T, status: 200, headers: {} };
      };

      let putBodySent: any = null;
      api.put = async <T>(_url: string, body: any) => {
        putBodySent = body;
        return {
          data: { ...currentConfig, ...body } as unknown as T,
          status: 200,
          headers: {},
        };
      };

      try {
        const fetched = await fetchAutoTopupConfig(101);
        assert.strictEqual(fetched.config?.is_active, false);

        const updated = await updateAutoTopupConfig(101, {
          is_active: true,
          threshold_amount: '25000.00',
          topup_amount: '100000.00',
        });

        assert.strictEqual(putBodySent.is_active, true);
        assert.strictEqual(updated.is_active, true);
        assert.strictEqual(updated.threshold_amount, '25000.00');
      } finally {
        api.get = originalGet;
        api.put = originalPut;
      }
    });
  });

  describe('Topup Intent & Polling (WAL-005, PAR-006, PAR-007, PAR-008)', () => {
    it('creates topup intent and fetches intent detail', async () => {
      const originalPost = api.post;
      const originalGet = api.get;

      const mockIntent: WalletTopupIntentItem = {
        id: 42,
        method: 'VA',
        provider: 'MOCK',
        amount: '50000.00',
        currency: 'IDR',
        va_bank: 'BCA',
        va_number: '12345678901234',
        qris_payload: '',
        external_id: 'topup-ext-42',
        status: 'PENDING',
        expires_at: '2026-09-18T00:00:00Z',
      };

      api.post = async <T>(_url: string, _body: any) => {
        return { data: mockIntent as unknown as T, status: 201, headers: {} };
      };
      api.get = async <T>(_url: string) => {
        return { data: mockIntent as unknown as T, status: 200, headers: {} };
      };

      try {
        const created = await createTopupIntent(101, {
          method: 'VA',
          amount: '50000.00',
          bank: 'BCA',
        });
        assert.strictEqual(created.id, 42);
        assert.strictEqual(created.va_bank, 'BCA');
        assert.strictEqual(created.va_number, '12345678901234');

        const detail = await fetchTopupIntent(101, 42);
        assert.strictEqual(detail.status, 'PENDING');
      } finally {
        api.post = originalPost;
        api.get = originalGet;
      }
    });

    it('polls intent until SETTLED and triggers onUpdate callback', async () => {
      const originalGet = api.get;
      let callCount = 0;

      const updates: string[] = [];

      api.get = async <T>(_url: string) => {
        callCount++;
        const currentStatus = callCount >= 3 ? 'SETTLED' : 'PENDING';
        const intent: WalletTopupIntentItem = {
          id: 42,
          method: 'VA',
          provider: 'MOCK',
          amount: '50000.00',
          currency: 'IDR',
          va_bank: 'BCA',
          va_number: '12345678901234',
          qris_payload: '',
          external_id: 'topup-ext-42',
          status: currentStatus as any,
          expires_at: '2026-09-18T00:00:00Z',
        };
        return { data: intent as unknown as T, status: 200, headers: {} };
      };

      try {
        const finalIntent = await pollTopupIntent(101, 42, {
          intervalMs: 10, // Short interval for test speed
          maxAttempts: 10,
          onUpdate: (intent) => {
            updates.push(intent.status);
          },
        });

        assert.strictEqual(finalIntent.status, 'SETTLED');
        assert.strictEqual(callCount, 3);
        assert.deepStrictEqual(updates, ['PENDING', 'PENDING', 'SETTLED']);
      } finally {
        api.get = originalGet;
      }
    });
  });
});
