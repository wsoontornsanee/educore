/**
 * Shared attendance presentation constants (spec/08 §2).
 *
 * Parent-facing screens must never render the raw backend enum
 * (HADIR / ALPA / ...), and Home and Absensi must agree on the wording.
 */

export const STATUS_LABEL_ID: Record<string, string> = {
  HADIR: 'Sudah di sekolah',
  TERLAMBAT: 'Sudah di sekolah (Terlambat)',
  SAKIT: 'Sakit',
  IZIN: 'Izin',
  ALPA: 'Tidak hadir',
  DISPEN: 'Dispensasi',
};

export const STATUS_LABEL_EN: Record<string, string> = {
  HADIR: 'At school',
  TERLAMBAT: 'At school (Late)',
  SAKIT: 'Sick',
  IZIN: 'Excused',
  ALPA: 'Absent',
  DISPEN: 'Dispensation',
};

export const STATUS_LABEL = STATUS_LABEL_ID;

export function attendanceStatusLabel(
  status: string | null | undefined,
  locale: 'id-ID' | 'en-US' = 'id-ID'
): string {
  if (!status) return locale === 'en-US' ? 'Not arrived yet' : 'Belum tiba';
  const map = locale === 'en-US' ? STATUS_LABEL_EN : STATUS_LABEL_ID;
  return map[status] ?? status;
}

