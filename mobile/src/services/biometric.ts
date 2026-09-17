/**
 * Biometric unlock service — PAR-018.
 *
 * Lazy-requires expo-local-authentication following the same codebase pattern
 * as expo-secure-store and expo-notifications (try/catch, in-memory fallback).
 *
 * In headless test environments (Node test runner), LocalAuthentication is null
 * and all functions return safe no-op/false values.
 */

let LocalAuthentication: any = null;
try {
  LocalAuthentication = require('expo-local-authentication');
} catch {
  // Not available in headless/test environment
}

export type BiometricAuthResult = { success: true } | { success: false; error: string };

/**
 * Check if biometric authentication is available on this device.
 * Returns false in headless environments (no hardware / no enrollment).
 */
export async function isBiometricAvailable(): Promise<boolean> {
  if (!LocalAuthentication) return false;
  try {
    const hasHardware = await LocalAuthentication.hasHardwareAsync();
    if (!hasHardware) return false;
    const isEnrolled = await LocalAuthentication.isEnrolledAsync();
    return isEnrolled;
  } catch {
    return false;
  }
}

/**
 * Check if biometric hardware exists (even if not enrolled).
 * Used to decide whether to show the toggle at all.
 */
export async function hasBiometricHardware(): Promise<boolean> {
  if (!LocalAuthentication) return false;
  try {
    return await LocalAuthentication.hasHardwareAsync();
  } catch {
    return false;
  }
}

/**
 * Get a human-readable label for the available biometric type.
 */
export async function getBiometricTypeLabel(
  locale: 'id-ID' | 'en-US' = 'id-ID'
): Promise<string> {
  if (!LocalAuthentication) return locale === 'id-ID' ? 'Biometrik' : 'Biometric';
  try {
    const types: number[] = await LocalAuthentication.supportedAuthenticationTypesAsync();
    // AuthenticationType: 1 = FINGERPRINT, 2 = FACIAL_RECOGNITION, 3 = IRIS
    if (types.includes(2)) {
      return 'Face ID';
    }
    if (types.includes(1)) {
      return locale === 'id-ID' ? 'Sidik Jari' : 'Fingerprint';
    }
    return locale === 'id-ID' ? 'Biometrik' : 'Biometric';
  } catch {
    return locale === 'id-ID' ? 'Biometrik' : 'Biometric';
  }
}

/**
 * Prompt the user for biometric authentication.
 *
 * @param promptMessage - Shown in the system biometric prompt dialog
 * @returns BiometricAuthResult
 */
export async function authenticateBiometric(promptMessage: string): Promise<BiometricAuthResult> {
  if (!LocalAuthentication) {
    return { success: false, error: 'Biometric module not available' };
  }
  try {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage,
      cancelLabel: 'Batal',
      disableDeviceFallback: false,
    });
    if (result.success) {
      return { success: true };
    }
    return {
      success: false,
      error: result.error ?? 'authentication_failed',
    };
  } catch (err: any) {
    return { success: false, error: err?.message ?? 'unknown_error' };
  }
}
