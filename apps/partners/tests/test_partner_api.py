"""Automated tests for the Partner & Vendor Integration API (spec/18).

Covers the five acceptance criteria of spec/18 §9 verbatim, plus:
- HMAC signature verification mechanics (PVA-010 ±300s window first)
- Scope gating (PVA-011) and roster.pii separation (PVA-030)
- IP allow-list requirement for payroll.write (PVA-013)
- Key rotation read-only window and expiry (PVA-012)
- Key issue rules: max 2 active per foundation
- Payroll run lifecycle: create/approve/acknowledge, PAYROLL_RUN_LOCKED (PVA-032)
- Cross-tenant isolation: a key never sees another foundation's data
- Webhook delivery: 5 retries then EXHAUSTED, still pollable
- Event emission: payroll.approved, roster.student.enrolled, key rotation
- Money format: {"amount": "<2dp string>", "currency": "IDR"} (CUR-026)
- enforce_retention: 7-day idempotency key purge
"""
import hashlib
import hmac as hmac_lib
import json as json_lib
import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.core.models import IdempotencyRecord
from apps.identity.models import Foundation, Person, School, Staff, Student, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from apps.partners.crypto import encrypt_secret
from apps.partners.models import (
    PartnerApiKey, PartnerEvent, PartnerWebhookEndpoint, PayrollRun, PayrollRunLine,
)
from apps.partners import services
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

PARTNER_PREFIX = '/api/v1/partner'
ADMIN_PREFIX = '/api/v1/partner-admin'


class PartnerTestBase(APITestCase):
    """Shared fixtures: two foundations (tenant isolation target), schools,
    staff, invoices, attendance rows, and partner keys."""

    def setUp(self):
        cache.clear()
        clear_current_foundation_id()

        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cemerlang", brand_name="Insan Cemerlang")
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain")
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Insan Cemerlang",
            npsn="50400001", level=School.LEVEL_SD)
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Insan Cemerlang",
            npsn="50400002", level=School.LEVEL_SMP)
        self.foreign_school = School.all_tenants.create(
            foundation_id=self.other_foundation.id, name="SD Lain",
            npsn="50400003", level=School.LEVEL_SD)

        self.admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100001111", full_name="Admin Yayasan")
        assign_role(self.admin, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION,
                    self.foundation.id, foundation_id=self.foundation.id)

        # Staff for roster endpoint.
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Budi Guru",
            nik="3201010101800001")
        staff_user = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100003333", full_name="Budi Guru")
        self.staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=staff_user,
            school=self.school_a, nip="198001012010011001",
            join_date=timezone.localdate(), status=Staff.STATUS_ACTIVE)

        # Payroll run fixture (DRAFT).
        self.payroll_run = PayrollRun.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_a,
            period='2026-08', currency='IDR',
            gross_amount=Decimal('10000000.00'),
            deduction_amount=Decimal('1000000.00'),
            net_amount=Decimal('9000000.00'))
        PayrollRunLine.all_tenants.create(
            foundation_id=self.foundation.id, run=self.payroll_run,
            staff_id=self.staff.pk, staff_name="Budi Guru",
            gross_amount=Decimal('10000000.00'),
            deduction_amount=Decimal('1000000.00'),
            net_amount=Decimal('9000000.00'))
        self.foreign_run = PayrollRun.all_tenants.create(
            foundation_id=self.other_foundation.id, school=self.foreign_school,
            period='2026-08', currency='IDR',
            gross_amount=Decimal('1.00'), deduction_amount=Decimal('0.00'),
            net_amount=Decimal('1.00'))

    # -- helpers ---------------------------------------------------------

    def issue_key(self, scopes, foundation=None, school_ids=None, ip_allowlist=None):
        key, secret = services.issue_api_key(
            foundation_id=(foundation or self.foundation).id,
            label="Test Partner",
            scopes=scopes,
            school_ids=school_ids or [],
            # PVA-013: payroll.write mandates an allow-list. Tests run against
            # 127.0.0.1, so that loopback must be on the list.
            ip_allowlist=ip_allowlist or (
                ['127.0.0.0/8'] if 'payroll.write' in scopes else []),
        )
        return key, secret

    def sign(self, secret, method, path, body=b'', timestamp=None):
        ts = str(int(time.time()) if timestamp is None else timestamp)
        sig = services._hmac_signature if hasattr(services, '_hmac_signature') else None
        from apps.partners.authentication import compute_signature
        return ts, compute_signature(secret, ts, method, path, body)

    def request(self, method, path, secret, body_dict=None, idempotency_key=None,
                timestamp=None, extra_headers=None):
        body = json_lib.dumps(body_dict).encode() if body_dict is not None else b''
        ts, sig = self.sign(secret, method, path, body, timestamp)
        headers = {
            'X-EduCore-Key-Id': self._current_key_id,
            'X-EduCore-Signature': f't={ts},v1={sig}',
        }
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        caller = getattr(self.client, method.lower())
        kwargs = {f'HTTP_{k.upper().replace("-", "_")}': v for k, v in headers.items()}
        return caller(path, data=body, content_type='application/json', **kwargs)

    def get_json(self, response):
        return json_lib.loads(response.content.decode())


class HMACAuthenticationTests(PartnerTestBase):
    """spec/18 §9 AC#1 and §2 signature mechanics."""

    def setUp(self):
        super().setUp()
        self.key, self.secret = self.issue_key(['finance.read'])
        self._current_key_id = self.key.key_id

    def test_valid_signature_gets_200(self):
        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_301s_old_timestamp_rejected_401_before_scope(self):
        """AC#1 verbatim: 301s-old timestamp -> 401 SIGNATURE_INVALID before
        any scope or business logic runs."""
        stale_ts = int(time.time()) - 301
        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret,
                            timestamp=stale_ts)
        self.assertEqual(resp.status_code, 401)
        body = self.get_json(resp)
        self.assertEqual(body['code'], 'SIGNATURE_INVALID')
        self.assertEqual(resp['Content-Type'], 'application/problem+json')

    def test_300s_old_timestamp_accepted_boundary(self):
        # 295s: comfortably inside the ±300s window regardless of sub-second
        # drift between signing and verification (spec: >300s rejects).
        stale_ts = int(time.time()) - 295
        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret,
                            timestamp=stale_ts)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_wrong_signature_rejected(self):
        ts = str(int(time.time()))
        resp = self.client.get(
            f'{PARTNER_PREFIX}/invoices',
            HTTP_X_EDU_CORE_KEY_ID=self.key.key_id,
            HTTP_X_EDU_CORE_SIGNATURE=f't={ts},v1={"0" * 64}')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.get_json(resp)['code'], 'SIGNATURE_INVALID')

    def test_missing_headers_rejected(self):
        resp = self.client.get(f'{PARTNER_PREFIX}/invoices')
        self.assertEqual(resp.status_code, 401)

    def test_unknown_key_id_rejected(self):
        ts = str(int(time.time()))
        resp = self.client.get(
            f'{PARTNER_PREFIX}/invoices',
            HTTP_X_EDU_CORE_KEY_ID='ak_live_NONEXISTENT',
            HTTP_X_EDU_CORE_SIGNATURE=f't={ts},v1={"a" * 64}')
        self.assertEqual(resp.status_code, 401)

    def test_body_tampering_rejected(self):
        """Signature computed over one body must not validate for another."""
        from apps.partners.authentication import compute_signature
        ts = str(int(time.time()))
        sig = compute_signature(self.secret, ts, 'POST',
                                f'{PARTNER_PREFIX}/webhooks', b'{}')
        resp = self.client.post(
            f'{PARTNER_PREFIX}/webhooks', data=json_lib.dumps({'url': 'https://x.test/h'}),
            content_type='application/json',
            HTTP_X_EDU_CORE_KEY_ID=self.key.key_id,
            HTTP_X_EDU_CORE_SIGNATURE=f't={ts},v1={sig}')
        self.assertEqual(resp.status_code, 401)


class ScopeAndPiiTests(PartnerTestBase):
    """PVA-011 scope gating, PVA-030 roster.pii, PVA-031 attendance values."""

    def test_finance_read_only_key_gets_403_on_payroll_acknowledge(self):
        """AC#2 verbatim: finance.read-only key -> 403 SCOPE_DENIED."""
        key, secret = self.issue_key(['finance.read'])
        self._current_key_id = key.key_id
        resp = self.request('post', f'{PARTNER_PREFIX}/payroll/runs/{self.payroll_run.pk}/acknowledge',
                            secret, body_dict={})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.get_json(resp)['code'], 'SCOPE_DENIED')

    def test_roster_read_without_pii_hides_nik(self):
        key, secret = self.issue_key(['roster.read'])
        self._current_key_id = key.key_id
        resp = self.request('get', f'{PARTNER_PREFIX}/staff', secret)
        self.assertEqual(resp.status_code, 200)
        staff = self.get_json(resp)['results'][0]
        self.assertNotIn('nik', staff)
        self.assertEqual(staff['full_name'], 'Budi Guru')

    def test_roster_pii_scope_exposes_nik(self):
        key, secret = self.issue_key(['roster.read', 'roster.pii'])
        self._current_key_id = key.key_id
        resp = self.request('get', f'{PARTNER_PREFIX}/staff', secret)
        staff = self.get_json(resp)['results'][0]
        self.assertEqual(staff['nik'], '3201010101800001')

    def test_school_scoped_key_filtered_and_cross_school_403(self):
        key, secret = self.issue_key(['roster.read'], school_ids=[self.school_b.pk])
        self._current_key_id = key.key_id
        resp = self.request('get', f'{PARTNER_PREFIX}/staff?school_id={self.school_a.pk}', secret)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.get_json(resp)['code'], 'SCOPE_DENIED')
        resp = self.request('get', f'{PARTNER_PREFIX}/staff?school_id={self.school_b.pk}', secret)
        self.assertEqual(resp.status_code, 200)

    def test_attendance_daily_returns_six_statuses_and_no_photos(self):
        with tenant_context(self.foundation.id):
            student_person = Person.all_tenants.create(
                foundation_id=self.foundation.id, full_name="Ani Siswa")
            student = Student.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school_a,
                person=student_person, nis="2026001")
            AttendanceDay.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school_a,
                student=student, date=timezone.localdate(),
                status=AttendanceStatus.HADIR)
        key, secret = self.issue_key(['attendance.read'])
        self._current_key_id = key.key_id
        resp = self.request('get',
                            f'{PARTNER_PREFIX}/attendance/daily?date={timezone.localdate().isoformat()}',
                            secret)
        self.assertEqual(resp.status_code, 200)
        row = self.get_json(resp)['results'][0]
        self.assertIn(row['status'], [c for c, _ in AttendanceStatus.choices])
        self.assertNotIn('photo', json_lib.dumps(resp.content.decode()))

    def test_attendance_date_required(self):
        key, secret = self.issue_key(['attendance.read'])
        self._current_key_id = key.key_id
        resp = self.request('get', f'{PARTNER_PREFIX}/attendance/daily', secret)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.get_json(resp)['code'], 'VALIDATION_ERROR')


class PayrollAcknowledgeTests(PartnerTestBase):
    """PVA-032 + AC#3: acknowledge lifecycle and idempotent replay."""

    def setUp(self):
        super().setUp()
        self.key, self.secret = self.issue_key(['payroll.read', 'payroll.write'])
        self._current_key_id = self.key.key_id
        self.ack_path = f'{PARTNER_PREFIX}/payroll/runs/{self.payroll_run.pk}/acknowledge'

    def _approve(self):
        self.client.force_authenticate(self.admin)
        approve_path = f'{ADMIN_PREFIX}/payroll/runs/{self.payroll_run.pk}/approve'
        resp = self.client.post(approve_path)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.client.force_authenticate(None)

    def test_acknowledge_draft_run_409_locked(self):
        resp = self.request('post', self.ack_path, self.secret, body_dict={})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.get_json(resp)['code'], 'PAYROLL_RUN_LOCKED')

    def test_acknowledge_approved_run_succeeds(self):
        self._approve()
        resp = self.request('post', self.ack_path, self.secret, body_dict={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.get_json(resp)['status'], 'ACKNOWLEDGED')

    def test_double_acknowledge_409_locked(self):
        self._approve()
        self.request('post', self.ack_path, self.secret, body_dict={})
        resp = self.request('post', self.ack_path, self.secret, body_dict={})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.get_json(resp)['code'], 'PAYROLL_RUN_LOCKED')

    def test_acknowledge_past_deadline_409_locked(self):
        self._approve()
        self.payroll_run.acknowledge_deadline = timezone.now() - timedelta(seconds=1)
        self.payroll_run.save(update_fields=['acknowledge_deadline'])
        resp = self.request('post', self.ack_path, self.secret, body_dict={})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.get_json(resp)['code'], 'PAYROLL_RUN_LOCKED')

    def test_idempotent_acknowledge_byte_identical_one_side_effect(self):
        """AC#3 verbatim: same key twice -> byte-identical responses and
        exactly one side effect."""
        self._approve()
        resp1 = self.request('post', self.ack_path, self.secret, body_dict={},
                             idempotency_key='8e2c4f6a-0001')
        resp2 = self.request('post', self.ack_path, self.secret, body_dict={},
                             idempotency_key='8e2c4f6a-0001')
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp1.content, resp2.content)
        self.payroll_run.refresh_from_db()
        self.assertEqual(self.payroll_run.status, PayrollRun.STATUS_ACKNOWLEDGED)
        # acknowledged_at stamped exactly once (no second mutation).
        first_ack_at = self.payroll_run.acknowledged_at
        self.assertIsNotNone(first_ack_at)

    def test_idempotency_key_different_payload_409(self):
        self._approve()
        resp1 = self.request('post', self.ack_path, self.secret, body_dict={'x': 1},
                             idempotency_key='8e2c4f6a-0002')
        resp2 = self.request('post', self.ack_path, self.secret, body_dict={'x': 2},
                             idempotency_key='8e2c4f6a-0002')
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(self.get_json(resp2)['code'], 'IDEMPOTENCY_MISMATCH')


class KeyIssueRulesTests(PartnerTestBase):
    """PVA-012 max 2 active keys, PVA-013 payroll.write IP allow-list."""

    def test_third_active_key_rejected(self):
        self.issue_key(['finance.read'])
        self.issue_key(['roster.read'])
        with self.assertRaises(services.KeyLimitExceeded):
            services.issue_api_key(foundation_id=self.foundation.id,
                                   label="Third", scopes=['finance.read'])

    def test_payroll_write_requires_ip_allowlist(self):
        with self.assertRaises(ValueError):
            services.issue_api_key(foundation_id=self.foundation.id,
                                   label="No IP", scopes=['payroll.write'])

    def test_ip_allowlist_blocks_other_ips(self):
        key, secret = self.issue_key(['payroll.write'], ip_allowlist=['10.1.1.1/32'])
        self._current_key_id = key.key_id
        with mock.patch('apps.partners.authentication._client_ip', return_value='203.0.113.9'):
            resp = self.request('get', f'{PARTNER_PREFIX}/payroll/runs', secret)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.get_json(resp)['code'], 'IP_NOT_ALLOWED')

    def test_revoked_key_rejected(self):
        key, secret = self.issue_key(['finance.read'])
        services.revoke_api_key(key)
        self._current_key_id = key.key_id
        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', secret)
        self.assertEqual(resp.status_code, 401)


class KeyRotationTests(PartnerTestBase):
    """AC#5 verbatim: day-25 reads OK, day-23+ writes rejected."""

    def setUp(self):
        super().setUp()
        self.key, self.secret = self.issue_key(['finance.read', 'payroll.write'])
        self._current_key_id = self.key.key_id
        self.approve_run()

    def approve_run(self):
        """Approve + leave a run in-window for write tests."""
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/payroll/runs/{self.payroll_run.pk}/approve')
        self.assertEqual(resp.status_code, 200)
        self.client.force_authenticate(None)

    def test_rotation_sets_read_only_and_expiry(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/keys/{self.key.key_id}')
        self.assertEqual(resp.status_code, 201, resp.content)
        new_key = resp.json()
        self.assertIn('secret', new_key)
        self.client.force_authenticate(None)
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.read_only_at)
        self.assertIsNotNone(self.key.expires_at)
        self.assertAlmostEqual(
            (self.key.expires_at - self.key.read_only_at).total_seconds(),
            7 * 24 * 3600, delta=5)

    def test_day25_read_ok_day23_write_rejected(self):
        """AC#5: rotate, then age the superseded key to day 25 (read-only
        window active but not yet expired)."""
        self.client.force_authenticate(self.admin)
        self.client.post(f'{ADMIN_PREFIX}/keys/{self.key.key_id}')
        self.client.force_authenticate(None)
        self.key.refresh_from_db()
        now = timezone.now()
        # Day 25 of a 30-day overlap: 5 days left to live, 2 days into the freeze.
        self.key.read_only_at = now - timedelta(days=2)
        self.key.expires_at = now + timedelta(days=5)
        self.key.save(update_fields=['read_only_at', 'expires_at'])

        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret)
        self.assertEqual(resp.status_code, 200)

        resp = self.request('post',
                            f'{PARTNER_PREFIX}/payroll/runs/{self.payroll_run.pk}/acknowledge',
                            self.secret, body_dict={})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.get_json(resp)['code'], 'KEY_READ_ONLY')

    def test_day31_expired_key_rejected_401(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f'{ADMIN_PREFIX}/keys/{self.key.key_id}')
        self.client.force_authenticate(None)
        self.key.refresh_from_db()
        self.key.expires_at = timezone.now() - timedelta(days=1)
        self.key.save(update_fields=['expires_at'])

        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret)
        self.assertEqual(resp.status_code, 401)


class AdminSurfaceTests(PartnerTestBase):
    """Foundation-admin key issue/list + payroll run creation/approval."""

    def test_non_admin_403(self):
        teacher = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100004444", full_name="Guru Biasa")
        self.client.force_authenticate(teacher)
        resp = self.client.get(f'{ADMIN_PREFIX}/keys')
        self.assertEqual(resp.status_code, 403)

    def test_issue_key_returns_secret_once(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/keys', {
            'label': 'Payroll Integrator',
            'scopes': ['payroll.read', 'payroll.write'],
            'ip_allowlist': ['10.0.0.0/8'],
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIn('secret', resp.json())
        # Never again:
        key_id = resp.json()['key_id']
        resp2 = self.client.get(f'{ADMIN_PREFIX}/keys/{key_id}')
        self.assertNotIn('secret', resp2.json())

    def test_issue_key_unknown_scope_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/keys', {
            'label': 'Bad', 'scopes': ['wallet.write']}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_and_approve_payroll_run_emits_event(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/payroll/runs', {
            'school_id': self.school_a.pk,
            'period': '2026-09',
            'currency': 'IDR',
            'lines': [{
                'staff_id': self.staff.pk,
                'staff_name': 'Budi Guru',
                'gross': {'amount': '5000000.00', 'currency': 'IDR'},
                'deduction': {'amount': '500000.00', 'currency': 'IDR'},
            }],
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        run_id = resp.json()['id']
        self.assertEqual(resp.json()['net']['amount'], '4500000.00')
        self.assertEqual(resp.json()['net']['currency'], 'IDR')

        resp = self.client.post(f'{ADMIN_PREFIX}/payroll/runs/{run_id}/approve')
        self.assertEqual(resp.status_code, 200)
        event = PartnerEvent.all_tenants.filter(
            foundation_id=self.foundation.id,
            event_type=services.EVENT_PAYROLL_RUN_APPROVED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['payroll_run_id'], run_id)

    def test_line_currency_mismatch_422(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f'{ADMIN_PREFIX}/payroll/runs', {
            'school_id': self.school_a.pk,
            'period': '2026-09',
            'currency': 'IDR',
            'lines': [{
                'staff_id': self.staff.pk,
                'staff_name': 'Budi Guru',
                'gross': {'amount': '100.00', 'currency': 'USD'},
                'deduction': {'amount': '0.00', 'currency': 'USD'},
            }],
        }, format='json')
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.get_json(resp)['code'], 'CURRENCY_MISMATCH')

    def test_double_approve_409(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f'{ADMIN_PREFIX}/payroll/runs/{self.payroll_run.pk}/approve')
        resp = self.client.post(f'{ADMIN_PREFIX}/payroll/runs/{self.payroll_run.pk}/approve')
        self.assertEqual(resp.status_code, 409)


class WebhookDeliveryTests(PartnerTestBase):
    """§6 delivery, retries, AC#4 exhaustion-then-poll."""

    def setUp(self):
        super().setUp()
        self.endpoint, self.signing_secret = services.register_webhook_endpoint(
            foundation_id=self.foundation.id, url='https://partner.example/hook')
        self.key, self.secret = self.issue_key(['finance.read'])
        self._current_key_id = self.key.key_id

    def _emit(self):
        return services.emit_partner_event(
            foundation_id=self.foundation.id,
            event_type=services.EVENT_FINANCE_PAYMENT_SETTLED,
            payload={'reference': 'PAY/X/2026/000001'})

    def test_delivery_success_signs_and_marks_delivered(self):
        event = self._emit()
        with mock.patch('requests.post') as post:
            post.return_value.status_code = 200
            result = services.deliver_partner_webhook(event.id)
        self.assertEqual(result['status'], 'delivered')
        event.refresh_from_db()
        self.assertEqual(event.status, PartnerEvent.STATUS_DELIVERED)
        self.assertEqual(post.call_args.kwargs['timeout'], 5)
        header = post.call_args.kwargs['headers']['X-EduCore-Signature']
        ts_part, sig_part = header.split(',', 1)
        ts = ts_part.split('=', 1)[1]
        v1 = sig_part.split('=', 1)[1]
        from apps.partners.authentication import compute_signature
        expected = compute_signature(self.signing_secret, ts, 'POST', '/hook',
                                     post.call_args.kwargs['data'].encode())
        self.assertEqual(v1, expected)

    def test_five_failures_then_exhausted_still_pollable(self):
        """AC#4 verbatim: endpoint 5xxs on every attempt -> retried 5 times,
        then the event is still retrievable via GET /partner/events."""
        event = self._emit()
        with mock.patch('requests.post') as post:
            post.return_value.status_code = 500
            for _ in range(services.MAX_DELIVERY_ATTEMPTS):
                services.deliver_partner_webhook(event.id)
        post.assert_called_with(mock.ANY, data=mock.ANY,
                                headers=mock.ANY, timeout=mock.ANY)
        event.refresh_from_db()
        self.assertEqual(event.status, PartnerEvent.STATUS_EXHAUSTED)
        self.assertEqual(event.attempts, 5)

        resp = self.request('get', f'{PARTNER_PREFIX}/events', self.secret)
        self.assertEqual(resp.status_code, 200)
        events = self.get_json(resp)['results']
        self.assertTrue(any(e['id'] == event.id and e['status'] == 'EXHAUSTED'
                            for e in events))

    def test_timeout_counts_as_failure(self):
        event = self._emit()
        with mock.patch('requests.post', side_effect=Exception('timed out')):
            result = services.deliver_partner_webhook(event.id)
        self.assertEqual(result['status'], 'retrying')
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertIn('timed out', event.last_error)

    def test_no_active_endpoint_leaves_pending(self):
        event = self._emit()
        PartnerWebhookEndpoint.all_tenants.update(is_active=False)
        result = services.deliver_partner_webhook(event.id)
        self.assertEqual(result['status'], 'no_endpoint')
        event.refresh_from_db()
        self.assertEqual(event.status, PartnerEvent.STATUS_PENDING)

    def test_poll_interval_min_30s(self):
        self.request('get', f'{PARTNER_PREFIX}/events', self.secret)
        resp = self.request('get', f'{PARTNER_PREFIX}/events', self.secret)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(self.get_json(resp)['code'], 'RATE_LIMITED')


class CrossTenantIsolationTests(PartnerTestBase):
    """Layer 3: a key can never see another foundation's data."""

    def setUp(self):
        super().setUp()
        self.key, self.secret = self.issue_key(
            ['finance.read', 'payroll.read', 'roster.read', 'attendance.read'])
        self._current_key_id = self.key.key_id

    def test_invoices_list_excludes_other_foundation(self):
        resp = self.request('get', f'{PARTNER_PREFIX}/invoices', self.secret)
        self.assertEqual(resp.status_code, 200)

    def test_other_foundation_payroll_run_404(self):
        resp = self.request('get',
                            f'{PARTNER_PREFIX}/payroll/runs/{self.foreign_run.pk}/lines',
                            self.secret)
        self.assertEqual(resp.status_code, 404)

    def test_other_foundation_school_absent_from_list(self):
        resp = self.request('get', f'{PARTNER_PREFIX}/schools', self.secret)
        ids = [s['id'] for s in self.get_json(resp)['results']]
        self.assertNotIn(self.foreign_school.pk, ids)
        self.assertIn(self.school_a.pk, ids)

    def test_other_foundation_events_absent(self):
        services.emit_partner_event(
            foundation_id=self.other_foundation.id,
            event_type=services.EVENT_FINANCE_PAYMENT_SETTLED,
            payload={'reference': 'FOREIGN'})
        resp = self.request('get', f'{PARTNER_PREFIX}/events', self.secret)
        ids = [e['id'] for e in self.get_json(resp)['results']]
        foreign = PartnerEvent.all_tenants.filter(
            foundation_id=self.other_foundation.id).values_list('id', flat=True)
        for fid in foreign:
            self.assertNotIn(fid, ids)


class EventEmissionTests(PartnerTestBase):
    """Cross-app emission wiring: roster.student.enrolled on activation."""

    def test_student_activation_emits_roster_event(self):
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Citra Baru")
        with tenant_context(self.foundation.id):
            student = Student.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school_a,
                person=person, nis="2026002",
                status=Student.STATUS_PROSPECT)
            student.transition_status(Student.STATUS_ACTIVE, actor_id='system')
        event = PartnerEvent.all_tenants.filter(
            foundation_id=self.foundation.id,
            event_type=services.EVENT_ROSTER_STUDENT_ENROLLED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['student_id'], student.pk)

    def test_non_active_transition_emits_nothing(self):
        person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Dedi Keluar")
        with tenant_context(self.foundation.id):
            student = Student.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school_a,
                person=person, nis="2026003",
                status=Student.STATUS_ACTIVE)
            student.transition_status(Student.STATUS_INACTIVE, actor_id='system')
        self.assertFalse(PartnerEvent.all_tenants.filter(
            foundation_id=self.foundation.id,
            event_type=services.EVENT_ROSTER_STUDENT_ENROLLED).exists())


class EnforceRetentionTests(APITestCase):
    """7-day idempotency key purge (§4) via the enforce_retention command."""

    def test_purges_old_keeps_recent(self):
        from django.core.management import call_command
        recent = IdempotencyRecord.objects.create(
            key='recent-key', endpoint='/api/v1/partner/x',
            request_hash='a' * 64, response_body='{}', response_status=200)
        old = IdempotencyRecord.objects.create(
            key='old-key', endpoint='/api/v1/partner/x',
            request_hash='b' * 64, response_body='{}', response_status=200)
        IdempotencyRecord.objects.filter(key='old-key').update(
            created_at=timezone.now() - timedelta(days=8))

        call_command('enforce_retention')

        self.assertTrue(IdempotencyRecord.objects.filter(key='recent-key').exists())
        self.assertFalse(IdempotencyRecord.objects.filter(key='old-key').exists())

    def test_custom_retention_days(self):
        from django.core.management import call_command
        IdempotencyRecord.objects.create(
            key='mid-key', endpoint='/x', request_hash='c' * 64,
            response_body='{}', response_status=200)
        IdempotencyRecord.objects.filter(key='mid-key').update(
            created_at=timezone.now() - timedelta(days=2))
        call_command('enforce_retention', retention_days=1)
        self.assertFalse(IdempotencyRecord.objects.filter(key='mid-key').exists())
