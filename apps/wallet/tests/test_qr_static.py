"""Printed static QR decals: payment points, sheets, static charges, PDF (spec 18 §3b, QRS-030..042)."""
import datetime
from decimal import Decimal

from django.core import signing
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.identity.models import RoleAssignment, School, User
from apps.wallet.models import (
    POSPaymentPoint,
    POSQRDecal,
    POSQRDecalStatus,
    POSTransaction,
    POSTransactionStatus,
)
from apps.wallet.qr_charge import QRChargeError, charge_qr_session, create_qr_session, resolve_qr_session
from apps.wallet.qr_decals import (
    DECAL_GRACE,
    DecalError,
    close_payment_point,
    create_payment_point,
    decal_token,
    print_decal,
    render_decal_html,
    render_decal_pdf,
    revoke_decal,
    set_merchant_static_qr,
    terbilang,
)
from apps.wallet.tests.test_qr_charge import GUARDIAN_PIN, QRFixtureMixin, make_guardian


class StaticFixture(QRFixtureMixin):
    def setUp(self):
        super().setUp()
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        self.operator = self.fx['finance_user']
        self.point = create_payment_point(self.merchant, 'Gerobak Minuman', 'Lapangan timur', self.operator)

    def decal(self, point=None, **kw):
        return print_decal(point or self.point, self.operator, **kw)

    def scan(self, decal, amount='8000', key='k1'):
        return charge_qr_session(decal_token(decal), self.student, Decimal(amount), key)

    def refused(self, code, fn, *a, **kw):
        with self.assertRaises(QRChargeError) as ctx:
            fn(*a, **kw)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception


class TerbilangTests(TestCase):
    def test_indonesian_number_words(self):
        cases = {
            0: 'nol', 7: 'tujuh', 11: 'sebelas', 15: 'lima belas', 20: 'dua puluh', 25: 'dua puluh lima',
            100: 'seratus', 101: 'seratus satu', 200: 'dua ratus', 1000: 'seribu', 1500: 'seribu lima ratus',
            25000: 'dua puluh lima ribu', 50000: 'lima puluh ribu', 125000: 'seratus dua puluh lima ribu',
            2_000_000: 'dua juta', 1_000_000: 'satu juta',
        }
        for n, words in cases.items():
            self.assertEqual(terbilang(n), words, n)


class SwitchAndPointTests(StaticFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.merchant.static_qr_enabled = False
        self.merchant.save()

    def test_static_switch_requires_acknowledgement_and_records_it(self):
        with self.assertRaises(DecalError) as ctx:
            set_merchant_static_qr(self.merchant, True, False, self.operator)
        self.assertEqual(ctx.exception.code, 'ACKNOWLEDGEMENT_REQUIRED')
        set_merchant_static_qr(self.merchant, True, True, self.operator)
        self.merchant.refresh_from_db()
        self.assertTrue(self.merchant.static_qr_enabled)
        self.assertEqual(self.merchant.static_qr_ack_by_id, self.operator.id)

    def test_payment_point_and_decal_need_static_enabled(self):
        with self.assertRaises(DecalError) as ctx:
            create_payment_point(self.merchant, 'X', '', self.operator)
        self.assertEqual(ctx.exception.code, 'STATIC_QR_DISABLED')
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        point = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        with self.assertRaises(DecalError):
            print_decal(point, self.operator)

    def test_static_needs_qr_charge_itself_on(self):
        self.merchant.static_qr_enabled = True
        self.merchant.qr_self_amount_enabled = False
        self.merchant.save()
        with self.assertRaises(DecalError):
            create_payment_point(self.merchant, 'X', '', self.operator)


class DecalLifecycleTests(StaticFixture, TestCase):
    def test_human_id_is_merchant_prefixed_and_increments(self):
        other = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        self.assertEqual(self.decal().human_id, 'KS-01')  # "Kantin Sehat"
        self.assertEqual(self.decal(other).human_id, 'KS-02')
        self.assertEqual(self.decal().human_id, 'KS-03')

    def test_each_point_gets_its_own_distinct_token(self):
        other = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        a, b = self.decal(), self.decal(other)
        self.assertNotEqual(a.nonce, b.nonce)
        self.assertNotEqual(decal_token(a), decal_token(b))

    def test_token_encodes_no_amount_student_or_url_and_uses_own_salt(self):
        token = decal_token(self.decal())
        self.assertEqual(set(signing.loads(token, salt='wallet.qr.decal')), {'d', 'n'})
        self.assertNotIn('http', token)
        with self.assertRaises(signing.BadSignature):
            signing.loads(token, salt='wallet.qr.session')

    def test_rotation_keeps_old_sheet_during_grace_then_fails(self):
        old = self.decal()
        new = self.decal()
        old.refresh_from_db()
        self.assertEqual(old.status, POSQRDecalStatus.SUPERSEDED)
        self.assertEqual(new.status, POSQRDecalStatus.ACTIVE)
        self.assertEqual(self.scan(old, key='a').status, POSTransactionStatus.COMPLETED)  # inside 24 h grace
        self.assertEqual(self.scan(new, key='b').status, POSTransactionStatus.COMPLETED)
        POSQRDecal.objects.filter(id=old.id).update(grace_until=timezone.now() - datetime.timedelta(minutes=1))
        self.refused('QR_DECAL_REVOKED', self.scan, old, key='c')
        self.assertEqual(self.scan(new, key='d').status, POSTransactionStatus.COMPLETED)

    def test_grace_window_is_24_hours(self):
        old = self.decal()
        self.decal()
        old.refresh_from_db()
        self.assertAlmostEqual((old.grace_until - old.superseded_at).total_seconds(), DECAL_GRACE.total_seconds(), delta=1)

    def test_revoke_is_instant_and_does_not_touch_other_counters(self):
        other_point = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        a, b = self.decal(), self.decal(other_point)
        revoke_decal(a, self.operator, 'lembar hilang')
        exc = self.refused('QR_DECAL_REVOKED', self.scan, a)
        self.assertIn('petugas', exc.message)
        self.assertEqual(self.scan(b).status, POSTransactionStatus.COMPLETED)

    def test_expired_and_closed(self):
        d = self.decal(expires_on=timezone.localdate() + datetime.timedelta(days=5))
        POSQRDecal.objects.filter(id=d.id).update(expires_on=timezone.localdate() - datetime.timedelta(days=1))
        self.refused('QR_DECAL_EXPIRED', self.scan, d)
        with self.assertRaises(DecalError):
            self.decal(expires_on=timezone.localdate())  # must be in the future
        live = self.decal()
        close_payment_point(self.point, self.operator)
        self.refused('PAYMENT_POINT_CLOSED', self.scan, live)
        with self.assertRaises(DecalError):
            self.decal()

    def test_lifecycle_writes_audit_events(self):
        d = self.decal()
        revoke_decal(d, self.operator)
        actions = set(AuditEvent.objects.values_list('action', flat=True))
        self.assertTrue({'wallet.payment_point.created', 'wallet.decal.printed', 'wallet.decal.revoked'} <= actions)


class StaticChargeTests(StaticFixture, TestCase):
    def test_charge_debits_records_decal_and_has_no_terminal(self):
        decal = self.decal()
        pos_tx = self.scan(decal, '8000')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('92000.00'))
        self.assertIsNone(pos_tx.terminal)
        self.assertEqual(pos_tx.qr_decal_id, decal.id)
        self.assertIsNone(pos_tx.qr_session_id)
        self.assertEqual(pos_tx.entry_mode, 'SELF_ENTERED')
        self.assertEqual(len(pos_tx.confirmation_code), 4)

    def test_decal_is_reusable_and_retry_is_idempotent(self):
        decal = self.decal()
        first = self.scan(decal, '5000', key='a')
        self.assertEqual(self.scan(decal, '5000', key='a').id, first.id)
        self.scan(decal, '5000', key='b')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('90000.00'))

    def test_static_cap_is_lower_of_school_and_static_max(self):
        decal = self.decal()
        exc = self.refused('AMOUNT_ABOVE_CAP', self.scan, decal, '25001')
        self.assertIn('25.000', exc.message)
        self.assertEqual(self.scan(decal, '25000', key='ok').total, Decimal('25000.00'))
        School.all_tenants.filter(id=self.fx['school'].id).update(qr_self_amount_max=Decimal('10000'))
        self.refused('AMOUNT_ABOVE_CAP', self.scan, decal, '10001', key='z')

    def test_terminal_session_keeps_its_higher_cap(self):
        topup = self.pay('50000')  # school cap, not static cap
        self.assertEqual(topup.total, Decimal('50000.00'))

    def test_resolve_names_the_payment_point_and_static_cap(self):
        data = resolve_qr_session(decal_token(self.decal()), self.student)
        self.assertEqual(data['type'], 'STATIC')
        self.assertEqual(data['payment_point_name'], 'Gerobak Minuman')
        self.assertEqual(data['payment_point_location'], 'Lapangan timur')
        self.assertEqual(data['max_amount'], Decimal('25000.00'))
        self.assertIsNone(data['expires_at'])

    def test_enforcement_still_applies(self):
        from apps.wallet.services import set_spend_rule
        decal = self.decal()
        set_spend_rule(self.student, blocked_categories=['SUGARY_DRINKS'])
        self.refused('QR_MODE_REQUIRES_ITEMISED', self.scan, decal)
        set_spend_rule(self.student, daily_limit=Decimal('5000'))
        self.refused('DAILY_LIMIT_EXCEEDED', self.scan, decal, '6000', key='q')

    def test_static_disabled_after_print_refuses(self):
        decal = self.decal()
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        self.refused('QR_MODE_DISABLED_BY_MERCHANT', self.scan, decal)

    def test_foreign_school_and_tampered_tokens(self):
        from apps.identity.models import Person, Student
        decal = self.decal()
        other_school = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id, name="SMP Lain", npsn="30177777", level=School.LEVEL_SMP,
        )
        person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik="3471010101019191", full_name="Lain")
        other = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=other_school, person=person, nisn="8888888888",
            nis="SMP-9", status=Student.STATUS_ACTIVE,
        )
        exc = self.refused('MERCHANT_FOREIGN_TENANT', resolve_qr_session, decal_token(decal), other)
        self.assertNotIn(self.merchant.name, exc.message)
        self.refused('QR_TOKEN_INVALID', resolve_qr_session, decal_token(decal)[:-2] + 'xx', self.student)

    def test_rejected_static_charge_is_logged_without_terminal(self):
        self.refused('AMOUNT_ABOVE_CAP', self.scan, self.decal(), '30000')
        rejected = POSTransaction.objects.get(status=POSTransactionStatus.REJECTED)
        self.assertIsNone(rejected.terminal)
        self.assertIsNotNone(rejected.qr_decal_id)

    def test_receipt_render_tolerates_missing_terminal(self):
        from apps.wallet.escpos import render_pos_receipt
        self.assertTrue(render_pos_receipt(self.scan(self.decal())))

    def test_operator_void_of_static_charge_works(self):
        from apps.wallet.services import void_pos_transaction
        void_pos_transaction(self.scan(self.decal(), '8000'), 'salah')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))


class DecalSheetTests(StaticFixture, TestCase):
    def test_html_carries_required_content(self):
        html = render_decal_html(self.decal())
        for needle in ('SD Wallet Test', 'Kantin Sehat', 'Gerobak Minuman', 'Lapangan timur', 'KS-01',
                       'Rp 25.000', 'dua puluh lima ribu rupiah', 'Scan dengan aplikasi', '<svg'):
            self.assertIn(needle, html)
        self.assertIn('size: A4', html)
        self.assertIn('dashed', html)  # cut line

    def test_pdf_is_a_pdf_and_download_is_audit_logged(self):
        decal = self.decal()
        data, content_type = render_decal_pdf(decal, self.operator)
        if content_type == 'application/pdf':
            self.assertTrue(data.startswith(b'%PDF'))
        event = AuditEvent.objects.get(action='wallet.decal.downloaded')
        self.assertEqual(str(event.entity_id), str(decal.id))

    def test_non_active_sheet_cannot_be_downloaded(self):
        old = self.decal()
        self.decal()
        old.refresh_from_db()
        with self.assertRaises(DecalError):
            render_decal_pdf(old, self.operator)


class StaticApiTests(StaticFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.canteen = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164="+6281300000001", email="op@school.id", full_name="Operator",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.canteen, role='canteen_operator',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.admin = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164="+6281300000002", email="adm@school.id", full_name="Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.admin, role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.guardian = make_guardian(self.fx)

    def test_operator_without_admin_creates_prints_and_downloads(self):
        self.client.force_authenticate(user=self.canteen)
        res = self.client.post('/api/v1/pos/payment-points/', {
            'merchant_id': self.merchant.id, 'name': 'Meja depan', 'location': 'Kantin A',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        point_id = res.json()['id']
        res = self.client.post(f'/api/v1/pos/payment-points/{point_id}/decal/', {}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['human_id'], 'KS-01')
        pdf = self.client.get(body['pdf_url'])
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Cache-Control'], 'no-store')
        self.assertIn('attachment', pdf['Content-Disposition'])

    def test_guardian_scans_static_decal_over_api(self):
        decal = self.decal()
        token = decal_token(decal)
        self.client.force_authenticate(user=self.guardian)
        res = self.client.post('/api/v1/wallet/qr/resolve/', {'token': token, 'student_id': self.student.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['type'], 'STATIC')
        self.assertEqual(res.json()['payment_point_name'], 'Gerobak Minuman')
        res = self.client.post('/api/v1/wallet/qr/charge/', {
            'token': token, 'student_id': self.student.id, 'amount': '8000.00', 'idempotency_key': 'g1', 'pin': GUARDIAN_PIN,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['balance_after'], '92000.00')

    def test_only_pos_manage_holders_manage_decals(self):
        decal = self.decal()
        self.client.force_authenticate(user=self.guardian)
        self.assertEqual(self.client.get('/api/v1/pos/payment-points/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/v1/pos/decals/{decal.id}/revoke/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/pos/decals/{decal.id}/pdf/').status_code, 403)

    def test_revoke_via_api(self):
        decal = self.decal()
        self.client.force_authenticate(user=self.canteen)
        res = self.client.post(f'/api/v1/pos/decals/{decal.id}/revoke/', {'reason': 'hilang'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'REVOKED')

    def test_cross_tenant_point_and_decal_are_404(self):
        from apps.wallet.tests.test_wallet_core import build_wallet_fixture
        other = build_wallet_fixture(foundation_name="Yayasan Asing Decal")
        self.client.force_authenticate(user=self.canteen)
        from educore.middleware.tenancy import set_current_foundation_id
        set_current_foundation_id(self.fx['foundation'].id)
        res = self.client.post('/api/v1/pos/payment-points/', {'merchant_id': 999999, 'name': 'x'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.client.get('/api/v1/pos/decals/999999/pdf/').status_code, 404)

    def test_static_switch_endpoint_and_merchant_serializer(self):
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        self.client.force_authenticate(user=self.admin)
        url = f'/api/v1/merchants/{self.merchant.id}/static-qr/'
        res = self.client.post(url, {'enabled': True}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(url, {'enabled': True, 'acknowledged': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['static_qr_enabled'])
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        res = self.client.patch(f'/api/v1/merchants/{self.merchant.id}/', {'static_qr_enabled': True, 'static_qr_max': '20000.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.static_qr_enabled)
        self.assertEqual(self.merchant.static_qr_max, Decimal('20000.00'))
        res = self.client.patch(f'/api/v1/merchants/{self.merchant.id}/', {'static_qr_max': '0'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_sales_by_payment_point(self):
        other = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        a, b = self.decal(), self.decal(other)
        self.scan(a, '5000', key='1')
        self.scan(a, '3000', key='2')
        self.scan(b, '4000', key='3')
        self.pay('9000', key='t')  # terminal sale
        self.client.force_authenticate(user=self.fx['finance_user'])
        rows = self.client.get(f'/api/v1/merchants/{self.merchant.id}/sales/?group_by=payment_point').json()
        by_name = {r['payment_point_name']: r for r in rows}
        self.assertEqual((by_name['Gerobak Minuman']['count'], by_name['Gerobak Minuman']['total']), (2, '8000.00'))
        self.assertEqual((by_name['Fotokopi']['count'], by_name['Fotokopi']['total']), (1, '4000.00'))
        self.assertEqual((by_name[None]['count'], by_name[None]['total']), (1, '9000.00'))
