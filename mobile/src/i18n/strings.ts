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

  // ── Common & Generic UI ───────────────────────────────────────────
  'common.loading':      { 'id-ID': 'Memuat\u2026',           'en-US': 'Loading\u2026' },
  'common.error':        { 'id-ID': 'Terjadi kesalahan',       'en-US': 'An error occurred' },
  'common.retry':        { 'id-ID': 'Coba lagi',               'en-US': 'Retry' },
  'common.save':         { 'id-ID': 'Simpan',                  'en-US': 'Save' },
  'common.cancel':       { 'id-ID': 'Batal',                   'en-US': 'Cancel' },
  'common.close':        { 'id-ID': 'Tutup',                   'en-US': 'Close' },
  'common.back':         { 'id-ID': 'Kembali',                 'en-US': 'Back' },
  'common.submit':       { 'id-ID': 'Kirim',                   'en-US': 'Submit' },
  'common.submitting':   { 'id-ID': 'Mengirim\u2026',         'en-US': 'Submitting\u2026' },
  'common.empty':        { 'id-ID': 'Belum ada data',          'en-US': 'No data available' },
  'common.all':          { 'id-ID': 'Semua',                   'en-US': 'All' },
  'common.active':       { 'id-ID': 'Aktif',                   'en-US': 'Active' },
  'common.status':       { 'id-ID': 'Status',                  'en-US': 'Status' },
  'common.date':         { 'id-ID': 'Tanggal',                 'en-US': 'Date' },
  'common.time':         { 'id-ID': 'Waktu',                   'en-US': 'Time' },
  'common.success':      { 'id-ID': 'Berhasil',                'en-US': 'Success' },
  'common.notes':        { 'id-ID': 'Catatan',                 'en-US': 'Notes' },
  'common.unlimited':    { 'id-ID': 'Tanpa Batas',             'en-US': 'Unlimited' },

  // ── Stale / Offline Banner ────────────────────────────────────────
  'banner.offline_title':  { 'id-ID': 'Mode Offline Aktif',                'en-US': 'Offline Mode Active' },
  'banner.pending_title':  { 'id-ID': 'Presensi Menunggu Sinkron',         'en-US': 'Pending Synchronization' },
  'banner.offline_sub':    { 'id-ID': 'Perubahan akan otomatis dikirim saat sinyal pulih.', 'en-US': 'Changes will automatically sync when connection returns.' },
  'banner.sync_btn':       { 'id-ID': 'Sinkronkan',                        'en-US': 'Sync Now' },
  'banner.last_synced':    { 'id-ID': 'Sinkron:',                          'en-US': 'Synced:' },

  // ── Attendance Statuses & Labels ──────────────────────────────────
  'attendance.status.HADIR':      { 'id-ID': 'Sudah di sekolah',             'en-US': 'At school' },
  'attendance.status.TERLAMBAT':  { 'id-ID': 'Sudah di sekolah (Terlambat)', 'en-US': 'At school (Late)' },
  'attendance.status.SAKIT':      { 'id-ID': 'Sakit',                        'en-US': 'Sick' },
  'attendance.status.IZIN':       { 'id-ID': 'Izin',                         'en-US': 'Excused' },
  'attendance.status.ALPA':       { 'id-ID': 'Tidak hadir',                  'en-US': 'Absent' },
  'attendance.status.DISPEN':     { 'id-ID': 'Dispensasi',                   'en-US': 'Dispensation' },
  'attendance.status.NONE':       { 'id-ID': 'Belum tiba',                   'en-US': 'Not arrived yet' },
  'attendance.status.NO_DATA':    { 'id-ID': 'Belum ada data absensi hari ini.', 'en-US': 'No attendance data for today.' },

  // ── Attendance Screen ─────────────────────────────────────────────
  'attendance.tab_timeline':       { 'id-ID': 'Riwayat Kehadiran', 'en-US': 'Attendance History' },
  'attendance.tab_absence':        { 'id-ID': 'Pengajuan Izin',    'en-US': 'Absence Requests' },
  'attendance.request_absence_btn':{ 'id-ID': '+ Ajukan Izin / Sakit', 'en-US': '+ Request Absence / Sick' },
  'attendance.first_in':           { 'id-ID': 'Masuk:',            'en-US': 'In:' },
  'attendance.first_out':          { 'id-ID': 'Pulang:',           'en-US': 'Out:' },
  'attendance.no_history':         { 'id-ID': 'Belum ada riwayat presensi.', 'en-US': 'No attendance records yet.' },
  'attendance.no_absence_records': { 'id-ID': 'Belum ada pengajuan izin.', 'en-US': 'No absence requests yet.' },
  'attendance.absence_pending':    { 'id-ID': 'Menunggu Persetujuan', 'en-US': 'Pending Approval' },
  'attendance.absence_approved':   { 'id-ID': 'Disetujui',         'en-US': 'Approved' },
  'attendance.absence_rejected':   { 'id-ID': 'Ditolak',           'en-US': 'Rejected' },
  'attendance.modal_title':        { 'id-ID': 'Pengajuan Izin Siswa', 'en-US': 'Student Absence Request' },
  'attendance.form_type':          { 'id-ID': 'Jenis Izin',        'en-US': 'Absence Type' },
  'attendance.type_sick':          { 'id-ID': 'Sakit (Surat Dokter)', 'en-US': 'Sick (Doctor Note)' },
  'attendance.type_excused':       { 'id-ID': 'Izin Keperluan Keluarga', 'en-US': 'Family / Excused' },
  'attendance.date_from':          { 'id-ID': 'Dari Tanggal (YYYY-MM-DD)', 'en-US': 'From Date (YYYY-MM-DD)' },
  'attendance.date_to':            { 'id-ID': 'Sampai Tanggal (YYYY-MM-DD)', 'en-US': 'To Date (YYYY-MM-DD)' },
  'attendance.reason':             { 'id-ID': 'Alasan Izin / Keterangan', 'en-US': 'Reason / Notes' },
  'attendance.reason_placeholder': { 'id-ID': 'Jelaskan alasan izin secara singkat...', 'en-US': 'Briefly explain the reason...' },
  'attendance.attachment':         { 'id-ID': 'Lampiran Surat Dokter / Bukti', 'en-US': 'Doctor Note / Attachment' },
  'attendance.attach_photo_btn':   { 'id-ID': 'Ambil Foto Bukti',  'en-US': 'Take Photo Proof' },
  'attendance.attach_gallery_btn': { 'id-ID': 'Pilih dari Galeri', 'en-US': 'Choose from Gallery' },
  'attendance.attach_sample':      { 'id-ID': 'Gunakan Contoh Foto', 'en-US': 'Use Sample Photo' },
  'attendance.attached':           { 'id-ID': 'Terlampir:',        'en-US': 'Attached:' },
  'attendance.err_reason_req':     { 'id-ID': 'Alasan izin wajib diisi.', 'en-US': 'Reason is required.' },
  'attendance.err_sick_doc':       { 'id-ID': 'Izin sakit lebih dari 2 hari wajib menyertakan surat dokter.', 'en-US': 'Sick leave over 2 days requires a doctor note.' },
  'attendance.submit_success':     { 'id-ID': 'Pengajuan izin berhasil dikirim.', 'en-US': 'Absence request submitted successfully.' },

  // ── Home Tab ──────────────────────────────────────────────────────
  'home.arrived':          { 'id-ID': 'Tiba:',                         'en-US': 'Arrived:' },
  'home.wallet_title':     { 'id-ID': 'Dompet Kantin Digital',         'en-US': 'Digital Canteen Wallet' },
  'home.open_wallet':      { 'id-ID': 'Buka Dompet Siswa \u2192',      'en-US': 'Open Student Wallet \u2192' },
  'home.invoices_title':   { 'id-ID': 'Tagihan',                       'en-US': 'Invoices' },
  'home.no_invoices':      { 'id-ID': 'Tidak ada tagihan tertunggak.', 'en-US': 'No outstanding invoices.' },
  'home.invoices_due':     { 'id-ID': 'tagihan belum lunas \u2014 jatuh tempo terdekat', 'en-US': 'unpaid invoices \u2014 nearest due date' },

  // ── Messages Tab (Permission Slips) ───────────────────────────────
  'messages.title':           { 'id-ID': 'Pesan & Surat Izin',            'en-US': 'Messages & Permission Slips' },
  'messages.empty':           { 'id-ID': 'Belum ada surat izin kegiatan.', 'en-US': 'No permission slips yet.' },
  'messages.event_date':      { 'id-ID': 'Tanggal Acara:',                'en-US': 'Event Date:' },
  'messages.location':        { 'id-ID': 'Lokasi:',                       'en-US': 'Location:' },
  'messages.deadline':        { 'id-ID': 'Batas Waktu:',                  'en-US': 'Deadline:' },
  'messages.signed_at':       { 'id-ID': 'Ditandatangani:',               'en-US': 'Signed at:' },
  'messages.sign_now_btn':    { 'id-ID': 'Beri Persetujuan / Tanda Tangan', 'en-US': 'Provide Consent / Sign' },
  'messages.closed_notice':   { 'id-ID': 'Pendaftaran ditutup oleh pihak sekolah', 'en-US': 'Submission closed by school' },
  'messages.offline_notice':  { 'id-ID': 'Penandatanganan online dinonaktifkan saat offline (PAR-015)', 'en-US': 'Online signing disabled while offline (PAR-015)' },
  'messages.tab_announcements': { 'id-ID': 'Pengumuman',                   'en-US': 'Announcements' },
  'messages.tab_slips':       { 'id-ID': 'Izin',                          'en-US': 'Permission Slips' },
  'messages.loading_broadcasts': { 'id-ID': 'Memuat pengumuman\u2026',    'en-US': 'Loading announcements\u2026' },
  'messages.empty_broadcasts_title': { 'id-ID': 'Belum Ada Pengumuman',   'en-US': 'No Announcements' },
  'messages.empty_broadcasts_desc':  { 'id-ID': 'Belum ada pengumuman dari sekolah.', 'en-US': 'No announcements from school.' },
  'messages.error_broadcasts': { 'id-ID': 'Tidak dapat memuat pengumuman. Periksa koneksi Anda lalu coba lagi.', 'en-US': 'Could not load announcements. Check your connection and try again.' },
  'slip.approved':            { 'id-ID': 'Disetujui',                     'en-US': 'Approved' },
  'slip.declined':            { 'id-ID': 'Ditolak',                       'en-US': 'Declined' },
  'slip.pending':             { 'id-ID': 'Menunggu Tanda Tangan',         'en-US': 'Signature Needed' },
  'slip.modal_title':         { 'id-ID': 'Konfirmasi Surat Izin Kegiatan', 'en-US': 'Permission Slip Confirmation' },
  'slip.signature_label':     { 'id-ID': 'Tanda Tangan Digital (Ketik Nama Lengkap)', 'en-US': 'Digital Signature (Type Full Name)' },
  'slip.signature_placeholder':{ 'id-ID': 'Contoh: Budi Santoso',        'en-US': 'e.g. John Doe' },
  'slip.btn_approve':         { 'id-ID': '\u2713 Setuju Mengikuti',       'en-US': '\u2713 Consent & Approve' },
  'slip.btn_decline':         { 'id-ID': '\u2717 Tidak Setuju / Tolak',   'en-US': '\u2717 Decline / Do Not Consent' },
  'slip.err_signature_req':   { 'id-ID': 'Tanda tangan wajib diisi: ketik nama lengkap Anda.', 'en-US': 'Signature required: type your full name.' },

  // ── Academic Tab ──────────────────────────────────────────────────
  'academic.tab_grades':      { 'id-ID': 'Nilai',                         'en-US': 'Grades' },
  'academic.tab_homework':    { 'id-ID': 'Tugas',                         'en-US': 'Homework' },
  'academic.tab_reports':     { 'id-ID': 'Rapor',                         'en-US': 'Report Card' },
  'academic.tab_timetable':   { 'id-ID': 'Jadwal',                        'en-US': 'Timetable' },
  'academic.empty_grades_title':{ 'id-ID': 'Belum Ada Nilai',              'en-US': 'No Grades Yet' },
  'academic.empty_grades':    { 'id-ID': 'Belum ada penilaian yang dipublikasikan.', 'en-US': 'No published assessments yet.' },
  'academic.empty_homework_title':{ 'id-ID': 'Tidak Ada Tugas',           'en-US': 'No Homework' },
  'academic.empty_homework':  { 'id-ID': 'Belum ada tugas rumah aktif.', 'en-US': 'No active homework assignments.' },
  'academic.empty_reports_title':{ 'id-ID': 'Belum Ada Rapor',            'en-US': 'No Report Cards' },
  'academic.empty_reports':   { 'id-ID': 'Belum ada buku rapor yang diterbitkan.', 'en-US': 'No report cards published yet.' },
  'academic.empty_timetable_title':{ 'id-ID': 'Belum Ada Jadwal',        'en-US': 'No Timetable Yet' },
  'academic.empty_timetable': { 'id-ID': 'Tidak ada jadwal pelajaran pada hari ini.', 'en-US': 'No classes scheduled for this day.' },
  'academic.weight':          { 'id-ID': 'Bobot:',                        'en-US': 'Weight:' },
  'academic.max':             { 'id-ID': 'Maks:',                         'en-US': 'Max:' },
  'academic.score':           { 'id-ID': 'Nilai:',                        'en-US': 'Score:' },
  'academic.final_grade':     { 'id-ID': 'Nilai Akhir:',                  'en-US': 'Final Grade:' },
  'academic.due_date':        { 'id-ID': 'Batas Waktu:',                  'en-US': 'Due Date:' },
  'academic.status_submitted':{ 'id-ID': 'Sudah Dikumpulkan',             'en-US': 'Submitted' },
  'academic.status_pending':  { 'id-ID': 'Belum Dikumpulkan',             'en-US': 'Not Submitted' },
  'academic.status_graded':   { 'id-ID': 'Sudah Dinilai',                 'en-US': 'Graded' },
  'academic.status_late':     { 'id-ID': 'Terlambat',                     'en-US': 'Late' },
  'academic.status_returned': { 'id-ID': 'Dikembalikan',                 'en-US': 'Returned' },
  'academic.files_attached':  { 'id-ID': 'berkas terlampir',              'en-US': 'files attached' },
  'academic.teacher_feedback':{ 'id-ID': 'Catatan Guru:',                 'en-US': 'Teacher Notes:' },
  'academic.download_rapor':  { 'id-ID': 'Unduh Rapor PDF',               'en-US': 'Download Report Card PDF' },
  'academic.arrears_title':   { 'id-ID': 'Rapor Ditangguhkan (Tunggakan)', 'en-US': 'Report Card Withheld (Arrears)' },
  'academic.arrears_notice':  { 'id-ID': 'Buku rapor ditahan sementara karena adanya tagihan yang belum lunas (ACD-014).', 'en-US': 'Report card withheld due to outstanding tuition arrears (ACD-014).' },
  'academic.view_pay_btn':    { 'id-ID': 'Lihat Tagihan & Bayar',         'en-US': 'View Invoices & Pay' },
  'academic.published':       { 'id-ID': 'Diterbitkan',                   'en-US': 'Published' },
  'academic.hadir':           { 'id-ID': 'Hadir',                         'en-US': 'Present' },
  'academic.sakit':           { 'id-ID': 'Sakit',                         'en-US': 'Sick' },
  'academic.izin':            { 'id-ID': 'Izin',                          'en-US': 'Permitted' },
  'academic.alpa':            { 'id-ID': 'Alpa',                          'en-US': 'Unexcused' },
  'academic.substitute':      { 'id-ID': 'Guru Pengganti:',               'en-US': 'Substitute Teacher:' },
  'academic.period_no':       { 'id-ID': 'Jam ke-',                       'en-US': 'Period ' },


  // Days of Week
  'day.1': { 'id-ID': 'Senin',   'en-US': 'Monday' },
  'day.2': { 'id-ID': 'Selasa',  'en-US': 'Tuesday' },
  'day.3': { 'id-ID': 'Rabu',    'en-US': 'Wednesday' },
  'day.4': { 'id-ID': 'Kamis',   'en-US': 'Thursday' },
  'day.5': { 'id-ID': 'Jumat',   'en-US': 'Friday' },
  'day.6': { 'id-ID': 'Sabtu',   'en-US': 'Saturday' },
  'day.0': { 'id-ID': 'Minggu',  'en-US': 'Sunday' },

  // ── Wallet Tab ────────────────────────────────────────────────────
  'wallet.title':             { 'id-ID': 'Dompet Kantin Digital',         'en-US': 'Digital Canteen Wallet' },
  'wallet.balance_label':     { 'id-ID': 'Saldo Siswa',                   'en-US': 'Student Balance' },
  'wallet.badge_active':      { 'id-ID': 'Aktif',                         'en-US': 'Active' },
  'wallet.badge_frozen':      { 'id-ID': 'Dibekukan',                     'en-US': 'Frozen' },
  'wallet.topup_btn':         { 'id-ID': '+ Top Up Saldo',                'en-US': '+ Top Up Balance' },
  'wallet.nutrition_shortcut':{ 'id-ID': 'Lihat Analitik Nutrisi Makanan \u2192', 'en-US': 'View Nutrition Analytics \u2192' },
  'wallet.spend_limits':      { 'id-ID': 'Batas Belanja Harian',          'en-US': 'Daily Spending Limit' },
  'wallet.limit_amount':      { 'id-ID': 'Maksimal per hari:',            'en-US': 'Max per day:' },
  'wallet.category_blocks':   { 'id-ID': 'Blokir Kategori Makanan',       'en-US': 'Blocked Food Categories' },
  'wallet.cat_sweet':         { 'id-ID': 'Minuman Manis / Bersoda',       'en-US': 'Sweet Drinks / Soda' },
  'wallet.cat_snacks':        { 'id-ID': 'Camilan / Makanan Ringan',      'en-US': 'Snacks / Junk Food' },
  'wallet.cat_fastfood':      { 'id-ID': 'Makanan Cepat Saji',            'en-US': 'Fast Food' },
  'wallet.time_window':       { 'id-ID': 'Jendela Waktu Pembelian',       'en-US': 'Allowed Purchase Window' },
  'wallet.time_window_sub':   { 'id-ID': 'Hanya izinkan transaksi pada jam istirahat sekolah', 'en-US': 'Only allow purchases during school recess hours' },
  'wallet.auto_topup':        { 'id-ID': 'Pengaturan Auto Top-Up',        'en-US': 'Auto Top-Up Settings' },
  'wallet.auto_topup_sub':    { 'id-ID': 'Otomatis buat tagihan saat saldo di bawah batas minimum', 'en-US': 'Automatically generate invoice when balance falls below threshold' },
  'wallet.history_title':     { 'id-ID': 'Riwayat Transaksi Kantin',      'en-US': 'Canteen Transaction History' },
  'wallet.history_empty':     { 'id-ID': 'Belum ada transaksi kantin.',   'en-US': 'No canteen transactions yet.' },
  'wallet.disclaimer':        { 'id-ID': 'Pembaruan batas dan aturan belanja memerlukan waktu hingga 1 menit untuk disinkronkan ke seluruh terminal kasir kantin sekolah.', 'en-US': 'Spending rule updates take up to 1 minute to sync across all campus canteen POS kiosks.' },
  'wallet.topup_modal_title': { 'id-ID': 'Top Up Saldo Kantin',           'en-US': 'Top Up Canteen Balance' },
  'wallet.topup_nominal':     { 'id-ID': 'Pilih atau Masukkan Nominal',   'en-US': 'Select or Enter Amount' },
  'wallet.topup_method':      { 'id-ID': 'Metode Pembayaran',             'en-US': 'Payment Method' },
  'wallet.topup_create_btn':  { 'id-ID': 'Buat Tagihan Top Up',           'en-US': 'Generate Top-Up VA' },
  'wallet.va_number':         { 'id-ID': 'Nomor Virtual Account',         'en-US': 'Virtual Account Number' },
  'wallet.copy_va':           { 'id-ID': 'Salin Nomor',                   'en-US': 'Copy Number' },
  'wallet.va_copied':         { 'id-ID': 'Nomor VA berhasil disalin',     'en-US': 'VA number copied to clipboard' },
  'wallet.topup_waiting':     { 'id-ID': 'Menunggu pembayaran...',        'en-US': 'Waiting for payment...' },
  'wallet.topup_settled':     { 'id-ID': 'Top up berhasil diselesaikan!', 'en-US': 'Top up successfully settled!' },

  // ── Nutrition Tab ─────────────────────────────────────────────────
  'nutrition.title':          { 'id-ID': 'Analisis Nutrisi & Pola Makan',  'en-US': 'Nutrition & Dietary Analytics' },
  'nutrition.period_today':   { 'id-ID': 'Hari Ini',                       'en-US': 'Today' },
  'nutrition.period_week':    { 'id-ID': '7 Hari Terakhir',                'en-US': 'Last 7 Days' },
  'nutrition.period_month':   { 'id-ID': '30 Hari Terakhir',               'en-US': 'Last 30 Days' },
  'nutrition.kpi_calories':   { 'id-ID': 'Rata-rata Kalori',               'en-US': 'Avg Daily Calories' },
  'nutrition.kpi_sugar':      { 'id-ID': 'Asupan Gula',                    'en-US': 'Sugar Intake' },
  'nutrition.sugar_normal':   { 'id-ID': 'Normal',                         'en-US': 'Normal' },
  'nutrition.sugar_elevated': { 'id-ID': 'Meningkat',                      'en-US': 'Elevated' },
  'nutrition.sugar_high':     { 'id-ID': 'Tinggi (Waspada)',               'en-US': 'High (Caution)' },
  'nutrition.kpi_healthy':    { 'id-ID': 'Pilihan Sehat',                  'en-US': 'Healthy Ratio' },
  'nutrition.kpi_allergens':  { 'id-ID': 'Peringatan Alergen',             'en-US': 'Allergen Alerts' },
  'nutrition.allergen_safe':  { 'id-ID': 'Aman (Tidak Ada Paparan)',       'en-US': 'Safe (No Exposure)' },
  'nutrition.log_title':      { 'id-ID': 'Catatan Menu Makanan & Minuman', 'en-US': 'Itemized Food & Drink Log' },
  'nutrition.log_empty':      { 'id-ID': 'Tidak ada pembelian makanan pada periode ini.', 'en-US': 'No purchases recorded for this period.' },

  // ── Invoices & Payments Tab ───────────────────────────────────────
  'invoice.tab_invoices':     { 'id-ID': 'Tagihan',                        'en-US': 'Invoices' },
  'invoice.tab_receipts':     { 'id-ID': 'Bukti Pembayaran',               'en-US': 'Payment Receipts' },
  'invoice.empty_invoices':   { 'id-ID': 'Tidak ada tagihan sekolah.',     'en-US': 'No school invoices found.' },
  'invoice.empty_receipts':   { 'id-ID': 'Belum ada bukti pembayaran.',    'en-US': 'No payment receipts yet.' },
  'invoice.status_unpaid':    { 'id-ID': 'Belum Lunas',                    'en-US': 'Unpaid' },
  'invoice.status_paid':      { 'id-ID': 'Lunas',                          'en-US': 'Paid' },
  'invoice.status_overdue':   { 'id-ID': 'Jatuh Tempo',                    'en-US': 'Overdue' },
  'invoice.due_date':         { 'id-ID': 'Jatuh Tempo:',                   'en-US': 'Due Date:' },
  'invoice.total_amount':     { 'id-ID': 'Total Tagihan:',                 'en-US': 'Total Amount:' },
  'invoice.balance_due':      { 'id-ID': 'Sisa Pembayaran:',               'en-US': 'Balance Due:' },
  'invoice.pay_now_btn':      { 'id-ID': 'Bayar Sekarang \u2192',          'en-US': 'Pay Now \u2192' },
  'invoice.download_pdf':     { 'id-ID': 'Unduh Bukti PDF',                'en-US': 'Download PDF' },
  'invoice.share_pdf':        { 'id-ID': 'Bagikan Bukti Pembayaran',       'en-US': 'Share Receipt' },
  'invoice.receipt_number':   { 'id-ID': 'Nomor Resi:',                    'en-US': 'Receipt No:' },
  'payment.title':            { 'id-ID': 'Pembayaran Tagihan Sekolah',     'en-US': 'School Tuition Payment' },
  'payment.choose_bank':      { 'id-ID': 'Pilih Bank Virtual Account',     'en-US': 'Select Virtual Account Bank' },
  'payment.qris_option':      { 'id-ID': 'QRIS (Semua E-Wallet & Bank)',   'en-US': 'QRIS (All E-Wallets & Banks)' },
  'payment.admin_fee':        { 'id-ID': 'Biaya Administrasi:',            'en-US': 'Admin Fee:' },
  'payment.total_pay':        { 'id-ID': 'Total Bayar:',                   'en-US': 'Total to Pay:' },
  'payment.generate_va_btn':  { 'id-ID': 'Dapatkan Nomor Pembayaran',      'en-US': 'Generate Payment Details' },

  // ── Profile Screen & Notification Prefs ───────────────────────────
  'profile.title':               { 'id-ID': 'Profil',                      'en-US': 'Profile' },
  'profile.section.children':    { 'id-ID': 'Anak Terhubung',              'en-US': 'Linked Children' },
  'profile.section.notif':       { 'id-ID': 'Notifikasi',                  'en-US': 'Notifications' },
  'profile.section.language':    { 'id-ID': 'Bahasa',                      'en-US': 'Language' },
  'profile.section.security':    { 'id-ID': 'Keamanan',                    'en-US': 'Security' },
  'profile.logout':              { 'id-ID': 'Keluar',                      'en-US': 'Log Out' },
  'notif.quiet_hours':           { 'id-ID': 'Jam Tenang',                  'en-US': 'Quiet Hours' },
  'notif.quiet_start':           { 'id-ID': 'Mulai',                       'en-US': 'From' },
  'notif.quiet_end':             { 'id-ID': 'Selesai',                     'en-US': 'To' },
  'notif.channels':              { 'id-ID': 'Saluran',                     'en-US': 'Channels' },
  'notif.always_on':             { 'id-ID': 'Selalu aktif',                'en-US': 'Always on' },
  'notif.saving':                { 'id-ID': 'Menyimpan\u2026',            'en-US': 'Saving\u2026' },
  'notif.save_error':            { 'id-ID': 'Gagal menyimpan preferensi',  'en-US': 'Failed to save preference' },

  // Notification category labels
  'cat.ARRIVAL':               { 'id-ID': 'Kedatangan',                    'en-US': 'Arrival' },
  'cat.DEPARTURE':             { 'id-ID': 'Kepulangan',                    'en-US': 'Departure' },
  'cat.PAYMENT_DUE':           { 'id-ID': 'Tagihan Pembayaran',            'en-US': 'Payment Due' },
  'cat.PAYMENT_RECEIVED':      { 'id-ID': 'Pembayaran Diterima',           'en-US': 'Payment Received' },
  'cat.GRADE_PUBLISHED':       { 'id-ID': 'Nilai Diumumkan',               'en-US': 'Grades Published' },
  'cat.REPORT_CARD':           { 'id-ID': 'Buku Rapor',                    'en-US': 'Report Card' },
  'cat.HOMEWORK':              { 'id-ID': 'Tugas Sekolah',                 'en-US': 'Homework' },
  'cat.CANTEEN':               { 'id-ID': 'Transaksi Kantin',              'en-US': 'Canteen' },
  'cat.ANNOUNCEMENT':          { 'id-ID': 'Pengumuman Sekolah',            'en-US': 'Announcements' },
  'cat.EMERGENCY':             { 'id-ID': 'Darurat',                       'en-US': 'Emergency' },
  'cat.WALLET_RECONCILIATION': { 'id-ID': 'Rekonsiliasi Dompet',           'en-US': 'Wallet Reconciliation' },

  // Channel labels
  'channel.WHATSAPP': { 'id-ID': 'WhatsApp',    'en-US': 'WhatsApp' },
  'channel.PUSH':     { 'id-ID': 'Notif Push',  'en-US': 'Push Notification' },
  'channel.SMS':      { 'id-ID': 'SMS',          'en-US': 'SMS' },
  'channel.EMAIL':    { 'id-ID': 'Email',        'en-US': 'Email' },

  // Language options
  'lang.id':  { 'id-ID': 'Bahasa Indonesia', 'en-US': 'Bahasa Indonesia' },
  'lang.en':  { 'id-ID': 'English',          'en-US': 'English' },

  // Biometric
  'bio.toggle':          { 'id-ID': 'Kunci Biometrik',                     'en-US': 'Biometric Lock' },
  'bio.toggle_sub':      { 'id-ID': 'Gunakan sidik jari / Face ID untuk membuka aplikasi', 'en-US': 'Use fingerprint / Face ID to unlock the app' },
  'bio.enroll_prompt':   { 'id-ID': 'Konfirmasi untuk mengaktifkan kunci biometrik', 'en-US': 'Confirm to enable biometric lock' },
  'bio.not_available':   { 'id-ID': 'Biometrik tidak tersedia di perangkat ini', 'en-US': 'Biometric not available on this device' },
  'bio.not_enrolled':    { 'id-ID': 'Tidak ada biometrik terdaftar di perangkat ini', 'en-US': 'No biometrics enrolled on this device' },
  'bio.auth_prompt':     { 'id-ID': 'Masuk ke EduCore',                   'en-US': 'Sign in to EduCore' },
  'bio.auth_failed':     { 'id-ID': 'Autentikasi biometrik gagal. Silakan masuk ulang.', 'en-US': 'Biometric authentication failed. Please log in again.' },
  'bio.cancel':          { 'id-ID': 'Batal',                               'en-US': 'Cancel' },
};

/**
 * Translate a string key to the given locale.
 * Falls back to id-ID if the key is missing for en-US.
 * Falls back to defaultText or key if missing entirely.
 */
export function t(key: string, locale: Locale = 'id-ID', defaultText?: string): string {
  const entry = strings[key];
  if (!entry) return defaultText ?? key;
  return entry[locale] ?? entry['id-ID'] ?? defaultText ?? key;
}
