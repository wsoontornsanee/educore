"""QR Charge web console: settings/oversight page and the operator terminal screen (spec 18)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.wallet.models import Merchant, MerchantType, POSQRSession, POSTerminal, QRDisputeStatus
from apps.wallet.qr_charge import charge_qr_session, create_qr_session
from apps.wallet.qr_oversight import open_qr_dispute
from apps.wallet.services import get_or_create_wallet, topup_wallet
from apps.wallet.tests.test_qr_charge import make_guardian
from educore.middleware.tenancy import set_current_foundation_id

PAGE = '/web/wallet/canteen/qr/'


def make_staff(fx, role, nik, phone):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=f"Staf {role}")
    user = User.objects.create(
        foundation_id=fx['foundation'].id, phone_e164=phone, email=f"{nik}@sch.id", full_name=f"Staf {role}",
    )
    Staff.all_tenants.create(
        foundation_id=fx['foundation'].id, person=person, user=user, school=fx['school'], nip=nik,
        employment_type=Staff.TYPE_PERMANENT, join_date=datetime.date(2020, 1, 1), status=Staff.STATUS_ACTIVE,
    )
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id, user=user, role=role,
        scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=fx['school'].id,
    )
    return user


class QRConsoleBase(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan QR Web")
        self.foundation, self.school, self.student = self.fx['foundation'], self.fx['school'], self.fx['student']
        set_current_foundation_id(self.foundation.id)
        self.operator = self.fx['teacher_user']
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.operator, role='canteen_operator',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.admin = make_staff(self.fx, 'school_admin', '3471010101012001', '+6281200000001')
        self.finance = make_staff(self.fx, 'finance_officer', '3471010101012002', '+6281200000002')
        self.merchant = Merchant.objects.create(
            foundation_id=self.foundation.id, school=self.school, name="Kantin Sehat", type=MerchantType.CANTEEN,
        )
        self.terminal = POSTerminal.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, device_id="TERM-QW-1", name="Kasir 1",
        )
        self.wallet = get_or_create_wallet(self.student)
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-qw')
        self.client = APIClient()

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def enable(self):
        self.merchant.qr_self_amount_enabled = True
        self.merchant.save()


class QRSettingsPageTests(QRConsoleBase):
    def test_admin_enables_with_acknowledgement_and_can_disable(self):
        self.as_user(self.admin)
        url = f'{PAGE}merchants/{self.merchant.id}/switch/'
        self.client.post(url, {'enabled': '1'})
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)  # no acknowledgement -> refused
        self.client.post(url, {'enabled': '1', 'acknowledged': 'on'})
        self.merchant.refresh_from_db()
        self.assertTrue(self.merchant.qr_self_amount_enabled)
        self.assertEqual(self.merchant.qr_self_amount_ack_by_id, self.admin.id)
        self.client.post(url, {'enabled': '0'})
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)

    def test_finance_can_view_but_not_switch(self):
        self.as_user(self.finance)
        res = self.client.get(PAGE)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Kantin Sehat")
        self.assertNotContains(res, "Aktifkan QR Charge")
        res = self.client.post(f'{PAGE}merchants/{self.merchant.id}/switch/', {'enabled': '1', 'acknowledged': 'on'})
        self.assertIn(res.status_code, (302, 403))
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.qr_self_amount_enabled)

    def test_admin_page_shows_switch_and_terminal_link_when_enabled(self):
        self.as_user(self.admin)
        self.assertContains(self.client.get(PAGE), "Aktifkan QR Charge")
        self.enable()
        res = self.client.get(PAGE)
        self.assertContains(res, f'/web/wallet/canteen/qr/terminal/{self.terminal.id}/')

    def test_operator_and_guardian_cannot_open_settings_page(self):
        self.as_user(self.operator)
        self.assertEqual(self.client.get(PAGE).status_code, 302)  # bounced home, no dead end
        guardian = make_guardian(self.fx | {'student': self.student}, nik='3471010101013333')
        self.as_user(guardian)
        self.assertIn(self.client.get(PAGE).status_code, (302, 404))

    def test_other_school_id_is_404(self):
        self.as_user(self.admin)
        self.assertIn(self.client.get(f'{PAGE}?school_id=999999').status_code, (302, 404))  # never rendered

    def test_dispute_resolved_from_page(self):
        self.enable()
        guardian = make_guardian(self.fx | {'student': self.student}, nik='3471010101013334')
        token = create_qr_session(self.terminal)['token']
        pos_tx = charge_qr_session(token, self.student, Decimal('18000'), 'k')
        dispute = open_qr_dispute(pos_tx, guardian, 'Salah')
        self.as_user(self.admin)
        self.assertContains(self.client.get(PAGE), "Salah")
        self.client.post(f'{PAGE}disputes/{dispute.id}/resolve/', {'outcome': 'UPHELD', 'note': 'ok'})
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, QRDisputeStatus.UPHELD)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_bad_refund_amount_flashes_error_and_changes_nothing(self):
        self.enable()
        guardian = make_guardian(self.fx | {'student': self.student}, nik='3471010101013335')
        pos_tx = charge_qr_session(create_qr_session(self.terminal)['token'], self.student, Decimal('18000'), 'k')
        dispute = open_qr_dispute(pos_tx, guardian, 'x')
        self.as_user(self.admin)
        self.client.post(f'{PAGE}disputes/{dispute.id}/resolve/', {'outcome': 'UPHELD', 'refund_amount': 'abc'})
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, QRDisputeStatus.OPEN)

    def test_cross_tenant_dispute_and_merchant_are_404(self):
        other = build_academic_fixture("Yayasan Asing QRW")
        set_current_foundation_id(other['foundation'].id)
        other_admin = make_staff(other, 'school_admin', '3471010101014001', '+6281200000009')
        other_merchant = Merchant.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], name="Kantin Asing",
        )
        set_current_foundation_id(self.foundation.id)
        self.as_user(self.admin)
        self.assertEqual(self.client.post(f'{PAGE}merchants/{other_merchant.id}/switch/', {'enabled': '0'}).status_code, 404)
        self.assertEqual(self.client.post(f'{PAGE}merchants/{other_merchant.id}/flag/clear/').status_code, 404)


class QRTerminalScreenTests(QRConsoleBase):
    def setUp(self):
        super().setUp()
        self.enable()
        self.base = f'{PAGE}terminal/{self.terminal.id}/'
        self.guardian = make_guardian(self.fx | {'student': self.student}, nik='3471010101015001')

    def test_operator_page_renders_screen_and_disabled_state(self):
        self.as_user(self.operator)
        res = self.client.get(self.base)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="qt-start"')
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        res = self.client.get(self.base)
        self.assertNotContains(res, 'id="qt-start"')

    def test_full_flow_mint_poll_paid_void(self):
        self.as_user(self.operator)
        res = self.client.post(self.base + 'session/', {}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertIn('<svg', body['qr_svg'])
        sid = body['session_id']
        self.assertEqual(self.client.get(f'{self.base}session/{sid}/result/').json()['status'], 'PENDING')

        from django.core import signing
        session = POSQRSession.all_tenants.get(id=sid)
        token = signing.dumps({'s': session.id, 'n': session.nonce}, salt='wallet.qr.session', compress=False)
        set_current_foundation_id(self.foundation.id)  # the guardian's request, not the operator's tab
        pos_tx = charge_qr_session(token, self.student, Decimal('18000'), 'kx')

        result = self.client.get(f'{self.base}session/{sid}/result/').json()
        self.assertEqual(result['status'], 'PAID')
        self.assertEqual(result['transaction']['amount'], '18000.00')
        self.assertEqual(result['transaction']['confirmation_code'], pos_tx.confirmation_code)

        res = self.client.post(f'{self.base}session/{sid}/void/')
        self.assertEqual(res.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(self.client.post(f'{self.base}session/{sid}/void/').status_code, 404)  # already voided

    def test_regenerate_cancels_previous_session(self):
        self.as_user(self.operator)
        first = self.client.post(self.base + 'session/', {}, format='json').json()['session_id']
        second = self.client.post(self.base + 'session/', {'previous_session_id': first}, format='json').json()['session_id']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(f'{self.base}session/{first}/result/').json()['status'], 'EXPIRED')
        self.client.post(f'{self.base}session/{second}/cancel/')
        self.assertEqual(self.client.get(f'{self.base}session/{second}/result/').json()['status'], 'EXPIRED')

    def test_disabled_merchant_cannot_mint(self):
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        self.as_user(self.operator)
        res = self.client.post(self.base + 'session/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['error'], 'QR_MODE_DISABLED_BY_MERCHANT')

    def test_guardian_cannot_use_terminal(self):
        self.as_user(self.guardian)
        self.assertIn(self.client.get(self.base).status_code, (302, 403, 404))
        self.assertIn(self.client.post(self.base + 'session/', {}, format='json').status_code, (302, 403, 404))
        self.assertEqual(POSQRSession.all_tenants.count(), 0)

    def test_other_tenants_terminal_and_session_are_404(self):
        other = build_academic_fixture("Yayasan Asing Term")
        other_merchant = Merchant.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], name="K", qr_self_amount_enabled=True,
        )
        other_terminal = POSTerminal.objects.create(
            foundation_id=other['foundation'].id, merchant=other_merchant, device_id="TERM-OTHER",
        )
        set_current_foundation_id(self.foundation.id)
        self.as_user(self.operator)
        self.assertEqual(self.client.get(f'{PAGE}terminal/{other_terminal.id}/').status_code, 404)
        sid = self.client.post(self.base + 'session/', {}, format='json').json()['session_id']
        # a real session id addressed through a different terminal of this tenant is also a 404
        second = POSTerminal.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, device_id="TERM-QW-2",
        )
        self.assertEqual(self.client.get(f'{PAGE}terminal/{second.id}/session/{sid}/result/').status_code, 404)

    def test_canteen_page_links_terminal_to_operator_and_settings_to_finance(self):
        self.as_user(self.operator)
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, f'/web/wallet/canteen/qr/terminal/{self.terminal.id}/')
        self.assertNotContains(res, 'Pengaturan, sanggahan')
        self.as_user(self.finance)
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, 'Pengaturan, sanggahan')
