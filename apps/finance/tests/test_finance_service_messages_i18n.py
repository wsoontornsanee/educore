"""User-facing errors raised by finance services that the Keuangan console flashes must be
translatable (id-ID source, EN via the catalog) instead of hard-coded English."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.finance.models import DiscrepancyResolution, GatewaySettlementBatch, PaymentDiscrepancy
from apps.finance.services.payments import CurrencyMismatchError, InvalidPaymentError, record_cash_payment
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.finance.tests.test_web_console import make_invoice
from apps.finance.tests.test_web_console_actions import ActionTestBase, flashes
from educore.middleware.tenancy import tenant_context


class ResolveDiscrepancyMessageTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        self.discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=self.foundation.id, batch=batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )

    def _resolve(self, resolution=DiscrepancyResolution.WAIVED):
        return resolve_discrepancy(
            discrepancy_id=self.discrepancy.id, resolution=resolution,
            resolved_by=self.officer, foundation_id=self.foundation.id,
        )

    def test_already_resolved_message_is_indonesian_by_default(self):
        self._resolve()
        with translation.override('id'):
            with self.assertRaises(ValueError) as ctx:
                self._resolve()
        self.assertIn('sudah', str(ctx.exception))
        self.assertIn(f'#{self.discrepancy.id}', str(ctx.exception))
        self.assertNotIn('already', str(ctx.exception))

    def test_already_resolved_message_has_an_english_translation(self):
        self._resolve()
        with translation.override('en'):
            with self.assertRaises(ValueError) as ctx:
                self._resolve()
        self.assertIn('already', str(ctx.exception))
        self.assertIn(f'#{self.discrepancy.id}', str(ctx.exception))

    def test_invalid_resolution_message_is_translated(self):
        with translation.override('en'):
            with self.assertRaises(ValueError) as ctx:
                self._resolve(resolution='AUTO_SETTLED')
        self.assertEqual(str(ctx.exception), 'Invalid resolution choice.')

    def test_console_flash_for_a_double_resolve_is_indonesian(self):
        self.client.force_login(self.officer)
        url = reverse('finance-console-discrepancy-resolve', args=[self.discrepancy.id])
        self.client.post(url, {'resolution': 'WAIVED'}, follow=True)
        response = self.client.post(url, {'resolution': 'ESCALATED'}, follow=True)
        shown = flashes(response)
        self.assertEqual(len(shown), 1)
        self.assertIn('sudah', shown[0])
        self.assertNotIn('already', shown[0])


class CashPaymentMessageTests(ActionTestBase):
    def test_non_positive_amount_message_is_translated(self):
        with translation.override('id'):
            with self.assertRaises(InvalidPaymentError) as ctx:
                record_cash_payment(school=self.school1, student=self.s1, amount=Decimal('0.00'), received_by=self.officer)
        self.assertEqual(str(ctx.exception), 'Jumlah pembayaran tunai harus lebih dari nol.')
        with translation.override('en'):
            with self.assertRaises(InvalidPaymentError) as ctx:
                record_cash_payment(school=self.school1, student=self.s1, amount=Decimal('0.00'), received_by=self.officer)
        self.assertEqual(str(ctx.exception), 'Cash payment amount must be greater than zero.')

    def test_currency_mismatch_message_is_translated(self):
        # Reachable through the API with explicit invoice ids (the console never passes them).
        invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        invoice.currency = 'USD'
        invoice.save(update_fields=['currency'])
        for language, expected in (
            ('id', 'Mata uang tagihan (USD) tidak sama dengan mata uang pembayaran (IDR).'),
            ('en', 'Invoice currency (USD) does not match the payment currency (IDR).'),
        ):
            with translation.override(language), tenant_context(self.foundation.id):
                with self.assertRaises(CurrencyMismatchError) as ctx:
                    record_cash_payment(
                        school=self.school1, student=self.s1, amount=Decimal('100000.00'),
                        invoice_ids=[invoice.id], received_by=self.officer,
                    )
            self.assertEqual(str(ctx.exception), expected)
