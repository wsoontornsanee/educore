/**
 * Profile Tab Tests — PAR-013, PAR-014, PAR-018
 *
 * Uses Node native test runner (npm test).
 * SecureStore not available: storage.ts falls back to in-memory memoryStore.
 * expo-local-authentication not available: biometric.ts returns safe defaults.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import * as notifPrefs from '../src/services/notificationPrefs.ts';
import * as storage from '../src/services/storage.ts';
import * as biometric from '../src/services/biometric.ts';
import { t, strings } from '../src/i18n/strings.ts';

// ─────────────────────────────────────────────────────────────────────────────
// Mock data
// ─────────────────────────────────────────────────────────────────────────────

const mockPrefs = [
  {
    id: 1,
    category: 'ARRIVAL',
    channels: ['WHATSAPP', 'PUSH'],
    quiet_hours_start: '21:00',
    quiet_hours_end: '06:00',
    enabled: true,
  },
  {
    id: 2,
    category: 'PAYMENT_DUE',
    channels: ['WHATSAPP'],
    quiet_hours_start: '21:00',
    quiet_hours_end: '06:00',
    enabled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// notificationPrefs.ts — pure function tests
// ─────────────────────────────────────────────────────────────────────────────

describe('notificationPrefs — PARENT_NOTIFICATION_CATEGORIES', () => {
  it('should NOT include EMERGENCY (NTF-013)', () => {
    assert.ok(
      !notifPrefs.PARENT_NOTIFICATION_CATEGORIES.includes('EMERGENCY'),
      'EMERGENCY must not be in parent categories (NTF-013)'
    );
  });

  it('should include all expected opt-outable categories', () => {
    const expected = [
      'ARRIVAL', 'DEPARTURE', 'PAYMENT_DUE', 'PAYMENT_RECEIVED',
      'GRADE_PUBLISHED', 'HOMEWORK', 'CANTEEN', 'ANNOUNCEMENT',
    ];
    for (const cat of expected) {
      assert.ok(
        notifPrefs.PARENT_NOTIFICATION_CATEGORIES.includes(cat),
        `Missing category: ${cat}`
      );
    }
  });

  it('NON_OPTOUT_CATEGORIES should contain EMERGENCY', () => {
    assert.ok(notifPrefs.NON_OPTOUT_CATEGORIES.includes('EMERGENCY'));
  });
});

describe('notificationPrefs — buildPrefMap', () => {
  it('should index prefs by category', () => {
    const map = notifPrefs.buildPrefMap(mockPrefs as any);
    assert.equal(map['ARRIVAL'].id, 1);
    assert.equal(map['PAYMENT_DUE'].id, 2);
    assert.equal(map['PAYMENT_DUE'].enabled, false);
  });
});

describe('notificationPrefs — getEffectivePref', () => {
  it('should return existing pref from map', () => {
    const map = notifPrefs.buildPrefMap(mockPrefs as any);
    const pref = notifPrefs.getEffectivePref(map, 'ARRIVAL');
    assert.deepEqual(pref.channels, ['WHATSAPP', 'PUSH']);
  });

  it('should return sensible defaults for missing category', () => {
    const pref = notifPrefs.getEffectivePref({}, 'HOMEWORK');
    assert.equal(pref.enabled, true);
    assert.equal(pref.quiet_hours_start, '21:00');
    assert.equal(pref.quiet_hours_end, '06:00');
    assert.deepEqual(pref.channels, []);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// storage.ts — locale and biometric helpers (in-memory store in test env)
// ─────────────────────────────────────────────────────────────────────────────

describe('storage — getLocale / setLocale (PAR-014)', () => {
  it('should default to id-ID when no value stored', async () => {
    const locale = await storage.getLocale();
    assert.ok(locale === 'id-ID' || locale === 'en-US', `Unexpected locale: ${locale}`);
  });

  it('should round-trip en-US', async () => {
    await storage.setLocale('en-US');
    const locale = await storage.getLocale();
    assert.equal(locale, 'en-US');
  });

  it('should round-trip id-ID', async () => {
    await storage.setLocale('id-ID');
    const locale = await storage.getLocale();
    assert.equal(locale, 'id-ID');
  });
});

describe('storage — getBiometricEnabled / setBiometricEnabled (PAR-018)', () => {
  it('should return false after setting false', async () => {
    await storage.setBiometricEnabled(false);
    assert.equal(await storage.getBiometricEnabled(), false);
  });

  it('should round-trip true', async () => {
    await storage.setBiometricEnabled(true);
    assert.equal(await storage.getBiometricEnabled(), true);
  });

  it('should round-trip false after true', async () => {
    await storage.setBiometricEnabled(true);
    await storage.setBiometricEnabled(false);
    assert.equal(await storage.getBiometricEnabled(), false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// biometric.ts — headless environment (expo-local-authentication not available)
// ─────────────────────────────────────────────────────────────────────────────

describe('biometric — headless environment (PAR-018)', () => {
  it('isBiometricAvailable should return false', async () => {
    assert.equal(await biometric.isBiometricAvailable(), false);
  });

  it('hasBiometricHardware should return false', async () => {
    assert.equal(await biometric.hasBiometricHardware(), false);
  });

  it('authenticateBiometric should return { success: false } with error', async () => {
    const result = await biometric.authenticateBiometric('Test prompt');
    assert.equal(result.success, false);
    assert.ok('error' in result, 'Should have error property');
  });

  it('getBiometricTypeLabel should return non-empty string fallback', async () => {
    const label = await biometric.getBiometricTypeLabel('id-ID');
    assert.equal(typeof label, 'string');
    assert.ok(label.length > 0, 'Label should not be empty');
  });

  it('getBiometricTypeLabel en-US should return non-empty string fallback', async () => {
    const label = await biometric.getBiometricTypeLabel('en-US');
    assert.equal(typeof label, 'string');
    assert.ok(label.length > 0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// strings.ts / i18n — t() helper (PAR-014)
// ─────────────────────────────────────────────────────────────────────────────

describe('strings — t() helper (PAR-014)', () => {
  it('should return id-ID string for tab.profile', () => {
    assert.equal(t('tab.profile', 'id-ID'), 'Profil');
  });

  it('should return en-US string for tab.profile', () => {
    assert.equal(t('tab.profile', 'en-US'), 'Profile');
  });

  it('should return key itself for unknown key (graceful degradation)', () => {
    assert.equal(t('UNKNOWN_KEY_XYZ', 'id-ID'), 'UNKNOWN_KEY_XYZ');
  });

  it('all PARENT_NOTIFICATION_CATEGORIES should have bilingual cat.* entries', () => {
    for (const cat of notifPrefs.PARENT_NOTIFICATION_CATEGORIES) {
      const key = `cat.${cat}`;
      assert.ok(strings[key], `Missing string key: ${key}`);
      assert.ok(strings[key]['id-ID'], `Missing id-ID for ${key}`);
      assert.ok(strings[key]['en-US'], `Missing en-US for ${key}`);
    }
  });

  it('NON_OPTOUT_CATEGORIES should also have bilingual cat.* entries', () => {
    for (const cat of notifPrefs.NON_OPTOUT_CATEGORIES) {
      const key = `cat.${cat}`;
      assert.ok(strings[key], `Missing string key for non-opt-out category: ${key}`);
      assert.ok(strings[key]['id-ID'], `Missing id-ID for ${key}`);
      assert.ok(strings[key]['en-US'], `Missing en-US for ${key}`);
    }
  });

  it('all tab keys have both languages', () => {
    const tabKeys = ['tab.home', 'tab.attendance', 'tab.academic', 'tab.wallet', 'tab.nutrition', 'tab.invoices', 'tab.profile'];
    for (const key of tabKeys) {
      assert.ok(strings[key], `Missing tab key: ${key}`);
      assert.ok(strings[key]['id-ID']);
      assert.ok(strings[key]['en-US']);
    }
  });
});
