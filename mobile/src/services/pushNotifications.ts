/**
 * Push Notifications Registration and Listener (docs/frontend-plan.md §4).
 * 
 * Interacts with /api/v1/me/push-tokens/ to register device token
 * and handles incoming SUBSTITUTE_ASSIGNED pushes.
 */
import { Platform } from 'react-native';
import { apiClient } from './api';
import { getItem, setItem } from './storage';

const TOKEN_KEY = 'registered_push_token';

let Notifications: any = null;
try {
  Notifications = require('expo-notifications');
} catch {
  // Headless test fallback
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
