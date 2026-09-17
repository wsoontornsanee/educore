/**
 * Absence Request service for parent mobile client (PAR-011, ATT-002, spec/08 §2).
 */
import { apiClient } from './api.ts';
import { cacheGet, cacheSet } from './storage.ts';
import type {
  AbsenceRequestItem,
  CreateAbsenceRequestPayload,
} from '../types/index.ts';

export const MAX_ABSENCE_ATTACHMENT_BYTES = 1024 * 1024; // 1MB per PAR-011 for 3G

export function getAbsenceCacheKey(studentId: number): string {
  return `educore_parent_absence_requests_${studentId}`;
}

export function validateAbsenceAttachment(fileSize?: number | null): { valid: boolean; error?: string } {
  if (fileSize != null && fileSize > MAX_ABSENCE_ATTACHMENT_BYTES) {
    return {
      valid: false,
      error: 'Ukuran lampiran maksimal 1MB (PAR-011). Silakan pilih foto dengan ukuran lebih kecil.',
    };
  }
  return { valid: true };
}

export async function fetchAbsenceRequests(studentId: number): Promise<AbsenceRequestItem[]> {
  const cacheKey = getAbsenceCacheKey(studentId);
  try {
    const response = await apiClient.get<AbsenceRequestItem[]>(
      `/attendance/students/${studentId}/absence-requests/`
    );
    const data = Array.isArray(response.data) ? response.data : ((response.data as any).results || []);
    await cacheSet(cacheKey, data);
    return data;
  } catch (error) {
    const cached = await cacheGet<AbsenceRequestItem[]>(cacheKey);
    if (cached && Array.isArray(cached.value)) {
      return cached.value;
    }
    throw error;
  }
}

export async function submitAbsenceRequest(
  payload: CreateAbsenceRequestPayload
): Promise<AbsenceRequestItem> {
  const validation = validateAbsenceAttachment(payload.attachmentSize);
  if (!validation.valid) {
    throw new Error(validation.error);
  }

  let response;
  if (payload.attachmentUri) {
    const formData = new FormData();
    formData.append('date_from', payload.date_from);
    formData.append('date_to', payload.date_to);
    formData.append('type', payload.type);
    formData.append('reason', payload.reason);
    formData.append('attachment', {
      uri: payload.attachmentUri,
      name: payload.attachmentName || 'surat_keterangan.jpg',
      type: payload.attachmentType || 'image/jpeg',
    } as any);

    response = await apiClient.post<AbsenceRequestItem>(
      `/attendance/students/${payload.student_id}/absence-requests/`,
      formData
    );
  } else {
    response = await apiClient.post<AbsenceRequestItem>(
      `/attendance/students/${payload.student_id}/absence-requests/`,
      {
        date_from: payload.date_from,
        date_to: payload.date_to,
        type: payload.type,
        reason: payload.reason,
      }
    );
  }

  const newItem = response.data;

  // Update local cache if available
  const cacheKey = getAbsenceCacheKey(payload.student_id);
  const cached = await cacheGet<AbsenceRequestItem[]>(cacheKey);
  if (cached && Array.isArray(cached.value)) {
    await cacheSet(cacheKey, [newItem, ...cached.value]);
  } else {
    await cacheSet(cacheKey, [newItem]);
  }

  return newItem;
}
