/**
 * Shared attendance presentation constants (spec/08 §2).
 *
 * Parent-facing screens must never render the raw backend enum
 * (HADIR / ALPA / ...), and Home and Absensi must agree on the wording.
 */

export const STATUS_LABEL: Record<string, string> = {
  HADIR: 'Sudah di sekolah',
  TERLAMBAT: 'Sudah di sekolah (Terlambat)',
  SAKIT: 'Sakit',
  IZIN: 'Izin',
  ALPA: 'Tidak hadir',
  DISPEN: 'Dispensasi',
};

export function attendanceStatusLabel(status: string | null | undefined): string {
  if (!status) return 'Belum tiba';
  return STATUS_LABEL[status] ?? status;
}
