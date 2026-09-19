/**
 * Formatting bugs from the simulator QA pass: "-Rp -18.000" in wallet history, untranslated transaction types,
 * English category chips on the kiosk. Layout fixes (child chip height, tab labels, kiosk safe area) are style
 * changes checked by type-check only; they need a device to see.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { formatRupiah, formatSignedRupiah, isCreditTransaction } from '../src/services/wallet.ts';
import { categoryLabel } from '../src/constants/posCategories.ts';
import { strings } from '../src/i18n/strings.ts';

describe('formatRupiah', () => {
  it('puts the minus sign before the currency, not between it and the digits', () => {
    assert.strictEqual(formatRupiah(-18000), '-Rp 18.000');
    assert.strictEqual(formatRupiah('-18000.00'), '-Rp 18.000');
  });

  it('leaves positive amounts, zero and junk as before', () => {
    assert.strictEqual(formatRupiah(50000), 'Rp 50.000');
    assert.strictEqual(formatRupiah(0), 'Rp 0');
    assert.strictEqual(formatRupiah(null), 'Rp 0');
    assert.strictEqual(formatRupiah('abc'), 'Rp 0');
  });

  it('does not show a minus for a negative that rounds to zero', () => {
    assert.strictEqual(formatRupiah(-0.4), 'Rp 0');
  });
});

describe('ledger row amounts', () => {
  it('a purchase stored as a negative amount reads "-Rp 18.000", never "-Rp -18.000"', () => {
    assert.strictEqual(formatSignedRupiah('PURCHASE', -18000), '-Rp 18.000');
    assert.strictEqual(formatSignedRupiah('PURCHASE', '-18000.00'), '-Rp 18.000');
    // Some responses may carry the magnitude unsigned; the type still says it is a debit.
    assert.strictEqual(formatSignedRupiah('PURCHASE', 18000), '-Rp 18.000');
  });

  it('top-ups and refunds are credits whatever sign the amount carries', () => {
    assert.strictEqual(formatSignedRupiah('TOPUP', 50000), '+Rp 50.000');
    assert.strictEqual(formatSignedRupiah('REFUND', '18000.00'), '+Rp 18.000');
  });

  it('an adjustment follows its own sign', () => {
    assert.strictEqual(formatSignedRupiah('ADJUSTMENT', 6000), '+Rp 6.000');
    assert.strictEqual(formatSignedRupiah('ADJUSTMENT', -6000), '-Rp 6.000');
    assert.strictEqual(isCreditTransaction('ADJUSTMENT', -1), false);
    assert.strictEqual(isCreditTransaction('ADJUSTMENT', 0), true);
  });

  it('credit/debit agrees with the sign shown for every type', () => {
    for (const type of ['TOPUP', 'PURCHASE', 'REFUND', 'ADJUSTMENT']) {
      for (const amount of [-5000, 5000]) {
        const shown = formatSignedRupiah(type, amount)[0];
        assert.strictEqual(shown === '+', isCreditTransaction(type, amount), `${type} ${amount}`);
      }
    }
  });
});

describe('wallet history labels', () => {
  it('every ledger type has an id-ID and en-US label, so no raw "PURCHASE" reaches the screen', () => {
    for (const type of ['TOPUP', 'PURCHASE', 'REFUND', 'ADJUSTMENT']) {
      const entry = strings[`wallet.tx.${type}`];
      assert.ok(entry, type);
      assert.notStrictEqual(entry['id-ID'], type);
    }
    assert.strictEqual(strings['wallet.tx.PURCHASE']['id-ID'], 'Belanja');
  });
});

describe('kiosk category chips', () => {
  it('shows the catalog categories in Indonesian', () => {
    assert.strictEqual(categoryLabel('FOOD'), 'Makanan');
    assert.strictEqual(categoryLabel('DRINK'), 'Minuman');
    assert.strictEqual(categoryLabel('SNACK'), 'Camilan');
    assert.strictEqual(categoryLabel('ALL'), 'Semua');
  });

  it('is case-insensitive and shows an unknown category as the server sent it', () => {
    assert.strictEqual(categoryLabel('food'), 'Makanan');
    assert.strictEqual(categoryLabel('STATIONERY'), 'STATIONERY');
  });
});
