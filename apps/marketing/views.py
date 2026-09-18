"""Public marketing website views.

No auth, no tenancy, no database reads — every number here is static
illustrative marketing copy (matching the approved design mockup), not a
live cross-foundation query. A public, unauthenticated page must never
expose real aggregate school/student counts.
"""
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

MODULES = [
    {'no': '01', 'title': _('Akademik & rapor'), 'body': _('Kurikulum, jadwal, nilai, dan rapor siap cetak yang mengikuti format resmi.')},
    {'no': '02', 'title': _('Kehadiran & gerbang'), 'body': _('Absensi per sesi, pemindaian gerbang, dan notifikasi kedatangan ke orang tua dalam hitungan detik.')},
    {'no': '03', 'title': _('Keuangan & tagihan'), 'body': _('Tagihan SPP otomatis, VA dan QRIS, alokasi pembayaran, serta buku besar yang selalu seimbang.')},
    {'no': '04', 'title': _('Kantin & dompet'), 'body': _('Dompet siswa non-tunai, batas belanja harian, dan POS kantin yang tetap jalan saat jaringan mati.')},
    {'no': '05', 'title': _('Suite guru'), 'body': _('Agenda mengajar, antrean penilaian, dan catatan kelas di web maupun ponsel.')},
    {'no': '06', 'title': _('Aplikasi orang tua'), 'body': _('Kehadiran, tagihan, saldo kantin, dan rapor anak — dalam satu aplikasi ringan.')},
]

PRINCIPLES = [
    {'no': '01', 'title': _('Dibuat untuk ponsel 3G'), 'body': _('Setiap layar menyatakan kondisi memuat, data lama, dan offline secara eksplisit. Aplikasi guru dan POS bekerja penuh tanpa jaringan.')},
    {'no': '02', 'title': _('Bahasa Indonesia lebih dulu'), 'body': _('Indonesia adalah bahasa sumber, Inggris adalah terjemahan. Tata letak menampung pemuaian teks 30% tanpa rusak.')},
    {'no': '03', 'title': _('Siap diaudit'), 'body': _('Setiap tindakan yang mengubah data menulis jejak audit dengan aktor, waktu, dan alasan. Tidak ada pengecualian untuk proses internal.')},
]

ROLES = [
    {'title': _('Ketua yayasan'), 'body': _('Bandingkan sekolah, pantau kolektibilitas, dan setujui keringanan dalam satu antrean.')},
    {'title': _('Admin sekolah'), 'body': _('Pendaftaran, mutasi, jadwal, dan pelaporan resmi tanpa spreadsheet paralel.')},
    {'title': _('Guru'), 'body': _('Absen satu sentuh, nilai tanpa menunggu jaringan, dan rapor yang terisi sendiri.')},
    {'title': _('Orang tua'), 'body': _('Tahu anak sudah sampai, tahu berapa yang harus dibayar, bayar dari ponsel.')},
]

COMPLIANCE = [
    {'title': _('Isolasi tenant'), 'body': _('Setiap tabel dikunci per yayasan di lapisan basis data, bukan hanya di aplikasi.')},
    {'title': _('Retensi terjadwal'), 'body': _('Foto gerbang dan dokumen kedaluwarsa dihapus otomatis sesuai periode statutori.')},
    {'title': _('Tanpa PII di log'), 'body': _('Nama, NISN, dan NIK tidak pernah masuk log, analitik, atau isi SMS.')},
    {'title': _('Ekspor resmi'), 'body': _('Jalur ekspor Dapodik dan EMIS mengikuti skema versi terbaru sebagai data, bukan kode.')},
]

# Numbers, currency, and mock timestamps are locale-neutral display strings
# (spec/appendix §2.10 covers language, not number formatting) — not wrapped.
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
        'badge': _('STABIL'),
        'title': _('Aplikasi Orang Tua'),
        'body': _('Kehadiran harian, tagihan dan pembayaran, saldo kantin, rapor, dan notifikasi gerbang.'),
        'version': 'v3.4.1', 'size': '18 MB', 'os': 'Android 9+ / iOS 15+',
        'qr_static': 'img/qr-orang-tua.png', 'qr_label': _('ORANG TUA'),
        'mock': {
            'eyebrow': _('HARI INI'),
            'heading': _('Aisyah — kelas X MIPA 2'),
            'rows': [
                {'label': _('Tiba di sekolah'), 'value': _('07:02 · HADIR')},
                {'label': _('SPP September'), 'value': 'Rp 750.000'},
                {'label': _('Saldo kantin'), 'value': 'Rp 42.500'},
            ],
            'cta': _('Bayar sekarang'),
        },
        'stores': True,
    },
    {
        'badge': _('STABIL'),
        'title': _('Aplikasi Guru'),
        'body': _('Agenda mengajar, absensi sesi, antrean penilaian, dan catatan kelas — bekerja penuh offline.'),
        'version': 'v2.9.0', 'size': '22 MB', 'os': 'Android 9+ / iOS 15+',
        'qr_static': 'img/qr-guru.png', 'qr_label': _('GURU'),
        'mock': {
            'eyebrow': _('AGENDA'),
            'heading': _('Rabu, 17 September'),
            'rows': [
                {'label': _('07:30 · X MIPA 2'), 'value': _('Absen belum diisi')},
                {'label': _('09:15 · XI MIPA 1'), 'value': _('Selesai · 32 hadir')},
                {'label': _('11:00 · X MIPA 3'), 'value': _('8 tugas menunggu nilai')},
            ],
            'cta': _('Ambil absen'),
        },
        'stores': True,
    },
    {
        'badge': _('STABIL'),
        'title': _('POS Kantin'),
        'body': _('Terminal kantin dan kiosk. Dipasang oleh tim penerapan; pembaruan diturunkan per sekolah.'),
        'version': 'v1.8.2', 'size': '31 MB', 'os': 'Android 11+ (tablet)',
        'mock': {
            'eyebrow': _('TRANSAKSI'),
            'heading': _('Kantin — terminal 2'),
            'rows': [
                {'label': _('Nasi ayam'), 'value': 'Rp 12.000'},
                {'label': _('Susu kotak'), 'value': 'Rp 8.000'},
                {'label': _('Sisa saldo'), 'value': 'Rp 38.500'},
            ],
            'cta': _('Tap kartu siswa'),
        },
        'stores': False,
    },
]

# 'support_active' drives the template's active/EOL styling — kept as a plain
# bool rather than comparing the translated 'support' label, which would break
# once that label renders in English.
RELEASES = [
    {'label': _('Orang tua — saat ini'), 'version': '3.4.1', 'support': _('Aktif'), 'support_active': True},
    {'label': _('Orang tua — sebelumnya'), 'version': '3.3.x', 'support': _('Sampai Des 2026'), 'support_active': False},
    {'label': _('Guru — saat ini'), 'version': '2.9.0', 'support': _('Aktif'), 'support_active': True},
    {'label': _('POS kantin'), 'version': '1.8.2', 'support': _('Aktif'), 'support_active': True},
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


DOMAINS = [
    {'scope': 'roster.read · roster.pii', 'title': _('Roster & staf'), 'body': _('Yayasan, sekolah, kelas, siswa aktif, dan staf beserta jabatan serta status kepegawaian.')},
    {'scope': 'finance.read', 'title': _('Keuangan'), 'body': _('Tagihan, alokasi pembayaran, AR aging, dan statement per siswa. Uang selalu string 2dp dengan mata uang.')},
    {'scope': 'attendance.read', 'title': _('Kehadiran'), 'body': _('Rekap harian dan per sesi dengan enam status tetap. Foto gerbang tidak pernah diekspor.')},
    {'scope': 'academic.read', 'title': _('Akademik'), 'body': _('Struktur kurikulum, jadwal, dan ringkasan capaian per periode penilaian.')},
]

PARTNER_FEATURES = [
    {'no': '01', 'title': _('Autentikasi yang bisa dirotasi'), 'body': _('Pasangan key-id dan secret per yayasan, ditandatangani HMAC-SHA256 per permintaan. Rotasi tanpa downtime.')},
    {'no': '02', 'title': _('Aman diulang'), 'body': _('Setiap endpoint mutasi menerima Idempotency-Key. Kirim ulang permintaan yang sama dan Anda mendapat respons yang sama.')},
    {'no': '03', 'title': _('Webhook, dengan jaring polling'), 'body': _('Webhook bertanda tangan untuk kejadian domain, lima kali coba ulang — dan endpoint events bila penerima Anda pernah mati.')},
]


class PartnerApiView(TemplateView):
    """Renders the spec/18-partner-vendor-api.md content as a public page."""
    template_name = 'marketing/partner_api.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_page'] = 'partner-api'
        ctx['api_base_url'] = self.request.build_absolute_uri('/api/v1/')
        ctx['domains'] = DOMAINS
        ctx['features'] = PARTNER_FEATURES
        return ctx
