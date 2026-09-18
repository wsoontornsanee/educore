"""Tests for Web session authentication and Web SSO login views (spec/14 §6, spec/17)."""
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse

from apps.identity.models import Foundation, RoleAssignment, School, Staff, User
from apps.identity.social_auth import AccountNotLinkedError, TokenVerificationError


class WebAuthViewsTests(TestCase):
    """Test suite for WebLoginView, WebSSOLoginView, and WebLogoutView."""

    def setUp(self):
        self.client = Client()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Maju",
            brand_name="Maju School",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Maju Bersama",
            npsn="20409999",
            level=School.LEVEL_SMA,
        )
        self.password = "StrongPassword123!"
        self.user = User.objects.create_user(
            phone_e164="+6281234567890",
            email="guru@yayasan.sch.id",
            full_name="Budi Guru",
            password=self.password,
            foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user,
            role="teacher",
            scope_type="SCHOOL",
            scope_id=self.school.id,
        )

    def test_get_login_page_renders_sso_buttons(self):
        """GET /web/login/ renders the login page containing Google and Microsoft SSO buttons."""
        response = self.client.get('/web/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Masuk ke Portal')
        self.assertContains(response, 'Masuk dengan Google Workspace')
        self.assertContains(response, 'Masuk dengan Microsoft 365')
        self.assertContains(response, 'ATAU MASUK DENGAN SSO')

    def test_get_login_page_redirects_if_already_authenticated(self):
        """GET /web/login/ redirects to the Teacher role's own landing page (agenda) if
        user already has an active session — Teacher now has a dedicated landing URL
        under ROLE_LANDING_URLS rather than falling back to the grades.read check that
        used to send every grades.read holder to the permission-slip console."""
        self.client.login(username='+6281234567890', password=self.password)
        response = self.client.get('/web/login/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/web/home/agenda/')

    def test_post_login_success(self):
        """POST /web/login/ with valid credentials creates session and redirects."""
        response = self.client.post('/web/login/', {
            'identifier': '+6281234567890',
            'password': self.password,
            'next': '/web/academic/permission-slips/',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/web/academic/permission-slips/')

        # User is authenticated in session
        self.assertTrue('_auth_user_id' in self.client.session)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.id)

    def test_post_login_invalid_credentials(self):
        """POST /web/login/ with invalid credentials re-renders with 401 and error message."""
        response = self.client.post('/web/login/', {
            'identifier': '+6281234567890',
            'password': 'WrongPassword!',
        })
        self.assertEqual(response.status_code, 401)
        self.assertContains(response, 'Kredensial tidak valid', status_code=401)

    def test_post_login_inactive_user(self):
        """POST /web/login/ with inactive user returns 403."""
        self.user.is_active = False
        self.user.save()

        response = self.client.post('/web/login/', {
            'identifier': '+6281234567890',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Akun pengguna tidak aktif', status_code=403)

    @patch('apps.identity.web_views.social_login')
    def test_web_sso_login_success(self, mock_social_login):
        """POST /web/auth/sso/login/ logs user into session on valid provider token."""
        mock_social_login.return_value = (self.user, {'email': self.user.email, 'sub': 'google-123'})

        response = self.client.post(
            '/web/auth/sso/login/',
            data={
                'provider': 'google',
                'id_token': 'valid-mock-google-id-token',
                'next': '/web/academic/permission-slips/',
            },
            content_type='application/json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['redirect_url'], '/web/academic/permission-slips/')

        # Session established
        self.assertTrue('_auth_user_id' in self.client.session)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.id)

    @patch('apps.identity.web_views.social_login')
    def test_web_sso_login_account_not_linked(self, mock_social_login):
        """POST /web/auth/sso/login/ returns 404 ACCOUNT_NOT_LINKED when user is not linked."""
        mock_social_login.side_effect = AccountNotLinkedError("No EduCore account is linked.")

        response = self.client.post(
            '/web/auth/sso/login/',
            data={
                'provider': 'microsoft',
                'id_token': 'unlinked-ms-token',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data['code'], 'ACCOUNT_NOT_LINKED')

    @patch('apps.identity.web_views.social_login')
    def test_web_sso_login_invalid_token(self, mock_social_login):
        """POST /web/auth/sso/login/ returns 400 TOKEN_INVALID on expired or bad token."""
        mock_social_login.side_effect = TokenVerificationError("Token expired.")

        response = self.client.post(
            '/web/auth/sso/login/',
            data={
                'provider': 'google',
                'id_token': 'bad-token',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['code'], 'TOKEN_INVALID')

    def test_web_logout(self):
        """GET /web/auth/logout/ clears session and redirects to /web/login/."""
        self.client.login(username='+6281234567890', password=self.password)
        self.assertTrue('_auth_user_id' in self.client.session)

        response = self.client.get('/web/auth/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/web/login/')
        self.assertFalse('_auth_user_id' in self.client.session)
