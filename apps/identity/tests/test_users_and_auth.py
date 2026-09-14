"""Tests for custom User model, Person PII vault, and authentication (spec/02 §2, §3)."""
from datetime import date, timedelta
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import Foundation, OTPChallenge, Person, User
from apps.identity.services import (
    create_user_with_person,
    normalize_phone_e164,
    request_phone_otp,
    verify_phone_otp,
)
from educore.middleware.tenancy import tenant_context

class UserAndAuthTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Al-Hikmah Nusantara",
            brand_name="Al-Hikmah",
        )

    def test_normalize_phone_e164(self):
        """Test normalization of Indonesian phone formats to E.164."""
        self.assertEqual(normalize_phone_e164('081234567890'), '+6281234567890')
        self.assertEqual(normalize_phone_e164('6281234567890'), '+6281234567890')
        self.assertEqual(normalize_phone_e164('+6281234567890'), '+6281234567890')
        with self.assertRaises(ValidationError):
            normalize_phone_e164('12345')

    def test_create_user_with_person_pii_vault(self):
        """Verify user creation with isolated Person PII vault (spec/02 §2, spec/14 §4)."""
        with tenant_context(self.foundation.id):
            user, person = create_user_with_person(
                foundation_id=self.foundation.id,
                full_name="Ustadz Ahmad Fauzi",
                phone="081234567890",
                email="ahmad.fauzi@alhikmah.sch.id",
                password="AmanPassword123!",
                nik="3171012345670001",
                dob=date(1985, 5, 20),
                gender="L",
                address="Jl. Melati No. 12, Tebet, Jakarta Selatan",
            )

            # Assert User model attributes
            self.assertEqual(user.phone_e164, "+6281234567890")
            self.assertEqual(user.email, "ahmad.fauzi@alhikmah.sch.id")
            self.assertEqual(user.foundation_id, self.foundation.id)
            self.assertTrue(user.check_password("AmanPassword123!"))

            # PII fields (NIK, DOB, address) must NOT be columns on User model
            user_fields = {f.name for f in User._meta.get_fields()}
            self.assertNotIn("nik", user_fields)
            self.assertNotIn("dob", user_fields)

            # Person model holds PII
            self.assertEqual(person.nik, "3171012345670001")
            self.assertEqual(person.dob, date(1985, 5, 20))
            self.assertEqual(person.gender, "L")
            self.assertEqual(person.foundation_id, self.foundation.id)

    def test_dual_auth_backend_login(self):
        """IAM-001: Authenticate with email or phone + password."""
        with tenant_context(self.foundation.id):
            user = User.objects.create_user(
                phone_e164="+6281198765432",
                email="guru@alhikmah.sch.id",
                password="SangatAman123!",
                foundation_id=self.foundation.id,
                full_name="Ibu Siti Rahma",
            )

        # 1. Authenticate via Phone E.164
        auth_by_phone = authenticate(username="+6281198765432", password="SangatAman123!")
        self.assertIsNotNone(auth_by_phone)
        self.assertEqual(auth_by_phone.pk, user.pk)

        # 2. Authenticate via Email
        auth_by_email = authenticate(username="guru@alhikmah.sch.id", password="SangatAman123!")
        self.assertIsNotNone(auth_by_email)
        self.assertEqual(auth_by_email.pk, user.pk)

        # 3. Wrong password fails
        wrong = authenticate(username="guru@alhikmah.sch.id", password="SalahPassword!")
        self.assertIsNone(wrong)

    def test_account_lockout_after_ten_failed_attempts(self):
        """IAM-008: Account lockout after 10 failed attempts in 15 min."""
        with tenant_context(self.foundation.id):
            user = User.objects.create_user(
                phone_e164="+6281311223344",
                password="ValidPassword123!",
                foundation_id=self.foundation.id,
                full_name="Budi Santoso",
            )

        # 9 failed attempts
        for i in range(9):
            res = authenticate(username="+6281311223344", password="WrongPassword!")
            self.assertIsNone(res)

        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 9)
        self.assertFalse(user.is_locked)

        # 10th failed attempt triggers lock
        authenticate(username="+6281311223344", password="WrongPassword!")
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 10)
        self.assertTrue(user.is_locked)

        # Attempting with correct password while locked MUST fail
        locked_attempt = authenticate(username="+6281311223344", password="ValidPassword123!")
        self.assertIsNone(locked_attempt)

    def test_otp_challenge_workflow(self):
        """IAM-002, IAM-003: 6-digit OTP, 5-minute TTL, attempt count, verification."""
        challenge, raw_code = request_phone_otp("081299998888")

        self.assertEqual(challenge.phone_e164, "+6281299998888")
        self.assertEqual(len(raw_code), 6)
        self.assertTrue(raw_code.isdigit())
        self.assertGreater(challenge.expires_at, timezone.now())

        # Wrong code attempt
        success, msg = verify_phone_otp(challenge.id, "000000")
        self.assertFalse(success)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

        # Correct code
        success, msg = verify_phone_otp(challenge.id, raw_code)
        self.assertTrue(success)
        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.verified_at)

        # Reuse of verified code fails
        reused, msg = verify_phone_otp(challenge.id, raw_code)
        self.assertFalse(reused)

    def test_otp_rate_limiting(self):
        """IAM-003: Max 3 sends per phone per 15 minutes."""
        phone = "081277776666"

        # 1st, 2nd, 3rd requests succeed
        request_phone_otp(phone)
        request_phone_otp(phone)
        request_phone_otp(phone)

        # 4th request must raise ValidationError
        with self.assertRaises(ValidationError):
            request_phone_otp(phone)
