from django.core.management.base import BaseCommand
from apps.identity.models import Foundation
from apps.notifications.models import (
    ChannelType,
    NotificationTemplate,
    TemplateApprovalStatus,
)


CANONICAL_TEMPLATES = [
    {
        'key': 'attendance.arrival',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Pemberitahuan Kehadiran Siswa',
        'body': 'Ananda {student_name} telah tiba di sekolah ({school_name}) melalui {gate_name} pada pukul {time} WIB.',
        'variables': ['student_name', 'school_name', 'gate_name', 'time', 'date'],
    },
    {
        'key': 'attendance.arrival',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Kehadiran Siswa: {student_name}',
        'body': '{student_name} telah tiba di sekolah ({gate_name}) pukul {time} WIB.',
        'variables': ['student_name', 'school_name', 'gate_name', 'time', 'date'],
    },
    {
        'key': 'attendance.departure',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Pemberitahuan Kepulangan Siswa',
        'body': 'Ananda {student_name} telah keluar dari sekolah ({school_name}) melalui {gate_name} pada pukul {time} WIB.',
        'variables': ['student_name', 'school_name', 'gate_name', 'time', 'date'],
    },
    {
        'key': 'attendance.departure',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Kepulangan Siswa: {student_name}',
        'body': '{student_name} telah meninggalkan sekolah ({gate_name}) pukul {time} WIB.',
        'variables': ['student_name', 'school_name', 'gate_name', 'time', 'date'],
    },
    {
        'key': 'emergency.alert',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'PERINGATAN DARURAT SEKOLAH',
        'body': 'PERINGATAN DARURAT: {message}. Harap segera hubungi pihak sekolah {school_name}.',
        'variables': ['message', 'school_name'],
    },
    {
        'key': 'emergency.alert',
        'channel': ChannelType.SMS,
        'locale': 'id-ID',
        'subject': 'DARURAT',
        'body': 'DARURAT: {message}. Hubungi {school_name} segera.',
        'variables': ['message', 'school_name'],
    },
    {
        'key': 'finance.payment_due',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Pengingat Tagihan Pembayaran',
        'body': 'Pengingat: Tagihan {invoice_number} ({period}) sebesar {amount} untuk ananda {student_name} jatuh tempo pada {due_date}. Pembayaran dapat dilakukan melalui tautan: {deep_link}',
        'variables': ['invoice_number', 'amount', 'student_name', 'due_date', 'period', 'deep_link'],
    },
    {
        'key': 'finance.payment_due',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Tagihan Pembayaran: {student_name}',
        'body': 'Tagihan {invoice_number} ({period}) ananda {student_name} sebesar {amount} jatuh tempo {due_date}. Bayar: {deep_link}',
        'variables': ['invoice_number', 'amount', 'student_name', 'due_date', 'period', 'deep_link'],
    },
    {
        'key': 'finance.payment_due',
        'channel': ChannelType.SMS,
        'locale': 'id-ID',
        'subject': 'Tagihan Pembayaran',
        'body': 'Tagihan EduCore ananda {student_name} periode {period} jatuh tempo {due_date}. Buka aplikasi untuk bayar: {deep_link}',
        'variables': ['student_name', 'period', 'due_date', 'deep_link'],
    },
    {
        'key': 'finance.payment_received',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Bukti Pembayaran Diterima',
        'body': 'Terima kasih: Pembayaran sebesar {amount} untuk tagihan {invoice_number} ananda {student_name} telah kami terima.',
        'variables': ['invoice_number', 'amount', 'student_name'],
    },
    # PAR-008: Payment confirmation push within 30s of settlement.
    {
        'key': 'finance.payment_received',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Pembayaran Diterima',
        'body': 'Pembayaran sebesar {amount} untuk ananda {student_name} telah diterima. {invoice_info}',
        'variables': ['amount', 'student_name', 'invoice_info', 'payment_reference'],
    },
    # spec/17 §5 — closes the WAL-017 "notify the guardian" gap. Meta approval for the
    # WhatsApp variants is a launch blocker for the canteen module.
    {
        'key': 'wallet.recon.notice',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Saldo Dompet Kantin Perlu Dilengkapi',
        'body': 'Yth. {guardian_name}, pembelian {student_name} di kantin {school_name} pada {detected_date} melebihi saldo dompet sebesar Rp {shortfall} ({txn_count} transaksi). Ini terjadi karena terminal kantin sedang luring, sehingga transaksi tetap diproses. Saldo dompet kini minus. Mohon isi ulang sebelum {deadline_date}. Buka aplikasi: {deep_link}',
        'variables': ['guardian_name', 'student_name', 'school_name', 'shortfall', 'txn_count', 'detected_date', 'deadline_date', 'deep_link'],
    },
    {
        'key': 'wallet.recon.notice',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Saldo Dompet Perlu Dilengkapi',
        'body': 'Saldo dompet kantin {student_name} minus Rp {shortfall}. Mohon isi ulang sebelum {deadline_date}.',
        'variables': ['student_name', 'shortfall', 'deadline_date'],
    },
    {
        'key': 'wallet.recon.reminder',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Pengingat: Saldo Dompet Kantin Masih Minus',
        'body': 'Pengingat: Saldo dompet kantin {student_name} masih minus Rp {shortfall} sejak {detected_date}. Jika belum diselesaikan sebelum {deadline_date}, jumlah ini akan dimasukkan ke tagihan berikutnya sebagai Penyesuaian Saldo Kantin.',
        'variables': ['student_name', 'shortfall', 'detected_date', 'deadline_date'],
    },
    {
        'key': 'wallet.recon.reminder',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Pengingat Saldo Dompet',
        'body': 'Saldo dompet kantin {student_name} masih minus Rp {shortfall}. Batas waktu {deadline_date}.',
        'variables': ['student_name', 'shortfall', 'deadline_date'],
    },
    {
        'key': 'wallet.recon.settled',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Saldo Dompet Telah Diselesaikan',
        'body': 'Terima kasih, saldo dompet kantin {student_name} telah diselesaikan.',
        'variables': ['student_name'],
    },
    {
        'key': 'wallet.recon.invoiced',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Saldo Dompet Kantin Dimasukkan ke Tagihan',
        'body': 'Saldo dompet kantin {student_name} sebesar Rp {shortfall} yang belum diselesaikan sejak {detected_date} kini dimasukkan ke tagihan sebagai Penyesuaian Saldo Kantin.',
        'variables': ['student_name', 'shortfall', 'detected_date'],
    },
    # ACD-019 — closes the "assign_substitution never notifies the substitute" gap.
    {
        'key': 'academic.substitution.assigned',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Penugasan Guru Pengganti',
        'body': 'Yth. Bapak/Ibu Guru, Anda ditugaskan menggantikan {original_teacher} mengajar {subject} di kelas {class_group} pada {date}, periode ke-{period_no}.',
        'variables': ['original_teacher', 'subject', 'class_group', 'date', 'period_no'],
    },
    {
        'key': 'academic.substitution.assigned',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Penugasan Guru Pengganti',
        'body': 'Anda menggantikan {original_teacher}: {subject} di {class_group}, {date} periode ke-{period_no}.',
        'variables': ['original_teacher', 'subject', 'class_group', 'date', 'period_no'],
    },
    # ACD-029/030 — closes the "homework never actually sends anything" gap.
    {
        'key': 'academic.homework.assigned',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Tugas Baru',
        'body': 'Tugas baru "{title}" ({subject}, {class_group}) untuk {due_at}.',
        'variables': ['title', 'subject', 'class_group', 'due_at'],
    },
    {
        'key': 'academic.homework.reminder',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Pengingat Tugas',
        'body': 'Pengingat: tugas "{title}" ({subject}) belum dikumpulkan, batas waktu {due_at}.',
        'variables': ['title', 'subject', 'due_at'],
    },
    # RPT-002 — closes the "export finishes with no notification" gap for async report exports.
    {
        'key': 'core.export.ready',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Ekspor {report_name} Siap',
        'body': 'Ekspor {report_name} ({format}) sudah siap diunduh. Tautan berlaku 24 jam: {deep_link}',
        'variables': ['report_name', 'format', 'deep_link'],
    },
    # PAR-012 — closes the "permission slip notification template seed" gap.
    # Dispatched by create_permission_slip on slip creation (ANNOUNCEMENT category).
    # Payload keys: message, permission_slip_id, student_name, title, class_group_name,
    # event_date (isoformat or ''), location.
    {
        'key': 'academic.permission_slip.new',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Permintaan Izin Baru',
        'body': 'Yth. Orang Tua/Wali {student_name}, terdapat permintaan izin baru dari sekolah: "{title}" untuk {class_group_name}. Mohon berikan persetujuan digital melalui aplikasi EduCore.',
        'variables': ['student_name', 'title', 'class_group_name', 'event_date', 'location'],
    },
    {
        'key': 'academic.permission_slip.new',
        'channel': ChannelType.PUSH,
        'locale': 'id-ID',
        'subject': 'Izin Baru: {title}',
        'body': 'Permintaan izin baru untuk {student_name}: "{title}". Buka aplikasi untuk menyetujui atau menolak.',
        'variables': ['student_name', 'title', 'class_group_name'],
    },
]


class Command(BaseCommand):
    help = "Seeds canonical id-ID notification templates for foundations (NTF-005, NTF-008)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--foundation-id',
            type=int,
            help="Foundation ID to seed templates for. If omitted, seeds for all foundations."
        )

    def handle(self, *args, **options):
        f_id = options.get('foundation_id')
        if f_id:
            foundations = Foundation.objects.filter(id=f_id)
        else:
            foundations = Foundation.objects.all()

        if not foundations.exists():
            self.stdout.write(self.style.WARNING("No foundations found. Run seed_demo_foundation first."))
            return

        total_seeded = 0
        for foundation in foundations:
            for item in CANONICAL_TEMPLATES:
                template, created = NotificationTemplate.all_tenants.update_or_create(
                    foundation_id=foundation.id,
                    key=item['key'],
                    channel=item['channel'],
                    locale=item['locale'],
                    version=1,
                    defaults={
                        'subject': item['subject'],
                        'body': item['body'],
                        'variables': item['variables'],
                        'approval_status': TemplateApprovalStatus.APPROVED,
                        'is_active': True,
                    }
                )
                total_seeded += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {total_seeded} notification templates."))
