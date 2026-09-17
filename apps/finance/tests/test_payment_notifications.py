"""
PAR-008: Payment confirmation MUST arrive as a push within 30 seconds of settlement.
Tests that all three settlement paths dispatch PAYMENT_RECEIVED notifications.
"""
import hashlib
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
)
from apps.finance.services.payments import (
    process_payment_webhook,
    record_cash_payment,
    submit_manual_transfer,
    verify_manual_transfer,
)
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def _make_mock_settlement_payload(
    order_id: str,
    amount: Decimal = Decimal('500000.00'),
    fee: Decimal = Decimal('2500.00'),
    channel: str = 'BCA_VA',
    secret: str = 'mock-secret',
) -> dict:
    """Build a deterministic mock webhook payload that MockPaymentProvider accepts."""
    sig = hashlib.sha256(f"{order_id}{amount}{secret}".encode('utf-8')).hexdigest()
    return {
        'order_id': order_id,
        'amount': str(amount),
        'fee': str(fee),
        'status': 'SETTLED',
        'channel': channel,
        'signature': sig,
    }


class PaymentNotificationDispatchTests(TestCase):
    """PAR-008: Verify dispatch_intent is called on all three settlement paths."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Edu Nusantara",
            brand_name="Edu Nusantara",
            npwp="01.234.567.8-999.000",
            address="Jakarta Pusat",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Edu Nusantara",
            npsn="20199999",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        # Student
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Ani Rahmawati",
            nik="3171010101010005",
            gender=Person.GENDER_FEMALE,
            dob="2016-04-10",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="202699001",
            nisn="0098765432",
            status=Student.STATUS_ACTIVE,
        )

        # Guardian user and person
        self.guardian_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Ibu Dewi Sartika",
            nik="3171010101010006",
            gender=Person.GENDER_FEMALE,
            dob="1985-07-22",
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="dewi@example.com",
            full_name="Ibu Dewi Sartika",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.guardian_person,
            user=self.guardian_user,
        )
        self.guardian_link = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_MOTHER,
            is_primary=True,
            financial_responsible=True,
        )

        # Invoice
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20199999/2026/001000",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # Finance officer (for cash & manual paths)
        self.finance_officer = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281212345678",
            email="bendahara@edunusantara.sch.id",
            full_name="Bapak Ahmad (Bendahara)",
        )

    def tearDown(self):
        clear_current_foundation_id()

    # --- Gateway webhook settlement path ---

    @patch('apps.notifications.services.dispatch_intent')
    def test_webhook_settlement_dispatches_notification(self, mock_dispatch):
        """PAR-008: Gateway webhook settlement triggers dispatch_intent."""
        payload = _make_mock_settlement_payload(order_id=f"INT-{self.invoice.id}")
        result = process_payment_webhook(provider_name='MOCK', payload=payload)

        self.assertEqual(result['status'], 'settled')
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        self.assertEqual(call_kwargs['category'], 'PAYMENT_RECEIVED')
        self.assertEqual(call_kwargs['template_key'], 'finance.payment_received')
        self.assertEqual(call_kwargs['foundation_id'], self.foundation.id)
        self.assertEqual(call_kwargs['school_id'], self.school.id)
        self.assertEqual(call_kwargs['recipient_user'], self.guardian_user)
        self.assertTrue(call_kwargs['immediate'])
        self.assertIn('payment_received', call_kwargs.get('dedupe_key', ''))

    @patch('apps.notifications.services.dispatch_intent')
    def test_webhook_settlement_payload_has_amount_and_student(self, mock_dispatch):
        """PAR-008: Notification payload includes amount, student_name, invoice_info."""
        payload = _make_mock_settlement_payload(order_id=f"INT-{self.invoice.id}")
        process_payment_webhook(provider_name='MOCK', payload=payload)

        payload = mock_dispatch.call_args[1]['payload']
        self.assertEqual(payload['student_name'], 'Ani Rahmawati')
        self.assertEqual(payload['amount'], '500000.00')
        self.assertIn(self.invoice.number, payload['invoice_info'])

    # --- Cash payment path ---

    @patch('apps.notifications.services.dispatch_intent')
    def test_cash_payment_dispatches_notification(self, mock_dispatch):
        """PAR-008: Cash payment settlement triggers dispatch_intent."""
        payment = record_cash_payment(
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            invoice_ids=[self.invoice.id],
            received_by=self.finance_officer,
        )

        self.assertEqual(payment.status, PaymentStatus.SETTLED)
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        self.assertEqual(call_kwargs['category'], 'PAYMENT_RECEIVED')
        self.assertEqual(call_kwargs['template_key'], 'finance.payment_received')
        self.assertEqual(call_kwargs['recipient_user'], self.guardian_user)
        self.assertTrue(call_kwargs['immediate'])

    # --- Manual transfer verification path ---

    @patch('apps.notifications.services.dispatch_intent')
    def test_manual_transfer_approval_dispatches_notification(self, mock_dispatch):
        """PAR-008: Manual transfer verification (approve) triggers dispatch_intent."""
        payment = submit_manual_transfer(
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            invoice_ids=[self.invoice.id],
            channel='MANUAL_TRANSFER',
        )
        verify_manual_transfer(
            payment=payment,
            verified_by=self.finance_officer,
            decision='APPROVE',
        )

        self.assertEqual(payment.status, PaymentStatus.SETTLED)
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        self.assertEqual(call_kwargs['category'], 'PAYMENT_RECEIVED')
        self.assertEqual(call_kwargs['template_key'], 'finance.payment_received')
        self.assertEqual(call_kwargs['recipient_user'], self.guardian_user)
        self.assertTrue(call_kwargs['immediate'])

    # --- Edge cases ---

    @patch('apps.notifications.services.dispatch_intent')
    def test_webhook_already_settled_does_not_dispatch_again(self, mock_dispatch):
        """PAR-008: Idempotent webhook replay does not dispatch duplicate notification."""
        payload = _make_mock_settlement_payload(order_id=f"INT-{self.invoice.id}")
        # First call dispatches
        process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(mock_dispatch.call_count, 1)

        # Second call (idempotent) should NOT dispatch
        result = process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(result['status'], 'already_settled')
        self.assertEqual(mock_dispatch.call_count, 1)

    @patch('apps.notifications.services.dispatch_intent')
    def test_manual_transfer_rejection_does_not_dispatch(self, mock_dispatch):
        """PAR-008: Manual transfer rejection does not trigger notification."""
        payment = submit_manual_transfer(
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            invoice_ids=[self.invoice.id],
            channel='MANUAL_TRANSFER',
        )
        verify_manual_transfer(
            payment=payment,
            verified_by=self.finance_officer,
            decision='REJECT',
            reason='Bukti transfer tidak jelas',
        )

        self.assertEqual(payment.status, PaymentStatus.REJECTED)
        mock_dispatch.assert_not_called()

    @patch('apps.notifications.services.dispatch_intent')
    def test_no_guardian_does_not_crash(self, mock_dispatch):
        """PAR-008: Settlement with no guardian links is handled gracefully."""
        # Remove guardian link
        self.guardian_link.delete()

        payload = _make_mock_settlement_payload(order_id=f"INT-{self.invoice.id}")
        # Should not raise
        result = process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(result['status'], 'settled')
        mock_dispatch.assert_not_called()

    @patch('apps.notifications.services.dispatch_intent')
    def test_guardian_without_user_skipped_gracefully(self, mock_dispatch):
        """PAR-008: Guardian with no linked user account is skipped, not crashed."""
        # Create a second guardian with no user
        extra_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Bapak Anto",
            nik="3171010101010007",
            gender=Person.GENDER_MALE,
            dob="1982-01-15",
        )
        extra_guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=extra_person,
            user=None,  # No linked user
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=extra_guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=False,
            financial_responsible=True,
        )

        payload = _make_mock_settlement_payload(order_id=f"INT-{self.invoice.id}")
        # Should not raise; should dispatch to the first guardian only
        result = process_payment_webhook(provider_name='MOCK', payload=payload)
        self.assertEqual(result['status'], 'settled')
        # Called for the guardian WITH a user
        mock_dispatch.assert_called_once()

    @patch('apps.notifications.services.dispatch_intent')
    def test_cash_payment_dispatch_on_multiple_invoices(self, mock_dispatch):
        """PAR-008: Cash payment across multiple invoices includes all invoice numbers."""
        inv2 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20199999/2026/001001",
            period="2026-11",
            due_date="2026-11-10",
            subtotal=Decimal('300000.00'),
            total=Decimal('300000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        record_cash_payment(
            school=self.school,
            student=self.student,
            amount=Decimal('800000.00'),
            invoice_ids=[self.invoice.id, inv2.id],
            received_by=self.finance_officer,
        )

        payload = mock_dispatch.call_args[1]['payload']
        self.assertIn(self.invoice.number, payload['invoice_info'])
        self.assertIn(inv2.number, payload['invoice_info'])

    def test_seed_templates_include_push_variant(self):
        """PAR-008: The PUSH template for finance.payment_received exists in seed data."""
        from apps.notifications.management.commands.seed_notification_templates import CANONICAL_TEMPLATES
        push_templates = [
            t for t in CANONICAL_TEMPLATES
            if t['key'] == 'finance.payment_received' and t['channel'] == 'PUSH'
        ]
        self.assertEqual(len(push_templates), 1)
        self.assertEqual(push_templates[0]['channel'], 'PUSH')
        self.assertEqual(push_templates[0]['locale'], 'id-ID')
        self.assertIn('amount', push_templates[0]['variables'])
        self.assertIn('student_name', push_templates[0]['variables'])
        self.assertIn('invoice_info', push_templates[0]['variables'])
        self.assertIn('payment_reference', push_templates[0]['variables'])
