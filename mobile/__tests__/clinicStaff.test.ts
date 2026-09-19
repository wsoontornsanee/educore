/**
 * Clinic officer data layer: recent visits and medication stock (services/clinicStaff.ts).
 * The screens are thin over this; the point worth pinning is that health notes never reach on-device storage.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  fetchMedicationStock,
  fetchRecentClinicVisits,
  isExpired,
  isLowStock,
  sortStockForAttention,
} from '../src/services/clinicStaff.ts';
import { apiClient } from '../src/services/api.ts';
import type { ClinicVisitItem, MedicationStockItem } from '../src/types/index.ts';

const visit = (id: number): ClinicVisitItem => ({
  id, school: 1, student: 10 + id, student_name: `Siswa ${id}`, occurred_at: '2026-09-15T02:00:00Z',
  complaint: 'Demam tinggi', treatment: 'Parasetamol', vitals: {}, medication_given: null, medication_name: null,
  medication_quantity_used: null, outcome: 'RETURNED_TO_CLASS', handled_by: 5, handled_by_name: 'Bu Wati',
  guardian_consent_confirmed: false, guardian_consent_note: '', guardian_notified_at: null,
  created_at: '2026-09-15T02:00:00Z', updated_at: '2026-09-15T02:00:00Z',
});

const stock = (id: number, name: string, quantity: number, reorder: number, expiry: string | null): MedicationStockItem => ({
  id, school: 1, name, unit: 'tablet', quantity, expiry_date: expiry, reorder_level: reorder,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
});

async function withGet<T>(payload: unknown, run: (paths: string[]) => Promise<T>): Promise<T> {
  const original = apiClient.get;
  const paths: string[] = [];
  apiClient.get = async (path: string): Promise<any> => {
    paths.push(path);
    return { data: payload, status: 200, headers: {} };
  };
  try {
    return await run(paths);
  } finally {
    apiClient.get = original;
  }
}

describe('clinic officer: recent visits', () => {
  it('reads the first page of the clinic-visits endpoint', async () => {
    await withGet({ results: [visit(1), visit(2)] }, async (paths) => {
      const result = await fetchRecentClinicVisits();
      assert.deepStrictEqual(result.map((v) => v.id), [1, 2]);
      assert.deepStrictEqual(paths, ['/campus/clinic-visits/']);
    });
  });

  it('accepts a bare array and an empty response', async () => {
    await withGet([visit(3)], async () => assert.deepStrictEqual((await fetchRecentClinicVisits()).map((v) => v.id), [3]));
    await withGet({}, async () => assert.deepStrictEqual(await fetchRecentClinicVisits(), []));
  });

  it('never writes health notes to on-device storage', () => {
    // The parent client caches visits (services/clinic.ts); the officer client must not, so it must not even
    // import the storage module. Guarding the source is the check that survives a renamed cache key.
    const source = readFileSync(new URL('../src/services/clinicStaff.ts', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /from\s+['"][^'"]*storage/);
    assert.doesNotMatch(source, /\bcacheSet\b|\bAsyncStorage\b|\bsetItem\b/);
  });

  it('lets a failed request surface, instead of serving a stale copy of health notes', async () => {
    const original = apiClient.get;
    apiClient.get = async () => { throw new Error('offline'); };
    try {
      await assert.rejects(fetchRecentClinicVisits(), /offline/);
    } finally {
      apiClient.get = original;
    }
  });
});

describe('clinic officer: medication stock', () => {
  it('reads the medication-stock endpoint', async () => {
    await withGet({ results: [stock(1, 'Paracetamol', 50, 10, null)] }, async (paths) => {
      const result = await fetchMedicationStock();
      assert.strictEqual(result[0].name, 'Paracetamol');
      assert.deepStrictEqual(paths, ['/campus/medication-stock/']);
    });
  });

  it('low stock is at or below the reorder level', () => {
    assert.strictEqual(isLowStock(stock(1, 'A', 10, 10, null)), true);
    assert.strictEqual(isLowStock(stock(1, 'A', 11, 10, null)), false);
    assert.strictEqual(isLowStock(stock(1, 'A', 0, 0, null)), true);
  });

  it('expired means before today, not today itself, and no expiry date is never expired', () => {
    assert.strictEqual(isExpired(stock(1, 'A', 5, 1, '2026-09-18'), '2026-09-19'), true);
    assert.strictEqual(isExpired(stock(1, 'A', 5, 1, '2026-09-19'), '2026-09-19'), false);
    assert.strictEqual(isExpired(stock(1, 'A', 5, 1, null), '2026-09-19'), false);
  });

  it('puts low or expired items first, then sorts by name, without mutating the input', () => {
    const list = [
      stock(1, 'Zinc', 50, 5, null),
      stock(2, 'Antasida', 50, 5, null),
      stock(3, 'Oralit', 2, 5, null),
      stock(4, 'Betadine', 50, 5, '2026-01-01'),
    ];
    const sorted = sortStockForAttention(list, '2026-09-19');
    assert.deepStrictEqual(sorted.map((i) => i.name), ['Betadine', 'Oralit', 'Antasida', 'Zinc']);
    assert.deepStrictEqual(list.map((i) => i.name), ['Zinc', 'Antasida', 'Oralit', 'Betadine']);
  });
});
