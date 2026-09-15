import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase

from apps.finance.models import (
    FeeCategory,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
)
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    School,
    Student,
    User,
)
from apps.notifications.models import (
    ChannelType,
    NotificationCategory,
    NotificationIntent,
)
from apps.notifications.providers import (
    MockPushProvider,
    MockSmsProvider,
    MockWhatsAppProvider,
    register_provider,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class RunArrearsLadderCommandTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()

        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Command Test",
            brand_name="Command Test",
            npwp="01.999.888.7-666.000",
            address="Yogyakarta",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Command Test",
            npsn="30999991",
            level=School.LEVEL_SMA,
            base_currency="IDR",
            is_active=True,
        )

        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281299998888",
            email="wali.cmd@example.sch.id",
            full_name="Pak Command",
        )
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101010001",
            full_name="Murid Command",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nisn="1122334455",
            nis="CMD-01",
            status=Student.STATUS_ACTIVE,
        )
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101010002",
            full_name="Pak Command",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
        )

        register_provider(ChannelType.WHATSAPP, MockWhatsAppProvider())
        register_provider(ChannelType.PUSH, MockPushProvider())
        register_provider(ChannelType.SMS, MockSmsProvider())
        call_command('seed_notification_templates', stdout=open('nul', 'w'))

        self.fee_type = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_CMD",
            name="SPP Command",
            category=FeeCategory.SPP,
            recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('500000.00'),
            currency='IDR',
        )

        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/2026/09/9999",
            period="2026-09",
            currency="IDR",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
            due_date=datetime.date(2026, 9, 10),
            issue_date=datetime.date(2026, 9, 1),
        )
        InvoiceLine.objects.create(
            foundation_id=self.foundation.id,
            invoice=self.invoice,
            fee_type=self.fee_type,
            code=self.fee_type.code,
            description="SPP September 2026",
            amount=Decimal('500000.00'),
            subtotal=Decimal('500000.00'),
            currency="IDR",
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_command_execution_with_as_of_date(self):
        """Management command dispatches reminders for matching ladder date (FIN-026)."""
        out = StringIO()
        call_command('run_arrears_ladder', '--as-of-date=2026-09-07', stdout=out)
        output = out.getvalue()

        self.assertIn("Arrears ladder completed", output)
        self.assertIn("Dispatched: 1", output)

        intents = NotificationIntent.objects.filter(
            school_id=self.school.id,
            category=NotificationCategory.PAYMENT_DUE,
        )
        self.assertEqual(intents.count(), 1)

    def test_command_dry_run_flag(self):
        """Management command --dry-run does not queue notification intents."""
        out = StringIO()
        call_command('run_arrears_ladder', '--as-of-date=2026-09-07', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn("dry run", output.lower())
        intents = NotificationIntent.objects.filter(category=NotificationCategory.PAYMENT_DUE)
        self.assertEqual(intents.count(), 0)

    def test_command_advisory_lock_already_held(self):
        """ARC-007: Command exits immediately if advisory lock cannot be acquired."""
        class MockLockContext:
            def __enter__(self):
                return False  # Lock acquisition failed
            def __exit__(self, *args):
                pass

        with patch('apps.finance.management.commands.run_arrears_ladder.advisory_lock', return_value=MockLockContext()):
            out = StringIO()
            call_command('run_arrears_ladder', stdout=out)
            output = out.getvalue()
            self.assertIn("already held", output)
