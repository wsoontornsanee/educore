/**
 * POS kiosk contract with the server (spec/07 §8). The fixtures in ./fixtures are captured from the server's real
 * pos_session / pos_sync output (apps/wallet/tests/test_pos_mobile_contract.py fails if the server's field names
 * drift from them), so these tests cannot pass against a hand-written mock the server does not actually send.
 * The kiosk crashed on a real device (`full_name` undefined on student select) because they once did.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  adaptDeltas,
  adaptRosterEntry,
  adaptSession,
  applyRules,
  toClockMinutes,
} from '../src/services/posAdapter.ts';
import { checkStudentSpendRules, mergeCatalogDeltas, mergeRosterDeltas } from '../src/services/pos.ts';
import type { POSCartItem, POSProduct, POSStudent } from '../src/types/index.ts';

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));

const cart = (product: POSProduct, qty = 1): POSCartItem => ({ product, qty, unit_price: Number(product.price) });
const NOON = new Date('2026-09-19T12:00:00');

describe('session adapter (real POST /pos/sessions/ output)', () => {
  const session = adaptSession(fixture('pos_session.json'), 7);
  const student = session.roster[0];

  it('maps every roster student to what the kiosk screen reads, so selecting one cannot crash', () => {
    assert.strictEqual(student.id, 1);
    assert.strictEqual(student.full_name, 'Dimas');
    assert.strictEqual(typeof student.full_name, 'string');
    assert.strictEqual(student.balance, '100000.00');
    assert.ok(Number.isFinite(Number(student.balance)), 'balance must be numeric, not NaN');
  });

  it('a roster entry with no name still yields a string (the screen calls charAt on it)', () => {
    assert.strictEqual(adaptRosterEntry({ student_id: 9 }).full_name, '');
    assert.strictEqual(adaptRosterEntry({ student_id: 9 }).balance, '0');
  });

  it('identifies products by SKU: the server catalog has no id', () => {
    assert.strictEqual(session.catalog[0].sku, 'NASI-01');
    assert.strictEqual(session.catalog[0].price, '15000.00');
    assert.strictEqual(session.catalog[0].id, undefined);
  });

  it('applies the spend rules to the roster, with the window as HH:MM and the rule daily limit winning', () => {
    assert.deepStrictEqual(student.blocked_categories, ['SNACK']);
    assert.deepStrictEqual(student.blocked_products, ['GUM-01']);
    assert.strictEqual(student.allowed_window_start, '09:30');
    assert.strictEqual(student.allowed_window_end, '13:30');
    assert.strictEqual(student.daily_limit, '25000.00');
  });

  it('falls back to the terminal id and a generic name, because the server sends no terminal or merchant object', () => {
    assert.strictEqual(session.terminal_id, 7);
    assert.strictEqual(session.terminal_name, 'Terminal #7');
    assert.strictEqual(session.merchant_name, 'Kantin');
  });

  it('keeps the sync cursor', () => {
    assert.strictEqual(session.sync_cursor, fixture('pos_session.json').cursor);
  });

  it('survives an empty or missing payload', () => {
    const empty = adaptSession({}, 3);
    assert.deepStrictEqual([empty.roster, empty.catalog], [[], []]);
    assert.doesNotThrow(() => adaptSession(undefined, 3));
  });
});

describe('sync adapter (real GET /pos/sync/ output)', () => {
  const deltas = adaptDeltas(fixture('pos_sync.json'));

  it('reads the *_delta keys the server sends and next_cursor', () => {
    assert.strictEqual(deltas.roster[0].full_name, 'Dimas');
    assert.strictEqual(deltas.catalog[0].sku, 'NASI-01');
    assert.strictEqual(deltas.rules[0].student_id, 1);
    assert.strictEqual(deltas.nextCursor, fixture('pos_sync.json').next_cursor);
  });

  it('a delta that carries a new balance updates it and keeps the rules set earlier', () => {
    const session = adaptSession(fixture('pos_session.json'), 7);
    const changed = { ...deltas.roster[0], balance: '82000.00' };
    const merged = mergeRosterDeltas(session.roster, [changed], []);
    assert.strictEqual(merged[0].balance, '82000.00');
    assert.deepStrictEqual(merged[0].blocked_categories, ['SNACK']);
    assert.strictEqual(merged[0].allowed_window_start, '09:30');
    assert.strictEqual(merged[0].daily_limit, '25000.00');
  });

  it('a rules delta changes rules without touching the balance', () => {
    const session = adaptSession(fixture('pos_session.json'), 7);
    const merged = mergeRosterDeltas(session.roster, [], [{ student_id: 1, blocked_categories: [] }]);
    assert.deepStrictEqual(merged[0].blocked_categories, []);
    assert.strictEqual(merged[0].balance, '100000.00');
    assert.deepStrictEqual(merged[0].blocked_products, ['GUM-01']); // not mentioned by the delta, kept
  });

  it('adds a student who is new to the roster and keeps the ones the delta did not mention', () => {
    const session = adaptSession(fixture('pos_session.json'), 7);
    const merged = mergeRosterDeltas(session.roster, [adaptRosterEntry({ student_id: 2, name: 'Sari', wallet_balance: '5000.00' })], []);
    assert.deepStrictEqual(merged.map((s) => s.id).sort(), [1, 2]);
  });

  it('merges catalog deltas by SKU: a changed price replaces, two products never merge into one', () => {
    const session = adaptSession(fixture('pos_session.json'), 7);
    const merged = mergeCatalogDeltas(session.catalog, [
      { sku: 'NASI-01', name: 'Nasi Goreng', price: '16000.00', category: 'FOOD' },
      { sku: 'TEH-01', name: 'Teh', price: '3000.00', category: 'DRINK' },
    ]);
    assert.deepStrictEqual(merged.map((p) => p.sku).sort(), ['NASI-01', 'TEH-01']);
    assert.strictEqual(merged.find((p) => p.sku === 'NASI-01')!.price, '16000.00');
  });
});

describe('spend rules on adapted students', () => {
  const session = adaptSession(fixture('pos_session.json'), 7);
  const student = session.roster[0];
  const nasi = session.catalog[0];

  it('allows a normal purchase inside the window', () => {
    assert.strictEqual(checkStudentSpendRules(student, [cart(nasi)], NOON).allowed, true);
  });

  it('a window of 09:30 to 13:30 is inclusive at both ends (HH:MM, not HH:MM:SS)', () => {
    assert.strictEqual(checkStudentSpendRules(student, [cart(nasi)], new Date('2026-09-19T09:30:00')).allowed, true);
    assert.strictEqual(checkStudentSpendRules(student, [cart(nasi)], new Date('2026-09-19T13:30:00')).allowed, true);
    assert.strictEqual(checkStudentSpendRules(student, [cart(nasi)], new Date('2026-09-19T13:31:00')).allowed, false);
    assert.strictEqual(checkStudentSpendRules(student, [cart(nasi)], new Date('2026-09-19T09:29:00')).allowed, false);
  });

  it('refuses a blocked category and a blocked product, as the server would', () => {
    const snack: POSProduct = { sku: 'CHIP-01', name: 'Keripik', price: '5000.00', category: 'SNACK' };
    const gum: POSProduct = { sku: 'GUM-01', name: 'Permen karet', price: '2000.00', category: 'CANDY' };
    assert.match(checkStudentSpendRules(student, [cart(snack)], NOON).reason ?? '', /BLOCKED_CATEGORY/);
    assert.match(checkStudentSpendRules(student, [cart(gum)], NOON).reason ?? '', /BLOCKED_PRODUCT/);
  });

  it('enforces the rule daily limit on a single purchase', () => {
    const big: POSProduct = { sku: 'BIG-01', name: 'Paket', price: '30000.00', category: 'FOOD' };
    assert.match(checkStudentSpendRules(student, [cart(big)], NOON).reason ?? '', /LIMIT_EXCEEDED/);
  });
});

describe('helpers', () => {
  it('trims Django time strings to HH:MM and passes null through', () => {
    assert.strictEqual(toClockMinutes('09:30:00'), '09:30');
    assert.strictEqual(toClockMinutes('09:30'), '09:30');
    assert.strictEqual(toClockMinutes(null), null);
    assert.strictEqual(toClockMinutes(undefined), null);
  });

  it('a rule for a student who is not on the roster is ignored', () => {
    const students: POSStudent[] = [adaptRosterEntry({ student_id: 1, name: 'A' })];
    assert.deepStrictEqual(applyRules(students, [{ student_id: 99, blocked_categories: ['X'] }]), students);
  });
});
