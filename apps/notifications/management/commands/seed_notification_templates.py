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
        'body': 'Pengingat: Tagihan {invoice_number} sebesar {amount} untuk ananda {student_name} jatuh tempo pada {due_date}. Silakan selesaikan pembayaran.',
        'variables': ['invoice_number', 'amount', 'student_name', 'due_date'],
    },
    {
        'key': 'finance.payment_received',
        'channel': ChannelType.WHATSAPP,
        'locale': 'id-ID',
        'subject': 'Bukti Pembayaran Diterima',
        'body': 'Terima kasih: Pembayaran sebesar {amount} untuk tagihan {invoice_number} ananda {student_name} telah kami terima.',
        'variables': ['invoice_number', 'amount', 'student_name'],
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
