"""Tests for Step 8.1 - Gateway Reconciliation (FIN-024).

Covers:
- MockPaymentProvider.fetch_settlement deterministic output
- reconcile_gateway_settlement service: matched, missing, mismatch cases
- resolve_discrepancy service: valid/invalid transitions, cross-tenant isolation
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import (
    DiscrepancyResolution,
    DiscrepancyType,
    GatewaySettlementBatch,
    Payment,
    PaymentDiscrepancy,
    PaymentMethod,
    PaymentStatus,
    SettlementBatchStatus,
)
from apps.finance.services.payment_providers import MockPaymentProvider, PaymentGatewayError
from apps.finance.services.reconciliation import reconcile_gateway_settlement, resolve_discrepancy

User = get_user_model()

FOUNDATION_ID = 1
DATE = datetime.date(2026, 9, 14)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch(status=SettlementBatchStatus.PARTIAL, **kwargs):
    """Create a GatewaySettlementBatch using save() directly (bypassing TenantManager)."""
    b = GatewaySettlementBatch(
        foundation_id=FOUNDATION_ID,
        provider='MOCK',
        settlement_date=DATE,
        status=status,
        **kwargs,
    )
    b.save()
    return b


def _make_discrepancy(batch, **kwargs):
    """Create a PaymentDiscrepancy using save() directly."""
    d = PaymentDiscrepancy(
        foundation_id=FOUNDATION_ID,
        batch=batch,
        external_id=kwargs.pop('external_id', 'TEST-EXT-001'),
        discrepancy_type=kwargs.pop('discrepancy_type', DiscrepancyType.MISSING_IN_SYSTEM),
        gateway_amount=kwargs.pop('gateway_amount', Decimal('500000.00')),
        gateway_fee=kwargs.pop('gateway_fee', Decimal('3000.00')),
        gateway_net=kwargs.pop('gateway_net', Decimal('497000.00')),
        resolution=kwargs.pop('resolution', DiscrepancyResolution.PENDING),
        **kwargs,
    )
    d.save()
    return d


def _make_user():
    return User.objects.create(
        foundation_id=FOUNDATION_ID,
        phone_e164='+6289900000001',
        email='recon_test@test.id',
        full_name='Recon Tester',
    )


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------

class MockProviderFetchSettlementTest(TestCase):
    def setUp(self):
        self.provider = MockPaymentProvider()
        self.date = datetime.date(2026, 9, 15)

    def test_returns_two_records(self):
        records = self.provider.fetch_settlement(self.date)
        self.assertEqual(len(records), 2)

    def test_record_schema(self):
        records = self.provider.fetch_settlement(self.date)
        for r in records:
            self.assertIn('external_id', r)
            self.assertIn('amount', r)
            self.assertIn('fee', r)
            self.assertIn('net', r)
            self.assertIn('settled_at', r)
            self.assertIn('channel', r)
            self.assertIn('raw', r)

    def test_net_equals_amount_minus_fee(self):
        records = self.provider.fetch_settlement(self.date)
        for r in records:
            self.assertEqual(r['net'], r['amount'] - r['fee'])

    def test_external_ids_contain_date(self):
        records = self.provider.fetch_settlement(self.date)
        date_str = self.date.strftime('%Y%m%d')
        for r in records:
            self.assertIn(date_str, r['external_id'])

    def test_deterministic_across_calls(self):
        r1 = self.provider.fetch_settlement(self.date)
        r2 = self.provider.fetch_settlement(self.date)
        self.assertEqual([r['external_id'] for r in r1], [r['external_id'] for r in r2])


# ---------------------------------------------------------------------------
# Reconciliation service tests
# ---------------------------------------------------------------------------

class ReconciliationServiceTest(TestCase):
    """Tests for reconcile_gateway_settlement using MockPaymentProvider."""

    def _run(self, **kwargs):
        defaults = dict(
            provider_name='MOCK',
            settlement_date=DATE,
            foundation_id=FOUNDATION_ID,
            dry_run=False,
        )
        defaults.update(kwargs)
        return reconcile_gateway_settlement(**defaults)

    def _discrepancies(self):
        """Query PaymentDiscrepancy bypassing TenantManager."""
        return PaymentDiscrepancy.all_tenants.filter(foundation_id=FOUNDATION_ID)

    def test_missing_in_system_creates_discrepancy(self):
        """Gateway records with no matching Payment -> MISSING_IN_SYSTEM discrepancy."""
        result = self._run()
        # Mock returns 2 records; no payments exist -> 2 missing
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['missing'], 2)
        self.assertEqual(result['matched'], 0)
        missing_discs = self._discrepancies().filter(
            discrepancy_type=DiscrepancyType.MISSING_IN_SYSTEM,
        )
        self.assertEqual(missing_discs.count(), 2)

    def test_batch_created_with_partial_status(self):
        self._run()
        batch = GatewaySettlementBatch.all_tenants.get(
            foundation_id=FOUNDATION_ID,
            provider='MOCK',
            settlement_date=DATE,
        )
        self.assertEqual(batch.status, SettlementBatchStatus.PARTIAL)
        self.assertEqual(batch.total_records, 2)
        self.assertEqual(batch.missing_count, 2)

    def test_dry_run_creates_no_discrepancies(self):
        result = self._run(dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(self._discrepancies().count(), 0)

    def test_matched_payment_auto_settled(self):
        """When Payment with matching external_id and amount exists, it is auto-settled."""
        # Create real Payment objects with spec=Payment so Django FK accepts them
        mock_payment_matched = mock.create_autospec(Payment, instance=True)
        mock_payment_matched.amount = Decimal('500000.00')
        mock_payment_matched.status = PaymentStatus.PENDING
        mock_payment_matched.save = mock.MagicMock()

        call_count = {'n': 0}

        def side_effect(**kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return mock_payment_matched
            raise Payment.DoesNotExist

        with mock.patch(
            'apps.finance.services.reconciliation.Payment.all_tenants.get',
            side_effect=side_effect,
        ):
            result = self._run()

        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['missing'], 1)
        mock_payment_matched.save.assert_called()

    def test_amount_mismatch_creates_discrepancy(self):
        """Payment with wrong amount -> AMOUNT_MISMATCH discrepancy."""
        mock_payment = mock.MagicMock()
        mock_payment.amount = Decimal('999999.00')  # Deliberately wrong

        call_count = {'n': 0}

        def payment_get_side_effect(**kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return mock_payment
            raise Payment.DoesNotExist

        mock_disc = mock.MagicMock()

        with mock.patch(
            'apps.finance.services.reconciliation.Payment.all_tenants.get',
            side_effect=payment_get_side_effect,
        ), mock.patch(
            'apps.finance.services.reconciliation.PaymentDiscrepancy.all_tenants.update_or_create',
            return_value=(mock_disc, True),
        ) as mock_uoc:
            result = self._run()

        self.assertEqual(result['mismatch'], 1)
        # Verify update_or_create was called at least once with AMOUNT_MISMATCH type
        mock_uoc.assert_called()
        types_called = [c.kwargs.get('discrepancy_type') for c in mock_uoc.call_args_list]
        self.assertIn(DiscrepancyType.AMOUNT_MISMATCH, types_called)

    def test_completed_batch_skips_rerun(self):
        """A COMPLETED batch is not reprocessed (idempotency)."""
        _make_batch(
            status=SettlementBatchStatus.COMPLETED,
            total_records=2,
            matched_count=2,
        )
        result = self._run()
        self.assertEqual(result['matched'], 2)
        self.assertEqual(result['total'], 2)

    def test_gateway_error_marks_batch_failed(self):
        """PaymentGatewayError -> batch.status == FAILED."""
        with mock.patch(
            'apps.finance.services.reconciliation.get_payment_provider',
        ) as mock_factory:
            mock_prov = mock.MagicMock()
            mock_prov.fetch_settlement.side_effect = PaymentGatewayError("Connection timeout")
            mock_factory.return_value = mock_prov
            result = self._run()

        self.assertIn('error', result)
        batch = GatewaySettlementBatch.all_tenants.get(
            foundation_id=FOUNDATION_ID,
            provider='MOCK',
            settlement_date=DATE,
        )
        self.assertEqual(batch.status, SettlementBatchStatus.FAILED)
        self.assertIn('Connection timeout', batch.error_message)


# ---------------------------------------------------------------------------
# resolve_discrepancy service tests
# ---------------------------------------------------------------------------

class ResolveDiscrepancyTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.batch = _make_batch()
        self.discrepancy = _make_discrepancy(self.batch)

    def test_waive_discrepancy(self):
        result = resolve_discrepancy(
            discrepancy_id=self.discrepancy.id,
            resolution=DiscrepancyResolution.WAIVED,
            resolved_by=self.user,
            foundation_id=FOUNDATION_ID,
            notes='Confirmed duplicate transfer',
        )
        self.assertEqual(result.resolution, DiscrepancyResolution.WAIVED)
        self.assertIsNotNone(result.resolved_at)
        self.assertEqual(result.resolution_notes, 'Confirmed duplicate transfer')

    def test_escalate_discrepancy(self):
        result = resolve_discrepancy(
            discrepancy_id=self.discrepancy.id,
            resolution=DiscrepancyResolution.ESCALATED,
            resolved_by=self.user,
            foundation_id=FOUNDATION_ID,
        )
        self.assertEqual(result.resolution, DiscrepancyResolution.ESCALATED)

    def test_invalid_resolution_raises(self):
        with self.assertRaises(ValueError):
            resolve_discrepancy(
                discrepancy_id=self.discrepancy.id,
                resolution='INVALID',
                resolved_by=self.user,
                foundation_id=FOUNDATION_ID,
            )

    def test_already_resolved_raises(self):
        resolve_discrepancy(
            discrepancy_id=self.discrepancy.id,
            resolution=DiscrepancyResolution.WAIVED,
            resolved_by=self.user,
            foundation_id=FOUNDATION_ID,
        )
        with self.assertRaises(ValueError):
            resolve_discrepancy(
                discrepancy_id=self.discrepancy.id,
                resolution=DiscrepancyResolution.ESCALATED,
                resolved_by=self.user,
                foundation_id=FOUNDATION_ID,
            )

    def test_cross_tenant_isolation(self):
        """Discrepancy from a different foundation is not accessible."""
        with self.assertRaises(PaymentDiscrepancy.DoesNotExist):
            resolve_discrepancy(
                discrepancy_id=self.discrepancy.id,
                resolution=DiscrepancyResolution.WAIVED,
                resolved_by=self.user,
                foundation_id=9999,  # Different foundation
            )
