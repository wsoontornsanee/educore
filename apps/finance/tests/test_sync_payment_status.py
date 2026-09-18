from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
)
from apps.finance.services.payment_providers import MockPaymentProvider
from apps.finance.services.payments import create_payment_intent, sync_payment_status
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class SyncPaymentStatusTests(TestCase):
    """ARC-006/ARC-009 sync_payment_status poller: catches a missed FIN-013
    settlement webhook, and expires/cancels intents the gateway can't settle.
    """

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cahaya Bangsa", brand_name="Cahaya Bangsa",
            npwp="01.111.222.3-444.000", address="Bandung",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Cahaya Bangsa", npsn="20500002",
            level=School.LEVEL_SMP, base_currency="IDR",
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Sinta Wulandari", nik="3471010101010404",
            gender=Person.GENDER_FEMALE, dob="2012-05-10",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="202603002", nisn="0034567891", status=Student.STATUS_ACTIVE,
        )
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number=f"INV/{self.school.npsn}/2026/000200", period="2026-10", due_date="2026-10-10",
            subtotal=Decimal('300000.00'), total=Decimal('300000.00'), paid=Decimal('0.00'),
            currency='IDR', status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _make_intent(self, method=PaymentMethod.QRIS):
        """sync_payment_status only polls intents past its 15-minute grace
        period (a webhook usually beats the poller by far less than that), so
        every intent used to exercise the poller is backdated past it.
        """
        intent = create_payment_intent(
            school=self.school, student=self.student, invoice_ids=[self.invoice.id],
            method=method, bank='BCA' if method == PaymentMethod.VA else None,
            provider_name='MOCK',
        )
        PaymentIntent.objects.filter(id=intent.id).update(
            created_at=timezone.now() - timezone.timedelta(minutes=30)
        )
        intent.refresh_from_db()
        return intent

    def test_settles_pending_intent_via_gateway_poll(self):
        intent = self._make_intent()
        parsed = {
            'external_id': f'MOCK-POLL-{intent.id}', 'status': 'SETTLED',
            'amount': intent.amount, 'fee': Decimal('2000.00'), 'net': intent.amount - Decimal('2000.00'),
            'paid_at': timezone.now(), 'channel': 'QRIS', 'bank': None, 'raw': {},
        }
        with patch.object(MockPaymentProvider, 'check_status', return_value=parsed):
            result = sync_payment_status()

        self.assertEqual(result, {'checked': 1, 'settled': 1, 'expired': 0, 'cancelled': 0, 'errors': 0})

        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntentStatus.COMPLETED)
        payment = Payment.objects.get(external_id=f'MOCK-POLL-{intent.id}')
        self.assertEqual(payment.status, 'SETTLED')

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)

    def test_settlement_is_idempotent_with_a_later_webhook(self):
        """A poll-settled intent must not be double-settled if the real webhook
        eventually arrives too (FIN-013 idempotency, shared across both paths).
        """
        intent = self._make_intent()
        external_id = f'MOCK-POLL-{intent.id}'
        parsed = {
            'external_id': external_id, 'status': 'SETTLED',
            'amount': intent.amount, 'fee': Decimal('2000.00'), 'net': intent.amount - Decimal('2000.00'),
            'paid_at': timezone.now(), 'channel': 'QRIS', 'bank': None, 'raw': {},
        }
        with patch.object(MockPaymentProvider, 'check_status', return_value=parsed):
            sync_payment_status()

        payment_count_before = Payment.objects.filter(external_id=external_id).count()
        self.assertEqual(payment_count_before, 1)

        with patch.object(MockPaymentProvider, 'check_status', return_value=parsed):
            result = sync_payment_status()

        # Already COMPLETED, so the intent is no longer PENDING and isn't re-checked.
        self.assertEqual(result['checked'], 0)
        self.assertEqual(Payment.objects.filter(external_id=external_id).count(), 1)

    def test_expires_intent_past_expiry_with_no_gateway_match(self):
        intent = self._make_intent()
        PaymentIntent.objects.filter(id=intent.id).update(
            expires_at=timezone.now() - timezone.timedelta(hours=1)
        )

        with patch.object(MockPaymentProvider, 'check_status', return_value=None):
            result = sync_payment_status()

        self.assertEqual(result, {'checked': 1, 'settled': 0, 'expired': 1, 'cancelled': 0, 'errors': 0})
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntentStatus.EXPIRED)

    def test_does_not_expire_intent_still_within_expiry_window(self):
        self._make_intent()
        with patch.object(MockPaymentProvider, 'check_status', return_value=None):
            result = sync_payment_status()

        self.assertEqual(result, {'checked': 1, 'settled': 0, 'expired': 0, 'cancelled': 0, 'errors': 0})

    def test_cancels_intent_when_gateway_reports_failed(self):
        intent = self._make_intent()
        parsed = {
            'external_id': f'MOCK-POLL-{intent.id}', 'status': 'FAILED',
            'amount': intent.amount, 'fee': Decimal('0.00'), 'net': intent.amount,
            'paid_at': timezone.now(), 'channel': 'QRIS', 'bank': None, 'raw': {},
        }
        with patch.object(MockPaymentProvider, 'check_status', return_value=parsed):
            result = sync_payment_status()

        self.assertEqual(result, {'checked': 1, 'settled': 0, 'expired': 0, 'cancelled': 1, 'errors': 0})
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntentStatus.CANCELLED)

    def test_ignores_manual_and_cash_methods(self):
        """MANUAL/CASH never touch a gateway, so they must never be polled."""
        manual_intent = create_payment_intent(
            school=self.school, student=self.student, invoice_ids=[self.invoice.id],
            method=PaymentMethod.MANUAL, provider_name='MOCK',
        )
        with patch.object(MockPaymentProvider, 'check_status') as mock_check:
            result = sync_payment_status()

        mock_check.assert_not_called()
        self.assertEqual(result['checked'], 0)
        manual_intent.refresh_from_db()
        self.assertEqual(manual_intent.status, PaymentIntentStatus.PENDING)

    def test_respects_limit(self):
        self._make_intent()
        self._make_intent()
        with patch.object(MockPaymentProvider, 'check_status', return_value=None) as mock_check:
            result = sync_payment_status(limit=1)

        self.assertEqual(result['checked'], 1)
        self.assertEqual(mock_check.call_count, 1)

    def test_errors_are_isolated_per_intent(self):
        intent = self._make_intent()
        with patch.object(MockPaymentProvider, 'check_status', side_effect=RuntimeError("gateway down")):
            result = sync_payment_status()

        self.assertEqual(result, {'checked': 1, 'settled': 0, 'expired': 0, 'cancelled': 0, 'errors': 1})
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntentStatus.PENDING)

    def test_skips_intent_still_within_the_grace_period(self):
        """A webhook is typically fast — polling a just-created intent would
        only waste gateway API calls before it's had a fair chance to arrive.
        """
        create_payment_intent(
            school=self.school, student=self.student, invoice_ids=[self.invoice.id],
            method=PaymentMethod.QRIS, provider_name='MOCK',
        )
        with patch.object(MockPaymentProvider, 'check_status') as mock_check:
            result = sync_payment_status()

        mock_check.assert_not_called()
        self.assertEqual(result['checked'], 0)

    def test_max_age_hours_excludes_stale_intents(self):
        intent = self._make_intent()
        PaymentIntent.objects.filter(id=intent.id).update(
            created_at=timezone.now() - timezone.timedelta(hours=48)
        )
        with patch.object(MockPaymentProvider, 'check_status') as mock_check:
            result = sync_payment_status(max_age_hours=24)

        mock_check.assert_not_called()
        self.assertEqual(result['checked'], 0)

    def test_provider_filter_only_polls_matching_provider(self):
        self._make_intent()  # provider='MOCK'
        with patch.object(MockPaymentProvider, 'check_status') as mock_check:
            result = sync_payment_status(provider_name='XENDIT')

        mock_check.assert_not_called()
        self.assertEqual(result['checked'], 0)

    def test_dry_run_reports_without_writing(self):
        intent = self._make_intent()
        parsed = {
            'external_id': f'MOCK-POLL-{intent.id}', 'status': 'SETTLED',
            'amount': intent.amount, 'fee': Decimal('2000.00'), 'net': intent.amount - Decimal('2000.00'),
            'paid_at': timezone.now(), 'channel': 'QRIS', 'bank': None, 'raw': {},
        }
        with patch.object(MockPaymentProvider, 'check_status', return_value=parsed):
            result = sync_payment_status(dry_run=True)

        self.assertEqual(result, {'checked': 1, 'settled': 1, 'expired': 0, 'cancelled': 0, 'errors': 0})
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntentStatus.PENDING)
        self.assertFalse(Payment.objects.filter(external_id=f'MOCK-POLL-{intent.id}').exists())
