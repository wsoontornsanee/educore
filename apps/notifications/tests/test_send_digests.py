import datetime
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.identity.models import Foundation, School, User
from apps.notifications.models import (
    IntentStatus,
    NotificationCategory,
    NotificationDelivery,
    NotificationIntent,
)
from apps.notifications.providers import (
    MockPushProvider,
    MockWhatsAppProvider,
    register_provider,
)
from apps.notifications.services import (
    build_digest_body,
    dispatch_intent,
    run_daily_digest,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class RunDailyDigestTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia Nusantara",
            brand_name="Cendekia",
            npwp="01.234.567.8-999.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Cendekia",
            npsn="20200001",
            level=School.LEVEL_SMP,
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="guardian@example.sch.id",
            full_name="Budi Pratama",
        )
        register_provider('WHATSAPP', MockWhatsAppProvider())
        register_provider('PUSH', MockPushProvider())

    def _create_held_intent(self, category, payload, dedupe_key, recipient_user=None):
        with tenant_context(self.foundation.id):
            return dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=recipient_user or self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                recipient_email=self.guardian_user.email,
                category=category,
                template_key='irrelevant.for.this.test',
                payload=payload,
                dedupe_key=dedupe_key,
            )

    def test_held_intents_default_to_pending_not_dispatched(self):
        # NTF-007 root gap: digest_only categories must NOT be dispatched immediately.
        intent = self._create_held_intent(
            NotificationCategory.HOMEWORK,
            {'student_name': 'Ali', 'subject': 'Matematika', 'title': 'PR Bab 3', 'due_at': '2026-09-19'},
            dedupe_key='homework:1:1',
        )
        self.assertEqual(intent.status, IntentStatus.PENDING)
        self.assertEqual(NotificationDelivery.objects.filter(intent=intent).count(), 0)

    def test_siblings_under_one_guardian_collapse_into_one_digest(self):
        self._create_held_intent(
            NotificationCategory.HOMEWORK,
            {'student_name': 'Ali', 'subject': 'Matematika', 'title': 'PR Bab 3', 'due_at': '2026-09-19'},
            dedupe_key='homework:1:1',
        )
        self._create_held_intent(
            NotificationCategory.BEHAVIOUR_MINOR,
            {'student_name': 'Sari', 'reason_label': 'Terlambat masuk kelas', 'points': -1},
            dedupe_key='behaviour:2:1',
        )

        with tenant_context(self.foundation.id):
            result = run_daily_digest(foundation_id=self.foundation.id, as_of=timezone.now())

        self.assertEqual(result['recipients'], 1)
        self.assertEqual(result['items_digested'], 2)
        self.assertEqual(result['digests_sent'], 1)

        digest_intents = NotificationIntent.all_tenants.filter(
            foundation_id=self.foundation.id, category=NotificationCategory.DAILY_DIGEST
        )
        self.assertEqual(digest_intents.count(), 1)
        digest_intent = digest_intents.first()
        self.assertIn('Ali', digest_intent.payload['digest_body'])
        self.assertIn('Sari', digest_intent.payload['digest_body'])
        self.assertEqual(digest_intent.status, IntentStatus.DISPATCHED)

        source_statuses = set(
            NotificationIntent.all_tenants.filter(
                foundation_id=self.foundation.id,
                category__in=[NotificationCategory.HOMEWORK, NotificationCategory.BEHAVIOUR_MINOR],
            ).values_list('status', flat=True)
        )
        self.assertEqual(source_statuses, {IntentStatus.DIGESTED})

    def test_high_and_critical_never_held_for_digest(self):
        with tenant_context(self.foundation.id):
            urgent_intent = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                category=NotificationCategory.EMERGENCY,
                template_key='emergency.alert',
                payload={'message': 'Uji Darurat'},
                dedupe_key='emergency:test:1',
            )
            result = run_daily_digest(foundation_id=self.foundation.id, as_of=timezone.now())

        # EMERGENCY was never PENDING+digest_only, so it's untouched by the digest run.
        self.assertEqual(result['digests_sent'], 0)
        urgent_intent.refresh_from_db()
        self.assertNotEqual(urgent_intent.status, IntentStatus.DIGESTED)

    def test_empty_run_sends_nothing(self):
        with tenant_context(self.foundation.id):
            result = run_daily_digest(foundation_id=self.foundation.id, as_of=timezone.now())
        self.assertEqual(result, {'recipients': 0, 'items_digested': 0, 'digests_sent': 0})
        self.assertEqual(
            NotificationIntent.all_tenants.filter(category=NotificationCategory.DAILY_DIGEST).count(), 0
        )

    def test_dry_run_makes_no_mutations(self):
        intent = self._create_held_intent(
            NotificationCategory.CANTEEN,
            {'student_name': 'Rudi', 'summary': 'Total belanja Rp 15.000'},
            dedupe_key='canteen:3:1',
        )
        with tenant_context(self.foundation.id):
            result = run_daily_digest(foundation_id=self.foundation.id, as_of=timezone.now(), dry_run=True)

        self.assertEqual(result['digests_sent'], 0)
        self.assertEqual(result['items_digested'], 1)
        intent.refresh_from_db()
        self.assertEqual(intent.status, IntentStatus.PENDING)
        self.assertEqual(
            NotificationIntent.all_tenants.filter(category=NotificationCategory.DAILY_DIGEST).count(), 0
        )

    def test_channel_override_restricts_delivery_target(self):
        self._create_held_intent(
            NotificationCategory.HOMEWORK,
            {'student_name': 'Ali', 'subject': 'Matematika', 'title': 'PR Bab 3', 'due_at': '2026-09-19'},
            dedupe_key='homework:channel:1',
        )
        with tenant_context(self.foundation.id):
            run_daily_digest(foundation_id=self.foundation.id, as_of=timezone.now(), channel='email')

        digest_intent = NotificationIntent.all_tenants.get(category=NotificationCategory.DAILY_DIGEST)
        self.assertEqual(digest_intent.recipient_phone, '')
        self.assertEqual(digest_intent.recipient_email, self.guardian_user.email)

    def test_build_digest_body_skips_empty_sections(self):
        body = build_digest_body({'Tugas Sekolah': ['Ali: Matematika - PR (batas: besok)'], 'Kosong': []})
        self.assertIn('Tugas Sekolah', body)
        self.assertNotIn('Kosong', body)


class SendDigestsCommandTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia Nusantara",
            brand_name="Cendekia",
            npwp="01.234.567.8-999.001",
            address="Jakarta",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Cendekia",
            npsn="20200002",
            level=School.LEVEL_SMP,
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567891",
            email="guardian2@example.sch.id",
            full_name="Sari Wulandari",
        )
        register_provider('WHATSAPP', MockWhatsAppProvider())
        register_provider('PUSH', MockPushProvider())

    def test_command_dry_run_reports_and_does_not_mutate(self):
        with tenant_context(self.foundation.id):
            dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                recipient_email=self.guardian_user.email,
                category=NotificationCategory.HOMEWORK,
                template_key='irrelevant',
                payload={'student_name': 'Dewi', 'subject': 'IPA', 'title': 'Laporan', 'due_at': '2026-09-20'},
                dedupe_key='homework:cmd:1',
            )

        out = StringIO()
        call_command('send_digests', '--foundation-id', self.foundation.id, '--dry-run', stdout=out, force=True)

        self.assertIn('Would digest 1 item(s)', out.getvalue())
        self.assertEqual(
            NotificationIntent.all_tenants.filter(category=NotificationCategory.DAILY_DIGEST).count(), 0
        )

    def test_command_sends_and_marks_digested(self):
        with tenant_context(self.foundation.id):
            dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                recipient_email=self.guardian_user.email,
                category=NotificationCategory.HOMEWORK,
                template_key='irrelevant',
                payload={'student_name': 'Dewi', 'subject': 'IPA', 'title': 'Laporan', 'due_at': '2026-09-20'},
                dedupe_key='homework:cmd:2',
            )

        out = StringIO()
        call_command('send_digests', '--foundation-id', self.foundation.id, stdout=out, force=True)

        self.assertIn('Digested 1 item(s) into 1 digest message(s)', out.getvalue())
        self.assertEqual(
            NotificationIntent.all_tenants.filter(category=NotificationCategory.DAILY_DIGEST).count(), 1
        )


class SendDueNotificationsExcludesDigestTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia Nusantara",
            brand_name="Cendekia",
            npwp="01.234.567.8-999.002",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Cendekia",
            npsn="20200003",
            level=School.LEVEL_SMP,
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567892",
            email="guardian3@example.sch.id",
            full_name="Andi Saputra",
        )
        register_provider('WHATSAPP', MockWhatsAppProvider())
        register_provider('PUSH', MockPushProvider())

    def test_send_due_notifications_leaves_digest_only_intents_pending(self):
        with tenant_context(self.foundation.id):
            homework_intent = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                category=NotificationCategory.HOMEWORK,
                template_key='irrelevant',
                payload={'student_name': 'Dewi'},
                dedupe_key='homework:due:1',
            )
            arrival_intent = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.guardian_user,
                recipient_phone=self.guardian_user.phone_e164,
                category=NotificationCategory.ARRIVAL,
                template_key='attendance.arrival',
                payload={'student_name': 'Dewi', 'school_name': 'SMP Cendekia', 'gate_name': 'Gerbang', 'time': '07:00'},
                dedupe_key='arrival:due:1',
            )

        out = StringIO()
        call_command('send_due_notifications', stdout=out, force=True)

        homework_intent.refresh_from_db()
        arrival_intent.refresh_from_db()
        self.assertEqual(homework_intent.status, IntentStatus.PENDING)
        self.assertEqual(arrival_intent.status, IntentStatus.DISPATCHED)
