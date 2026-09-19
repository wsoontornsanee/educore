"""Canteen QR Charge single-use guarantees under real concurrency (MySQL only).

SQLite serialises writers and enforces conditional unique constraints, so the
simulated race tests in test_qr_offline_charge.py pass there while proving nothing
about MySQL. These tests run two real threads against the database; they skip on
any other backend.
"""
import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.wallet.models import POSEntryMode, POSTransaction, Wallet
from apps.wallet.qr_charge import QRChargeError, _offline_nonce_claimed, charge_qr_session, create_qr_session
from apps.wallet.qr_offline import issue_terminal_session_key, mint_offline_session_token
from apps.wallet.tests.test_qr_charge import QRFixtureMixin
from educore.middleware.tenancy import set_current_foundation_id


def _run_together(foundation_id, *calls):
    """Run each callable in its own thread and return [(result, error), ...] in call order."""
    outcomes = [None] * len(calls)

    def worker(index, call):
        set_current_foundation_id(foundation_id)
        try:
            outcomes[index] = (call(), None)
        except Exception as exc:  # noqa: BLE001 - the test inspects the outcome
            outcomes[index] = (None, exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


@skipUnless(connection.vendor == 'mysql', 'real-concurrency test needs MySQL/InnoDB')
class QRSingleUseConcurrencyTests(QRFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.foundation_id = self.fx['foundation'].id

    def _assert_exactly_one_sale(self, outcomes):
        winners = [result for result, error in outcomes if error is None]
        losers = [error for _result, error in outcomes if error is not None]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(losers), 1, outcomes)
        self.assertIsInstance(losers[0], QRChargeError)
        self.assertEqual(losers[0].code, 'QR_TOKEN_USED')
        self.assertEqual(Wallet.all_tenants.get(student=self.student).balance, Decimal('99000.00'))
        sales = POSTransaction.all_tenants.filter(entry_mode=POSEntryMode.SELF_ENTERED)
        self.assertEqual(sales.count(), 1)

    def test_two_phones_on_one_session_token_yield_one_sale(self):
        token = create_qr_session(self.terminal)['token']
        outcomes = _run_together(
            self.foundation_id,
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'phone-a'),
            lambda: charge_qr_session(token, self.student, Decimal('1000'), 'phone-b'),
        )
        self._assert_exactly_one_sale(outcomes)

    def test_two_phones_on_one_offline_token_yield_one_sale(self):
        key, secret = issue_terminal_session_key(self.terminal)
        token = mint_offline_session_token(key, secret, ttl_seconds=120)

        # Force the interleaving that matters: both requests pass the pre-check
        # (nothing committed yet) before either inserts, so only the database's
        # unique index can pick a single winner.
        both_prechecked = threading.Barrier(2, timeout=30)
        seen = threading.local()

        def checked_then_wait(terminal, nonce):
            claimed = _offline_nonce_claimed(terminal, nonce)
            if not getattr(seen, 'waited', False):
                seen.waited = True
                both_prechecked.wait()
            return claimed

        with mock.patch('apps.wallet.qr_charge._offline_nonce_claimed', side_effect=checked_then_wait):
            outcomes = _run_together(
                self.foundation_id,
                lambda: charge_qr_session(token, self.student, Decimal('1000'), 'phone-a'),
                lambda: charge_qr_session(token, self.student, Decimal('1000'), 'phone-b'),
            )
        self._assert_exactly_one_sale(outcomes)
