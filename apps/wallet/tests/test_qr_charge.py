"""Tests for canteen QR Charge: student-entered amount (spec 18, QRS-*)."""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import Guardian, GuardianLink, Person, RoleAssignment, School, Student, User
from apps.identity.rbac import ROLE_PARENT, SCOPE_SCHOOL, assign_role
from apps.wallet.models import (
    Merchant,
    POSEntryMode,
    POSTerminal,
    POSTransaction,
    POSTransactionStatus,
    Wallet,
    WalletStatus,
)
from apps.wallet.qr_charge import (
    QRChargeError,
    cancel_qr_session,
    charge_qr_session,
    create_qr_session,
    get_qr_session_result,
    resolve_qr_session,
    set_merchant_qr_charge,
)
from apps.wallet.services import get_or_create_wallet, set_spend_rule, topup_wallet, void_pos_transaction
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import set_current_foundation_id


def make_guardian(fx, student=None, nik='3471010101017777'):
    student = student or fx['student']
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name="Ibu Wali")
    user = User.objects.create(
        foundation_id=fx['foundation'].id, phone_e164=f"+62855{nik[-8:]}", email=f"{nik}@parent.id", full_name="Ibu Wali",
    )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=student,
        relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
    )
    assign_role(
        user=user, role=ROLE_PARENT, scope_type=SCOPE_SCHOOL, scope_id=fx['school'].id,
        foundation_id=fx['foundation'].id,
    )
    return user


class QRFixtureMixin:
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.student = self.fx['student']
        self.merchant, _product, self.terminal = build_pos_fixture(self.fx)
        self.merchant.qr_self_amount_enabled = True
        self.merchant.save()
        self.wallet = get_or_create_wallet(self.student)
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-topup')

    def mint(self):
        return create_qr_session(self.terminal)['token']

    def pay(self, amount='18000', key='k1', token=None):
        return charge_qr_session(token or self.mint(), self.student, Decimal(amount), key)

    def assertRefused(self, code, fn, *args, **kwargs):
        with self.assertRaises(QRChargeError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception


class ChargeHappyPathTests(QRFixtureMixin, TestCase):
    def test_charge_debits_wallet_and_stamps_self_entered(self):
        pos_tx = self.pay('18000')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('82000.00'))
        self.assertEqual(pos_tx.entry_mode, POSEntryMode.SELF_ENTERED)
        self.assertEqual(pos_tx.status, POSTransactionStatus.COMPLETED)
        self.assertEqual(pos_tx.items, [])
        self.assertEqual(pos_tx.total, Decimal('18000.00'))
        self.assertEqual(pos_tx.commission, Decimal('360.00'))  # 2% commission, same as card-tap
        self.assertEqual(len(pos_tx.confirmation_code), 4)
        self.assertEqual(pos_tx.wallet_transaction.balance_after, Decimal('82000.00'))

    def test_token_encodes_no_amount_student_or_balance(self):
        from django.core import signing
        token = self.mint()
        payload = signing.loads(token, salt='wallet.qr.session')
        self.assertEqual(set(payload), {'s', 'n'})

    def test_idempotent_retry_returns_original_and_debits_once(self):
        token = self.mint()
        first = charge_qr_session(token, self.student, Decimal('18000'), 'same-key')
        again = charge_qr_session(token, self.student, Decimal('18000'), 'same-key')
        self.assertEqual(first.id, again.id)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('82000.00'))
        self.assertEqual(POSTransaction.objects.filter(entry_mode=POSEntryMode.SELF_ENTERED).count(), 1)

    def test_amount_rounds_half_up_to_two_places(self):
        pos_tx = self.pay('100.005')
        self.assertEqual(pos_tx.total, Decimal('100.01'))

    def test_operator_void_restores_balance(self):
        pos_tx = self.pay('18000')
        void_pos_transaction(pos_tx, 'wrong amount')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_nutrition_summary_excludes_qr_charges(self):
        self.pay('18000')
        guardian_user = make_guardian(self.fx)
        client = APIClient()
        client.force_authenticate(user=guardian_user)
        res = client.get(f'/api/v1/students/{self.student.id}/nutrition-summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['total_items'], 0)


class TokenTests(QRFixtureMixin, TestCase):
    def test_expired_token_fails_and_charges_nothing(self):
        token = self.mint()
        with mock.patch('apps.wallet.qr_charge.timezone.now', return_value=timezone.now() + datetime.timedelta(seconds=121)):
            self.assertRefused('QR_TOKEN_EXPIRED', charge_qr_session, token, self.student, Decimal('5000'), 'k')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_token_is_single_use(self):
        token = self.mint()
        charge_qr_session(token, self.student, Decimal('5000'), 'k1')
        self.assertRefused('QR_TOKEN_USED', charge_qr_session, token, self.student, Decimal('5000'), 'k2')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('95000.00'))

    def test_garbage_and_tampered_tokens_rejected(self):
        self.assertRefused('QR_TOKEN_INVALID', resolve_qr_session, 'not-a-token', self.student)
        token = self.mint()
        self.assertRefused('QR_TOKEN_INVALID', resolve_qr_session, token[:-2] + 'xx', self.student)

    def test_cancelled_session_no_longer_resolves(self):
        minted = create_qr_session(self.terminal)
        cancel_qr_session(minted['session'])
        self.assertRefused('QR_TOKEN_EXPIRED', resolve_qr_session, minted['token'], self.student)

    def test_token_from_other_school_is_foreign_and_reveals_nothing(self):
        other_school = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id, name="SMP Lain", npsn="30199999", level=School.LEVEL_SMP,
        )
        other_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101018888", full_name="Siswa Lain",
        )
        other_student = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=other_school, person=other_person,
            nisn="9999999999", nis="SMP-001", status=Student.STATUS_ACTIVE,
        )
        exc = self.assertRefused('MERCHANT_FOREIGN_TENANT', resolve_qr_session, self.mint(), other_student)
        self.assertNotIn(self.merchant.name, exc.message)
        self.assertNotIn('SD Wallet Test', exc.message)

    def test_token_from_other_foundation_is_foreign(self):
        token = self.mint()
        other = build_wallet_fixture(foundation_name="Yayasan Lain")
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertRefused('MERCHANT_FOREIGN_TENANT', resolve_qr_session, token, other['student'])

    def test_only_enabled_merchant_can_mint(self):
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        self.assertRefused('QR_MODE_DISABLED_BY_MERCHANT', create_qr_session, self.terminal)


class EnforcementTests(QRFixtureMixin, TestCase):
    def test_amount_above_cap_refused_and_cap_named(self):
        exc = self.assertRefused('AMOUNT_ABOVE_CAP', self.pay, '50001')
        self.assertIn('50.000', exc.message)
        self.assertEqual(POSTransaction.objects.filter(status=POSTransactionStatus.REJECTED).count(), 1)

    def test_amount_at_cap_allowed(self):
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-2')
        self.assertEqual(self.pay('50000').total, Decimal('50000.00'))

    def test_school_cap_is_configurable(self):
        school = School.all_tenants.get(id=self.fx['school'].id)
        school.qr_self_amount_max = Decimal('10000.00')
        school.save()
        self.assertRefused('AMOUNT_ABOVE_CAP', self.pay, '10001')

    def test_non_positive_amount_refused(self):
        self.assertRefused('AMOUNT_INVALID', self.pay, '0')
        self.assertRefused('AMOUNT_INVALID', self.pay, '-5', key='k2')

    def test_insufficient_balance_refused_never_negative(self):
        self.pay('50000', key='a')
        self.pay('40000', key='b')
        self.assertRefused('INSUFFICIENT_BALANCE', self.pay, '10001', key='c')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('10000.00'))
        self.assertFalse(self.wallet.requires_reconciliation)

    def test_daily_limit_enforced_against_fresh_balance(self):
        set_spend_rule(self.student, daily_limit=Decimal('20000'))
        self.pay('15000', key='a')
        self.assertRefused('DAILY_LIMIT_EXCEEDED', self.pay, '6000', key='b')

    def test_allowed_window_enforced(self):
        now = timezone.localtime().time()
        outside = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(hours=2)).time()
        later = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(hours=3)).time()
        if outside < now or later < outside:  # midnight wrap; pick a fixed window instead
            outside, later = datetime.time(3, 0), datetime.time(3, 1)
            if outside <= now <= later:
                self.skipTest('clock inside fixed window')
        set_spend_rule(self.student, allowed_window_start=outside, allowed_window_end=later)
        self.assertRefused('OUTSIDE_ALLOWED_WINDOW', self.pay, '5000')

    def test_frozen_wallet_refused(self):
        Wallet.objects.filter(id=self.wallet.id).update(status=WalletStatus.FROZEN)
        self.assertRefused('WALLET_FROZEN', self.pay, '5000')

    def test_blocked_categories_refuse_qr_and_explain(self):
        set_spend_rule(self.student, blocked_categories=['SUGARY_DRINKS'])
        self.assertRefused('QR_MODE_REQUIRES_ITEMISED', resolve_qr_session, self.mint(), self.student)
        exc = self.assertRefused('QR_MODE_REQUIRES_ITEMISED', self.pay, '5000')
        self.assertIn('kartu', exc.message)

    def test_blocked_products_refuse_qr(self):
        set_spend_rule(self.student, blocked_products=['NASI-01'])
        self.assertRefused('QR_MODE_REQUIRES_ITEMISED', self.pay, '5000')

    def test_guardian_switch_off_refuses_neutrally(self):
        set_spend_rule(self.student, qr_charge_enabled=False)
        exc = self.assertRefused('QR_MODE_DISABLED_BY_GUARDIAN', self.pay, '5000')
        self.assertNotIn('5', exc.message)  # names no amount or detail

    def test_merchant_disabled_after_mint_refuses(self):
        token = self.mint()
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        self.assertRefused('QR_MODE_DISABLED_BY_MERCHANT', charge_qr_session, token, self.student, Decimal('5000'), 'k')

    def test_rejection_does_not_consume_token_so_student_can_retry_lower(self):
        set_spend_rule(self.student, daily_limit=Decimal('10000'))
        token = self.mint()
        self.assertRefused('DAILY_LIMIT_EXCEEDED', charge_qr_session, token, self.student, Decimal('12000'), 'k1')
        pos_tx = charge_qr_session(token, self.student, Decimal('8000'), 'k2')
        self.assertEqual(pos_tx.status, POSTransactionStatus.COMPLETED)

    def test_rejected_key_can_be_reused_without_replaying_the_rejection(self):
        set_spend_rule(self.student, daily_limit=Decimal('10000'))
        token = self.mint()
        self.assertRefused('DAILY_LIMIT_EXCEEDED', charge_qr_session, token, self.student, Decimal('12000'), 'k1')
        self.assertEqual(charge_qr_session(token, self.student, Decimal('8000'), 'k1').status, POSTransactionStatus.COMPLETED)


class ResolveAndResultTests(QRFixtureMixin, TestCase):
    def test_resolve_returns_stall_balance_and_cap(self):
        data = resolve_qr_session(self.mint(), self.student)
        self.assertEqual(data['merchant_name'], 'Kantin Sehat')
        self.assertEqual(data['balance'], Decimal('100000.00'))
        self.assertEqual(data['max_amount'], Decimal('50000.00'))
        self.assertEqual(data['currency'], 'IDR')

    def test_result_pending_then_paid_then_expired(self):
        minted = create_qr_session(self.terminal)
        self.assertEqual(get_qr_session_result(minted['session'])['status'], 'PENDING')
        pos_tx = charge_qr_session(minted['token'], self.student, Decimal('9000'), 'k')
        minted['session'].refresh_from_db()
        result = get_qr_session_result(minted['session'])
        self.assertEqual(result['status'], 'PAID')
        self.assertEqual(result['transaction'].id, pos_tx.id)

        fresh = create_qr_session(self.terminal)['session']
        with mock.patch('apps.wallet.qr_charge.timezone.now', return_value=timezone.now() + datetime.timedelta(seconds=200)):
            self.assertEqual(get_qr_session_result(fresh)['status'], 'EXPIRED')


class MerchantSwitchTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()

    def test_enabling_requires_acknowledgement(self):
        self.assertRefused(
            'ACKNOWLEDGEMENT_REQUIRED', set_merchant_qr_charge, self.merchant, True, False, self.fx['finance_user'],
        )
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)

    def test_acknowledged_enable_records_who_and_when(self):
        set_merchant_qr_charge(self.merchant, True, True, self.fx['finance_user'])
        self.merchant.refresh_from_db()
        self.assertTrue(self.merchant.qr_self_amount_enabled)
        self.assertEqual(self.merchant.qr_self_amount_ack_by_id, self.fx['finance_user'].id)
        self.assertIsNotNone(self.merchant.qr_self_amount_ack_at)

    def test_disable_needs_no_acknowledgement(self):
        set_merchant_qr_charge(self.merchant, True, True, self.fx['finance_user'])
        set_merchant_qr_charge(self.merchant, False, False, self.fx['finance_user'])
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)


class QRApiTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.guardian_user = make_guardian(self.fx)
        self.operator = self.fx['finance_user']  # holds wallet.topup.write, is not a guardian
        self.admin = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164="+6281100000001", email="admin@school.id", full_name="Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.admin, role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )

    def _mint_via_api(self):
        self.client.force_authenticate(user=self.operator)
        res = self.client.post('/api/v1/pos/qr-sessions/', {'terminal_id': self.terminal.id}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def test_full_flow_over_api(self):
        minted = self._mint_via_api()
        self.assertEqual(set(minted), {'session_id', 'token', 'expires_at'})

        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.post('/api/v1/wallet/qr/resolve/', {'token': minted['token'], 'student_id': self.student.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['balance'], '100000.00')
        self.assertEqual(res.json()['max_amount'], '50000.00')

        res = self.client.post('/api/v1/wallet/qr/charge/', {
            'token': minted['token'], 'student_id': self.student.id, 'amount': '18000.00', 'idempotency_key': 'abc',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['amount'], '18000.00')
        self.assertEqual(body['balance_after'], '82000.00')

        self.client.force_authenticate(user=self.operator)
        res = self.client.get(f"/api/v1/pos/qr-sessions/{minted['session_id']}/result/")
        result = res.json()
        self.assertEqual(result['status'], 'PAID')
        self.assertEqual(result['transaction']['confirmation_code'], body['code'])
        self.assertEqual(result['transaction']['amount'], '18000.00')

    def test_operator_cannot_charge_a_student_by_qr(self):
        minted = self._mint_via_api()
        res = self.client.post('/api/v1/wallet/qr/charge/', {
            'token': minted['token'], 'student_id': self.student.id, 'amount': '5000.00', 'idempotency_key': 'x',
        }, format='json')
        self.assertEqual(res.status_code, 404)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_guardian_cannot_charge_unlinked_student(self):
        minted = self._mint_via_api()
        other = make_guardian(self.fx, nik='3471010101016666')
        Student.all_tenants.filter(id=self.student.id)  # linked only to the first guardian
        GuardianLink.all_tenants.filter(guardian__user=other).delete()
        self.client.force_authenticate(user=other)
        res = self.client.post('/api/v1/wallet/qr/resolve/', {'token': minted['token'], 'student_id': self.student.id}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_refusal_returns_code_and_message(self):
        minted = self._mint_via_api()
        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.post('/api/v1/wallet/qr/charge/', {
            'token': minted['token'], 'student_id': self.student.id, 'amount': '50001.00', 'idempotency_key': 'z',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['error'], 'AMOUNT_ABOVE_CAP')
        self.assertIn('message', res.json())

    def test_terminal_endpoints_require_permission(self):
        self.client.force_authenticate(user=self.guardian_user)  # parent: wallet.topup.write held, but see below
        # A user with no role at all is refused outright.
        nobody = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164="+6281100000002", email="n@school.id", full_name="N",
        )
        self.client.force_authenticate(user=nobody)
        res = self.client.post('/api/v1/pos/qr-sessions/', {'terminal_id': self.terminal.id}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_cross_tenant_terminal_and_session_are_404(self):
        other = build_wallet_fixture(foundation_name="Yayasan Asing")
        other_merchant = Merchant.all_tenants.create(
            foundation_id=other['foundation'].id, school=other['school'], name="Kantin Asing", qr_self_amount_enabled=True,
        )
        other_terminal = POSTerminal.all_tenants.create(
            foundation_id=other['foundation'].id, merchant=other_merchant, device_id="TERM-ASING",
        )
        set_current_foundation_id(self.fx['foundation'].id)
        self.client.force_authenticate(user=self.operator)
        res = self.client.post('/api/v1/pos/qr-sessions/', {'terminal_id': other_terminal.id}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_cancel_via_api_expires_session(self):
        minted = self._mint_via_api()
        res = self.client.delete(f"/api/v1/pos/qr-sessions/{minted['session_id']}/")
        self.assertEqual(res.status_code, 204)
        res = self.client.get(f"/api/v1/pos/qr-sessions/{minted['session_id']}/result/")
        self.assertEqual(res.json()['status'], 'EXPIRED')

    def test_merchant_switch_endpoint_and_serializer_is_read_only(self):
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        self.client.force_authenticate(user=self.admin)
        url = f'/api/v1/merchants/{self.merchant.id}/qr-charge/'
        res = self.client.post(url, {'enabled': True}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['error'], 'ACKNOWLEDGEMENT_REQUIRED')
        res = self.client.post(url, {'enabled': True, 'acknowledged': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['qr_self_amount_enabled'])

        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        res = self.client.patch(f'/api/v1/merchants/{self.merchant.id}/', {'qr_self_amount_enabled': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)  # cannot be flipped without the acknowledgement

    def test_merchant_switch_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.operator)  # finance_officer: no school_config.write
        res = self.client.post(
            f'/api/v1/merchants/{self.merchant.id}/qr-charge/', {'enabled': True, 'acknowledged': True}, format='json',
        )
        self.assertEqual(res.status_code, 403)

    def test_history_marks_self_entered_and_spend_rule_exposes_availability(self):
        self.pay('18000')
        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.get(f'/api/v1/wallets/{self.student.id}/transactions/')
        rows = res.json()['results'] if 'results' in res.json() else res.json()
        purchase = next(r for r in rows if r['type'] == 'PURCHASE')
        topup = next(r for r in rows if r['type'] == 'TOPUP')
        self.assertEqual(purchase['entry_mode'], 'SELF_ENTERED')
        self.assertEqual(topup['entry_mode'], 'OPERATOR')

        res = self.client.get(f'/api/v1/wallets/{self.student.id}/rules/')
        self.assertTrue(res.json()['qr_charge_available'])
        res = self.client.put(f'/api/v1/wallets/{self.student.id}/rules/', {'qr_charge_enabled': False}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['qr_charge_enabled'])
        self.assertFalse(res.json()['qr_charge_available'])

    def test_blocks_make_qr_unavailable_in_rules(self):
        set_spend_rule(self.student, blocked_categories=['SUGARY_DRINKS'])
        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.get(f'/api/v1/wallets/{self.student.id}/rules/')
        self.assertTrue(res.json()['qr_charge_enabled'])
        self.assertFalse(res.json()['qr_charge_available'])
