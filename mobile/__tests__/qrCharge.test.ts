/**
 * Canteen QR Charge guardian-app data layer (spec 18 QRS-011, QRS-026, QRS-029).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyKeypadKey,
  changePin,
  checkNewPinEntry,
  canDisputeTransaction,
  chargeQr,
  createPin,
  describeQrError,
  evaluateAmount,
  fetchPinStatus,
  getDisputedTransactionIds,
  isDeadQrFailure,
  isPinBlocking,
  isPinFailure,
  isValidPinFormat,
  newIdempotencyKey,
  normalizeScannedToken,
  openQrDispute,
  requestPinResetOtp,
  resetPinWithOtp,
  resolveQr,
  setQrChargeEnabled,
} from '../src/services/qrCharge.ts';
import { api } from '../src/services/api.ts';
import { clearCachedData } from '../src/services/storage.ts';

function apiError(status: number, data: any): any {
  const err: any = new Error(data?.error || `HTTP ${status}`);
  err.response = { status, data };
  return err;
}

describe('Canteen QR Charge guardian client', () => {
  beforeEach(async () => {
    await clearCachedData();
  });

  describe('normalizeScannedToken', () => {
    it('accepts a signed token and trims surrounding whitespace', () => {
      assert.strictEqual(normalizeScannedToken('  abc:def.ghi\n'), 'abc:def.ghi');
    });
    it('rejects empty, spaced, and over-long payloads', () => {
      assert.strictEqual(normalizeScannedToken(''), null);
      assert.strictEqual(normalizeScannedToken(null), null);
      assert.strictEqual(normalizeScannedToken('WIFI:S:my net;'), null);
      assert.strictEqual(normalizeScannedToken('a'.repeat(513)), null);
      assert.strictEqual(normalizeScannedToken('a'.repeat(512)), 'a'.repeat(512));
    });
  });

  describe('keypad + amount', () => {
    it('builds digits, ignores leading zeros, supports 00 and delete', () => {
      let d = '';
      d = applyKeypadKey(d, '0');
      assert.strictEqual(d, '');
      d = applyKeypadKey(d, '1');
      d = applyKeypadKey(d, '5');
      d = applyKeypadKey(d, '00');
      d = applyKeypadKey(d, '0');
      assert.strictEqual(d, '15000');
      assert.strictEqual(applyKeypadKey(d, 'DEL'), '1500');
      assert.strictEqual(applyKeypadKey('', 'DEL'), '');
      assert.strictEqual(applyKeypadKey(d, 'x'), d);
    });
    it('stops accepting digits at nine', () => {
      assert.strictEqual(applyKeypadKey('99999999', '9'), '999999999');
      assert.strictEqual(applyKeypadKey('999999999', '9'), '999999999');
      assert.strictEqual(applyKeypadKey('99999999', '00'), '99999999');
    });
    it('classifies the amount against cap and balance, cap first', () => {
      assert.deepStrictEqual(evaluateAmount('', '50000.00', '20000.00'), { amount: 0, state: 'EMPTY' });
      assert.deepStrictEqual(evaluateAmount('15000', '50000.00', '20000.00'), { amount: 15000, state: 'OK' });
      assert.deepStrictEqual(evaluateAmount('20000', '50000.00', '20000.00'), { amount: 20000, state: 'OK' });
      assert.deepStrictEqual(evaluateAmount('25000', '50000.00', '20000.00'), { amount: 25000, state: 'ABOVE_CAP' });
      assert.deepStrictEqual(evaluateAmount('15000', '10000.00', '20000.00'), { amount: 15000, state: 'INSUFFICIENT' });
      assert.strictEqual(evaluateAmount('25000', '10000.00', '20000.00').state, 'ABOVE_CAP');
    });
  });

  describe('describeQrError', () => {
    it('reads code and message from the response body', () => {
      const f = describeQrError(apiError(400, { error: 'QR_TOKEN_EXPIRED', message: 'Kode ini sudah kedaluwarsa.' }));
      assert.deepStrictEqual(f, {
        code: 'QR_TOKEN_EXPIRED', message: 'Kode ini sudah kedaluwarsa.', attemptsLeft: undefined, retryAfterSeconds: undefined,
      });
    });
    it('carries PIN attempt and lock hints', () => {
      const wrong = describeQrError(apiError(400, { error: 'PIN_INVALID', message: 'PIN salah.', attempts_left: 2 }));
      assert.strictEqual(wrong.attemptsLeft, 2);
      const locked = describeQrError(apiError(423, { error: 'PIN_LOCKED', message: 'Dikunci', retry_after_seconds: 900 }));
      assert.strictEqual(locked.retryAfterSeconds, 900);
    });
    it('falls back for network and unknown failures', () => {
      const net: any = new Error('Network request failed');
      net.isNetworkError = true;
      assert.strictEqual(describeQrError(net).code, 'NETWORK');
      assert.strictEqual(describeQrError(new Error('boom')).code, 'UNKNOWN');
      assert.strictEqual(describeQrError(undefined).code, 'UNKNOWN');
    });
    it('separates dead QR codes from retryable refusals and PIN problems', () => {
      assert.ok(isDeadQrFailure('QR_TOKEN_USED'));
      assert.ok(isDeadQrFailure('WALLET_FROZEN'));
      assert.ok(!isDeadQrFailure('INSUFFICIENT_BALANCE'));
      assert.ok(!isDeadQrFailure('DAILY_LIMIT_EXCEEDED'));
      assert.ok(isPinFailure('PIN_INVALID'));
      assert.ok(!isPinFailure('AMOUNT_ABOVE_CAP'));
      assert.ok(isPinBlocking('PIN_LOCKED'));
      assert.ok(isPinBlocking('PIN_RESET_REQUIRED'));
      assert.ok(!isPinBlocking('PIN_INVALID'));
    });
  });

  describe('API calls', () => {
    const calls: Array<{ method: string; path: string; body?: any }> = [];
    const original = { get: api.get, post: api.post };

    beforeEach(() => {
      calls.length = 0;
      (api as any).get = original.get;
      (api as any).post = original.post;
    });

    it('resolve posts student and token', async () => {
      (api as any).post = async (path: string, body: any) => {
        calls.push({ method: 'POST', path, body });
        return { data: { type: 'SESSION', merchant_name: 'Kantin A', balance: '50000.00', max_amount: '20000.00' } };
      };
      const res = await resolveQr(7, 'tok');
      assert.deepStrictEqual(calls[0], { method: 'POST', path: '/wallet/qr/resolve/', body: { student_id: 7, token: 'tok' } });
      assert.strictEqual(res.merchant_name, 'Kantin A');
    });

    it('charge sends a decimal amount, the PIN and the idempotency key', async () => {
      (api as any).post = async (path: string, body: any) => {
        calls.push({ method: 'POST', path, body });
        return { data: { txn_id: 1, code: 'K7QX', amount: '15000.00', balance_after: '35000.00', occurred_at: 'x' } };
      };
      const key = newIdempotencyKey();
      const res = await chargeQr({ studentId: 7, token: 'tok', amount: 15000, pin: '482913', idempotencyKey: key });
      assert.deepStrictEqual(calls[0].body, {
        student_id: 7, token: 'tok', amount: '15000.00', pin: '482913', idempotency_key: key,
      });
      assert.strictEqual(res.code, 'K7QX');
      assert.ok(key.startsWith('qr-') && key.length <= 100);
      assert.notStrictEqual(newIdempotencyKey(), key);
    });

    it('reads and creates the spending PIN', async () => {
      (api as any).get = async (path: string) => {
        calls.push({ method: 'GET', path });
        return { data: { is_set: false, locked_until: null, requires_otp_reset: false } };
      };
      (api as any).post = async (path: string, body: any) => {
        calls.push({ method: 'POST', path, body });
        return { data: { is_set: true, locked_until: null, requires_otp_reset: false } };
      };
      assert.strictEqual((await fetchPinStatus()).is_set, false);
      assert.strictEqual((await createPin('482913')).is_set, true);
      assert.deepStrictEqual(calls.map((c) => c.path), ['/me/pin/', '/me/pin/']);
      assert.deepStrictEqual(calls[1].body, { pin: '482913' });
    });

    it('validates PIN format as exactly six digits', () => {
      assert.ok(isValidPinFormat('482913'));
      assert.ok(!isValidPinFormat('48291'));
      assert.ok(!isValidPinFormat('4829134'));
      assert.ok(!isValidPinFormat('48a913'));
    });
  });

  describe('PIN change and OTP reset', () => {
    it('checks a new PIN entry: format first, then match', () => {
      assert.strictEqual(checkNewPinEntry('482913', '482913'), null);
      assert.strictEqual(checkNewPinEntry('4829', '4829'), 'PIN_INVALID_FORMAT');
      assert.strictEqual(checkNewPinEntry('482913', '482914'), 'PIN_MISMATCH');
    });

    it('change sends current and new PIN with PUT', async () => {
      const original = api.put;
      (api as any).put = async (path: string, body: any) => {
        assert.strictEqual(path, '/me/pin/');
        assert.deepStrictEqual(body, { current_pin: '482913', new_pin: '739158' });
        return { data: { is_set: true, locked_until: null, requires_otp_reset: false } };
      };
      try {
        assert.strictEqual((await changePin('482913', '739158')).is_set, true);
      } finally {
        (api as any).put = original;
      }
    });

    it('reset requests an OTP for the phone, then posts challenge, code and new PIN', async () => {
      const original = api.post;
      const calls: Array<{ path: string; body: any }> = [];
      (api as any).post = async (path: string, body: any) => {
        calls.push({ path, body });
        return path === '/auth/otp/request/'
          ? { data: { challenge_id: 42 } }
          : { data: { is_set: true, locked_until: null, requires_otp_reset: false } };
      };
      try {
        const challengeId = await requestPinResetOtp('+6281234567890');
        assert.strictEqual(challengeId, 42);
        const status = await resetPinWithOtp(challengeId, '123456', '739158');
        assert.strictEqual(status.requires_otp_reset, false);
        assert.deepStrictEqual(calls, [
          { path: '/auth/otp/request/', body: { phone_e164: '+6281234567890' } },
          { path: '/me/pin/reset/', body: { challenge_id: 42, code: '123456', new_pin: '739158' } },
        ]);
      } finally {
        (api as any).post = original;
      }
    });
  });

  describe('setQrChargeEnabled', () => {
    it('resends the saved rule so the PUT cannot reset limits, windows or itemised blocks', async () => {
      const original = api.put;
      let sent: any = null;
      (api as any).put = async (path: string, body: any) => {
        assert.strictEqual(path, '/wallets/7/rules/');
        sent = body;
        return { data: { ...body, qr_charge_available: false } };
      };
      try {
        const updated = await setQrChargeEnabled(
          7,
          {
            daily_limit: '25000.00',
            blocked_categories: ['Camilan'],
            blocked_products: [4, 9],
            allowed_window_start: '09:30:00',
            allowed_window_end: '13:30:00',
            qr_charge_enabled: false,
          },
          true,
        );
        assert.deepStrictEqual(sent, {
          daily_limit: '25000.00',
          blocked_categories: ['Camilan'],
          blocked_products: [4, 9],
          allowed_window_start: '09:30:00',
          allowed_window_end: '13:30:00',
          qr_charge_enabled: true,
        });
        assert.strictEqual(updated.qr_charge_available, false);
      } finally {
        (api as any).put = original;
      }
    });

    it('sends empty defaults when no rule has been saved yet', async () => {
      const original = api.put;
      let sent: any = null;
      (api as any).put = async (_p: string, body: any) => {
        sent = body;
        return { data: body };
      };
      try {
        await setQrChargeEnabled(7, null, true);
        assert.deepStrictEqual(sent, {
          daily_limit: null,
          blocked_categories: [],
          blocked_products: [],
          allowed_window_start: null,
          allowed_window_end: null,
          qr_charge_enabled: true,
        });
      } finally {
        (api as any).put = original;
      }
    });
  });

  describe('disputes', () => {
    const now = new Date('2026-09-19T10:00:00Z');
    const tx = { type: 'PURCHASE', entry_mode: 'SELF_ENTERED', status: 'COMPLETED', occurred_at: '2026-09-17T10:00:00Z' };

    it('offers a dispute only for recent, completed, self-entered purchases', () => {
      assert.ok(canDisputeTransaction(tx, now));
      assert.ok(!canDisputeTransaction({ ...tx, entry_mode: 'OPERATOR' }, now));
      assert.ok(!canDisputeTransaction({ ...tx, entry_mode: undefined }, now));
      assert.ok(!canDisputeTransaction({ ...tx, type: 'TOPUP' }, now));
      assert.ok(!canDisputeTransaction({ ...tx, status: 'REJECTED' }, now));
      assert.ok(!canDisputeTransaction({ ...tx, occurred_at: '2026-09-11T09:59:00Z' }, now));
      assert.ok(canDisputeTransaction({ ...tx, occurred_at: '2026-09-12T10:00:00Z' }, now));
      assert.ok(!canDisputeTransaction({ ...tx, occurred_at: 'garbage' }, now));
    });

    it('records a disputed transaction locally once the server accepts it', async () => {
      const original = api.post;
      (api as any).post = async (path: string, body: any) => {
        assert.strictEqual(path, '/wallet/transactions/55/dispute/');
        assert.deepStrictEqual(body, { reason: 'Bukan anak saya' });
        return { data: { id: 3, pos_transaction: 9, status: 'OPEN', reason: 'Bukan anak saya' } };
      };
      try {
        const res = await openQrDispute(7, 55, '  Bukan anak saya ');
        assert.strictEqual(res?.status, 'OPEN');
        assert.deepStrictEqual(await getDisputedTransactionIds(7), [55]);
        assert.deepStrictEqual(await getDisputedTransactionIds(8), []);
      } finally {
        (api as any).post = original;
      }
    });

    it('treats DISPUTE_EXISTS as already disputed, and rethrows other refusals without recording', async () => {
      const original = api.post;
      try {
        (api as any).post = async () => { throw apiError(400, { error: 'DISPUTE_EXISTS', message: 'Sudah' }); };
        assert.strictEqual(await openQrDispute(7, 56, 'x'), null);
        assert.deepStrictEqual(await getDisputedTransactionIds(7), [56]);

        (api as any).post = async () => { throw apiError(400, { error: 'DISPUTE_WINDOW_CLOSED', message: 'Lewat' }); };
        await assert.rejects(() => openQrDispute(7, 57, 'x'), /DISPUTE_WINDOW_CLOSED/);
        assert.deepStrictEqual(await getDisputedTransactionIds(7), [56]);
      } finally {
        (api as any).post = original;
      }
    });
  });
});
