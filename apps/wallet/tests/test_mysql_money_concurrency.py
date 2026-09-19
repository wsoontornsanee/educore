"""Money-path invariants under real InnoDB concurrency (MySQL only; spec/01 §5 locking).

Companion to test_qr_mysql_concurrency.py, which covers the two single-use tokens. These run real threads
against the database for the rest: idempotency-key retries, void vs charge, double void, settlement runs vs
dispute resolution, and the spending-PIN attempt counter. Skipped on any other backend, because SQLite
serialises writers and ignores ``select_for_update``.
"""
import threading
from decimal import Decimal
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import TransactionTestCase
from django.utils import timezone

from apps.identity.models import UserPin
from apps.identity.pin import PinError, check_pin
from apps.wallet.models import (
    Merchant,
    MerchantSettlement,
    MerchantSettlementAdjustment,
    MerchantSettlementStatus,
    POSEntryMode,
    POSTransaction,
    POSTransactionStatus,
    QRDispute,
    QRDisputeStatus,
    Wallet,
    WalletTransaction,
)
from apps.wallet.qr_charge import QRChargeError, charge_qr_session, create_qr_session
from apps.wallet.qr_decals import create_payment_point, decal_token, print_decal
from apps.wallet.qr_oversight import QRDisputeError, open_qr_dispute, resolve_qr_dispute
from apps.wallet.services import SettlementStateError, mark_settlement_paid, run_merchant_settlement, void_pos_transaction
from apps.wallet.tests.test_qr_charge import GUARDIAN_PIN, QRFixtureMixin, make_guardian
from apps.wallet.tests.test_qr_mysql_concurrency import _run_together
from educore.middleware.tenancy import set_current_foundation_id

START_BALANCE = Decimal('100000.00')


@skipUnless(connection.vendor == 'mysql', 'real-concurrency test needs MySQL/InnoDB')
class MoneyPathConcurrencyTests(QRFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.foundation_id = self.fx['foundation'].id

    def _balance(self):
        return Wallet.all_tenants.get(student=self.student).balance

    def _assert_ledger_balances(self):
        """The wallet balance equals the sum of its ledger rows: no debit or refund was applied twice or lost."""
        ledger = WalletTransaction.all_tenants.filter(wallet__student=self.student).aggregate(t=Sum('amount'))['t']
        self.assertEqual(self._balance(), ledger)

    def _sale(self, amount='1000', key='seed-sale'):
        return charge_qr_session(create_qr_session(self.terminal)['token'], self.student, Decimal(amount), key)

    # -- idempotency-key retries ----------------------------------------------------------------------

    def _assert_one_sale_for_one_key(self, outcomes, expected_balance):
        errors = [error for _result, error in outcomes if error is not None]
        winners = [result for result, error in outcomes if error is None]
        self.assertTrue(winners, outcomes)
        # A retry racing its own original may be told the token is spent; it must never be an unhandled error.
        for error in errors:
            self.assertIsInstance(error, QRChargeError, repr(error))
        self.assertEqual(self._balance(), expected_balance)
        self._assert_ledger_balances()
        self.assertEqual(POSTransaction.all_tenants.filter(entry_mode=POSEntryMode.SELF_ENTERED).count(), 1)
        self.assertEqual({tx.id for tx in winners}, {winners[0].id})

    def test_same_idempotency_key_from_two_threads_on_a_session_token_debits_once(self):
        token = create_qr_session(self.terminal)['token']
        outcomes = _run_together(
            self.foundation_id,
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'same-key'),
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'same-key'),
        )
        self._assert_one_sale_for_one_key(outcomes, START_BALANCE - Decimal('1000'))

    def test_same_idempotency_key_from_two_threads_on_a_reusable_decal_debits_once(self):
        # A decal is never consumed, so only the wallet lock and the idempotency key stand between a
        # double-tapped request and a double debit.
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        operator = self.fx['finance_user']
        point = create_payment_point(self.merchant, 'Gerobak', 'Lapangan', operator)
        token = decal_token(print_decal(point, operator))
        outcomes = _run_together(
            self.foundation_id,
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'same-key'),
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'same-key'),
        )
        self._assert_one_sale_for_one_key(outcomes, START_BALANCE - Decimal('1000'))

    # -- void ----------------------------------------------------------------------------------------

    def test_two_concurrent_voids_of_one_sale_refund_once(self):
        sale = self._sale()
        outcomes = _run_together(
            self.foundation_id,
            lambda: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
            lambda: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
        )
        for _result, error in outcomes:
            self.assertTrue(error is None or isinstance(error, ValueError), repr(error))
        sale.refresh_from_db()
        self.assertEqual(sale.status, POSTransactionStatus.VOIDED)
        self.assertEqual(self._balance(), START_BALANCE)
        self._assert_ledger_balances()

    def test_void_of_one_sale_while_another_sale_is_charged_keeps_the_ledger_balanced(self):
        sale = self._sale(key='sale-a')
        token_b = create_qr_session(self.terminal)['token']
        outcomes = _run_together(
            self.foundation_id,
            lambda: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
            lambda: charge_qr_session(token_b, self.student, Decimal('1000'), 'sale-b'),
        )
        self.assertEqual([error for _result, error in outcomes], [None, None], outcomes)
        # A voided (+1000) and B charged (-1000) from 99000.
        self.assertEqual(self._balance(), START_BALANCE - Decimal('1000'))
        self._assert_ledger_balances()

    def test_void_and_charge_for_one_student_never_deadlock_over_many_rounds(self):
        # Void and charge take the same locks in the same order (wallet, then sale). A single round hits a
        # bad ordering only some of the time, so repeat: a lock-order regression shows up as InnoDB 1213.
        rounds = 12
        for i in range(rounds):
            sale = self._sale(key=f'sale-a-{i}')
            token = create_qr_session(self.terminal)['token']
            outcomes = _run_together(
                self.foundation_id,
                lambda sale=sale: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
                lambda token=token, i=i: charge_qr_session(token, self.student, Decimal('1000'), f'sale-b-{i}'),
            )
            self.assertEqual([error for _result, error in outcomes], [None, None], (i, outcomes))
        # Each round: A charged and voided, B charged.
        self.assertEqual(self._balance(), START_BALANCE - Decimal('1000') * rounds)
        self._assert_ledger_balances()

    def test_void_racing_an_upheld_dispute_on_the_same_sale_refunds_once(self):
        sale = self._sale()
        dispute = open_qr_dispute(sale, make_guardian(self.fx), 'Saya hanya beli minum')
        staff = self.fx['finance_user']
        outcomes = _run_together(
            self.foundation_id,
            lambda: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
            lambda: resolve_qr_dispute(dispute, QRDisputeStatus.UPHELD, staff),
        )
        # Whichever order wins, exactly one of them refunds; the other is refused with its own domain error.
        self.assertEqual(sum(error is None for _result, error in outcomes), 1, outcomes)
        for _result, error in outcomes:
            self.assertTrue(error is None or isinstance(error, (ValueError, QRDisputeError)), repr(error))
        self.assertEqual(self._balance(), START_BALANCE)
        self._assert_ledger_balances()

    # -- dispute vs void -----------------------------------------------------------------------------

    def test_two_concurrent_opens_for_one_sale_create_one_dispute(self):
        sale = self._sale()
        guardian = make_guardian(self.fx)
        outcomes = _run_together(
            self.foundation_id,
            lambda: open_qr_dispute(POSTransaction.objects.get(id=sale.id), guardian, 'a'),
            lambda: open_qr_dispute(POSTransaction.objects.get(id=sale.id), guardian, 'b'),
        )
        winners = [result for result, error in outcomes if error is None]
        losers = [error for _result, error in outcomes if error is not None]
        self.assertEqual((len(winners), len(losers)), (1, 1), outcomes)
        self.assertIsInstance(losers[0], QRDisputeError)
        self.assertEqual(losers[0].code, 'DISPUTE_EXISTS')
        self.assertEqual(QRDispute.all_tenants.filter(pos_transaction_id=sale.id).count(), 1)

    def test_an_open_racing_a_void_never_leaves_an_open_dispute_on_a_voided_sale(self):
        # Whichever order wins: the open lands first and the void closes it as VOIDED, or the void lands
        # first and the open is refused as not eligible. Never an OPEN dispute on a VOIDED sale.
        guardian = make_guardian(self.fx)
        rounds = 8
        for i in range(rounds):
            sale = self._sale(key=f'race-{i}')
            outcomes = _run_together(
                self.foundation_id,
                lambda sale=sale: open_qr_dispute(POSTransaction.objects.get(id=sale.id), guardian, 'x'),
                lambda sale=sale: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
            )
            for _result, error in outcomes:
                self.assertTrue(error is None or isinstance(error, (ValueError, QRDisputeError)), (i, repr(error)))
            sale.refresh_from_db()
            self.assertEqual(sale.status, POSTransactionStatus.VOIDED, (i, outcomes))
            self.assertFalse(
                QRDispute.all_tenants.filter(pos_transaction_id=sale.id, status=QRDisputeStatus.OPEN).exists(), (i, outcomes),
            )
        self.assertEqual(self._balance(), START_BALANCE)
        self._assert_ledger_balances()

    def test_void_and_resolve_of_one_disputed_sale_never_deadlock_over_many_rounds(self):
        # Void closes the sale's open dispute, resolve takes the same three locks (wallet, sale, dispute) in the
        # same order. A regression to dispute-first shows up here as InnoDB 1213.
        guardian = make_guardian(self.fx)
        staff = self.fx['finance_user']
        rounds = 8
        for i in range(rounds):
            sale = self._sale(key=f'dv-{i}')
            dispute = open_qr_dispute(sale, guardian, 'x')
            outcomes = _run_together(
                self.foundation_id,
                lambda sale=sale: void_pos_transaction(POSTransaction.objects.get(id=sale.id), 'salah input'),
                lambda dispute=dispute: resolve_qr_dispute(dispute, QRDisputeStatus.REJECTED, staff, 'x'),
            )
            for _result, error in outcomes:
                self.assertTrue(error is None or isinstance(error, (ValueError, QRDisputeError)), (i, repr(error)))
            dispute = QRDispute.all_tenants.get(id=dispute.id)
            self.assertIn(dispute.status, (QRDisputeStatus.VOIDED, QRDisputeStatus.REJECTED), (i, outcomes))
        self.assertEqual(self._balance(), START_BALANCE)
        self._assert_ledger_balances()

    # -- settlement ----------------------------------------------------------------------------------

    def _period(self):
        today = timezone.localdate()
        return today, today

    def test_two_concurrent_settlement_runs_for_one_period_yield_one_settlement(self):
        self._sale()
        start, end = self._period()
        outcomes = _run_together(
            self.foundation_id,
            lambda: run_merchant_settlement(self.merchant, start, end),
            lambda: run_merchant_settlement(self.merchant, start, end),
        )
        self.assertEqual([error for _result, error in outcomes], [None, None], outcomes)
        settlements = MerchantSettlement.all_tenants.filter(merchant=self.merchant, deleted_at__isnull=True)
        self.assertEqual(settlements.count(), 1)
        self.assertEqual(settlements.get().gross, Decimal('1000.00'))

    def test_mark_paid_queues_behind_a_settlement_run_holding_the_merchant_lock(self):
        self._sale()
        start, end = self._period()
        settlement = run_merchant_settlement(self.merchant, start, end)
        finished = threading.Event()

        def mark_paid():
            set_current_foundation_id(self.foundation_id)
            try:
                mark_settlement_paid(MerchantSettlement.objects.get(id=settlement.id))
            finally:
                connections.close_all()
                finished.set()

        worker = threading.Thread(target=mark_paid)
        with transaction.atomic():
            # What a run in flight holds: mark-paid must wait for it, not slip in before its totals are final.
            Merchant.all_tenants.select_for_update().get(id=self.merchant.id)
            worker.start()
            self.assertFalse(finished.wait(1.5), 'mark_settlement_paid did not wait for the merchant lock')
        self.assertTrue(finished.wait(30))
        worker.join()
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, MerchantSettlementStatus.PAID)
        with self.assertRaises(SettlementStateError):
            run_merchant_settlement(self.merchant, start, end)

    def test_settlement_run_racing_a_dispute_resolution_absorbs_the_adjustment_exactly_once(self):
        sale = self._sale()
        guardian = make_guardian(self.fx)
        dispute = open_qr_dispute(sale, guardian, 'Saya hanya beli minum')
        staff = self.fx['finance_user']
        start, end = self._period()
        outcomes = _run_together(
            self.foundation_id,
            lambda: resolve_qr_dispute(dispute, QRDisputeStatus.UPHELD, staff),
            lambda: run_merchant_settlement(self.merchant, start, end),
        )
        self.assertEqual([error for _result, error in outcomes], [None, None], outcomes)
        # Whichever order won, a follow-up run must leave the single adjustment absorbed once, with totals consistent.
        run_merchant_settlement(self.merchant, start, end)
        settlement = MerchantSettlement.all_tenants.get(merchant=self.merchant, deleted_at__isnull=True)
        lines = MerchantSettlementAdjustment.all_tenants.filter(merchant=self.merchant)
        self.assertEqual(lines.count(), 1)
        self.assertEqual(lines.get().settlement_id, settlement.id)
        self.assertEqual(settlement.adjustments_total, lines.get().deduction)
        self.assertEqual(settlement.net, settlement.gross - settlement.commission - settlement.adjustments_total)
        self.assertEqual(self._balance(), START_BALANCE)  # the upheld dispute refunded the sale once
        self._assert_ledger_balances()


@skipUnless(connection.vendor == 'mysql', 'real-concurrency test needs MySQL/InnoDB')
class PinAttemptConcurrencyTests(QRFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.foundation_id = self.fx['foundation'].id
        self.user = make_guardian(self.fx)

    def _wrong_pin(self):
        return check_pin(self.user, '000001')

    def test_a_failed_attempt_is_counted_even_though_the_check_raises(self):
        with self.assertRaises(PinError) as ctx:
            self._wrong_pin()
        self.assertEqual(ctx.exception.code, 'PIN_INVALID')
        self.assertEqual(UserPin.all_tenants.get(user=self.user).failed_count, 1)

    def test_parallel_wrong_pins_cannot_beat_the_lockout(self):
        outcomes = _run_together(self.foundation_id, *[self._wrong_pin] * 8)
        codes = sorted(error.code for _result, error in outcomes)
        # Attempts 1 and 2 are PIN_INVALID; the 3rd locks; every later guess is refused without counting.
        self.assertEqual(codes, ['PIN_INVALID'] * 2 + ['PIN_LOCKED'] * 6, codes)
        pin = UserPin.all_tenants.get(user=self.user)
        self.assertEqual(pin.failed_count, 3)
        self.assertIsNotNone(pin.locked_until)
        # The right PIN is refused while locked, so the lock cannot be raced open.
        with self.assertRaises(PinError) as ctx:
            check_pin(self.user, GUARDIAN_PIN)
        self.assertEqual(ctx.exception.code, 'PIN_LOCKED')
