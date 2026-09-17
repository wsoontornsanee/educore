"""Public marketing website views.

No auth, no tenancy, no database reads — every number here is static
illustrative marketing copy (matching the approved design mockup), not a
live cross-foundation query. A public, unauthenticated page must never
expose real aggregate school/student counts.
"""
from django.views.generic import TemplateView

MODULES = [
    {'no': '01', 'title': 'Akademik & rapor', 'body': 'Kurikulum, jadwal, nilai, dan rapor siap cetak yang mengikuti format resmi.'},
    {'no': '02', 'title': 'Kehadiran & gerbang', 'body': 'Absensi per sesi, pemindaian gerbang, dan notifikasi kedatangan ke orang tua dalam hitungan detik.'},
    {'no': '03', 'title': 'Keuangan & tagihan', 'body': 'Tagihan SPP otomatis, VA dan QRIS, alokasi pembayaran, serta buku besar yang selalu seimbang.'},
    {'no': '04', 'title': 'Kantin & dompet', 'body': 'Dompet siswa non-tunai, batas belanja harian, dan POS kantin yang tetap jalan saat jaringan mati.'},
    {'no': '05', 'title': 'Suite guru', 'body': 'Agenda mengajar, antrean penilaian, dan catatan kelas di web maupun ponsel.'},
    {'no': '06', 'title': 'Aplikasi orang tua', 'body': 'Kehadiran, tagihan, saldo kantin, dan rapor anak — dalam satu aplikasi ringan.'},
]

PRINCIPLES = [
    {'no': '01', 'title': 'Dibuat untuk ponsel 3G', 'body': 'Setiap layar menyatakan kondisi memuat, data lama, dan offline secara eksplisit. Aplikasi guru dan POS bekerja penuh tanpa jaringan.'},
    {'no': '02', 'title': 'Bahasa Indonesia lebih dulu', 'body': 'Indonesia adalah bahasa sumber, Inggris adalah terjemahan. Tata letak menampung pemuaian teks 30% tanpa rusak.'},
    {'no': '03', 'title': 'Siap diaudit', 'body': 'Setiap tindakan yang mengubah data menulis jejak audit dengan aktor, waktu, dan alasan. Tidak ada pengecualian untuk proses internal.'},
]

ROLES = [
    {'title': 'Ketua yayasan', 'body': 'Bandingkan sekolah, pantau kolektibilitas, dan setujui keringanan dalam satu antrean.'},
    {'title': 'Admin sekolah', 'body': 'Pendaftaran, mutasi, jadwal, dan pelaporan resmi tanpa spreadsheet paralel.'},
    {'title': 'Guru', 'body': 'Absen satu sentuh, nilai tanpa menunggu jaringan, dan rapor yang terisi sendiri.'},
    {'title': 'Orang tua', 'body': 'Tahu anak sudah sampai, tahu berapa yang harus dibayar, bayar dari ponsel.'},
]

COMPLIANCE = [
    {'title': 'Isolasi tenant', 'body': 'Setiap tabel dikunci per yayasan di lapisan basis data, bukan hanya di aplikasi.'},
    {'title': 'Retensi terjadwal', 'body': 'Foto gerbang dan dokumen kedaluwarsa dihapus otomatis sesuai periode statutori.'},
    {'title': 'Tanpa PII di log', 'body': 'Nama, NISN, dan NIK tidak pernah masuk log, analitik, atau isi SMS.'},
    {'title': 'Ekspor resmi', 'body': 'Jalur ekspor Dapodik dan EMIS mengikuti skema versi terbaru sebagai data, bukan kode.'},
]

DASHBOARD_MOCK = {
    'collected_this_month': 'Rp1.482.500.000',
    'mom_change': '+4,2% MoM',
    'collectibility_pct': '92,4%',
    'attendance_avg_pct': '96,1%',
    'pending_approvals': 7,
    'per_school': [
        {'name': 'SMA Harapan', 'pct': 94},
        {'name': 'SMP Cendekia', 'pct': 88},
        {'name': 'SD Nusantara 1', 'pct': 76},
        {'name': 'SD Nusantara 2', 'pct': 61},
    ],
    'active_schools': 42,
    'enrolled_students': '52.000',
    'uptime_pct': '99,9%',
}

APPS = [
    {
        'badge': 'STABIL',
        'title': 'Aplikasi Orang Tua',
        'body': 'Kehadiran harian, tagihan dan pembayaran, saldo kantin, rapor, dan notifikasi gerbang.',
        'version': 'v3.4.1', 'size': '18 MB', 'os': 'Android 9+ / iOS 15+',
        'qr_static': 'img/qr-orang-tua.png', 'qr_label': 'ORANG TUA',
        'mock': {
            'eyebrow': 'HARI INI',
            'heading': 'Aisyah — kelas X MIPA 2',
            'rows': [
                {'label': 'Tiba di sekolah', 'value': '07:02 · HADIR'},
                {'label': 'SPP September', 'value': 'Rp 750.000'},
                {'label': 'Saldo kantin', 'value': 'Rp 42.500'},
            ],
            'cta': 'Bayar sekarang',
        },
        'stores': True,
    },
    {
        'badge': 'STABIL',
        'title': 'Aplikasi Guru',
        'body': 'Agenda mengajar, absensi sesi, antrean penilaian, dan catatan kelas — bekerja penuh offline.',
        'version': 'v2.9.0', 'size': '22 MB', 'os': 'Android 9+ / iOS 15+',
        'qr_static': 'img/qr-guru.png', 'qr_label': 'GURU',
        'mock': {
            'eyebrow': 'AGENDA',
            'heading': 'Rabu, 17 September',
            'rows': [
                {'label': '07:30 · X MIPA 2', 'value': 'Absen belum diisi'},
                {'label': '09:15 · XI MIPA 1', 'value': 'Selesai · 32 hadir'},
                {'label': '11:00 · X MIPA 3', 'value': '8 tugas menunggu nilai'},
            ],
            'cta': 'Ambil absen',
        },
        'stores': True,
    },
    {
        'badge': 'STABIL',
        'title': 'POS Kantin',
        'body': 'Terminal kantin dan kiosk. Dipasang oleh tim penerapan; pembaruan diturunkan per sekolah.',
        'version': 'v1.8.2', 'size': '31 MB', 'os': 'Android 11+ (tablet)',
        'mock': {
            'eyebrow': 'TRANSAKSI',
            'heading': 'Kantin — terminal 2',
            'rows': [
                {'label': 'Nasi ayam', 'value': 'Rp 12.000'},
                {'label': 'Susu kotak', 'value': 'Rp 8.000'},
                {'label': 'Sisa saldo', 'value': 'Rp 38.500'},
            ],
            'cta': 'Tap kartu siswa',
        },
        'stores': False,
    },
]

RELEASES = [
    {'label': 'Orang tua — saat ini', 'version': '3.4.1', 'support': 'Aktif'},
    {'label': 'Orang tua — sebelumnya', 'version': '3.3.x', 'support': 'Sampai Des 2026'},
    {'label': 'Guru — saat ini', 'version': '2.9.0', 'support': 'Aktif'},
    {'label': 'POS kantin', 'version': '1.8.2', 'support': 'Aktif'},
]


class HomeView(TemplateView):
    template_name = 'marketing/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'active_page': 'home',
            'modules': MODULES,
            'principles': PRINCIPLES,
            'roles': ROLES,
            'compliance': COMPLIANCE,
            'dashboard': DASHBOARD_MOCK,
        })
        return ctx


class DownloadsView(TemplateView):
    template_name = 'marketing/downloads.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_page'] = 'downloads'
        ctx['apps'] = APPS
        ctx['releases'] = RELEASES
        return ctx


class PartnerApiView(TemplateView):
    """Renders the spec/18-partner-vendor-api.md content as a public page."""
    template_name = 'marketing/partner_api.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_page'] = 'partner-api'
        return ctx
