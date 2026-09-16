"""Tests for guardian OTP login endpoints (spec/08 PAR-001, spec/02 IAM-002/003)."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import OTPChallenge
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from datetime import timedelta
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Student, User
from apps.identity.services import normalize_phone_e164


class OtpRequestEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_request_otp_returns_challenge_id(self):
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': '+6281234567890'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn('challenge_id', response.data)
        self.assertTrue(
            OTPChallenge.objects.filter(id=response.data['challenge_id']).exists()
        )

    def test_request_otp_throttles_after_three_sends(self):
        for _ in range(3):
            self.client.post(
                '/api/v1/auth/otp/request/',
                {'phone_e164': '+6281234567891'},
                format='json',
            )
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': '+6281234567891'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_request_otp_missing_phone_field(self):
        """Test that missing phone_e164 field returns 400 with Indonesian error message."""
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['error'], 'Nomor HP wajib diisi.')

    def test_request_otp_blank_phone_field(self):
        """Test that blank phone_e164 field returns 400 with Indonesian error message."""
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': ''},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['error'], 'Nomor HP wajib diisi.')

    def test_request_otp_malformed_phone_number(self):
        """Test that malformed phone number returns 400 with generic message (no raw input echoed)."""
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': 'not-a-phone'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)
        error_msg = response.data['error']
        # Verify generic message is returned and raw input is NOT echoed
        self.assertIn('Format nomor telepon tidak valid', error_msg)
        self.assertNotIn('not-a-phone', error_msg)

    def test_request_otp_throttle_message_is_not_format_error(self):
        """Test that throttle error returns actual throttle message, not generic format error."""
        # Make 3 successful OTP requests to trigger throttle on the 4th
        for _ in range(3):
            self.client.post(
                '/api/v1/auth/otp/request/',
                {'phone_e164': '+6281234567892'},
                format='json',
            )
        # 4th request should be throttled
        response = self.client.post(
            '/api/v1/auth/otp/request/',
            {'phone_e164': '+6281234567892'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)
        error_msg = response.data['error']
        # Verify we get the real throttle message (contains "Batas pengiriman")
        self.assertIn('Batas pengiriman', error_msg)
        # Verify we DON'T get the generic format error message
        self.assertNotIn('Format nomor telepon tidak valid', error_msg)


class OtpVerifyEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cahaya Ilmu", brand_name="Cahaya Ilmu"
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Cahaya Ilmu", npsn="12345678", level="SD"
        )
        self.phone = normalize_phone_e164("081200000001")
        self.guardian_user = User.all_tenants.create_user(
            phone_e164=self.phone,
            full_name="Ibu Siti",
            foundation_id=self.foundation.id,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siti Aminah")
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.guardian_user
        )
        student_person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Ahmad Kecil")
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person, nis="2026001"
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student,
            financial_responsible=True,
        )
        self.challenge = OTPChallenge.objects.create(
            phone_e164=self.phone,
            code_hash=make_password("111111"),
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_verify_otp_issues_jwt_and_grants_parent_role(self):
        self.assertFalse(
            RoleAssignment.all_tenants.filter(
                user=self.guardian_user, role=RoleAssignment.ROLE_PARENT
            ).exists()
        )

        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': self.challenge.id, 'code': '111111'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['phone_e164'], self.phone)

        self.assertTrue(
            RoleAssignment.all_tenants.filter(
                user=self.guardian_user,
                role=RoleAssignment.ROLE_PARENT,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                scope_id=self.foundation.id,
                deleted_at__isnull=True,
            ).exists()
        )

    def test_verify_otp_wrong_code_rejected(self):
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': self.challenge.id, 'code': '000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_verify_otp_unregistered_phone_returns_404(self):
        unregistered_challenge = OTPChallenge.objects.create(
            phone_e164='+6289999999999',
            code_hash=make_password("222222"),
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.post(
            '/api/v1/auth/otp/verify/',
            {'challenge_id': unregistered_challenge.id, 'code': '222222'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data.get('code'), 'GUARDIAN_NOT_REGISTERED')
