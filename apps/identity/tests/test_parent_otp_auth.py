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
