from io import StringIO
import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.finance.models import (
    FeeCategory,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    SchoolArrearsPolicy,
)
from apps.finance.services import (
    evaluate_invoice_arrears,
    get_school_arrears_policy,
    is_invoice_reminder_still_needed,
    run_arrears_ladder,
)
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.notifications.models import (
    ChannelType,
    IntentStatus,
    NotificationCategory,
    NotificationIntent,
)
from apps.notifications.providers import (
    MockPushProvider,
    MockSmsProvider,
    MockWhatsAppProvider,
    register_provider,
)
from apps.notifications.services import process_intent
from educore.middleware.tenancy import (
    clear_current_foundation_id,
    set_current_foundation_id,
    tenant_context,
)


class ArrearsLadderTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        # Foundation A
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Arrears Test",
            brand_name="Arrears Test",
            npwp="01.222.333.4-555.000",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Arrears Test",
            npsn="30199999",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        # Users
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628111000001",
            email="finance@arrears.test.id",
            full_name="Staff Keuangan",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="wali@arrears.test.id",
            full_name="Budi Santoso",
        )

        # Person & Student
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3273010101010001",
            full_name="Ananda Santoso",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nisn="9998887771",
            nis="ARR-001",
            status=Student.STATUS_ACTIVE,
        )

        # Guardian & Link
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3273010101010002",
            full_name="Budi Santoso",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        self.guardian_link = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

        # Notification providers and templates
        register_provider(ChannelType.WHATSAPP, MockWhatsAppProvider())
        register_provider(ChannelType.PUSH, MockPushProvider())
        register_provider(ChannelType.SMS, MockSmsProvider())
        call_command('seed_notification_templates', stdout=StringIO())

        # Fee & Invoice
        self.fee_type = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_MONTHLY",
            name="SPP Bulanan",
            category=FeeCategory.SPP,
            recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('1000000.00'),
            currency='IDR',
        )

        self.due_date = datetime.date(2026, 9, 10)
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/2026/09/0001",
            period="2026-09",
            currency="IDR",
            subtotal=Decimal('1000000.00'),
            total=Decimal('1000000.00'),
            paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
            due_date=self.due_date,
            issue_date=datetime.date(2026, 9, 1),
        )
        InvoiceLine.objects.create(
            foundation_id=self.foundation.id,
            invoice=self.invoice,
            fee_type=self.fee_type,
            code=self.fee_type.code,
            description="SPP September 2026",
            amount=Decimal('1000000.00'),
            subtotal=Decimal('1000000.00'),
            currency="IDR",
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_default_ladder_offsets_evaluation(self):
        """FIN-026: Default ladder offsets [-3, 0, 3, 7, 14, 30]."""
        ladder = [-3, 0, 3, 7, 14, 30]

        # T-3 days (2026-09-07)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 7), ladder), -3)
        # T-0 days (2026-09-10)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 10), ladder), 0)
        # T+3 days (2026-09-13)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 13), ladder), 3)
        # T+7 days (2026-09-17)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 17), ladder), 7)
        # T+14 days (2026-09-24)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 24), ladder), 14)
        # T+30 days (2026-10-10)
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 10, 10), ladder), 30)

        # Offsets not in ladder should return None
        self.assertIsNone(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 8), ladder))
        self.assertIsNone(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 11), ladder))
        self.assertIsNone(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 20), ladder))

    def test_custom_school_ladder_policy(self):
        """School policy allows custom ladder days override."""
        policy = get_school_arrears_policy(self.school)
        policy.ladder_days = [-5, 0, 5, 10]
        policy.save()

        self.assertEqual(policy.get_effective_ladder_days(), [-5, 0, 5, 10])

        # T-5 matches custom ladder
        self.assertEqual(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 5), policy.get_effective_ladder_days()), -5)
        # T-3 does not match custom ladder
        self.assertIsNone(evaluate_invoice_arrears(self.invoice, datetime.date(2026, 9, 7), policy.get_effective_ladder_days()))

    def test_run_arrears_ladder_dispatch_and_deduplication(self):
        """run_arrears_ladder creates notification intent and prevents duplicate reminders (FIN-027)."""
        as_of_date = datetime.date(2026, 9, 7)  # T-3

        with tenant_context(self.foundation.id):
            result = run_arrears_ladder(self.school, as_of_date=as_of_date)
            self.assertEqual(result['eligible_count'], 1)
            self.assertEqual(result['dispatched_count'], 1)
            self.assertEqual(result['skipped_existing_count'], 0)

            # Check created NotificationIntent
            intent = NotificationIntent.objects.filter(
                school_id=self.school.id,
                category=NotificationCategory.PAYMENT_DUE,
            ).first()
            self.assertIsNotNone(intent)
            self.assertEqual(intent.template_key, 'finance.payment_due')
            self.assertEqual(intent.payload['student_name'], 'Ananda Santoso')
            self.assertEqual(intent.payload['period'], '2026-09')
            self.assertIn('deep_link', intent.payload)
            self.assertEqual(intent.dedupe_key, f"arrears_reminder:{self.invoice.id}:-3:{self.guardian.id}")

            # Re-running for same date / step should skip
            result_second = run_arrears_ladder(self.school, as_of_date=as_of_date)
            self.assertEqual(result_second['eligible_count'], 1)
            self.assertEqual(result_second['dispatched_count'], 0)
            self.assertEqual(result_second['skipped_existing_count'], 1)

    def test_send_time_validation_cancels_when_paid(self):
        """NTF-004 & FIN-027: If invoice is settled before send time, intent is cancelled at send time."""
        as_of_date = datetime.date(2026, 9, 10)  # T-0

        with tenant_context(self.foundation.id):
            run_arrears_ladder(self.school, as_of_date=as_of_date)
            intent = NotificationIntent.objects.filter(
                school_id=self.school.id,
                category=NotificationCategory.PAYMENT_DUE,
            ).first()
            self.assertIsNotNone(intent)

            # Simulate invoice settlement prior to send time
            self.invoice.status = InvoiceStatus.PAID
            self.invoice.paid = Decimal('1000000.00')
            self.invoice.save()

            # Direct validator test
            self.assertFalse(is_invoice_reminder_still_needed(intent))

            # Process intent should transition status to CANCELLED per NTF-004
            process_intent(intent.id)
            intent.refresh_from_db()
            self.assertEqual(intent.status, IntentStatus.CANCELLED)
            self.assertIn("lunas", intent.cancellation_reason.lower())

    def test_partially_paid_invoice_reflects_remaining_balance(self):
        """Arrears reminder on partially paid invoice uses remaining balance (FIN-028)."""
        self.invoice.status = InvoiceStatus.PARTIALLY_PAID
        self.invoice.paid = Decimal('300000.00')
        self.invoice.save()

        as_of_date = datetime.date(2026, 9, 13)  # T+3

        with tenant_context(self.foundation.id):
            result = run_arrears_ladder(self.school, as_of_date=as_of_date)
            self.assertEqual(result['dispatched_count'], 1)

            intent = NotificationIntent.objects.filter(
                school_id=self.school.id,
                category=NotificationCategory.PAYMENT_DUE,
            ).latest('id')
            self.assertEqual(intent.payload['balance_due'], '700000.00')
            self.assertIn("700.000", intent.payload['amount'])

    def test_dry_run_mode(self):
        """Dry run simulates matching invoices without queueing notification intents."""
        as_of_date = datetime.date(2026, 9, 7)

        with tenant_context(self.foundation.id):
            initial_count = NotificationIntent.objects.count()
            result = run_arrears_ladder(self.school, as_of_date=as_of_date, dry_run=True)

            self.assertEqual(result['eligible_count'], 1)
            self.assertEqual(result['dispatched_count'], 0)
            self.assertEqual(len(result['reminders']), 1)
            self.assertEqual(result['reminders'][0]['invoice_id'], self.invoice.id)
            self.assertEqual(NotificationIntent.objects.count(), initial_count)

    def test_inactive_policy_skips_reminders(self):
        """When policy is deactivated, no reminders are evaluated or dispatched."""
        policy = get_school_arrears_policy(self.school)
        policy.is_active = False
        policy.save()

        as_of_date = datetime.date(2026, 9, 7)
        with tenant_context(self.foundation.id):
            result = run_arrears_ladder(self.school, as_of_date=as_of_date)
            self.assertFalse(result['is_active'])
            self.assertEqual(result['evaluated_count'], 0)
            self.assertEqual(result['dispatched_count'], 0)

    def test_api_get_and_put_policy(self):
        """API endpoints for viewing and updating arrears ladder policy."""
        self.client.force_authenticate(user=self.finance_user)

        # GET policy (auto-creates default policy)
        res_get = self.client.get(f'/api/v1/finance/schools/{self.school.id}/arrears-policy/')
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()['effective_ladder_days'], [-3, 0, 3, 7, 14, 30])
        self.assertTrue(res_get.json()['is_active'])

        # PUT policy with custom ladder
        payload = {
            'ladder_days': [-7, 0, 7, 14],
            'is_active': True,
            'payment_deep_link_base': 'https://pay.school.id/invoices/{invoice_id}',
        }
        res_put = self.client.put(f'/api/v1/finance/schools/{self.school.id}/arrears-policy/', payload, format='json')
        self.assertEqual(res_put.status_code, 200)
        self.assertEqual(res_put.json()['effective_ladder_days'], [-7, 0, 7, 14])
        self.assertEqual(res_put.json()['payment_deep_link_base'], 'https://pay.school.id/invoices/{invoice_id}')

    def test_cross_tenant_isolation(self):
        """Assert cross-tenant 404 when user from Foundation B accesses School A policy."""
        # Foundation B
        foundation_b = Foundation.objects.create(
            legal_name="Yayasan B", brand_name="Yayasan B", npwp="01.888.999.0-111.000", address="Jakarta",
        )
        user_b = User.objects.create(
            foundation_id=foundation_b.id,
            phone_e164="+628999999999",
            email="userb@yayasanb.sch.id",
            full_name="User B",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=foundation_b.id,
            user=user_b,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=foundation_b.id,
        )

        self.client.force_authenticate(user=user_b)
        res = self.client.get(f'/api/v1/finance/schools/{self.school.id}/arrears-policy/')
        self.assertEqual(res.status_code, 404)
