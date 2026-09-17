/**
 * EduCore Mobile — Comprehensive i18n Verification Suite (PAR-014).
 *
 * Verifies:
 * 1. Dictionary completeness: every key across all domains has non-empty id-ID and en-US strings.
 * 2. Fallback rules: unknown keys fallback to defaultText or key; missing translations fallback to id-ID.
 * 3. Attendance status label helper: attendanceStatusLabel supports both id-ID and en-US.
 * 4. Tab navigation labels: all 8 parent tabs have bilingual entries.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { strings, t } from '../src/i18n/strings.ts';
import { attendanceStatusLabel } from '../src/constants/attendance.ts';

describe('i18n — Static String Dictionary Completeness (PAR-014)', () => {
  it('every entry in strings dictionary has non-empty id-ID and en-US', () => {
    const keys = Object.keys(strings);
    assert.ok(keys.length >= 60, `Expected at least 60 strings, found ${keys.length}`);

    for (const key of keys) {
      const entry = strings[key];
      assert.ok(entry, `Entry for ${key} must exist`);
      assert.ok(
        typeof entry['id-ID'] === 'string' && entry['id-ID'].trim().length > 0,
        `Key "${key}" must have a non-empty id-ID translation`
      );
      assert.ok(
        typeof entry['en-US'] === 'string' && entry['en-US'].trim().length > 0,
        `Key "${key}" must have a non-empty en-US translation`
      );
    }
  });

  it('all 8 parent app tabs have bilingual translations', () => {
    const tabs = [
      'tab.home',
      'tab.attendance',
      'tab.academic',
      'tab.messages',
      'tab.wallet',
      'tab.nutrition',
      'tab.invoices',
      'tab.profile',
    ];

    for (const tab of tabs) {
      assert.ok(strings[tab], `Missing tab entry: ${tab}`);
      assert.notEqual(t(tab, 'id-ID'), tab);
      assert.notEqual(t(tab, 'en-US'), tab);
      assert.notEqual(t(tab, 'id-ID'), t(tab, 'en-US'), `Tab ${tab} should differ between id-ID and en-US`);
    }
  });

  it('all day of week keys (day.0 to day.6) are defined in both languages', () => {
    for (let d = 0; d <= 6; d++) {
      const key = `day.${d}`;
      assert.ok(strings[key], `Missing day key: ${key}`);
      assert.notEqual(t(key, 'id-ID'), t(key, 'en-US'));
    }
  });

  it('all core domain namespaces are populated', () => {
    const requiredPrefixes = [
      'common.',
      'banner.',
      'attendance.',
      'home.',
      'messages.',
      'slip.',
      'academic.',
      'wallet.',
      'nutrition.',
      'invoice.',
      'payment.',
      'profile.',
      'notif.',
      'cat.',
      'channel.',
      'bio.',
    ];

    const allKeys = Object.keys(strings);
    for (const prefix of requiredPrefixes) {
      const matching = allKeys.filter((k) => k.startsWith(prefix));
      assert.ok(
        matching.length > 0,
        `Namespace "${prefix}" must contain at least one string key`
      );
    }
  });
});

describe('i18n — Translation Helper t() & Fallbacks (PAR-014)', () => {
  it('returns exact translation when key exists', () => {
    assert.equal(t('tab.home', 'id-ID'), 'Beranda');
    assert.equal(t('tab.home', 'en-US'), 'Home');
    assert.equal(t('common.retry', 'id-ID'), 'Coba lagi');
    assert.equal(t('common.retry', 'en-US'), 'Retry');
  });

  it('defaults to id-ID if locale is not provided', () => {
    assert.equal(t('tab.home'), 'Beranda');
    assert.equal(t('tab.profile'), 'Profil');
  });

  it('returns defaultText or key if key is entirely unknown', () => {
    assert.equal(t('NON_EXISTENT_KEY_123', 'id-ID'), 'NON_EXISTENT_KEY_123');
    assert.equal(
      t('NON_EXISTENT_KEY_123', 'id-ID', 'Fallback Default'),
      'Fallback Default'
    );
  });
});

describe('i18n — Attendance Status Bilingual Labels (PAR-014)', () => {
  it('returns Indonesian status labels by default and when requested', () => {
    assert.equal(attendanceStatusLabel('HADIR'), 'Sudah di sekolah');
    assert.equal(attendanceStatusLabel('HADIR', 'id-ID'), 'Sudah di sekolah');
    assert.equal(attendanceStatusLabel('TERLAMBAT', 'id-ID'), 'Sudah di sekolah (Terlambat)');
    assert.equal(attendanceStatusLabel('SAKIT', 'id-ID'), 'Sakit');
    assert.equal(attendanceStatusLabel('IZIN', 'id-ID'), 'Izin');
    assert.equal(attendanceStatusLabel('ALPA', 'id-ID'), 'Tidak hadir');
    assert.equal(attendanceStatusLabel(null, 'id-ID'), 'Belum tiba');
  });

  it('returns English status labels when en-US is requested', () => {
    assert.equal(attendanceStatusLabel('HADIR', 'en-US'), 'At school');
    assert.equal(attendanceStatusLabel('TERLAMBAT', 'en-US'), 'At school (Late)');
    assert.equal(attendanceStatusLabel('SAKIT', 'en-US'), 'Sick');
    assert.equal(attendanceStatusLabel('IZIN', 'en-US'), 'Excused');
    assert.equal(attendanceStatusLabel('ALPA', 'en-US'), 'Absent');
    assert.equal(attendanceStatusLabel(null, 'en-US'), 'Not arrived yet');
  });

  it('falls back gracefully on unknown status', () => {
    assert.equal(attendanceStatusLabel('CUSTOM_STATUS' as any, 'id-ID'), 'CUSTOM_STATUS');
    assert.equal(attendanceStatusLabel('CUSTOM_STATUS' as any, 'en-US'), 'CUSTOM_STATUS');
  });
});
