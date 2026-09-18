"""id-ID first status-page copy, lifted verbatim from the source design mockup
(docs/superpowers/specs/2026-09-18-service-status-page-design.md)."""

STATUS_STRINGS = {
    'ID': {
        'st_eyebrow': 'Status layanan',
        'st_h1': 'Status langsung setiap layanan EduCore.',
        'st_banner': 'Semua sistem beroperasi normal',
        'st_banner_degraded': 'Sebagian sistem mengalami gangguan',
        'st_m1': 'uptime 90 hari terakhir',
        'st_m2': 'waktu respons API rata-rata',
        'st_m3': 'insiden terbuka',
        'st_comp_h': 'Komponen',
        'st_legend': '30 hari terakhir',
        'st_ok': 'Operasional',
        'st_degraded': 'Menurun',
        'st_down': 'Gangguan',
        'st_window': 'Jendela pemeliharaan terjadwal: Minggu 01:00–03:00 WIB, di luar jam sekolah.',
        'st_inc_h': 'Riwayat insiden',
        'sev_minor': 'Minor',
        'sev_major': 'Mayor',
        'sev_maint': 'Pemeliharaan',
        'st_sub_note': 'Berlangganan pemberitahuan insiden lewat surel atau webhook — sama seperti kejadian domain lain.',
        'st_sub_cta': 'Berlangganan pembaruan',
        'st_sub_placeholder': 'Alamat surel Anda',
        'st_sub_success': 'Anda telah berlangganan pembaruan status.',
    },
    'EN': {
        'st_eyebrow': 'Service status',
        'st_h1': 'Live status for every EduCore service.',
        'st_banner': 'All systems operational',
        'st_banner_degraded': 'Some systems are experiencing issues',
        'st_m1': 'uptime, last 90 days',
        'st_m2': 'average API response time',
        'st_m3': 'open incidents',
        'st_comp_h': 'Components',
        'st_legend': 'Last 30 days',
        'st_ok': 'Operational',
        'st_degraded': 'Degraded',
        'st_down': 'Down',
        'st_window': 'Scheduled maintenance window: Sundays 01:00–03:00 WIB, outside school hours.',
        'st_inc_h': 'Incident history',
        'sev_minor': 'Minor',
        'sev_major': 'Major',
        'sev_maint': 'Maintenance',
        'st_sub_note': 'Subscribe to incident notifications by email or webhook — same as other domain events.',
        'st_sub_cta': 'Subscribe to updates',
        'st_sub_placeholder': 'Your email address',
        'st_sub_success': 'You are now subscribed to status updates.',
    },
}


def get_status_strings(lang_code):
    """id-ID first: any unrecognized/missing lang_code falls back to Indonesian."""
    return STATUS_STRINGS.get((lang_code or '').upper(), STATUS_STRINGS['ID'])
