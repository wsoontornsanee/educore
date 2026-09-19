"""Settlement netting of upheld QR dispute refunds (spec 18 QRS-026; Notion: settlement netting)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.wallet.models import (
    MerchantSettlementAdjustment,
    MerchantSettlementStatus,
    POSTransaction,
    POSTransactionStatus,
)
from apps.wallet.qr_charge import charge_qr_session, create_qr_session
from apps.wallet.qr_oversight import open_qr_dispute, resolve_qr_dispute
from apps.wallet.services import (
    SettlementStateError,
    mark_settlement_paid,
    process_pos_transaction,
    render_settlement_statement_html,
    run_merchant_settlement,
    void_pos_transaction,
)
from apps.wallet.tests.test_qr_charge import QRFixtureMixin, make_guardian
from educore.middleware.tenancy import set_current_foundation_id


class NettingBase(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guardian = make_guardian(self.fx)
        self.staff = self.fx['finance_user']
        self.today = timezone.localdate()
        self.week = (self.today - datetime.timedelta(days=6), self.today)

    def sale(self, amount, key):
        set_current_foundation_id(self.fx['foundation'].id)
        return charge_qr_session(create_qr_session(self.terminal)['token'], self.student, Decimal(amount), key)

    def dispute(self, pos_tx, refund=None, outcome='UPHELD'):
        set_current_foundation_id(self.fx['foundation'].id)
        d = open_qr_dispute(pos_tx, self.guardian, 'x')
        return resolve_qr_dispute(d, outcome, self.staff, '', None if refund is None else Decimal(refund))

    def settle(self, period=None):
        set_current_foundation_id(self.fx['foundation'].id)
        return run_merchant_settlement(self.merchant, *(period or self.week))


class NetOfAdjustmentsTests(NettingBase):
    def test_full_refund_nets_exactly_what_a_void_would_have_removed(self):
        keep = self.sale('30000', 'a')
        refunded = self.sale('20000', 'b')
        self.dispute(refunded)
        s = self.settle()
        # gross counts both sales; the deduction is the refund minus its commission share (2%)
        self.assertEqual((s.gross, s.commission), (Decimal('50000.00'), Decimal('1000.00')))
        self.assertEqual(s.adjustments_total, Decimal('19600.00'))
        self.assertEqual(s.net, Decimal('29400.00'))  # == what settling only the kept sale would pay: 30000 - 600
        self.assertEqual(s.net, keep.total - keep.commission)

    def test_partial_refund_deducts_only_the_refunded_share(self):
        sale = self.sale('20000', 'a')
        self.dispute(sale, refund='5000')
        s = self.settle()
        self.assertEqual(s.adjustments_total, Decimal('4900.00'))  # 5000 - 2% of 5000
        self.assertEqual(s.net, Decimal('20000.00') - Decimal('400.00') - Decimal('4900.00'))

    def test_no_disputes_changes_nothing(self):
        self.sale('20000', 'a')
        s = self.settle()
        self.assertEqual((s.adjustments_total, s.net), (Decimal('0.00'), Decimal('19600.00')))

    def test_rejected_dispute_never_adjusts(self):
        self.dispute(self.sale('20000', 'a'), outcome='REJECTED')
        self.assertFalse(MerchantSettlementAdjustment.all_tenants.exists())
        self.assertEqual(self.settle().adjustments_total, Decimal('0.00'))

    def test_rerun_is_idempotent_and_recomputes(self):
        self.dispute(self.sale('20000', 'a'))
        first = self.settle()
        again = self.settle()
        self.assertEqual(first.id, again.id)
        self.assertEqual(again.adjustments_total, Decimal('19600.00'))
        self.assertEqual(MerchantSettlementAdjustment.all_tenants.filter(settlement=again).count(), 1)

    def test_adjustment_absorbed_is_audited(self):
        self.dispute(self.sale('20000', 'a'))
        self.settle()
        actions = set(AuditEvent.objects.values_list('action', flat=True))
        self.assertTrue({'wallet.settlement_adjustment.created', 'wallet.settlement_adjustment.absorbed'} <= actions)


class WaitingAndCarryTests(NettingBase):
    def test_net_lands_exactly_at_zero_never_below(self):
        self.dispute(self.sale('30000', 'a'))
        s = self.settle()
        self.assertEqual(s.adjustments_total, Decimal('29400.00'))
        self.assertEqual(s.net, Decimal('0.00'))  # 30000 - 600 commission - 29400: exactly zero, not negative

    def test_first_fit_skips_a_too_large_line_but_takes_a_smaller_later_one(self):
        big = self.sale('30000', 'big')
        small = self.sale('10000', 'small')
        self.dispute(big)     # deduction 29.400 (older)
        self.dispute(small)   # deduction  9.800 (newer)
        set_current_foundation_id(self.fx['foundation'].id)
        # Make room for only the small one: capacity = gross - commission of a period holding just the small sale
        MerchantSettlementAdjustment.all_tenants.update(occurred_at=timezone.now())
        POSTransaction.all_tenants.filter(id=big.id).update(occurred_at=timezone.now() - datetime.timedelta(days=20))
        s = self.settle((self.today - datetime.timedelta(days=2), self.today))  # only the 10.000 sale in range
        self.assertEqual(s.gross, Decimal('10000.00'))
        self.assertEqual(s.adjustments_total, Decimal('9800.00'))  # the small line fit
        waiting = MerchantSettlementAdjustment.all_tenants.filter(settlement__isnull=True)
        self.assertEqual(waiting.count(), 1)
        self.assertEqual(waiting.get().deduction, Decimal('29400.00'))  # the big one waits

    def test_future_dated_adjustments_are_not_candidates(self):
        self.dispute(self.sale('20000', 'a'))
        yesterday_period = (self.today - datetime.timedelta(days=10), self.today - datetime.timedelta(days=1))
        s = self.settle(yesterday_period)
        self.assertEqual(s.adjustments_total, Decimal('0.00'))
        self.assertIsNone(MerchantSettlementAdjustment.all_tenants.get().settlement_id)


class PaidSettlementTests(NettingBase):
    def test_paid_settlement_is_never_touched_and_a_late_dispute_lands_on_the_next_run(self):
        sale = self.sale('20000', 'a')
        first = self.settle()
        set_current_foundation_id(self.fx['foundation'].id)
        mark_settlement_paid(first)
        self.assertEqual(first.net, Decimal('19600.00'))
        self.dispute(sale)  # after payout, for a sale inside the paid period
        with self.assertRaises(SettlementStateError):
            self.settle()  # the paid period cannot be re-run
        first.refresh_from_db()
        self.assertEqual((first.net, first.adjustments_total), (Decimal('19600.00'), Decimal('0.00')))
        # The next period has its own sales to absorb the clawback
        self.sale('30000', 'b')
        nxt = self.settle((self.today + datetime.timedelta(days=1), self.today + datetime.timedelta(days=7)))
        self.assertEqual(nxt.status, MerchantSettlementStatus.PENDING)
        self.assertEqual(nxt.gross, Decimal('0.00'))  # sales are dated today, not in the next period


class VoidGuardTests(NettingBase):
    def test_a_disputed_sale_cannot_also_be_voided(self):
        sale = self.sale('20000', 'a')
        self.dispute(sale)
        with self.assertRaises(ValueError) as ctx:
            void_pos_transaction(sale, 'again')
        self.assertIn('INVALID_STATE', str(ctx.exception))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))  # refunded once, not twice

    def test_undisputed_void_still_works_and_is_excluded(self):
        sale = self.sale('20000', 'a')
        void_pos_transaction(sale, 'salah')
        self.assertEqual(self.settle().gross, Decimal('0.00'))


class StatementAndApiTests(NettingBase):
    def test_statement_lists_adjustment_lines_and_waiting_total(self):
        sale = self.sale('20000', 'a')
        self.dispute(sale)
        s = self.settle()
        html = render_settlement_statement_html(s)
        self.assertIn('Potongan sanggahan', html)
        self.assertIn('-19600.00', html)
        self.assertIn(f'#{sale.id}', html)
        self.assertNotIn('menunggu', html)
        # a fresh waiting line for a future date shows up as waiting
        extra = self.sale('10000', 'b')
        self.dispute(extra)
        MerchantSettlementAdjustment.all_tenants.filter(pos_transaction=extra).update(
            occurred_at=timezone.now() + datetime.timedelta(days=30),
        )
        self.assertIn('menunggu', render_settlement_statement_html(s))

    def test_settlement_api_exposes_adjustments(self):
        from rest_framework.test import APIClient
        self.dispute(self.sale('20000', 'a'))
        self.settle()
        client = APIClient()
        client.force_authenticate(user=self.staff)
        rows = client.get(f'/api/v1/merchants/{self.merchant.id}/settlements/').json()
        row = rows[0]
        self.assertEqual((row['adjustments_total'], row['net']), ('19600.00', '0.00'))
        self.assertEqual(row['adjustments'][0]['deduction'], '19600.00')

    def test_console_shows_waiting_deductions_and_refunded_badge(self):
        from apps.wallet.services import get_canteen_console_snapshot
        sale = self.sale('20000', 'a')
        self.dispute(sale)
        snap = get_canteen_console_snapshot(self.fx['foundation'].id, self.fx['school'])
        self.assertEqual(snap['merchants'][0]['waiting_deductions'], Decimal('19600.00'))
        self.settle()
        snap = get_canteen_console_snapshot(self.fx['foundation'].id, self.fx['school'])
        self.assertEqual(snap['merchants'][0]['waiting_deductions'], Decimal('0.00'))
        self.assertTrue(list(snap['recent_transactions'][0].dispute_adjustments.all()))
