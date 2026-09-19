/**
 * Clinic officer (Petugas UKS) read-only data layer: recent clinic visits and medication stock.
 *
 * Scoping is server-side: the same clinic.read-gated endpoints the web console uses, narrowed to the caller's
 * own schools (ClinicVisitViewSet / MedicationStockViewSet get_queryset).
 *
 * Visit notes (complaint, treatment) are medical data. Unlike the parent client (services/clinic.ts), nothing
 * here is written to on-device storage: offline, the screens show an error, never a stale copy of health notes.
 */
import { apiClient } from './api.ts';
import type { ClinicVisitItem, MedicationStockItem } from '../types/index.ts';

type Paged<T> = { results: T[] } | T[];

function items<T>(data: Paged<T> | undefined): T[] {
  if (Array.isArray(data)) return data;
  return data?.results ?? [];
}

/** Newest first; the first page only (the server page size), which is what an officer scans. */
export async function fetchRecentClinicVisits(): Promise<ClinicVisitItem[]> {
  const response = await apiClient.get<Paged<ClinicVisitItem>>('/campus/clinic-visits/');
  return items(response.data);
}

export async function fetchMedicationStock(): Promise<MedicationStockItem[]> {
  const response = await apiClient.get<Paged<MedicationStockItem>>('/campus/medication-stock/');
  return items(response.data);
}

export function isLowStock(item: MedicationStockItem): boolean {
  return item.quantity <= item.reorder_level;
}

/** Expiry is a calendar date; expired means before today, not merely today. */
export function isExpired(item: MedicationStockItem, today: string): boolean {
  return !!item.expiry_date && item.expiry_date < today;
}

/** Items needing attention first (low stock or expired), then by name. Does not mutate its input. */
export function sortStockForAttention(list: MedicationStockItem[], today: string): MedicationStockItem[] {
  const needsAttention = (i: MedicationStockItem) => (isLowStock(i) || isExpired(i, today) ? 0 : 1);
  return [...list].sort((a, b) => needsAttention(a) - needsAttention(b) || a.name.localeCompare(b.name));
}
