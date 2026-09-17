/**
 * Parent Nutrition Service and Analytics Unit Tests (spec/07 WAL-024, spec/08 PAR-010, PAR-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  calculateDailyAverageCalories,
  calculateHealthyRatio,
  calculateSugarStatus,
  fetchStudentNutritionSummary,
  formatDateYMD,
  getAllergenAlertDetails,
  getDateRangeForPeriod,
} from '../src/services/nutrition.ts';
import { api } from '../src/services/api.ts';
import { setItem, getItem } from '../src/services/storage.ts';
import type { StudentNutritionSummary } from '../src/types/index.ts';

describe('Parent Nutrition Service & Analytics', () => {
  const sampleSummary: StudentNutritionSummary = {
    student_id: 101,
    from_date: '2026-09-10',
    to_date: '2026-09-16',
    total_calories: 2100,
    total_sugar_g: '42.50',
    total_items: 7,
    healthy_items_count: 5,
    allergens: ['kacang', 'susu'],
    daily_breakdown: [
      {
        date: '2026-09-10',
        total_calories: 300,
        total_sugar_g: '6.00',
        items_count: 1,
        healthy_count: 1,
      },
      {
        date: '2026-09-11',
        total_calories: 350,
        total_sugar_g: '7.50',
        items_count: 1,
        healthy_count: 1,
      },
      {
        date: '2026-09-12',
        total_calories: 450,
        total_sugar_g: '10.00',
        items_count: 2,
        healthy_count: 1,
      },
      {
        date: '2026-09-15',
        total_calories: 500,
        total_sugar_g: '9.00',
        items_count: 1,
        healthy_count: 1,
      },
      {
        date: '2026-09-16',
        total_calories: 500,
        total_sugar_g: '10.00',
        items_count: 2,
        healthy_count: 1,
      },
    ],
    items: [
      {
        sku: 'BUAH-01',
        name: 'Apel Segar',
        qty: 1,
        unit_price: '5000.00',
        calories: 95,
        sugar_g: '19.00',
        allergens: [],
        is_healthy: true,
        occurred_at: '2026-09-16T10:00:00Z',
      },
      {
        sku: 'SUSU-01',
        name: 'Susu UHT Cokelat',
        qty: 1,
        unit_price: '7000.00',
        calories: 150,
        sugar_g: '18.00',
        allergens: ['susu'],
        is_healthy: true,
        occurred_at: '2026-09-16T12:30:00Z',
      },
    ],
  };

  describe('getDateRangeForPeriod', () => {
    it('returns single day range for TODAY', () => {
      const fixedDate = new Date('2026-09-16T10:00:00Z');
      const { fromDate, toDate, daysCount } = getDateRangeForPeriod('TODAY', fixedDate);

      assert.strictEqual(fromDate, '2026-09-16');
      assert.strictEqual(toDate, '2026-09-16');
      assert.strictEqual(daysCount, 1);
    });

    it('returns 7-day inclusive range for WEEK', () => {
      const fixedDate = new Date('2026-09-16T10:00:00Z');
      const { fromDate, toDate, daysCount } = getDateRangeForPeriod('WEEK', fixedDate);

      assert.strictEqual(fromDate, '2026-09-10');
      assert.strictEqual(toDate, '2026-09-16');
      assert.strictEqual(daysCount, 7);
    });

    it('returns 30-day inclusive range for MONTH', () => {
      const fixedDate = new Date('2026-09-16T10:00:00Z');
      const { fromDate, toDate, daysCount } = getDateRangeForPeriod('MONTH', fixedDate);

      assert.strictEqual(fromDate, '2026-08-18');
      assert.strictEqual(toDate, '2026-09-16');
      assert.strictEqual(daysCount, 30);
    });
  });

  describe('calculateDailyAverageCalories', () => {
    it('computes daily average calories accurately across window days', () => {
      const avg7Days = calculateDailyAverageCalories(sampleSummary, 7);
      assert.strictEqual(avg7Days, 300); // 2100 / 7

      const avg30Days = calculateDailyAverageCalories(sampleSummary, 30);
      assert.strictEqual(avg30Days, 70); // 2100 / 30 = 70
    });

    it('returns 0 when total calories is zero', () => {
      const emptySummary = { ...sampleSummary, total_calories: 0 };
      assert.strictEqual(calculateDailyAverageCalories(emptySummary, 7), 0);
    });
  });

  describe('calculateSugarStatus', () => {
    it('classifies normal sugar intake under 25g/day', () => {
      // 20g over 1 day = 20g/day
      const res = calculateSugarStatus('20.00', 1);
      assert.strictEqual(res.status, 'NORMAL');
      assert.strictEqual(res.avgSugarPerDay, 20.0);
    });

    it('classifies elevated sugar intake between 25g and 40g/day', () => {
      // 210g over 7 days = 30g/day
      const res = calculateSugarStatus(210, 7);
      assert.strictEqual(res.status, 'ELEVATED');
      assert.strictEqual(res.avgSugarPerDay, 30.0);
    });

    it('classifies high sugar intake above 40g/day with warning alert', () => {
      // 90g over 2 days = 45g/day
      const res = calculateSugarStatus('90.00', 2);
      assert.strictEqual(res.status, 'HIGH');
      assert.strictEqual(res.avgSugarPerDay, 45.0);
    });
  });

  describe('calculateHealthyRatio', () => {
    it('calculates healthy food percentage and descriptive text', () => {
      const res = calculateHealthyRatio(5, 7);
      assert.strictEqual(res.percentage, 71);
      assert.ok(res.label.includes('5 dari 7 item sehat'));
    });

    it('handles zero items safely without divide-by-zero error', () => {
      const res = calculateHealthyRatio(0, 0);
      assert.strictEqual(res.percentage, 0);
      assert.strictEqual(res.label, 'Belum ada pembelian');
    });
  });

  describe('getAllergenAlertDetails', () => {
    it('maps recognized allergens to Indonesian labels and safety warnings', () => {
      const alerts = getAllergenAlertDetails(['kacang', 'susu', 'gluten']);
      assert.strictEqual(alerts.length, 3);
      assert.strictEqual(alerts[0].allergen, 'kacang');
      assert.ok(alerts[0].name.includes('Kacang'));
      assert.ok(alerts[1].name.includes('Susu'));
      assert.ok(alerts[2].name.includes('Gluten'));
    });

    it('gracefully handles custom or unknown allergens', () => {
      const alerts = getAllergenAlertDetails(['wijen']);
      assert.strictEqual(alerts.length, 1);
      assert.strictEqual(alerts[0].allergen, 'wijen');
      assert.strictEqual(alerts[0].name, 'Wijen');
    });

    it('returns empty array when no allergens present', () => {
      assert.deepStrictEqual(getAllergenAlertDetails([]), []);
    });
  });

  describe('fetchStudentNutritionSummary & Offline Caching (PAR-015)', () => {
    it('fetches online data and saves to local cache', async () => {
      // Mock api.get
      const originalGet = api.get;
      api.get = async <T>(_url: string): Promise<{ data: T; status: number; headers: Record<string, string> }> => {
        return { data: sampleSummary as unknown as T, status: 200, headers: {} };
      };

      try {
        const result = await fetchStudentNutritionSummary(101, '2026-09-10', '2026-09-16');
        assert.strictEqual(result.isOfflineCached, false);
        assert.strictEqual(result.summary.total_calories, 2100);
        assert.strictEqual(result.summary.student_id, 101);

        // Verify it was stored in cache
        const cacheKey = `educore_nutrition_cache:101:2026-09-10:2026-09-16`;
        const cachedRaw = await getItem(cacheKey);
        assert.ok(cachedRaw !== null);
        const parsed = JSON.parse(cachedRaw!);
        assert.strictEqual(parsed.summary.total_calories, 2100);
      } finally {
        api.get = originalGet;
      }
    });

    it('falls back to offline cached data when API request fails (PAR-015)', async () => {
      // First seed the cache
      const cacheKey = `educore_nutrition_cache:102:2026-09-01:2026-09-07`;
      const cachedTime = '2026-09-07T12:00:00Z';
      await setItem(
        cacheKey,
        JSON.stringify({
          summary: { ...sampleSummary, student_id: 102, total_calories: 1850 },
          lastUpdated: cachedTime,
        })
      );

      // Mock api.get to reject with network error
      const originalGet = api.get;
      api.get = async <T>(_url: string): Promise<{ data: T; status: number; headers: Record<string, string> }> => {
        throw new Error('Network request failed: Offline');
      };

      try {
        const result = await fetchStudentNutritionSummary(102, '2026-09-01', '2026-09-07');
        assert.strictEqual(result.isOfflineCached, true);
        assert.strictEqual(result.summary.student_id, 102);
        assert.strictEqual(result.summary.total_calories, 1850);
        assert.strictEqual(result.lastUpdated, cachedTime);
      } finally {
        api.get = originalGet;
      }
    });

    it('rethrows network error if no offline cache is available', async () => {
      const originalGet = api.get;
      api.get = async <T>(_url: string): Promise<{ data: T; status: number; headers: Record<string, string> }> => {
        throw new Error('Server 500: Database unavailable');
      };

      try {
        await assert.rejects(
          async () => {
            await fetchStudentNutritionSummary(999, '2026-01-01', '2026-01-07');
          },
          { message: 'Server 500: Database unavailable' }
        );
      } finally {
        api.get = originalGet;
      }
    });
  });
});
