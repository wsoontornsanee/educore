/**
 * EduCore Mobile — Static bilingual string map (PAR-014).
 *
 * id-ID is the primary language (id-ID first rule per spec/appendix §2.10).
 * en-US is a secondary switch available in the Profile tab.
 *
 * Usage:
 *   import { t } from '../i18n/strings';
 *   const label = t('profile.logout', locale);
 */
export type Locale = 'id-ID' | 'en-US';

type StringMap = Record<string, { 'id-ID': string; 'en-US': string }>;

export const strings: StringMap = {
  // ── Tab bar labels ────────────────────────────────────────────────
  'tab.home':       { 'id-ID': 'Beranda',  'en-US': 'Home' },
  'tab.attendance': { 'id-ID': 'Absensi',  'en-US': 'Attendance' },
  'tab.academic':   { 'id-ID': 'Akademik', 'en-US': 'Academic' },
  'tab.messages':   { 'id-ID': 'Pesan',    'en-US': 'Messages' },
  'tab.wallet':     { 'id-ID': 'Dompet',   'en-US': 'Wallet' },
  'tab.nutrition':  { 'id-ID': 'Nutrisi',  'en-US': 'Nutrition' },
  'tab.invoices':   { 'id-ID': 'Tagihan',  'en-US': 'Invoices' },
  'tab.profile':    { 'id-ID': 'Profil',   'en-US': 'Profile' },

  // ── Profile screen — section headers ─────────────────────────────
  'profile.title':               { 'id-ID': 'Profil',                    'en-US': 'Profile' },
  'profile.section.children':    { 'id-ID': 'Anak Terhubung',            'en-US': 'Linked Children' },
  'profile.section.notif':       { 'id-ID': 'Notifikasi',                'en-US': 'Notifications' },
  'profile.section.language':    { 'id-ID': 'Bahasa',                    'en-US': 'Language' },
  'profile.section.security':    { 'id-ID': 'Keamanan',                  'en-US': 'Security' },
  'profile.logout':              { 'id-ID': 'Keluar',                    'en-US': 'Log Out' },

  // ── Notification prefs ───────────────────────────────────────────
  'notif.quiet_hours':           { 'id-ID': 'Jam Tenang',                'en-US': 'Quiet Hours' },
  'notif.quiet_start':           { 'id-ID': 'Mulai',                     'en-US': 'From' },
  'notif.quiet_end':             { 'id-ID': 'Selesai',                   'en-US': 'To' },
  'notif.channels':              { 'id-ID': 'Saluran',                   'en-US': 'Channels' },
  'notif.always_on':             { 'id-ID': 'Selalu aktif',              'en-US': 'Always on' },
  'notif.saving':                { 'id-ID': 'Menyimpan\u2026',          'en-US': 'Saving\u2026' },
  'notif.save_error':            { 'id-ID': 'Gagal menyimpan preferensi', 'en-US': 'Failed to save preference' },

  // notification category labels (id-ID first per spec domain language)
  'cat.ARRIVAL':               { 'id-ID': 'Kedatangan',              'en-US': 'Arrival' },
  'cat.DEPARTURE':             { 'id-ID': 'Kepulangan',              'en-US': 'Departure' },
  'cat.PAYMENT_DUE':           { 'id-ID': 'Tagihan Pembayaran',      'en-US': 'Payment Due' },
  'cat.PAYMENT_RECEIVED':      { 'id-ID': 'Pembayaran Diterima',     'en-US': 'Payment Received' },
  'cat.GRADE_PUBLISHED':       { 'id-ID': 'Nilai Diumumkan',         'en-US': 'Grades Published' },
  'cat.REPORT_CARD':           { 'id-ID': 'Buku Rapor',              'en-US': 'Report Card' },
  'cat.HOMEWORK':              { 'id-ID': 'Tugas Sekolah',           'en-US': 'Homework' },
  'cat.CANTEEN':               { 'id-ID': 'Transaksi Kantin',        'en-US': 'Canteen' },
  'cat.ANNOUNCEMENT':          { 'id-ID': 'Pengumuman Sekolah',      'en-US': 'Announcements' },
  'cat.EMERGENCY':             { 'id-ID': 'Darurat',                 'en-US': 'Emergency' },
  'cat.WALLET_RECONCILIATION': { 'id-ID': 'Rekonsiliasi Dompet',     'en-US': 'Wallet Reconciliation' },

  // channel labels
  'channel.WHATSAPP': { 'id-ID': 'WhatsApp',    'en-US': 'WhatsApp' },
  'channel.PUSH':     { 'id-ID': 'Notif Push',  'en-US': 'Push Notification' },
  'channel.SMS':      { 'id-ID': 'SMS',          'en-US': 'SMS' },
  'channel.EMAIL':    { 'id-ID': 'Email',        'en-US': 'Email' },

  // ── Language switch ──────────────────────────────────────────────
  'lang.id':  { 'id-ID': 'Bahasa Indonesia', 'en-US': 'Bahasa Indonesia' },
  'lang.en':  { 'id-ID': 'English',          'en-US': 'English' },

  // ── Biometric ────────────────────────────────────────────────────
  'bio.toggle':          { 'id-ID': 'Kunci Biometrik',                     'en-US': 'Biometric Lock' },
  'bio.toggle_sub':      { 'id-ID': 'Gunakan sidik jari / Face ID untuk membuka aplikasi', 'en-US': 'Use fingerprint / Face ID to unlock the app' },
  'bio.enroll_prompt':   { 'id-ID': 'Konfirmasi untuk mengaktifkan kunci biometrik', 'en-US': 'Confirm to enable biometric lock' },
  'bio.not_available':   { 'id-ID': 'Biometrik tidak tersedia di perangkat ini', 'en-US': 'Biometric not available on this device' },
  'bio.not_enrolled':    { 'id-ID': 'Tidak ada biometrik terdaftar di perangkat ini', 'en-US': 'No biometrics enrolled on this device' },
  'bio.auth_prompt':     { 'id-ID': 'Masuk ke EduCore',                   'en-US': 'Sign in to EduCore' },
  'bio.auth_failed':     { 'id-ID': 'Autentikasi biometrik gagal. Silakan masuk ulang.', 'en-US': 'Biometric authentication failed. Please log in again.' },
  'bio.cancel':          { 'id-ID': 'Batal',                               'en-US': 'Cancel' },

  // ── Common ───────────────────────────────────────────────────────
  'common.retry':        { 'id-ID': 'Coba lagi',         'en-US': 'Retry' },
  'common.loading':      { 'id-ID': 'Memuat\u2026',     'en-US': 'Loading\u2026' },
  'common.error':        { 'id-ID': 'Terjadi kesalahan', 'en-US': 'An error occurred' },
};

/**
 * Translate a string key to the given locale.
 * Falls back to id-ID if the key is missing for en-US.
 */
export function t(key: string, locale: Locale): string {
  const entry = strings[key];
  if (!entry) return key; // graceful degradation: return key itself
  return entry[locale] ?? entry['id-ID'] ?? key;
}
