"""Money-path invariants under real InnoDB concurrency (MySQL only; spec/01 §5 locking).

Companion to test_qr_mysql_concurrency.py, which covers the two single-use tokens. These run real threads
against the database for the rest: idempotency-key retries, void vs charge, double void, settlement runs vs
dispute resolution, and the spending-PIN attempt counter. Skipped on any other backend, because SQLite
serialises writers and ignores ``select_for_update``.
"""
import threading
import time
from decimal import Decimal
from unittest import mock, skipUnless

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
    QRDisputeStatus,
    Wallet,
    WalletTransaction,
)
from apps.wallet.qr_charge import QRChargeError, charge_qr_session, create_qr_session
from apps.wallet.qr_decals import create_payment_point, decal_token, print_decal
from apps.wallet import qr_oversight
from apps.wallet.qr_oversight import QRDisputeError, open_qr_dispute, resolve_qr_dispute
from apps.wallet.services import (
    SettlementStateError,
    mark_settlement_paid,
    process_offline_pos_batch,
    process_pos_transaction,
    run_merchant_settlement,
    topup_wallet,
    void_pos_transaction,
)
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

    # -- lock order across the other money paths (wallet, then sale; merchant for settlement) -----------

    def _many_rounds(self, rounds, make_calls):
        """Run two racing calls per round; any InnoDB lock error (1213 deadlock, 1205 timeout) fails the round."""
        for i in range(rounds):
            outcomes = _run_together(self.foundation_id, *make_calls(i))
            self.assertEqual([error for _result, error in outcomes], [None, None], (i, outcomes))

    def test_top_up_and_charge_on_one_wallet_never_deadlock(self):
        wallet = Wallet.all_tenants.get(student=self.student)
        tokens = [create_qr_session(self.terminal)['token'] for _ in range(10)]
        self._many_rounds(10, lambda i: (
            lambda: topup_wallet(Wallet.objects.get(id=wallet.id), Decimal('500'), 'CASH', f'top-{i}'),
            lambda: charge_qr_session(tokens[i], self.student, Decimal('1000'), f'pay-{i}'),
        ))
        self._assert_ledger_balances()

    def test_settlement_run_and_a_charge_never_deadlock(self):
        start, end = self._period()
        tokens = [create_qr_session(self.terminal)['token'] for _ in range(10)]
        self._many_rounds(10, lambda i: (
            lambda: run_merchant_settlement(self.merchant, start, end),
            lambda: charge_qr_session(tokens[i], self.student, Decimal('1000'), f'pay-{i}'),
        ))
        self.assertEqual(MerchantSettlement.all_tenants.filter(merchant=self.merchant).count(), 1)
        self._assert_ledger_balances()

    def test_card_tap_checkout_and_a_void_of_another_sale_never_deadlock(self):
        sales = [self._sale(key=f'qr-{i}') for i in range(10)]
        item = [{'sku': 'ROTI', 'name': 'Roti', 'qty': 1, 'unit_price': '1000.00'}]
        self._many_rounds(10, lambda i: (
            lambda: void_pos_transaction(POSTransaction.objects.get(id=sales[i].id), 'salah input'),
            lambda: process_pos_transaction(self.terminal, self.student, item, f'card-{i}'),
        ))
        self._assert_ledger_balances()

    # -- duplicate submissions of one client_transaction_id (card tap, offline sync) ----------------------

    def test_double_submitted_card_tap_checkout_returns_the_one_sale_to_both_requests(self):
        item = [{'sku': 'ROTI', 'name': 'Roti', 'qty': 1, 'unit_price': '1000.00'}]
        for i in range(6):
            outcomes = _run_together(
                self.foundation_id,
                lambda i=i: process_pos_transaction(self.terminal, self.student, item, f'dup-{i}'),
                lambda i=i: process_pos_transaction(self.terminal, self.student, item, f'dup-{i}'),
            )
            self.assertEqual([error for _result, error in outcomes], [None, None], (i, outcomes))
            self.assertEqual(outcomes[0][0].id, outcomes[1][0].id)
        self.assertEqual(POSTransaction.all_tenants.filter(client_transaction_id__startswith='dup-').count(), 6)
        self.assertEqual(self._balance(), START_BALANCE - Decimal('1000') * 6)
        self._assert_ledger_balances()

    def test_double_synced_offline_batch_neither_fails_nor_double_debits(self):
        batch = [
            {'client_transaction_id': f'off-{i}', 'student_id': self.student.id,
             'items': [{'sku': 'ROTI', 'name': 'Roti', 'qty': 1, 'unit_price': '1000.00'}]}
            for i in range(6)
        ]
        outcomes = _run_together(
            self.foundation_id,
            lambda: process_offline_pos_batch(self.terminal, batch),
            lambda: process_offline_pos_batch(self.terminal, batch),
        )
        self.assertEqual([error for _result, error in outcomes], [None, None], outcomes)
        for result, _error in outcomes:
            self.assertEqual(len(result['results']), 6)
        self.assertEqual(POSTransaction.all_tenants.filter(client_transaction_id__startswith='off-').count(), 6)
        self.assertEqual(self._balance(), START_BALANCE - Decimal('1000') * 6)
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

    def test_flagging_dispute_resolution_and_a_settlement_run_do_not_deadlock(self):
        # The third upheld dispute flags the merchant: resolve_qr_dispute inserts its settlement adjustment and
        # then updates the merchant row, while a settlement run takes the merchant row and then scans
        # adjustments. It holds because inserting the adjustment takes a shared lock on the merchant row (foreign key
        # check), which queues the settlement run behind the resolution instead of crossing it.
        guardian = make_guardian(self.fx)
        staff = self.fx['finance_user']
        sales = [self._sale(key=f'flag-{i}') for i in range(3)]
        disputes = [open_qr_dispute(sale, guardian, 'Saya hanya beli minum') for sale in sales]
        for dispute in disputes[:2]:
            resolve_qr_dispute(dispute, QRDisputeStatus.UPHELD, staff)
        start, end = self._period()
        adjustment_written = threading.Event()
        real_flag = qr_oversight._flag_merchant_if_needed

        def flag_after_the_settlement_run_has_started(merchant):
            adjustment_written.set()
            time.sleep(1.5)  # let the settlement run take its locks and reach the adjustment scan
            return real_flag(merchant)

        def settle():
            adjustment_written.wait(30)
            return run_merchant_settlement(self.merchant, start, end)

        with mock.patch.object(qr_oversight, '_flag_merchant_if_needed', flag_after_the_settlement_run_has_started):
            outcomes = _run_together(
                self.foundation_id,
                lambda: resolve_qr_dispute(disputes[2], QRDisputeStatus.UPHELD, staff),
                settle,
            )
        self.assertEqual([error for _result, error in outcomes], [None, None], outcomes)
        self.assertIsNotNone(Merchant.all_tenants.get(id=self.merchant.id).qr_dispute_flagged_at)
        self._assert_ledger_balances()

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
