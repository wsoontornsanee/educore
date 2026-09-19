"""Canteen QR Charge: offline-terminal-minted session tokens (QRS-022/023)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment, User
from apps.wallet.models import POSTerminalSessionKey, POSTerminalSessionKeyStatus, POSTransaction
from apps.wallet.qr_offline import (
    OfflineTokenError,
    issue_terminal_session_key,
    mint_offline_session_token,
    revoke_terminal_session_key,
    verify_offline_session_token,
)
from apps.wallet.services import get_or_create_wallet, process_offline_pos_batch, topup_wallet
from apps.wallet.tests.test_offline_sync import batch_item
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class _Base(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')
        self.key, self.secret = issue_terminal_session_key(self.terminal)

    def mint(self, ttl_seconds=120):
        return mint_offline_session_token(self.key, self.secret, ttl_seconds=ttl_seconds)


class VerifyOfflineSessionTokenTests(_Base):
    def test_valid_token_verifies_and_returns_nonce(self):
        token = self.mint()
        key, nonce = verify_offline_session_token(self.terminal, token, timezone.now())
        self.assertEqual(key.id, self.key.id)
        self.assertTrue(nonce)

    def test_tampered_signature_is_rejected(self):
        token = self.mint()
        payload_b64, sig = token.rsplit('.', 1)
        tampered = f"{payload_b64}.{'0' * len(sig)}"
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(self.terminal, tampered, timezone.now())
        self.assertEqual(ctx.exception.code, 'BAD_SIGNATURE')

    def test_malformed_token_is_rejected(self):
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(self.terminal, 'not-a-real-token', timezone.now())
        self.assertEqual(ctx.exception.code, 'MALFORMED')

    def test_wrong_terminal_is_rejected(self):
        from apps.wallet.models import POSTerminal
        other_terminal = POSTerminal.objects.create(
            foundation_id=self.fx['foundation'].id, merchant=self.merchant, device_id="TERM-OTHER", name="Kasir 2",
        )
        token = self.mint()
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(other_terminal, token, timezone.now())
        # Key lookup is scoped to `terminal`, so an unrelated terminal simply
        # finds no matching key_id.
        self.assertEqual(ctx.exception.code, 'KEY_INVALID')

    def test_expired_token_is_rejected(self):
        token = self.mint(ttl_seconds=1)
        occurred_at = timezone.now() + timedelta(minutes=30)  # well past TTL + skew
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(self.terminal, token, occurred_at)
        self.assertEqual(ctx.exception.code, 'EXPIRED')

    def test_within_clock_skew_tolerance_still_verifies(self):
        token = self.mint(ttl_seconds=60)
        occurred_at = timezone.now() + timedelta(seconds=90)  # past TTL, inside 5-min skew
        key, _nonce = verify_offline_session_token(self.terminal, token, occurred_at)
        self.assertEqual(key.id, self.key.id)

    def test_custom_skew_tolerance_narrows_the_window(self):
        token = self.mint(ttl_seconds=60)
        occurred_at = timezone.now() + timedelta(seconds=90)  # inside the 5 min default
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(self.terminal, token, occurred_at, skew_tolerance=timedelta(seconds=10))
        self.assertEqual(ctx.exception.code, 'EXPIRED')

    def test_revoked_key_outside_grace_period_is_rejected(self):
        revoke_terminal_session_key(self.key)
        self.key.grace_until = timezone.now() - timedelta(days=1)
        self.key.save(update_fields=['grace_until'])
        token = self.mint()
        with self.assertRaises(OfflineTokenError) as ctx:
            verify_offline_session_token(self.terminal, token, timezone.now())
        self.assertEqual(ctx.exception.code, 'KEY_INVALID')

    def test_revoked_key_within_grace_period_still_verifies(self):
        token = self.mint()
        revoke_terminal_session_key(self.key)  # default grace = 7 days, still in window
        key, _nonce = verify_offline_session_token(self.terminal, token, timezone.now())
        self.assertEqual(key.status, POSTerminalSessionKeyStatus.REVOKED)

    def test_issuing_a_new_key_revokes_the_old_one_with_grace(self):
        old_key = self.key
        new_key, _new_secret = issue_terminal_session_key(self.terminal)
        old_key.refresh_from_db()
        self.assertEqual(old_key.status, POSTerminalSessionKeyStatus.REVOKED)
        self.assertIsNotNone(old_key.grace_until)
        self.assertEqual(
            POSTerminalSessionKey.objects.filter(terminal=self.terminal, status=POSTerminalSessionKeyStatus.ACTIVE).count(), 1,
        )
        self.assertNotEqual(new_key.id, old_key.id)


class OfflineBatchWithQrTokenTests(_Base):
    def test_valid_qr_token_charges_and_records_the_key_and_nonce(self):
        token = self.mint()
        batch = [{**batch_item(self.product.sku, self.product.name, 'qr-1', student_id=self.fx['student'].id), 'qr_token': token}]
        report = process_offline_pos_batch(self.terminal, batch)
        self.assertEqual(report['results'][0]['status'], 'COMPLETED')
        tx = POSTransaction.objects.get(client_transaction_id='qr-1')
        self.assertEqual(tx.qr_offline_key_id, self.key.id)
        self.assertTrue(tx.qr_offline_nonce)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))

    def test_replayed_nonce_is_rejected_on_second_sync(self):
        token = self.mint()
        batch = [{**batch_item(self.product.sku, self.product.name, 'qr-2', student_id=self.fx['student'].id), 'qr_token': token}]
        process_offline_pos_batch(self.terminal, batch)
        # Simulate the terminal re-sending the SAME minted token under a different
        # client_transaction_id (e.g. a captured/replayed QR) — the nonce alone
        # must block it, independent of the client_transaction_id idempotency key.
        replay = [{**batch_item(self.product.sku, self.product.name, 'qr-2-replay', student_id=self.fx['student'].id), 'qr_token': token}]
        report = process_offline_pos_batch(self.terminal, replay)
        self.assertEqual(report['results'][0]['status'], 'QR_TOKEN_REPLAYED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))  # unchanged by the replay

    def test_tampered_qr_token_is_rejected_and_no_debit_occurs(self):
        token = self.mint()
        payload_b64, sig = token.rsplit('.', 1)
        tampered = f"{payload_b64}.{'0' * len(sig)}"
        batch = [{**batch_item(self.product.sku, self.product.name, 'qr-3', student_id=self.fx['student'].id), 'qr_token': tampered}]
        report = process_offline_pos_batch(self.terminal, batch)
        self.assertEqual(report['results'][0]['status'], 'QR_TOKEN_BAD_SIGNATURE')
        self.assertFalse(POSTransaction.objects.filter(client_transaction_id='qr-3').exists())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_batch_retry_after_qr_success_is_still_idempotent_on_client_transaction_id(self):
        token = self.mint()
        batch = [{**batch_item(self.product.sku, self.product.name, 'qr-4', student_id=self.fx['student'].id), 'qr_token': token}]
        process_offline_pos_batch(self.terminal, batch)
        report = process_offline_pos_batch(self.terminal, batch)  # exact retry, same client_transaction_id
        self.assertEqual(report['results'][0]['status'], 'COMPLETED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))


class SessionKeyApiTests(_Base):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+6281300009001', full_name='Admin Sekolah',
        )
        RoleAssignment.objects.create(
            foundation_id=self.fx['foundation'].id, user=self.admin, role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.client = APIClient()

    def _url(self, action):
        return f"/api/v1/pos/terminals/{self.terminal.id}/session-key/{action}/"

    def test_anonymous_is_rejected(self):
        response = self.client.post(self._url('issue'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_issue_returns_secret_once(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._url('issue'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('secret', response.data)
        self.assertTrue(response.data['key_id'])
        # The prior key from setUp is now revoked-with-grace, a fresh one is ACTIVE.
        self.assertEqual(
            POSTerminalSessionKey.all_tenants.filter(terminal=self.terminal, status=POSTerminalSessionKeyStatus.ACTIVE).count(), 1,
        )

    def test_revoke_then_issue_round_trip(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._url('revoke'))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.key.refresh_from_db()
        self.assertEqual(self.key.status, POSTerminalSessionKeyStatus.REVOKED)
        response = self.client.post(self._url('revoke'))  # no ACTIVE key left
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_foundations_terminal_is_404(self):
        from apps.wallet.models import Merchant, MerchantType, POSTerminal

        other_fx = build_wallet_fixture(foundation_name="Yayasan Lain")
        other_merchant = Merchant.objects.create(
            foundation_id=other_fx['foundation'].id, school=other_fx['school'], name="Kantin Lain",
            type=MerchantType.CANTEEN, commission_bps=200,
        )
        other_terminal = POSTerminal.objects.create(
            foundation_id=other_fx['foundation'].id, merchant=other_merchant, device_id="TERM-OTHER-FND", name="Kasir Lain",
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f"/api/v1/pos/terminals/{other_terminal.id}/session-key/issue/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
