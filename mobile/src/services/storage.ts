/**
 * Secure credentials and local settings storage abstraction.
 */
import type { UserProfile } from '../types/index.ts';

// In-memory fallback for test and non-native environments
const memoryStore = new Map<string, string>();

let SecureStore: any = null;
try {
  SecureStore = require('expo-secure-store');
} catch {
  // Expo SecureStore not available in standard node test runner
}

export const StorageKeys = {
  ACCESS_TOKEN: 'educore_access_token',
  REFRESH_TOKEN: 'educore_refresh_token',
  USER_PROFILE: 'educore_user_profile',
  PENDING_PUSH_TOKEN: 'educore_push_token',
  LAST_CHILD_ID: 'educore_parent_last_child_id',
};

export async function setItem(key: string, value: string): Promise<void> {
  if (SecureStore && typeof SecureStore.setItemAsync === 'function') {
    try {
      await SecureStore.setItemAsync(key, value);
      return;
    } catch {
      // Fallback if secure store fails
    }
  }
  memoryStore.set(key, value);
}

export async function getItem(key: string): Promise<string | null> {
  if (SecureStore && typeof SecureStore.getItemAsync === 'function') {
    try {
      const val = await SecureStore.getItemAsync(key);
      if (val !== null) return val;
    } catch {
      // Fallback
    }
  }
  return memoryStore.get(key) ?? null;
}

export async function removeItem(key: string): Promise<void> {
  if (SecureStore && typeof SecureStore.deleteItemAsync === 'function') {
    try {
      await SecureStore.deleteItemAsync(key);
    } catch {
      // Fallback
    }
  }
  memoryStore.delete(key);
}

export async function saveTokens(access: string, refresh: string): Promise<void> {
  await setItem(StorageKeys.ACCESS_TOKEN, access);
  await setItem(StorageKeys.REFRESH_TOKEN, refresh);
}

export async function getTokens(): Promise<{ access: string | null; refresh: string | null }> {
  const access = await getItem(StorageKeys.ACCESS_TOKEN);
  const refresh = await getItem(StorageKeys.REFRESH_TOKEN);
  return { access, refresh };
}

export async function clearAuth(): Promise<void> {
  await removeItem(StorageKeys.ACCESS_TOKEN);
  await removeItem(StorageKeys.REFRESH_TOKEN);
  await removeItem(StorageKeys.USER_PROFILE);
}

export async function saveUserProfile(user: UserProfile): Promise<void> {
  await setItem(StorageKeys.USER_PROFILE, JSON.stringify(user));
}

export async function getUserProfile(): Promise<UserProfile | null> {
  const raw = await getItem(StorageKeys.USER_PROFILE);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserProfile;
  } catch {
    return null;
  }
}

export async function cacheSet<T>(key: string, value: T): Promise<void> {
  await setItem(key, JSON.stringify({ value, cachedAt: new Date().toISOString() }));
}

export async function cacheGet<T>(key: string): Promise<{ value: T; cachedAt: string } | null> {
  const raw = await getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as { value: T; cachedAt: string };
  } catch {
    return null;
  }
}

export async function saveLastChildId(studentId: number): Promise<void> {
  await setItem(StorageKeys.LAST_CHILD_ID, String(studentId));
}

export async function getLastChildId(): Promise<number | null> {
  const raw = await getItem(StorageKeys.LAST_CHILD_ID);
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}
