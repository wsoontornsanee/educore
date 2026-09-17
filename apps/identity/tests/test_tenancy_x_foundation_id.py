"""
Validate X-Foundation-ID header against authenticated user's foundation.

TenancyMiddleware uses the authenticated user's foundation_id when available
and ignores the X-Foundation-ID header. For JWT requests (where the middleware
sees AnonymousUser), EduCoreJWTAuthentication validates the header against the
resolved user's foundation and rejects mismatches.
"""
from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import Foundation, RoleAssignment, User
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import tenant_context


class XFoundationIdValidationTests(TestCase):
    """Tests for X-Foundation-ID header enforcement across auth modes."""

    def setUp(self):
        self.client = APIClient()

        # Foundation A (user's own foundation)
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Al-Hikmah Nusantara",
            brand_name="Al-Hikmah",
        )
        with tenant_context(self.foundation_a.id):
            self.user, self.person = create_user_with_person(
                foundation_id=self.foundation_a.id,
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
                foundation_id=self.foundation_a.id,
                user=self.user,
                role=RoleAssignment.ROLE_TEACHER,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                scope_id=self.foundation_a.id,
            )

        # Foundation B (different foundation — used for mismatch tests)
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa",
            brand_name="Bina Bangsa",
        )

        self.protected_url = '/api/v1/me'

    def _obtain_jwt_token(self):
        """Helper: obtain a valid JWT access token for the test user."""
        resp = self.client.post(
            '/api/v1/auth/token/',
            {'identifier': '+6281234567890', 'password': 'AmanPassword123!'},
            format='json',
        )
        return resp.json()['access']

    # --- Session auth (force_authenticate) ---

    def test_session_auth_matching_header_allowed(self):
        """Session-authenticated user with matching X-Foundation-ID is allowed."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            self.protected_url,
            HTTP_X_FOUNDATION_ID=str(self.foundation_a.id),
        )
        self.assertEqual(resp.status_code, 200)

    def test_session_auth_mismatched_header_ignored(self):
        """Session-authenticated user with mismatched X-Foundation-ID is allowed
        because the middleware ignores the header when user is authenticated
        and uses the user's own foundation_id."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            self.protected_url,
            HTTP_X_FOUNDATION_ID=str(self.foundation_b.id),
        )
        # User is authenticated; header is ignored, user's foundation used
        self.assertEqual(resp.status_code, 200)

    def test_session_auth_no_header_allowed(self):
        """Session-authenticated user without X-Foundation-ID works as before."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.protected_url)
        self.assertEqual(resp.status_code, 200)

    # --- JWT auth ---

    def test_jwt_auth_matching_header_allowed(self):
        """JWT-authenticated user with matching X-Foundation-ID is allowed."""
        token = self._obtain_jwt_token()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get(
            self.protected_url,
            HTTP_X_FOUNDATION_ID=str(self.foundation_a.id),
        )
        self.assertEqual(resp.status_code, 200)

    def test_jwt_auth_mismatched_header_rejected(self):
        """JWT-authenticated user with mismatched X-Foundation-ID is rejected
        with 401 because EduCoreJWTAuthentication detects the conflict."""
        token = self._obtain_jwt_token()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get(
            self.protected_url,
            HTTP_X_FOUNDATION_ID=str(self.foundation_b.id),
        )
        self.assertEqual(resp.status_code, 401)
        data = resp.json()
        self.assertIn('does not match', str(data.get('detail', '')))

    def test_jwt_auth_no_header_allowed(self):
        """JWT-authenticated user without X-Foundation-ID works as before."""
        token = self._obtain_jwt_token()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.get(self.protected_url)
        self.assertEqual(resp.status_code, 200)

    # --- Unauthenticated ---

    def test_unauthenticated_with_header_allowed(self):
        """Unauthenticated request with X-Foundation-ID is allowed
        (service-to-service / privileged API access)."""
        resp = self.client.get(
            self.protected_url,
            HTTP_X_FOUNDATION_ID=str(self.foundation_a.id),
        )
        # Unauthenticated but header provides context — will still be 403
        # (no auth) but not a header validation error
        self.assertIn(resp.status_code, [401, 403])

    def test_unauthenticated_no_header_allowed(self):
        """Unauthenticated request without header works as before."""
        resp = self.client.get(self.protected_url)
        self.assertIn(resp.status_code, [401, 403])
