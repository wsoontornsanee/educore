"""A gateway this deployment does not use must not fail the nightly reconciliation (FIN-024), while a
misconfigured one that is in use must still fail loudly."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import JobRun
from apps.finance.models import GatewaySettlementBatch, PaymentIntent, PaymentIntentStatus, PaymentMethod, SettlementBatchStatus
from apps.finance.services.payment_providers import (
    MidtransPaymentProvider, MockPaymentProvider, XenditPaymentProvider,
)
from apps.finance.services.reconciliation import reconcile_gateway_settlement
from apps.academic.tests.base import build_academic_fixture as build_fixture

DATE = datetime.date(2026, 9, 14)
XENDIT_FETCH = 'apps.finance.services.payment_providers.XenditPaymentProvider.fetch_settlement'


class IsConfiguredTests(TestCase):
    def test_xendit_needs_an_api_key(self):
        self.assertFalse(XenditPaymentProvider(api_key='').is_configured())
        self.assertTrue(XenditPaymentProvider(api_key='xnd_key').is_configured())

    @override_settings(XENDIT_API_KEY='')
    def test_xendit_reads_the_key_from_settings(self):
        self.assertFalse(XenditPaymentProvider().is_configured())

    @override_settings(XENDIT_API_KEY='xnd_from_settings')
    def test_xendit_configured_from_settings(self):
        self.assertTrue(XenditPaymentProvider().is_configured())

    def test_providers_with_no_credentials_to_miss_are_always_configured(self):
        self.assertTrue(MockPaymentProvider().is_configured())
        self.assertTrue(MidtransPaymentProvider().is_configured())


@override_settings(XENDIT_API_KEY='')
class ReconcileUnconfiguredProviderTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()
        self.foundation = self.fx['foundation']

    def batches(self):
        return GatewaySettlementBatch.all_tenants.filter(foundation_id=self.foundation.id)

    def make_intent(self, provider):
        return PaymentIntent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.fx['school'], student=self.fx['student'], provider=provider,
            method=PaymentMethod.QRIS, amount=Decimal('100000.00'), currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=24), status=PaymentIntentStatus.PENDING,
            metadata={},
        )

    def reconcile(self, provider='XENDIT', dry_run=False):
        return reconcile_gateway_settlement(provider, DATE, self.foundation.id, dry_run=dry_run)

    def test_unconfigured_and_unused_is_skipped_without_a_batch_row_or_an_api_call(self):
        with mock.patch(XENDIT_FETCH) as fetch:
            result = self.reconcile()
        self.assertEqual(result['skipped'], 'PROVIDER_NOT_CONFIGURED')
        self.assertNotIn('error', result)
        self.assertIsNone(result['batch_id'])
        fetch.assert_not_called()
        self.assertFalse(self.batches().exists())

    def test_unconfigured_but_in_use_still_fails_loudly(self):
        self.make_intent('XENDIT')
        result = self.reconcile()
        self.assertIn('XENDIT_API_KEY is not configured', result['error'])
        self.assertNotIn('skipped', result)
        batch = self.batches().get()
        self.assertEqual(batch.status, SettlementBatchStatus.FAILED)

    def test_another_providers_intents_do_not_count_as_use(self):
        self.make_intent('MIDTRANS')
        self.assertEqual(self.reconcile('XENDIT')['skipped'], 'PROVIDER_NOT_CONFIGURED')

    def test_another_foundations_intents_do_not_count_as_use(self):
        other = build_fixture(foundation_name="Yayasan Lain XY")
        PaymentIntent.all_tenants.create(
            foundation_id=other['foundation'].id, school=other['school'], student=other['student'], provider='XENDIT',
            method=PaymentMethod.QRIS, amount=Decimal('1000.00'), currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=24), status=PaymentIntentStatus.PENDING, metadata={},
        )
        self.assertEqual(self.reconcile('XENDIT')['skipped'], 'PROVIDER_NOT_CONFIGURED')

    @override_settings(XENDIT_API_KEY='xnd_key')
    def test_a_configured_provider_reconciles_as_before(self):
        with mock.patch(XENDIT_FETCH, return_value=[]):
            result = self.reconcile()
        self.assertNotIn('skipped', result)
        self.assertEqual(self.batches().get().status, SettlementBatchStatus.COMPLETED)

    def test_midtrans_is_unaffected(self):
        result = self.reconcile('MIDTRANS')
        self.assertNotIn('skipped', result)
        self.assertEqual(self.batches().get().status, SettlementBatchStatus.COMPLETED)


class DryRunWritesNothingTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()
        self.foundation = self.fx['foundation']

    def batches(self):
        return GatewaySettlementBatch.all_tenants.filter(foundation_id=self.foundation.id)

    def test_a_dry_run_creates_no_batch_row(self):
        result = reconcile_gateway_settlement('MOCK', DATE, self.foundation.id, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertFalse(self.batches().exists())

    def test_a_dry_run_leaves_an_existing_batch_untouched(self):
        batch = GatewaySettlementBatch.all_tenants.create(
            foundation_id=self.foundation.id, provider='MOCK', settlement_date=DATE,
            status=SettlementBatchStatus.FAILED, error_message='earlier failure',
        )
        reconcile_gateway_settlement('MOCK', DATE, self.foundation.id, dry_run=True)
        batch.refresh_from_db()
        self.assertEqual(batch.status, SettlementBatchStatus.FAILED)
        self.assertEqual(batch.error_message, 'earlier failure')

    def test_a_real_run_after_a_dry_run_still_creates_the_batch(self):
        reconcile_gateway_settlement('MOCK', DATE, self.foundation.id, dry_run=True)
        reconcile_gateway_settlement('MOCK', DATE, self.foundation.id, dry_run=False)
        self.assertEqual(self.batches().count(), 1)


@override_settings(XENDIT_API_KEY='', PAYMENT_PROVIDERS=['XENDIT'])
class ReconcileCommandSkipTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def run_command(self):
        out = StringIO()
        call_command('reconcile_payments', '--date=2026-09-14', '--force', stdout=out)
        return out.getvalue()

    def test_the_nightly_run_stays_green_when_the_only_gateway_is_unused_and_unconfigured(self):
        output = self.run_command()
        self.assertIn('SKIPPED', output)
        row = JobRun.objects.get(job_name='reconcile_payments')
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.error_text, '')
        self.assertFalse(GatewaySettlementBatch.all_tenants.filter(foundation_id=self.fx['foundation'].id).exists())

    def test_the_run_is_recorded_failed_when_a_used_gateway_lacks_credentials(self):
        PaymentIntent.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'], provider='XENDIT',
            method=PaymentMethod.QRIS, amount=Decimal('100000.00'), currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=24), status=PaymentIntentStatus.PENDING, metadata={},
        )
        self.run_command()
        row = JobRun.objects.get(job_name='reconcile_payments')
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertIn('XENDIT_API_KEY is not configured', row.error_text)
