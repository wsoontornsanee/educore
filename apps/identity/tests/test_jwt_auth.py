"""Tests for JWT token authentication flow supporting dual auth (phone/email) and refresh (spec/01 §2, spec/02 §3)."""
from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import Foundation, RoleAssignment, User
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import tenant_context


class JWTAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Al-Hikmah Nusantara",
            brand_name="Al-Hikmah",
        )
        with tenant_context(self.foundation.id):
            self.user, self.person = create_user_with_person(
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
            RoleAssignment.all_tenants.create(
                foundation_id=self.foundation.id,
                user=self.user,
                role=RoleAssignment.ROLE_TEACHER,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                scope_id=self.foundation.id,
            )

    def test_token_obtain_pair_with_phone_success(self):
        """Verify obtaining JWT tokens using phone number."""
        response = self.client.post(
            '/api/v1/auth/token/',
            {
                'identifier': '+6281234567890',
                'password': 'AmanPassword123!',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertIn('user', data)
        self.assertEqual(data['user']['full_name'], 'Ustadz Ahmad Fauzi')
        self.assertEqual(data['user']['phone_e164'], '+6281234567890')
        self.assertEqual(len(data['user']['roles']), 1)
        self.assertEqual(data['user']['roles'][0]['role'], RoleAssignment.ROLE_TEACHER)

    def test_token_obtain_pair_with_email_success(self):
        """Verify obtaining JWT tokens using email identifier."""
        response = self.client.post(
            '/api/v1/auth/token/',
            {
                'identifier': 'ahmad.fauzi@alhikmah.sch.id',
                'password': 'AmanPassword123!',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertEqual(data['user']['email'], 'ahmad.fauzi@alhikmah.sch.id')

    def test_token_obtain_pair_invalid_password(self):
        """Verify rejection on incorrect password."""
        response = self.client.post(
            '/api/v1/auth/token/',
            {
                'identifier': '+6281234567890',
                'password': 'WrongPassword!',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_token_refresh_flow(self):
        """Verify token refresh yields a valid new access token."""
        login_resp = self.client.post(
            '/api/v1/auth/token/',
            {
                'identifier': '+6281234567890',
                'password': 'AmanPassword123!',
            },
            format='json',
        )
        refresh_token = login_resp.json()['refresh']

        refresh_resp = self.client.post(
            '/api/v1/auth/token/refresh/',
            {'refresh': refresh_token},
            format='json',
        )
        self.assertEqual(refresh_resp.status_code, 200)
        self.assertIn('access', refresh_resp.json())

    def test_access_protected_endpoint_with_bearer_token(self):
        """Verify accessing /api/v1/me using Bearer token authorization header."""
        login_resp = self.client.post(
            '/api/v1/auth/token/',
            {
                'identifier': '+6281234567890',
                'password': 'AmanPassword123!',
            },
            format='json',
        )
        access_token = login_resp.json()['access']

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        me_resp = self.client.get('/api/v1/me')
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.json()['user']['full_name'], 'Ustadz Ahmad Fauzi')
