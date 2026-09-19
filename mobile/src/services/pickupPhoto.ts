/**
 * Photo of the person a guardian authorises to collect a child (spec/05 ATT-015/ATT-016). Three steps against the
 * existing two-phase upload (apps/core): ask for a signed URL, PUT the bytes straight to storage, confirm. The
 * returned `key` goes on the authorisation as `photo_key`; the server only accepts the uploader's own confirmed
 * `pickup_photo` file (apps/attendance/pickup.py).
 *
 * This is a photo of a third party: it is never logged, cached or written anywhere by this module, and the
 * caller deletes the local file once the authorisation is created.
 */
import { apiClient } from './api.ts';

export const PICKUP_PHOTO_PURPOSE = 'pickup_photo';
/** Mirrors PURPOSE_RULES['pickup_photo'] in apps/core/services.py; the server enforces it, this fails fast. */
export const PICKUP_PHOTO_MAX_BYTES = 2 * 1024 * 1024;

export interface PickedPhoto {
  uri: string;
  contentType: 'image/jpeg' | 'image/png';
}

export type PickupPhotoFailure = 'TOO_LARGE' | 'READ_FAILED' | 'UPLOAD_FAILED';

/** Carries only a code: never the file, its uri or the signed URL. */
export class PickupPhotoError extends Error {
  readonly code: PickupPhotoFailure;

  constructor(code: PickupPhotoFailure) {
    super(code);
    this.name = 'PickupPhotoError';
    this.code = code;
  }
}

interface InitiatedUpload {
  id: number;
  key: string;
  upload_url: string;
}

/** Uploads the photo and returns its storage key. `fetchImpl` is injectable for tests. */
export async function uploadPickupPhoto(photo: PickedPhoto, fetchImpl: typeof fetch = fetch): Promise<string> {
  let blob: Blob;
  try {
    blob = await (await fetchImpl(photo.uri)).blob();
  } catch {
    throw new PickupPhotoError('READ_FAILED');
  }
  if (blob.size > PICKUP_PHOTO_MAX_BYTES) throw new PickupPhotoError('TOO_LARGE');

  const extension = photo.contentType === 'image/png' ? 'png' : 'jpg';
  const initiated = await apiClient.post<InitiatedUpload>('/files/uploads/', {
    purpose: PICKUP_PHOTO_PURPOSE,
    filename: `pickup.${extension}`,
    content_type: photo.contentType,
    size: blob.size,
  });

  // The signed URL is not an EduCore endpoint: no bearer token, and the Content-Type must match what was signed.
  let stored: Response;
  try {
    stored = await fetchImpl(initiated.data.upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': photo.contentType },
      body: blob,
    });
  } catch {
    throw new PickupPhotoError('UPLOAD_FAILED');
  }
  if (!stored.ok) throw new PickupPhotoError('UPLOAD_FAILED');

  await apiClient.post(`/files/uploads/${initiated.data.id}/confirm/`, {});
  return initiated.data.key;
}
