"""Canteen QR Charge: a student pays with a token the terminal signed while offline (QRS-022/024)."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wallet.models import (
    POSEntryMode,
    POSTerminalStatus,
    POSTransaction,
    POSTransactionStatus,
    Wallet,
    WalletReconciliation,
)
from apps.wallet.qr_charge import QRChargeError, charge_qr_session, resolve_qr_session
from apps.wallet.qr_offline import issue_terminal_session_key, mint_offline_session_token, revoke_terminal_session_key
from apps.wallet.services import process_offline_pos_batch
from apps.wallet.tests.test_offline_sync import batch_item
from apps.wallet.tests.test_qr_charge import QRFixtureMixin
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import set_current_foundation_id


class _OfflineBase(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.key, self.secret = issue_terminal_session_key(self.terminal)

    def mint(self, ttl_seconds=120):
        return mint_offline_session_token(self.key, self.secret, ttl_seconds=ttl_seconds)

    def balance(self):
        # all_tenants: after an API request the ambient tenant context is cleared.
        return Wallet.all_tenants.get(student=self.student).balance


class OfflineTokenChargeTests(_OfflineBase):
    def test_charge_debits_wallet_and_stamps_key_and_nonce(self):
        pos_tx = self.pay('18000')
        self.assertEqual(self.balance(), Decimal('82000.00'))
        self.assertEqual(pos_tx.entry_mode, POSEntryMode.SELF_ENTERED)
        self.assertEqual(pos_tx.terminal_id, self.terminal.id)
        self.assertEqual(pos_tx.qr_offline_key_id, self.key.id)
        self.assertTrue(pos_tx.qr_offline_nonce)
        self.assertIsNone(pos_tx.qr_session_id)
        self.assertEqual(pos_tx.commission, Decimal('360.00'))
        self.assertEqual(len(pos_tx.confirmation_code), 4)

    def test_qrs024_no_reconciliation_rows(self):
        self.pay('18000')
        self.assertFalse(WalletReconciliation.objects.exists())

    def test_resolve_shows_terminal_balance_and_cap(self):
        data = resolve_qr_session(self.mint(), self.student)
        self.assertEqual(data['type'], 'SESSION')
        self.assertIsNone(data['session_id'])
        self.assertEqual(data['merchant_name'], self.merchant.name)
        self.assertEqual(data['balance'], Decimal('100000.00'))

    def test_idempotent_retry_returns_original_and_debits_once(self):
        token = self.mint()
        first = charge_qr_session(token, self.student, Decimal('18000'), 'same-key')
        again = charge_qr_session(token, self.student, Decimal('18000'), 'same-key')
        self.assertEqual(first.id, again.id)
        self.assertEqual(self.balance(), Decimal('82000.00'))

    def test_token_is_single_use(self):
        token = self.mint()
        self.pay('1000', key='a', token=token)
        self.assertRefused('QR_TOKEN_USED', self.pay, '1000', key='b', token=token)
        self.assertEqual(self.balance(), Decimal('99000.00'))

    def test_expired_token_refused(self):
        token = self.mint()
        later = timezone.now() + timedelta(minutes=10)
        with mock.patch('django.utils.timezone.now', return_value=later):
            self.assertRefused('QR_TOKEN_EXPIRED', self.pay, token=token)
        self.assertEqual(self.balance(), Decimal('100000.00'))

    def test_online_charge_window_is_tighter_than_the_sync_tolerance(self):
        # 120 s TTL + 60 s online tolerance = 3 min. 4 min is still inside the 5 min sync tolerance,
        # so this pins that the online charge no longer inherits it.
        token = self.mint()
        later = timezone.now() + timedelta(minutes=4)
        with mock.patch('django.utils.timezone.now', return_value=later):
            self.assertRefused('QR_TOKEN_EXPIRED', self.pay, token=token)
        self.assertEqual(self.balance(), Decimal('100000.00'))

    def test_online_charge_tolerates_a_slightly_slow_terminal_clock(self):
        token = self.mint()
        later = timezone.now() + timedelta(seconds=120 + 30)  # past TTL, inside the 60 s tolerance
        with mock.patch('django.utils.timezone.now', return_value=later):
            self.pay('1000', token=token)
        self.assertEqual(self.balance(), Decimal('99000.00'))

    def test_online_charge_refuses_a_terminal_clock_running_ahead(self):
        # A token stamped 2 min in the future (fast terminal clock) is beyond the 60 s tolerance.
        token = mint_offline_session_token(self.key, self.secret)
        earlier = timezone.now() - timedelta(minutes=2)
        with mock.patch('django.utils.timezone.now', return_value=earlier):
            self.assertRefused('QR_TOKEN_EXPIRED', self.pay, token=token)

    def test_tampered_and_garbage_tokens_are_invalid(self):
        payload_b64, sig = self.mint().rsplit('.', 1)
        for bad in (f"{payload_b64}.{'0' * len(sig)}", 'nope.nope', 'a.b', '.', 'x' * 40 + '.' + 'y' * 64):
            self.assertRefused('QR_TOKEN_INVALID', self.pay, token=bad)
        self.assertEqual(self.balance(), Decimal('100000.00'))

    def test_token_signed_with_wrong_secret_is_invalid(self):
        forged = mint_offline_session_token(self.key, 'not-the-secret')
        self.assertRefused('QR_TOKEN_INVALID', self.pay, token=forged)

    def test_revoked_key_within_grace_still_charges(self):
        token = self.mint()
        revoke_terminal_session_key(self.key)
        self.pay('1000', token=token)
        self.assertEqual(self.balance(), Decimal('99000.00'))

    def test_revoked_key_outside_grace_is_invalid(self):
        token = self.mint()
        revoke_terminal_session_key(self.key)
        self.key.refresh_from_db()
        self.key.grace_until = timezone.now() - timedelta(minutes=1)
        self.key.save(update_fields=['grace_until'])
        self.assertRefused('QR_TOKEN_INVALID', self.pay, token=token)

    def test_inactive_terminal_refused(self):
        token = self.mint()
        self.terminal.status = POSTerminalStatus.INACTIVE
        self.terminal.save()
        self.assertRefused('QR_MODE_DISABLED_BY_MERCHANT', self.pay, token=token)

    def test_merchant_switch_off_refused(self):
        token = self.mint()
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        self.assertRefused('QR_MODE_DISABLED_BY_MERCHANT', self.pay, token=token)

    def test_token_from_other_foundation_is_foreign(self):
        token = self.mint()
        other = build_wallet_fixture(foundation_name="Yayasan Lain Offline")
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertRefused('MERCHANT_FOREIGN_TENANT', resolve_qr_session, token, other['student'])
        self.assertRefused('MERCHANT_FOREIGN_TENANT', charge_qr_session, token, other['student'], Decimal('1000'), 'x')

    def test_insufficient_balance_is_logged_without_burning_the_token(self):
        self.pay('50000', key='a')
        self.pay('40000', key='b')
        token = self.mint()
        self.assertRefused('INSUFFICIENT_BALANCE', self.pay, '10001', key='big', token=token)
        rejected = POSTransaction.objects.get(status=POSTransactionStatus.REJECTED)
        self.assertEqual(rejected.qr_offline_nonce, '')
        self.assertEqual(rejected.reject_reason, 'INSUFFICIENT_BALANCE')
        # A rejected attempt must not claim the nonce: the same QR still works for a valid amount.
        self.pay('1000', key='ok', token=token)
        self.assertEqual(self.balance(), Decimal('9000.00'))

    def test_nonce_race_loser_gets_used_and_is_not_debited(self):
        """Two phones pass the pre-check together; the unique constraint lets exactly one sale commit."""
        token = self.mint()
        self.pay('1000', key='winner', token=token)
        with mock.patch('apps.wallet.qr_charge._offline_nonce_claimed', side_effect=[False, True]):
            self.assertRefused('QR_TOKEN_USED', self.pay, '1000', key='loser', token=token)
        self.assertEqual(self.balance(), Decimal('99000.00'))
        self.assertEqual(POSTransaction.objects.filter(entry_mode=POSEntryMode.SELF_ENTERED).count(), 1)

    def test_offline_token_reads_as_offline_not_a_signed_session(self):
        from apps.wallet.qr_charge import _looks_offline
        from apps.wallet.qr_charge import create_qr_session
        self.assertTrue(_looks_offline(self.mint()))
        self.assertFalse(_looks_offline(create_qr_session(self.terminal)['token']))


class SyncAfterOnlineChargeTests(_OfflineBase):
    """QRS-022/024 + WAL-015: the debit already happened online, so the terminal's sync only reconciles."""

    def sync(self, token, client_id='term-1', **extra):
        item = {'client_transaction_id': client_id, 'qr_token': token, **extra}
        return process_offline_pos_batch(self.terminal, [item])['results'][0]

    def test_sync_after_charge_creates_no_second_sale(self):
        token = self.mint()
        pos_tx = self.pay('18000', token=token)
        result = self.sync(token, occurred_at=timezone.now())
        self.assertEqual(result['status'], POSTransactionStatus.COMPLETED)
        self.assertTrue(result['reconciled'])
        self.assertEqual(result['confirmation_code'], pos_tx.confirmation_code)
        self.assertEqual(result['total'], '18000.00')
        self.assertEqual(POSTransaction.objects.filter(terminal=self.terminal).count(), 1)
        self.assertEqual(self.balance(), Decimal('82000.00'))

    def test_repeated_sync_is_idempotent(self):
        token = self.mint()
        self.pay('18000', token=token)
        first = self.sync(token, occurred_at=timezone.now())
        again = self.sync(token, occurred_at=timezone.now())
        self.assertEqual(first, again)
        self.assertEqual(self.balance(), Decimal('82000.00'))

    def test_sync_reports_a_voided_charge_as_voided(self):
        from apps.wallet.services import void_pos_transaction
        token = self.mint()
        void_pos_transaction(self.pay('18000', token=token), 'wrong amount')
        self.assertEqual(self.sync(token, occurred_at=timezone.now())['status'], POSTransactionStatus.VOIDED)

    def test_sync_with_forged_token_does_not_reconcile(self):
        forged = mint_offline_session_token(self.key, 'not-the-secret')
        self.assertEqual(self.sync(forged, occurred_at=timezone.now())['status'], 'QR_TOKEN_BAD_SIGNATURE')

    def test_sale_synced_first_blocks_the_later_student_charge(self):
        token = self.mint()
        item = batch_item('SKU-1', 'Roti', 'card-sale-1', student_id=self.student.id)
        item.update(qr_token=token, occurred_at=timezone.now())
        self.assertEqual(process_offline_pos_batch(self.terminal, [item])['results'][0]['status'], 'COMPLETED')
        self.assertRefused('QR_TOKEN_USED', self.pay, token=token)

    def test_a_terminal_sale_holding_the_nonce_is_still_a_replay_on_resync(self):
        token = self.mint()
        item = batch_item('SKU-1', 'Roti', 'card-sale-2', student_id=self.student.id)
        item.update(qr_token=token, occurred_at=timezone.now())
        process_offline_pos_batch(self.terminal, [item])
        replay = {**item, 'client_transaction_id': 'card-sale-2-replay'}
        self.assertEqual(process_offline_pos_batch(self.terminal, [replay])['results'][0]['status'], 'QR_TOKEN_REPLAYED')


class BatchApiCarriesQrTokenTests(_OfflineBase):
    """#245's serializer dropped ``qr_token``, so the check never ran through the real endpoint."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['finance_user'])

    def post(self, transactions):
        return self.client.post(
            '/api/v1/pos/transactions/batch/',
            {'terminal_id': self.terminal.id, 'transactions': transactions}, format='json',
        )

    def test_bad_token_is_rejected_through_the_api(self):
        item = batch_item('SKU-1', 'Roti', 'api-qr-1', student_id=self.student.id)
        item['qr_token'] = 'garbage.token'
        res = self.post([item])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['results'][0]['status'], 'QR_TOKEN_MALFORMED')
        self.assertFalse(POSTransaction.objects.filter(client_transaction_id='api-qr-1').exists())

    def test_token_only_entry_reconciles_through_the_api(self):
        token = self.mint()
        self.pay('18000', token=token)
        res = self.post([{
            'client_transaction_id': 'api-qr-2', 'qr_token': token, 'occurred_at': timezone.now().isoformat(),
        }])
        self.assertEqual(res.status_code, 200, res.content)
        result = res.json()['results'][0]
        self.assertTrue(result['reconciled'])
        self.assertEqual(self.balance(), Decimal('82000.00'))

    def test_entry_without_token_still_requires_student_and_items(self):
        res = self.post([{'client_transaction_id': 'api-qr-3'}])
        self.assertEqual(res.status_code, 400)
