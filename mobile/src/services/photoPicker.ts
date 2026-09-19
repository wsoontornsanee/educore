/**
 * Camera / library capture for the pickup photo. The only module that touches the native picker, so the upload
 * logic in pickupPhoto.ts stays testable under node. The photo is squared and shrunk to a small JPEG here, which
 * keeps it far below the 2 MB server limit whatever the phone's camera produces.
 */
import * as FileSystem from 'expo-file-system';
import * as ImageManipulator from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import type { PickedPhoto } from './pickupPhoto.ts';

export type PhotoSource = 'camera' | 'library';
/** `null` when the guardian cancelled; a failure reason otherwise. */
export type PhotoPickResult = { photo: PickedPhoto } | { failure: 'DENIED' | 'UNAVAILABLE' } | null;

const PHOTO_SIDE_PX = 800;
const PHOTO_JPEG_QUALITY = 0.7;

export async function pickPickupPhoto(source: PhotoSource): Promise<PhotoPickResult> {
  try {
    if (source === 'camera') {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) return { failure: 'DENIED' };
    }
    const options: ImagePicker.ImagePickerOptions = {
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true,
      aspect: [1, 1],
      quality: 1,
    };
    const picked = source === 'camera'
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync(options);
    if (picked.canceled || !picked.assets?.[0]) return null;

    const shrunk = await ImageManipulator.manipulateAsync(
      picked.assets[0].uri,
      [{ resize: { width: PHOTO_SIDE_PX } }],
      { compress: PHOTO_JPEG_QUALITY, format: ImageManipulator.SaveFormat.JPEG },
    );
    return { photo: { uri: shrunk.uri, contentType: 'image/jpeg' } };
  } catch {
    return { failure: 'UNAVAILABLE' }; // e.g. no camera (simulator), or the picker failed to start
  }
}

/** Best-effort removal of the local copy: it is a photo of a third party and must not linger in the app cache. */
export async function discardPickupPhoto(photo: PickedPhoto): Promise<void> {
  try {
    await FileSystem.deleteAsync(photo.uri, { idempotent: true });
  } catch {
    // the OS clears the cache directory anyway
  }
}
