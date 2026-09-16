/**
 * Nutrition & Daily Intake Analytics Service (spec/07 WAL-024, spec/08 PAR-010, PAR-015).
 * 
 * Fetches student nutrition summary from /api/v1/students/:id/nutrition-summary?from&to,
 * provides local offline caching via Storage, and computes nutritional metrics.
 */
import { api } from './api.ts';
import { getItem, setItem } from './storage.ts';
import type {
  DailyNutritionBreakdown,
  NutritionPeriodFilter,
  StudentNutritionSummary,
} from '../types/index.ts';

export interface NutritionFetchResult {
  summary: StudentNutritionSummary;
  isOfflineCached: boolean;
  lastUpdated: string;
}

export interface SugarStatusResult {
  status: 'NORMAL' | 'ELEVATED' | 'HIGH';
  avgSugarPerDay: number;
  message: string;
}

export interface HealthyRatioResult {
  percentage: number;
  label: string;
}

export interface AllergenAlertInfo {
  allergen: string;
  name: string;
  warningText: string;
}

const NUTRITION_CACHE_PREFIX = 'educore_nutrition_cache';

/**
 * Format a Date object to YYYY-MM-DD string.
 */
export function formatDateYMD(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Calculate from and to date strings based on preset period filter.
 */
export function getDateRangeForPeriod(
  period: NutritionPeriodFilter,
  baseDate = new Date()
): { fromDate: string; toDate: string; daysCount: number } {
  const to = new Date(baseDate);
  const from = new Date(baseDate);

  if (period === 'TODAY') {
    return {
      fromDate: formatDateYMD(from),
      toDate: formatDateYMD(to),
      daysCount: 1,
    };
  }

  if (period === 'WEEK') {
    // 7 days inclusive
    from.setDate(from.getDate() - 6);
    return {
      fromDate: formatDateYMD(from),
      toDate: formatDateYMD(to),
      daysCount: 7,
    };
  }

  // MONTH: 30 days inclusive
  from.setDate(from.getDate() - 29);
  return {
    fromDate: formatDateYMD(from),
    toDate: formatDateYMD(to),
    daysCount: 30,
  };
}

/**
 * Fetch student nutrition summary with automatic offline caching (PAR-015).
 */
export async function fetchStudentNutritionSummary(
  studentId: number,
  fromDate: string,
  toDate: string,
  forceRefresh = false
): Promise<NutritionFetchResult> {
  const cacheKey = `${NUTRITION_CACHE_PREFIX}:${studentId}:${fromDate}:${toDate}`;

  try {
    const res = await api.get<StudentNutritionSummary>(
      `/students/${studentId}/nutrition-summary?from=${fromDate}&to=${toDate}`
    );

    const nowIso = new Date().toISOString();
    const cachePayload = {
      summary: res.data,
      lastUpdated: nowIso,
    };

    // Cache locally for offline resilience
    await setItem(cacheKey, JSON.stringify(cachePayload));

    return {
      summary: res.data,
      isOfflineCached: false,
      lastUpdated: nowIso,
    };
  } catch (networkError) {
    // Attempt to load from offline cache
    const cachedRaw = await getItem(cacheKey);
    if (cachedRaw) {
      try {
        const parsed = JSON.parse(cachedRaw);
        if (parsed && parsed.summary) {
          return {
            summary: parsed.summary,
            isOfflineCached: true,
            lastUpdated: parsed.lastUpdated || new Date().toISOString(),
          };
        }
      } catch {
        // Cache parse error, proceed to throw original
      }
    }

    throw networkError;
  }
}

/**
 * Calculate average calories per day across active breakdown days or date window.
 */
export function calculateDailyAverageCalories(
  summary: StudentNutritionSummary,
  totalWindowDays = 1
): number {
  if (!summary.total_calories || summary.total_calories <= 0) {
    return 0;
  }
  const denominator = Math.max(1, totalWindowDays);
  return Math.round(summary.total_calories / denominator);
}

/**
 * Calculate sugar consumption health status based on standard Kemenkes guidelines (25g/day).
 */
export function calculateSugarStatus(
  totalSugarG: number | string,
  daysCount = 1
): SugarStatusResult {
  const sugarNum = typeof totalSugarG === 'string' ? parseFloat(totalSugarG) || 0 : totalSugarG;
  const days = Math.max(1, daysCount);
  const avg = Number((sugarNum / days).toFixed(1));

  if (avg <= 25) {
    return {
      status: 'NORMAL',
      avgSugarPerDay: avg,
      message: 'Asupan gula dalam batas wajar rekomendasi harian.',
    };
  }

  if (avg <= 40) {
    return {
      status: 'ELEVATED',
      avgSugarPerDay: avg,
      message: 'Asupan gula sedikit di atas anjuran (maks. 25 g/hari).',
    };
  }

  return {
    status: 'HIGH',
    avgSugarPerDay: avg,
    message: 'Perhatian: Asupan gula tinggi, melebihi anjuran harian.',
  };
}

/**
 * Calculate ratio of healthy food choices vs total items consumed.
 */
export function calculateHealthyRatio(
  healthyCount: number,
  totalItems: number
): HealthyRatioResult {
  if (totalItems <= 0) {
    return {
      percentage: 0,
      label: 'Belum ada pembelian',
    };
  }

  const pct = Math.min(100, Math.round((healthyCount / totalItems) * 100));
  return {
    percentage: pct,
    label: `${healthyCount} dari ${totalItems} item sehat (${pct}%)`,
  };
}

/**
 * Map detected allergens to user-friendly Indonesian labels and descriptions.
 */
const ALLERGEN_DICT: Record<string, { name: string; warningText: string }> = {
  kacang: {
    name: 'Kacang (Peanuts / Tree Nuts)',
    warningText: 'Mengandung kacang-kacangan. Waspadai reaksi gatal atau alergi.',
  },
  susu: {
    name: 'Susu / Laktosa (Dairy)',
    warningText: 'Mengandung produk susu atau olahan laktosa.',
  },
  gluten: {
    name: 'Gluten / Gandum (Wheat)',
    warningText: 'Mengandung tepung terigu / gandum berserat gluten.',
  },
  telur: {
    name: 'Telur (Eggs)',
    warningText: 'Mengandung bahan olahan telur.',
  },
  seafood: {
    name: 'Makanan Laut / Udang (Seafood)',
    warningText: 'Mengandung udang, kepiting, atau sari ikan laut.',
  },
  udang: {
    name: 'Udang / Krustasea (Crustaceans)',
    warningText: 'Mengandung olahan udang atau hewan laut krustasea.',
  },
  kedelai: {
    name: 'Kedelai / Soy',
    warningText: 'Mengandung sari kedelai atau produk fermentasi kedelai.',
  },
};

export function getAllergenAlertDetails(allergens: string[]): AllergenAlertInfo[] {
  if (!allergens || allergens.length === 0) {
    return [];
  }

  return allergens.map((key) => {
    const cleanKey = key.trim().toLowerCase();
    const entry = ALLERGEN_DICT[cleanKey];
    if (entry) {
      return {
        allergen: cleanKey,
        name: entry.name,
        warningText: entry.warningText,
      };
    }
    return {
      allergen: cleanKey,
      name: cleanKey.charAt(0).toUpperCase() + cleanKey.slice(1),
      warningText: `Mengandung bahan ${cleanKey}.`,
    };
  });
}
