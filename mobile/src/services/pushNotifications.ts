/**
 * Push Notifications Registration and Listener (docs/frontend-plan.md §4).
 * 
 * Interacts with /api/v1/me/push-tokens/ to register device token
 * and handles incoming SUBSTITUTE_ASSIGNED pushes.
 */
import { apiClient } from './api.ts';
import { getItem, setItem } from './storage.ts';

const TOKEN_KEY = 'registered_push_token';

// `react-native` and `expo-notifications` ship Flow/Metro-only syntax that
// Node's native TS loader can't parse, so both are lazily required behind a
// try/catch (same pattern already used across this file) rather than
// statically imported. This keeps the module loadable under plain `node
// --test` for unit tests, while Metro/Expo still resolve them normally at
// runtime on-device.
let Platform: any = { OS: 'ios' };
try {
  Platform = require('react-native').Platform;
} catch {
  // Headless test fallback
}

let Notifications: any = null;
try {
  Notifications = require('expo-notifications');
} catch {
  // Headless test fallback
}

/**
 * Test-only seam: lets unit tests inject a mock `expo-notifications` module
 * directly, instead of intercepting the `require('expo-notifications')` call
 * above. A `Module.prototype.require`/`Module._load` monkey-patch was tried
 * first (see mobile/__tests__/pushNotifications.test.ts for details) but does
 * not reliably intercept that call in this repo: mobile/package.json sets
 * `"type": "module"`, so this file loads as an ES module, and when a test's
 * `require(esm)` interop pulls it in, this module's *own* internal
 * `require('expo-notifications')` call does not route through the same
 * `Module._load` hook the outer require does. Not exported from the public
 * barrel; only used by tests that import it directly by path.
 */
export function __setNotificationsModuleForTesting(mockModule: any): void {
  Notifications = mockModule;
}

export interface PushRegistrationResult {
  success: boolean;
  token?: string;
  error?: string;
}

export async function registerForPushNotificationsAsync(): Promise<PushRegistrationResult> {
  if (!Notifications || typeof Notifications.getPermissionsAsync !== 'function') {
    return { success: false, error: 'Notifications module not available' };
  }

  try {
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;

    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }

    if (finalStatus !== 'granted') {
      return { success: false, error: 'Izin notifikasi tidak diberikan pengguna.' };
    }

    const tokenResponse = await Notifications.getExpoPushTokenAsync();
    const token = tokenResponse.data;

    // Register with backend
    const platform = Platform.OS === 'ios' ? 'ios' : 'android';
    await apiClient.post('/me/push-tokens/', {
      token,
      platform,
      device_name: `${Platform.OS.toUpperCase()} Device`,
    });

    await setItem(TOKEN_KEY, token);
    return { success: true, token };
  } catch (err: any) {
    return {
      success: false,
      error: err.response?.data?.error || err.message || 'Gagal mendaftarkan push token.',
    };
  }
}

export async function deactivatePushTokenAsync(): Promise<void> {
  const token = await getItem(TOKEN_KEY);
  if (!token) return;

  try {
    await apiClient.post('/me/push-tokens/deactivate/', { token });
  } catch {
    // Best effort on logout
  }
}

export function subscribeToNotificationReceived(
  onNotification: (notification: any) => void
): { remove: () => void } {
  if (!Notifications || typeof Notifications.addNotificationReceivedListener !== 'function') {
    return { remove: () => {} };
  }

  const subscription = Notifications.addNotificationReceivedListener((notification: any) => {
    onNotification(notification);
  });

  return subscription;
}

export function subscribeToNotificationResponseReceived(
  onData: (data: any) => void
): { remove: () => void } {
  if (!Notifications || typeof Notifications.addNotificationResponseReceivedListener !== 'function') {
    return { remove: () => {} };
  }

  const subscription = Notifications.addNotificationResponseReceivedListener((response: any) => {
    const data = response?.notification?.request?.content?.data;
    if (data) {
      onData(data);
    }
  });

  return subscription;
}

export async function getInitialNotificationResponse(): Promise<any | null> {
  if (!Notifications || typeof Notifications.getLastNotificationResponseAsync !== 'function') {
    return null;
  }

  try {
    const response = await Notifications.getLastNotificationResponseAsync();
    return response?.notification?.request?.content?.data ?? null;
  } catch {
    return null;
  }
}
