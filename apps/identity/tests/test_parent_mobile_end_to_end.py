"""End-to-end authorization tests for the Parent Mobile App flow (spec/08 PAR-001..PAR-007).

Covers the seam the per-endpoint tests could not see: the JWT minted by
POST /auth/otp/verify/ must actually be able to drive every screen of the
parent app (Home, Absensi, Tagihan, Pembayaran) without a 403, while still
being unable to reach another family's or another foundation's data.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.finance.models import Invoice, InvoiceStatus, PaymentIntent
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    OTPChallenge,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.identity.services import normalize_phone_e164
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def _make_family(foundation, school, phone, nis, npsn_suffix="", financial_responsible=True):
    """Create guardian user + Guardian + Student + GuardianLink inside one foundation."""
    guardian_user = User.all_tenants.create_user(
        phone_e164=phone,
        full_name=f"Wali {nis}",
        foundation_id=foundation.id,
    )
    guardian_person = Person.all_tenants.create(
        foundation_id=foundation.id, full_name=f"Wali Person {nis}"
    )
    guardian = Guardian.all_tenants.create(
        foundation_id=foundation.id, person=guardian_person, user=guardian_user
    )
    student_person = Person.all_tenants.create(
        foundation_id=foundation.id, full_name=f"Siswa {nis}"
    )
    student = Student.all_tenants.create(
        foundation_id=foundation.id, school=school, person=student_person, nis=nis
    )
    GuardianLink.all_tenants.create(
        foundation_id=foundation.id,
        guardian=guardian,
        student=student,
        financial_responsible=financial_responsible,
    )
    return guardian_user, guardian, student


class ParentOtpEndToEndAccessTests(TestCase):
    """OTP verify → JWT → Home / Absensi / Tagihan / Pembayaran all succeed."""

    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa", brand_name="Harapan Bangsa"
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Harapan Bangsa",
            npsn="30100001",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )
        self.phone = normalize_phone_e164("081277700001")
        self.guardian_user, self.guardian, self.student = _make_family(
            self.foundation, self.school, self.phone, "2026PAR1"
        )

        self.today = timezone.localdate()
        AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            date=self.today,
            status=AttendanceStatus.HADIR,
        )
        self.invoice = Invoice.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000901",
            period="2026-09",
            due_date=self.today + datetime.timedelta(days=10),
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _login_via_otp(self, phone):
        challenge = OTPChallenge.objects.create(
            phone_e164=phone,
            code_hash=make_password("135790"),
            expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': challenge.id, 'code': '135790'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response

    def test_otp_jwt_can_drive_every_parent_screen(self):
        self._login_via_otp(self.phone)

        # Absensi tab / Home "did my child arrive" card.
        attendance = self.client.get(f'/api/v1/attendance/daily/?student_id={self.student.id}')
        self.assertEqual(attendance.status_code, 200, attendance.data)
        rows = attendance.data.get('results', attendance.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['student'], self.student.id)

        # Tagihan tab.
        invoices = self.client.get(f'/api/v1/finance/invoices/?student_id={self.student.id}')
        self.assertEqual(invoices.status_code, 200, invoices.data)
        invoice_rows = invoices.data.get('results', invoices.data)
        self.assertEqual([row['id'] for row in invoice_rows], [self.invoice.id])

        # Pembayaran (PAR-006): guardian may create an intent for their own invoice.
        intent = self.client.post(
            '/api/v1/finance/payment-intents/',
            {'invoice_ids': [self.invoice.id], 'method': 'VA', 'bank': 'BCA'},
            format='json',
        )
        self.assertEqual(intent.status_code, 201, intent.data)
        self.assertIsNotNone(intent.data['va_number'])

        # ...and read it back while polling for settlement.
        detail = self.client.get(f"/api/v1/finance/payment-intents/{intent.data['id']}/")
        self.assertEqual(detail.status_code, 200, detail.data)

    def test_guardian_cannot_see_or_pay_another_familys_invoices(self):
        other_user, _other_guardian, other_student = _make_family(
            self.foundation, self.school, normalize_phone_e164("081277700002"), "2026PAR2"
        )
        other_invoice = Invoice.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=other_student,
            number=f"INV/{self.school.npsn}/2026/000902",
            period="2026-09",
            due_date=self.today + datetime.timedelta(days=10),
            subtotal=Decimal('400000.00'),
            total=Decimal('400000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        PaymentIntent.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=other_student,
            invoice=other_invoice,
            method='VA',
            va_bank='BCA',
            va_number='8888000012345678',
            amount=Decimal('400000.00'),
            currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=2),
        )

        self._login_via_otp(self.phone)

        listed = self.client.get('/api/v1/finance/payment-intents/')
        self.assertEqual(listed.status_code, 200, listed.data)
        rows = listed.data.get('results', listed.data)
        self.assertEqual(
            [row['va_number'] for row in rows], [],
            "Guardian must not see another family's payment intents",
        )

        blocked = self.client.post(
            '/api/v1/finance/payment-intents/',
            {'invoice_ids': [other_invoice.id], 'method': 'VA', 'bank': 'BCA'},
            format='json',
        )
        self.assertEqual(blocked.status_code, 404, blocked.data)

    def test_guardian_cannot_reach_invoice_write_endpoints(self):
        """finance.payment_intent.create must not leak into finance.invoice.write."""
        self._login_via_otp(self.phone)

        cancel = self.client.post(f'/api/v1/finance/invoices/{self.invoice.id}/cancel/', {}, format='json')
        self.assertEqual(cancel.status_code, 403)

        fee_type = self.client.post(
            '/api/v1/finance/fee-types/',
            {'school': self.school.id, 'code': 'X', 'name': 'X'},
            format='json',
        )
        self.assertEqual(fee_type.status_code, 403)

        cash = self.client.post(
            '/api/v1/finance/payments/cash/',
            {'student_id': self.student.id, 'amount': '750000.00'},
            format='json',
        )
        self.assertEqual(cash.status_code, 403)

    def test_staff_phone_cannot_self_grant_parent_role_via_otp(self):
        """A User with no Guardian profile must not get ROLE_PARENT from the OTP flow."""
        staff_phone = normalize_phone_e164("081277700009")
        staff_user = User.all_tenants.create_user(
            phone_e164=staff_phone, full_name="Guru Bagus", foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=staff_user,
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )
        challenge = OTPChallenge.objects.create(
            phone_e164=staff_phone,
            code_hash=make_password("246800"),
            expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': challenge.id, 'code': '246800'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data.get('code'), 'GUARDIAN_NOT_REGISTERED')
        self.assertFalse(
            RoleAssignment.all_tenants.filter(
                user=staff_user, role=RoleAssignment.ROLE_PARENT
            ).exists()
        )

    def test_otp_role_grant_is_audited(self):
        from apps.core.models import AuditEvent

        self._login_via_otp(self.phone)
        event = AuditEvent.objects.filter(
            action='identity.role.parent_granted',
            actor_id=str(self.guardian_user.id),
        ).first()
        self.assertIsNotNone(event, "Granting ROLE_PARENT must write an audit event")
        self.assertEqual(event.entity_type, 'RoleAssignment')
        self.assertEqual(event.foundation_id, self.foundation.id)


class ParentCrossFoundationFinanceIsolationTests(TestCase):
    """A guardian of foundation A must never see foundation B finance rows (spec/02 §4)."""

    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Alfa", brand_name="Alfa"
        )
        set_current_foundation_id(self.foundation_a.id)
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation_a.id, name="SD Alfa", npsn="30200001",
            level=School.LEVEL_SD, base_currency="IDR",
        )
        self.phone_a = normalize_phone_e164("081288800001")
        self.user_a, _g_a, self.student_a = _make_family(
            self.foundation_a, self.school_a, self.phone_a, "2026ALF1"
        )
        self.today = timezone.localdate()
        self.invoice_a = Invoice.all_tenants.create(
            foundation_id=self.foundation_a.id, school=self.school_a, student=self.student_a,
            number=f"INV/{self.school_a.npsn}/2026/001001", period="2026-09",
            due_date=self.today + datetime.timedelta(days=5),
            subtotal=Decimal('100000.00'), total=Decimal('100000.00'), paid=Decimal('0.00'),
            currency='IDR', status=InvoiceStatus.ISSUED,
        )

        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Beta", brand_name="Beta"
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation_b.id, name="SD Beta", npsn="30300001",
            level=School.LEVEL_SD, base_currency="IDR",
        )
        self.phone_b = normalize_phone_e164("081288800002")
        self.user_b, _g_b, self.student_b = _make_family(
            self.foundation_b, self.school_b, self.phone_b, "2026BET1"
        )
        self.invoice_b = Invoice.all_tenants.create(
            foundation_id=self.foundation_b.id, school=self.school_b, student=self.student_b,
            number=f"INV/{self.school_b.npsn}/2026/001001", period="2026-09",
            due_date=self.today + datetime.timedelta(days=5),
            subtotal=Decimal('900000.00'), total=Decimal('900000.00'), paid=Decimal('0.00'),
            currency='IDR', status=InvoiceStatus.ISSUED,
        )
        self.intent_b = PaymentIntent.all_tenants.create(
            foundation_id=self.foundation_b.id, school=self.school_b, student=self.student_b,
            invoice=self.invoice_b, method='VA', va_bank='BCA', va_number='7777000087654321',
            amount=Decimal('900000.00'), currency='IDR',
            expires_at=timezone.now() + datetime.timedelta(hours=2),
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _auth(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_guardian_a_sees_no_foundation_b_invoices_or_intents(self):
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation_a.id, user=self.user_a,
            role=RoleAssignment.ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation_a.id,
        )
        self._auth(self.user_a)

        invoices = self.client.get('/api/v1/finance/invoices/')
        self.assertEqual(invoices.status_code, 200, invoices.data)
        invoice_ids = {row['id'] for row in invoices.data.get('results', invoices.data)}
        self.assertIn(self.invoice_a.id, invoice_ids)
        self.assertNotIn(self.invoice_b.id, invoice_ids)

        intents = self.client.get('/api/v1/finance/payment-intents/')
        self.assertEqual(intents.status_code, 200, intents.data)
        intent_ids = {row['id'] for row in intents.data.get('results', intents.data)}
        self.assertNotIn(self.intent_b.id, intent_ids)

        # Explicitly asking for foundation B's student must also come back empty.
        scoped = self.client.get(f'/api/v1/finance/invoices/?student_id={self.student_b.id}')
        self.assertEqual(scoped.status_code, 200, scoped.data)
        self.assertEqual(len(scoped.data.get('results', scoped.data)), 0)
