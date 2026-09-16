"""Tests for guardian OTP login endpoints (spec/08 PAR-001, spec/02 IAM-002/003)."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import OTPChallenge


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
