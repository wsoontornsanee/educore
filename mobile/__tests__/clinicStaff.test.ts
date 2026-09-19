/**
 * Clinic officer data layer: visits, medication stock, student lookup, health profile and recording a visit
 * (services/clinicStaff.ts). The screens are thin over this; the point worth pinning is that health notes never
 * reach on-device storage.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  buildVisitPayload,
  classifySubmitFailure,
  cursorFromNext,
  dispensableStock,
  fetchClinicVisitsPage,
  fetchMedicationStock,
  fetchStudentHealthProfile,
  isExpired,
  isLowStock,
  recordClinicVisit,
  searchStudents,
  sortStockForAttention,
  validateVisitDraft,
} from '../src/services/clinicStaff.ts';
import { apiClient } from '../src/services/api.ts';
import type { VisitDraft } from '../src/services/clinicStaff.ts';
import type { ClinicVisitItem, MedicationStockItem, StudentLookupItem } from '../src/types/index.ts';

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

describe('clinic officer: visits', () => {
  it('reads the first page of the clinic-visits endpoint', async () => {
    await withGet({ results: [visit(1), visit(2)], next: null }, async (paths) => {
      const page = await fetchClinicVisitsPage();
      assert.deepStrictEqual(page.visits.map((v) => v.id), [1, 2]);
      assert.strictEqual(page.nextCursor, null);
      assert.deepStrictEqual(paths, ['/campus/clinic-visits/']);
    });
  });

  it('hands back the cursor of the next page and sends only that cursor, not the server-built URL', async () => {
    const next = 'http://internal:8000/api/v1/campus/clinic-visits/?cursor=cD0yMDI2&page_size=50';
    await withGet({ results: [visit(1)], next }, async () => {
      assert.strictEqual((await fetchClinicVisitsPage()).nextCursor, 'cD0yMDI2');
    });
    await withGet({ results: [visit(2)], next: null }, async (paths) => {
      await fetchClinicVisitsPage('cD0yMDI2');
      assert.deepStrictEqual(paths, ['/campus/clinic-visits/?cursor=cD0yMDI2']);
    });
    assert.strictEqual(cursorFromNext(null), null);
    assert.strictEqual(cursorFromNext('http://x/y/?page=2'), null);
  });

  it('accepts a bare array and an empty response', async () => {
    await withGet([visit(3)], async () => assert.deepStrictEqual((await fetchClinicVisitsPage()).visits.map((v) => v.id), [3]));
    await withGet({}, async () => assert.deepStrictEqual((await fetchClinicVisitsPage()).visits, []));
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
      await assert.rejects(fetchClinicVisitsPage(), /offline/);
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

const student: StudentLookupItem = { id: 42, name: 'Budi Santoso', nis: '2024001', school: 1, school_name: 'SMP Harapan' };

const draft = (over: Partial<VisitDraft> = {}): VisitDraft => ({
  student, complaint: 'Pusing', treatment: '', temperature: '', pulse: '', outcome: 'RETURNED_TO_CLASS',
  medication: null, quantity: '', consentConfirmed: false, consentNote: '', ...over,
});

const TODAY = '2026-09-19';

describe('clinic officer: student lookup and health profile', () => {
  it('searches active students by the trimmed, encoded query and keeps only what identifies the child', async () => {
    const wire = [{
      id: 42, nis: '2024001', nisn: '0012345678', school: 1, school_name: 'SMP Harapan',
      person: { full_name: 'Budi Santoso', nik: '3174000000000001', address: 'Jl. Melati 1' },
    }];
    await withGet({ results: wire }, async (paths) => {
      const found = await searchStudents('  budi s ');
      assert.deepStrictEqual(found, [student]);
      assert.deepStrictEqual(paths, ['/students/?status=ACTIVE&q=budi%20s']);
    });
  });

  it('makes no request for a query too short to be a search', async () => {
    await withGet({ results: [] }, async (paths) => {
      assert.deepStrictEqual(await searchStudents(' b '), []);
      assert.deepStrictEqual(paths, []);
    });
  });

  it('falls back to the NIS when the student has no person name', async () => {
    await withGet({ results: [{ id: 7, nis: '9', school: 1, person: null }] }, async () => {
      assert.strictEqual((await searchStudents('9x'))[0].name, '9');
    });
  });

  it('reads the health profile of the chosen student', async () => {
    await withGet({ id: 1, student: 42, allergies: ['Kacang'], has_medical_alert: true }, async (paths) => {
      assert.deepStrictEqual((await fetchStudentHealthProfile(42)).allergies, ['Kacang']);
      assert.deepStrictEqual(paths, ['/campus/students/42/health-profile/']);
    });
  });
});

describe('clinic officer: recording a visit', () => {
  it('needs a complaint and an outcome', () => {
    assert.deepStrictEqual(validateVisitDraft(draft(), TODAY), []);
    assert.deepStrictEqual(validateVisitDraft(draft({ complaint: '   ' }), TODAY), ['complaint']);
    assert.deepStrictEqual(validateVisitDraft(draft({ outcome: null }), TODAY), ['outcome']);
  });

  it('accepts a decimal comma for temperature and rejects implausible or malformed vitals', () => {
    assert.deepStrictEqual(validateVisitDraft(draft({ temperature: '37,5', pulse: '88' }), TODAY), []);
    assert.deepStrictEqual(validateVisitDraft(draft({ temperature: '375' }), TODAY), ['temperature']);
    assert.deepStrictEqual(validateVisitDraft(draft({ temperature: 'hangat' }), TODAY), ['temperature']);
    assert.deepStrictEqual(validateVisitDraft(draft({ pulse: '88.5' }), TODAY), ['pulse']);
    assert.deepStrictEqual(validateVisitDraft(draft({ pulse: '5' }), TODAY), ['pulse']);
  });

  it('medication needs a whole positive quantity within stock, and in-date stock', () => {
    const paracetamol = stock(3, 'Paracetamol', 10, 2, '2027-01-01');
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: paracetamol, quantity: '2' }), TODAY), []);
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: paracetamol, quantity: '' }), TODAY), ['quantity']);
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: paracetamol, quantity: '0' }), TODAY), ['quantity']);
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: paracetamol, quantity: '1,5' }), TODAY), ['quantity']);
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: paracetamol, quantity: '11' }), TODAY), ['quantity_over_stock']);
    const expired = stock(4, 'Antasida', 10, 2, '2026-09-18');
    assert.deepStrictEqual(validateVisitDraft(draft({ medication: expired, quantity: '1' }), TODAY), ['medication_expired']);
  });

  it('builds the request with vitals as numbers and no medication or consent fields when none is given', () => {
    const payload = buildVisitPayload(draft({ complaint: ' Pusing ', treatment: ' Istirahat ', temperature: '37,5', pulse: '88' }));
    assert.deepStrictEqual(payload, {
      student_id: 42, complaint: 'Pusing', treatment: 'Istirahat',
      vitals: { temperature_c: 37.5, pulse_bpm: 88 }, outcome: 'RETURNED_TO_CLASS',
    });
    assert.deepStrictEqual((buildVisitPayload(draft()) as any).vitals, {});
  });

  it('sends medication, quantity and the consent the officer confirmed', () => {
    const payload = buildVisitPayload(draft({
      medication: stock(3, 'Paracetamol', 10, 2, null), quantity: ' 2 ', consentConfirmed: true, consentNote: ' via telepon ',
    }));
    assert.strictEqual(payload.medication_id, 3);
    assert.strictEqual(payload.medication_quantity, 2);
    assert.strictEqual(payload.guardian_consent_confirmed, true);
    assert.strictEqual(payload.guardian_consent_note, 'via telepon');
  });

  it('offers only in-date stock of the student school, soonest expiry first', () => {
    const list = [
      stock(1, 'Zinc', 5, 1, '2027-06-01'),
      stock(2, 'Oralit', 5, 1, '2026-12-01'),
      stock(3, 'Habis', 0, 1, '2027-06-01'),
      stock(4, 'Kadaluarsa', 5, 1, '2026-01-01'),
      { ...stock(5, 'Sekolah lain', 5, 1, '2027-06-01'), school: 2 },
    ];
    assert.deepStrictEqual(dispensableStock(list, 1, TODAY).map((i) => i.name), ['Oralit', 'Zinc']);
  });

  it('posts to the clinic-visits endpoint and returns the saved visit', async () => {
    const original = apiClient.post;
    const calls: Array<{ path: string; body: any }> = [];
    apiClient.post = async (path: string, body?: any): Promise<any> => {
      calls.push({ path, body });
      return { data: visit(9), status: 201, headers: {} };
    };
    try {
      assert.strictEqual((await recordClinicVisit(draft())).id, 9);
      assert.strictEqual(calls[0].path, '/campus/clinic-visits/');
      assert.strictEqual(calls[0].body.student_id, 42);
    } finally {
      apiClient.post = original;
    }
  });

  it('treats a 4xx as a refusal with the server reason, and anything else as not knowing', () => {
    const refused = { response: { status: 400, data: ['Pemberian obat memerlukan persetujuan wali.'] } };
    assert.deepStrictEqual(classifySubmitFailure(refused), {
      kind: 'rejected', message: 'Pemberian obat memerlukan persetujuan wali.',
    });
    const fieldErrors = { response: { status: 400, data: { complaint: ['Wajib.'], vitals: { x: ['Salah.'] } } } };
    assert.deepStrictEqual(classifySubmitFailure(fieldErrors), { kind: 'rejected', message: 'Wajib. Salah.' });
    assert.deepStrictEqual(classifySubmitFailure({ response: { status: 500, data: {} } }), { kind: 'unknown' });
    assert.deepStrictEqual(classifySubmitFailure({ response: { status: 408 } }), { kind: 'unknown' });
    assert.deepStrictEqual(classifySubmitFailure(new TypeError('Network request failed')), { kind: 'unknown' });
  });

  it('keeps no offline queue: the visit is never handed to a queue or to storage', () => {
    const source = readFileSync(new URL('../src/services/clinicStaff.ts', import.meta.url), 'utf8');
    assert.doesNotMatch(source, /import[^;]*(offlineQueue|posOfflineQueue|sqlite)/i);
  });
});
