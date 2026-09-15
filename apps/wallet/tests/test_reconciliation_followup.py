import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import Invoice, InvoiceStatus
from apps.notifications.models import NotificationCategory, NotificationIntent
from apps.wallet.models import WalletReconciliation, WalletReconciliationStatus
from apps.wallet.services import (
    get_reconciliations_due_for_invoice_handover,
    get_wallets_due_for_reminder,
    invoice_reconciliation_case,
    process_offline_pos_batch,
    queue_reconciliation_reminder,
    topup_wallet,
    get_or_create_wallet,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_offline_sync import batch_item
from apps.wallet.tests.test_reconciliation import attach_financial_guardian


def make_open_case(fx, merchant, product, terminal, student_id=None):
    wallet = get_or_create_wallet(fx['student'])
    topup_wallet(wallet, Decimal('5000'), 'CASH', f'seed-{terminal.id}-{product.sku}')
    process_offline_pos_batch(
        terminal, [batch_item(product.sku, product.name, f'off-{terminal.id}-{product.sku}', student_id=fx['student'].id)]
    )
    return WalletReconciliation.all_tenants.get(wallet=wallet)


class ReminderTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_no_reminder_if_notice_never_delivered(self):
        self.assertIsNone(self.case.notice_sent_at)
        wallets = list(get_wallets_due_for_reminder(foundation_id=self.fx['foundation'].id))
        self.assertEqual(wallets, [])

    def test_reminder_sent_once_eligible(self):
        self.case.notice_sent_at = timezone.now() - datetime.timedelta(hours=49)
        self.case.save()

        wallets = list(get_wallets_due_for_reminder(foundation_id=self.fx['foundation'].id))
        self.assertEqual(len(wallets), 1)

        intents = queue_reconciliation_reminder(wallets[0])
        self.assertEqual(len(intents), 1)

        self.case.refresh_from_db()
        self.assertIsNotNone(self.case.reminder_sent_at)

        # Not eligible again once reminder_sent_at is set.
        wallets_again = list(get_wallets_due_for_reminder(foundation_id=self.fx['foundation'].id))
        self.assertEqual(wallets_again, [])

    def test_reminder_not_due_before_48h(self):
        self.case.notice_sent_at = timezone.now() - datetime.timedelta(hours=10)
        self.case.save()
        wallets = list(get_wallets_due_for_reminder(foundation_id=self.fx['foundation'].id))
        self.assertEqual(wallets, [])


class InvoiceHandoverTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_not_due_before_7_days(self):
        cases = list(get_reconciliations_due_for_invoice_handover(foundation_id=self.fx['foundation'].id))
        self.assertEqual(cases, [])

    def test_creates_new_invoice_when_none_open(self):
        self.case.detected_at = timezone.now() - datetime.timedelta(days=8)
        self.case.save()

        cases = list(get_reconciliations_due_for_invoice_handover(foundation_id=self.fx['foundation'].id))
        self.assertEqual(len(cases), 1)

        invoice_reconciliation_case(cases[0])

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.INVOICED)
        self.assertIsNotNone(self.case.invoice)
        self.assertEqual(self.case.invoice.total, self.case.shortfall)
        self.assertEqual(self.case.invoice.lines.count(), 1)
        self.assertEqual(self.case.invoice.lines.first().code, 'PENYESUAIAN_SALDO_KANTIN')

    def test_appends_to_existing_open_invoice(self):
        existing = Invoice.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            number='INV/TEST/2026/999999',
            period=timezone.localdate().strftime('%Y-%m'),
            due_date=timezone.localdate() + datetime.timedelta(days=10),
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            status=InvoiceStatus.ISSUED,
        )
        self.case.detected_at = timezone.now() - datetime.timedelta(days=8)
        self.case.save()

        invoice_reconciliation_case(self.case)

        existing.refresh_from_db()
        self.assertEqual(existing.total, Decimal('500000.00') + self.case.shortfall)
        self.case.refresh_from_db()
        self.assertEqual(self.case.invoice_id, existing.id)

    def test_dispatches_payment_due_notice(self):
        self.case.detected_at = timezone.now() - datetime.timedelta(days=8)
        self.case.save()
        invoice_reconciliation_case(self.case)

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, template_key='wallet.recon.invoiced',
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.category, NotificationCategory.PAYMENT_DUE)
