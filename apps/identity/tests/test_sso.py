"""Tests for Third-Party SSO (Google Workspace / Microsoft 365) (spec/14 §6, TASK-036).

Real JWKS network calls are never made — `verify_id_token` is monkeypatched at
the social_auth module boundary, and the JWT/claims handling itself is covered
by dedicated verify-function tests using locally-generated RSA keys.
"""
import calendar
import time
import unittest.mock

import jwt as pyjwt
from django.test import TestCase, override_settings
from django.conf import settings
from rest_framework.test import APIClient

from apps.identity.models import Foundation, RoleAssignment, School, SocialLogin, User
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import set_current_foundation_id

try:
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


def _make_rsa_key():
    return _rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_to_jwk(private_key, kid='test-key'):
    """Minimal RSA JWK from a private key (public part only)."""
    pub = private_key.public_key().public_numbers()
    import base64
    def b64uint(n):
        b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
    return {
        'kid': kid,
        'kty': 'RSA',
        'alg': 'RS256',
        'use': 'sig',
        'n': b64uint(pub.n),
        'e': b64uint(pub.e),
    }


def _now():
    return int(time.time())


def _google_id_token(private_key, kid='test-key', sub='g-12345', email='guru@sekolah.sch.id',
                     client_id=None, expired=False, issuer='https://accounts.google.com'):
    client_id = client_id or settings.SOCIAL_AUTH_GOOGLE_CLIENT_ID or 'test-google-client-id'
    claims = {
        'sub': sub,
        'email': email,
        'email_verified': True,
        'name': 'Guru Google',
        'iss': issuer,
        'aud': client_id,
        'iat': _now() - 10,
        'exp': _now() - 60 if expired else _now() + 600,
    }
    return pyjwt.encode(claims, private_key, algorithm='RS256', headers={'kid': kid})


def _microsoft_id_token(private_key, kid='test-key', oid='ms-67890', email='guru@sekolah.sch.id',
                        client_id=None, tenant_id='common'):
    client_id = client_id or 'test-microsoft-client-id'
    claims = {
        'oid': oid,
        'email': email,
        'name': 'Guru Microsoft',
        'iss': f'https://login.microsoftonline.com/{tenant_id}/v2.0',
        'aud': client_id,
        'iat': _now() - 10,
        'exp': _now() + 600,
    }
    return pyjwt.encode(claims, private_key, algorithm='RS256', headers={'kid': kid})


class SocialAuthVerifyTests(TestCase):
    """Token verification tests with locally-generated keys (no network)."""

    def setUp(self):
        if not _HAS_CRYPTO:
            self.skipTest('cryptography not installed')
        self.private_key = _make_rsa_key()
        self.jwks = {'keys': [_key_to_jwk(self.private_key)]}
        import apps.identity.social_auth as sa
        self.sa = sa
        sa._jwks_cache.clear()
        sa._jwks_cache_timestamps.clear()

    @override_settings(SOCIAL_AUTH_GOOGLE_CLIENT_ID='test-google-client-id')
    def test_verify_google_token_valid(self):
        self.sa._jwks_cache['https://www.googleapis.com/oauth2/v3/certs'] = self.jwks
        self.sa._jwks_cache_timestamps['https://www.googleapis.com/oauth2/v3/certs'] = _now()
        token = _google_id_token(self.private_key)
        claims = self.sa.verify_google_token(token)
        self.assertEqual(claims['sub'], 'g-12345')
        self.assertEqual(claims['email'], 'guru@sekolah.sch.id')

    @override_settings(SOCIAL_AUTH_GOOGLE_CLIENT_ID='test-google-client-id')
    def test_verify_google_token_expired(self):
        self.sa._jwks_cache['https://www.googleapis.com/oauth2/v3/certs'] = self.jwks
        self.sa._jwks_cache_timestamps['https://www.googleapis.com/oauth2/v3/certs'] = _now()
        token = _google_id_token(self.private_key, expired=True)
        with self.assertRaises(self.sa.TokenVerificationError):
            self.sa.verify_google_token(token)

    @override_settings(SOCIAL_AUTH_GOOGLE_CLIENT_ID='wrong-client-id')
    def test_verify_google_token_wrong_audience(self):
        self.sa._jwks_cache['https://www.googleapis.com/oauth2/v3/certs'] = self.jwks
        self.sa._jwks_cache_timestamps['https://www.googleapis.com/oauth2/v3/certs'] = _now()
        token = _google_id_token(self.private_key, client_id='test-google-client-id')
        with self.assertRaises(self.sa.TokenVerificationError):
            self.sa.verify_google_token(token)

    def test_verify_google_token_not_configured(self):
        with override_settings(SOCIAL_AUTH_GOOGLE_CLIENT_ID=''):
            with self.assertRaises(self.sa.TokenVerificationError):
                self.sa.verify_google_token('anything')

    def test_extract_identity_google_uses_sub(self):
        provider_id, email = self.sa.extract_identity('google', {'sub': 'g-1', 'email': 'x@y.z'})
        self.assertEqual(provider_id, 'g-1')

    def test_extract_identity_microsoft_uses_oid(self):
        provider_id, email = self.sa.extract_identity('microsoft', {'oid': 'ms-1', 'email': 'x@y.z'})
        self.assertEqual(provider_id, 'ms-1')


class SocialLoginEndpointTests(TestCase):
    """Endpoint tests for /api/v1/auth/sso/login/ and /auth/sso/link/."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan SSO Test", brand_name="SSO Test",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA SSO", npsn="20409999",
            level=School.LEVEL_SMA,
        )
        set_current_foundation_id(self.foundation.id)
        self.client = APIClient()

        # Staff user with email (SSO target)
        self.staff_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123456001",
            email="guru@sekolah.sch.id",
            full_name="Guru SSO",
        )
        assign_role(
            user=self.staff_user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )
        # Guardian user with email but NO staff role (auto-link must NOT fire)
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123456002",
            email="wali@sekolah.sch.id",
            full_name="Wali SSO",
        )
        assign_role(
            user=self.guardian_user, role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

    def _mock_verify(self, provider='google', provider_user_id='g-12345',
                     email='guru@sekolah.sch.id'):
        """Placeholder retained for readability; tests use unittest.mock.patch directly."""
        return None

    def test_sso_login_existing_link_returns_jwt(self):
        SocialLogin.all_tenants.create(
            foundation_id=self.foundation.id, user=self.staff_user,
            provider='google', provider_user_id='g-12345', email='guru@sekolah.sch.id',
        )
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-12345', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('g-12345', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(res.data['user']['id'], self.staff_user.id)
        self.assertEqual(res.data['user']['email'], 'guru@sekolah.sch.id')

    def test_sso_login_autolinks_staff_user_by_email(self):
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-new', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('g-new', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user']['id'], self.staff_user.id)
        # Link was created
        self.assertTrue(SocialLogin.all_tenants.filter(
            provider_user_id='g-new', user=self.staff_user).exists())

    def test_sso_login_does_not_autolink_guardian_only_users(self):
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-wali', 'email': 'wali@sekolah.sch.id'}
            m_extract.return_value = ('g-wali', 'wali@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.data['code'], 'ACCOUNT_NOT_LINKED')
        self.assertFalse(SocialLogin.all_tenants.filter(provider_user_id='g-wali').exists())

    def test_sso_login_unknown_identity_returns_404(self):
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-unknown', 'email': 'nobody@else.tld'}
            m_extract.return_value = ('g-unknown', 'nobody@else.tld')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 404)

    def test_sso_login_provider_mismatch_isolated_by_foundation(self):
        """A SocialLogin link from another foundation is not consulted."""
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain",
        )
        other_user = User.objects.create(
            foundation_id=other_foundation.id,
            phone_e164="+628123456999",
            email="guru@lain.sch.id",
            full_name="Guru Foundation Lain",
        )
        SocialLogin.all_tenants.create(
            foundation_id=other_foundation.id, user=other_user,
            provider='google', provider_user_id='g-12345', email='guru@lain.sch.id',
        )
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-12345', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('g-12345', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        # Falls through to email auto-link against THIS foundation's staff user
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user']['id'], self.staff_user.id)
        self.assertEqual(res.data['user']['foundation_id'], self.foundation.id)

    def test_sso_login_invalid_provider_rejected(self):
        res = self.client.post('/api/v1/auth/sso/login/', {
            'provider': 'facebook', 'id_token': 'fake',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_sso_link_authenticated_user(self):
        self.client.force_authenticate(user=self.staff_user)
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-link', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('g-link', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/link/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['provider'], 'google')
        self.assertEqual(res.data['provider_user_id'], 'g-link')

    def test_sso_link_conflict_when_already_linked_to_other_user(self):
        SocialLogin.all_tenants.create(
            foundation_id=self.foundation.id, user=self.guardian_user,
            provider='google', provider_user_id='g-12345', email='wali@sekolah.sch.id',
        )
        self.client.force_authenticate(user=self.staff_user)
        with unittest.mock.patch('apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch('apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-12345', 'email': 'x@x'}
            m_extract.return_value = ('g-12345', 'x@x')
            res = self.client.post('/api/v1/auth/sso/link/', {
                'provider': 'google', 'id_token': 'fake-token',
            }, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'ACCOUNT_ALREADY_LINKED')

    def test_sso_unlink_soft_deletes(self):
        link = SocialLogin.all_tenants.create(
            foundation_id=self.foundation.id, user=self.staff_user,
            provider='google', provider_user_id='g-12345', email='guru@sekolah.sch.id',
        )
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.delete('/api/v1/auth/sso/link/?provider=google')
        self.assertEqual(res.status_code, 204)
        link.refresh_from_db()
        self.assertIsNotNone(link.deleted_at)
        self.assertFalse(SocialLogin.all_tenants.filter(user=self.staff_user, provider='google').exists())

    def test_sso_links_list(self):
        SocialLogin.all_tenants.create(
            foundation_id=self.foundation.id, user=self.staff_user,
            provider='microsoft', provider_user_id='ms-1', email='guru@sekolah.sch.id',
        )
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/sso/links/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']), 1)
        self.assertEqual(res.data['results'][0]['provider'], 'microsoft')


