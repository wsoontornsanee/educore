/**
 * Clinic Visit History & Health Profile service for parent mobile client
 * (spec/10 §3 LIF-006, Notion "Parent App: Guardian Read Scoping for Clinic
 * Visit History"). Backend guardian-scoping is enforced server-side via
 * get_guardian_student_ids/can_guardian_access_student — this client just
 * calls the same clinic.read-gated endpoints any other caller would.
 */
import { apiClient } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type { ClinicVisitItem, HealthProfileItem } from '../types/index.ts';

export function getClinicVisitsCacheKey(studentId: number): string {
  return `educore_parent_clinic_visits_${studentId}`;
}

export function getHealthProfileCacheKey(studentId: number): string {
  return `educore_parent_health_profile_${studentId}`;
}

export async function fetchClinicVisits(studentId: number): Promise<ClinicVisitItem[]> {
  const cacheKey = getClinicVisitsCacheKey(studentId);
  try {
    const response = await apiClient.get<{ results: ClinicVisitItem[] } | ClinicVisitItem[]>(
      `/campus/clinic-visits/?student_id=${studentId}`
    );
    const data = response.data as any;
    const visits: ClinicVisitItem[] = Array.isArray(data) ? data : data.results || [];
    await cacheSet(cacheKey, visits);
    return visits;
  } catch (error) {
    const cached = await cacheGet<ClinicVisitItem[]>(cacheKey);
    if (cached && Array.isArray(cached.value)) {
      return cached.value;
    }
    throw error;
  }
}

export async function fetchHealthProfile(studentId: number): Promise<HealthProfileItem> {
  const cacheKey = getHealthProfileCacheKey(studentId);
  try {
    const response = await apiClient.get<HealthProfileItem>(
      `/campus/students/${studentId}/health-profile/`
    );
    await cacheSet(cacheKey, response.data);
    return response.data;
  } catch (error) {
    const cached = await cacheGet<HealthProfileItem>(cacheKey);
    if (cached && cached.value) {
      return cached.value;
    }
    throw error;
  }
}
