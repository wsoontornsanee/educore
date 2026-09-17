from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import Foundation, School, User
from apps.notifications.models import (
    ChannelType,
    NotificationCategory,
    NotificationPreference,
    NotificationTemplate,
    TemplateApprovalStatus,
)
from apps.notifications.services import render_template_message
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class NotificationTemplateAndPreferenceTests(TestCase):
    def setUp(self):
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Pendidikan A",
            brand_name="Yayasan A",
            npwp="01.234.567.8-901.000",
            address="Jakarta",
        )
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Pendidikan B",
            brand_name="Yayasan B",
            npwp="01.234.567.8-902.000",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation_a.id)
        self.user_a = User.objects.create(
            foundation_id=self.foundation_a.id,
            phone_e164="+628111111111",
            email="wali_a@example.sch.id",
            full_name="Wali Murid A",
        )

    def test_template_creation_and_tenancy_isolation(self):
        with tenant_context(self.foundation_a.id):
            template_a = NotificationTemplate.objects.create(
                foundation_id=self.foundation_a.id,
                key='attendance.arrival',
                channel=ChannelType.WHATSAPP,
                locale='id-ID',
                subject='Kehadiran Siswa',
                body='Ananda {student_name} tiba di {school_name} pukul {time}.',
                variables=['student_name', 'school_name', 'time'],
                version=1,
            )
            self.assertEqual(NotificationTemplate.objects.count(), 1)

        # Cross-tenant check: Foundation B cannot see Foundation A's template
        with tenant_context(self.foundation_b.id):
            self.assertEqual(NotificationTemplate.objects.count(), 0)

    def test_template_rendering_id_id_fallback(self):
        with tenant_context(self.foundation_a.id):
            NotificationTemplate.objects.create(
                foundation_id=self.foundation_a.id,
                key='attendance.arrival',
                channel=ChannelType.WHATSAPP,
                locale='id-ID',
                subject='Kehadiran Siswa {student_name}',
                body='Ananda {student_name} telah hadir di {school_name} melalui {gate_name} pada {time} WIB.',
                variables=['student_name', 'school_name', 'gate_name', 'time'],
            )

            res = render_template_message(
                template_key='attendance.arrival',
                channel=ChannelType.WHATSAPP,
                foundation_id=self.foundation_a.id,
                payload={
                    'student_name': 'Ahmad Fauzi',
                    'school_name': 'SD Al-Hikmah',
                    'gate_name': 'Gerbang Utama',
                    'time': '06:45',
                }
            )
            self.assertEqual(res['subject'], 'Kehadiran Siswa Ahmad Fauzi')
            self.assertIn('Ahmad Fauzi telah hadir di SD Al-Hikmah melalui Gerbang Utama pada 06:45 WIB.', res['body'])

    def test_notification_preference_and_quiet_hours(self):
        with tenant_context(self.foundation_a.id):
            pref = NotificationPreference.objects.create(
                foundation_id=self.foundation_a.id,
                user=self.user_a,
                category=NotificationCategory.PAYMENT_DUE,
                channels=[ChannelType.WHATSAPP, ChannelType.EMAIL],
                enabled=True,
            )
            self.assertEqual(pref.channels, [ChannelType.WHATSAPP, ChannelType.EMAIL])
            self.assertEqual(str(pref.quiet_hours_start), '21:00:00')
            self.assertEqual(str(pref.quiet_hours_end), '06:00:00')

    def test_permission_slip_new_builtin_fallback(self):
        """Built-in fallback renders for academic.permission_slip.new without a seeded template."""
        res = render_template_message(
            template_key='academic.permission_slip.new',
            channel=ChannelType.WHATSAPP,
            foundation_id=self.foundation_a.id,
            payload={
                'message': 'Permintaan izin baru: Field Trip ke Museum. Mohon berikan persetujuan digital di aplikasi.',
                'permission_slip_id': 1,
                'student_name': 'Ahmad Fauzi',
            },
        )
        self.assertEqual(res['subject'], 'Permintaan Izin Baru')
        self.assertIn('Permintaan izin baru:', res['body'])
        self.assertIn('Field Trip ke Museum', res['body'])

    def test_permission_slip_new_seeded_template_whatsapp(self):
        """Seeded WhatsApp template renders all structured variables."""
        with tenant_context(self.foundation_a.id):
            NotificationTemplate.objects.create(
                foundation_id=self.foundation_a.id,
                key='academic.permission_slip.new',
                channel=ChannelType.WHATSAPP,
                locale='id-ID',
                subject='Permintaan Izin Baru',
                body='Yth. Orang Tua/Wali {student_name}, terdapat permintaan izin baru dari sekolah: "{title}" untuk {class_group_name}. Mohon berikan persetujuan digital melalui aplikasi EduCore.',
                variables=['student_name', 'title', 'class_group_name', 'event_date', 'location'],
                version=1,
                approval_status=TemplateApprovalStatus.APPROVED,
                is_active=True,
            )

            res = render_template_message(
                template_key='academic.permission_slip.new',
                channel=ChannelType.WHATSAPP,
                foundation_id=self.foundation_a.id,
                payload={
                    'student_name': 'Ahmad Fauzi',
                    'title': 'Field Trip ke Museum',
                    'class_group_name': '7A',
                    'event_date': '2026-09-20',
                    'location': 'Museum Nasional',
                    'message': 'Permintaan izin baru: Field Trip ke Museum.',
                    'permission_slip_id': 42,
                },
            )
            self.assertEqual(res['subject'], 'Permintaan Izin Baru')
            self.assertIn('Ahmad Fauzi', res['body'])
            self.assertIn('Field Trip ke Museum', res['body'])
            self.assertIn('7A', res['body'])

    def test_permission_slip_new_seeded_template_push(self):
        """Seeded PUSH template renders concise format."""
        with tenant_context(self.foundation_a.id):
            NotificationTemplate.objects.create(
                foundation_id=self.foundation_a.id,
                key='academic.permission_slip.new',
                channel=ChannelType.PUSH,
                locale='id-ID',
                subject='Izin Baru: {title}',
                body='Permintaan izin baru untuk {student_name}: "{title}". Buka aplikasi untuk menyetujui atau menolak.',
                variables=['student_name', 'title', 'class_group_name'],
                version=1,
                approval_status=TemplateApprovalStatus.APPROVED,
                is_active=True,
            )

            res = render_template_message(
                template_key='academic.permission_slip.new',
                channel=ChannelType.PUSH,
                foundation_id=self.foundation_a.id,
                payload={
                    'student_name': 'Ahmad Fauzi',
                    'title': 'Field Trip ke Museum',
                    'class_group_name': '7A',
                    'message': 'Permintaan izin baru: Field Trip ke Museum.',
                    'permission_slip_id': 42,
                },
            )
            self.assertEqual(res['subject'], 'Izin Baru: Field Trip ke Museum')
            self.assertIn('Ahmad Fauzi', res['body'])
            self.assertIn('Field Trip ke Museum', res['body'])
