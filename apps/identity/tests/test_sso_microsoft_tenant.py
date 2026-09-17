"""Tests for per-foundation Microsoft Entra tenant pinning (deferred from TASK-036 / PR #115).

Covers: model validation, tenant resolution (pinned vs global fallback), token
verification against a pinned tenant (local RSA keys, no network), login-time
wrong-tenant rejection, and the foundation-admin config API incl. cross-foundation
isolation and backward compatibility.
"""
import time
import unittest.mock

import jwt as pyjwt
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import (
    Foundation,
    MicrosoftTenantConfig,
    RoleAssignment,
    School,
    SocialLogin,
    User,
)
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


def _ms_token(private_key, kid='test-key', oid='ms-1', email='guru@sekolah.sch.id',
              client_id='test-ms-client-id', tenant_id='11111111-1111-1111-1111-111111111111',
              with_tid=True):
    claims = {
        'oid': oid,
        'email': email,
        'name': 'Guru Microsoft',
        'iss': f'https://login.microsoftonline.com/{tenant_id}/v2.0',
        'aud': client_id,
        'iat': _now() - 10,
        'exp': _now() + 600,
    }
    if with_tid:
        claims['tid'] = tenant_id
    return pyjwt.encode(claims, private_key, algorithm='RS256', headers={'kid': kid})


def _jwks_for(private_key):
    return {'keys': [_key_to_jwk(private_key)]}


def _seed_jwks_cache(sa, url, jwks):
    sa._jwks_cache[url] = jwks
    sa._jwks_cache_timestamps[url] = _now()


MS_TENANT_A = '11111111-1111-1111-1111-111111111111'
MS_TENANT_B = '22222222-2222-2222-2222-222222222222'


class ModelValidationTests(TestCase):
    """tenant_id accepts a GUID or registered domain; rejects everything else."""

    def test_accepts_guid(self):
        config = MicrosoftTenantConfig.all_tenants.create(
            foundation_id=1, tenant_id=MS_TENANT_A)
        config.full_clean(exclude=['foundation_id', 'created_by', 'updated_by'])
        self.assertEqual(config.tenant_id, MS_TENANT_A)

    def test_accepts_domain(self):
        config = MicrosoftTenantConfig.all_tenants.create(
            foundation_id=1, tenant_id='sekolah.sch.id')
        config.full_clean(exclude=['foundation_id', 'created_by', 'updated_by'])

    def test_rejects_random_string(self):
        config = MicrosoftTenantConfig(
            foundation_id=1, tenant_id='bukan-tenant!')
        with self.assertRaises(DjangoValidationError):
            config.full_clean(exclude=['foundation_id', 'created_by', 'updated_by'])

    def test_soft_delete_allows_recreate(self):
        first = MicrosoftTenantConfig.all_tenants.create(
            foundation_id=1, tenant_id=MS_TENANT_A)
        first.deleted_at = timezone.now()
        first.save(update_fields=['deleted_at'])
        second = MicrosoftTenantConfig.all_tenants.create(
            foundation_id=1, tenant_id=MS_TENANT_B)
        self.assertIsNotNone(second.id)


class TenantResolutionTests(TestCase):
    """resolve_microsoft_tenant_id: pinned config wins; fallback otherwise."""

    @override_settings(SOCIAL_AUTH_MICROSOFT_TENANT_ID='common')
    def test_foundation_without_config_falls_back_to_setting(self):
        from apps.identity.social_auth import resolve_microsoft_tenant_id
        self.assertEqual(resolve_microsoft_tenant_id(123), 'common')

    @override_settings(SOCIAL_AUTH_MICROSOFT_TENANT_ID='common')
    def test_foundation_with_config_returns_pinned_tenant(self):
        from apps.identity.social_auth import resolve_microsoft_tenant_id
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=123, tenant_id=MS_TENANT_A)
        self.assertEqual(resolve_microsoft_tenant_id(123), MS_TENANT_A)

    @override_settings(SOCIAL_AUTH_MICROSOFT_TENANT_ID='common')
    def test_soft_deleted_config_is_ignored(self):
        from apps.identity.social_auth import resolve_microsoft_tenant_id
        config = MicrosoftTenantConfig.all_tenants.create(
            foundation_id=123, tenant_id=MS_TENANT_A)
        config.delete()  # TenantModel.delete soft-deletes
        self.assertEqual(resolve_microsoft_tenant_id(123), 'common')

    def test_none_foundation_falls_back(self):
        from apps.identity.social_auth import resolve_microsoft_tenant_id
        self.assertEqual(
            resolve_microsoft_tenant_id(None),
            settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID,
        )


@override_settings(SOCIAL_AUTH_MICROSOFT_CLIENT_ID='test-ms-client-id')
class PinnedTokenVerificationTests(TestCase):
    """verify_microsoft_token with an explicit tenant (local RSA, no network)."""

    def setUp(self):
        if not _HAS_CRYPTO:
            self.skipTest('cryptography not installed')
        import apps.identity.social_auth as sa
        self.sa = sa
        sa._jwks_cache.clear()
        sa._jwks_cache_timestamps.clear()
        self.private_key = _make_rsa_key()

    def test_pinned_tenant_accepts_matching_token(self):
        _seed_jwks_cache(
            self.sa,
            f'https://login.microsoftonline.com/{MS_TENANT_A}/discovery/v2.0/keys',
            _jwks_for(self.private_key),
        )
        token = _ms_token(self.private_key, tenant_id=MS_TENANT_A)
        claims = self.sa.verify_microsoft_token(token, tenant_id=MS_TENANT_A)
        self.assertEqual(claims['oid'], 'ms-1')

    def test_pinned_tenant_rejects_other_tenant_token(self):
        # Only tenant A's JWKS is cached; token is issued by tenant B.
        _seed_jwks_cache(
            self.sa,
            f'https://login.microsoftonline.com/{MS_TENANT_A}/discovery/v2.0/keys',
            _jwks_for(self.private_key),
        )
        token = _ms_token(self.private_key, tenant_id=MS_TENANT_B)
        with self.assertRaises(self.sa.TokenVerificationError):
            self.sa.verify_microsoft_token(token, tenant_id=MS_TENANT_A)

    def test_pinned_tenant_rejects_tid_mismatch(self):
        """Token signed by tenant A's keys but claiming tenant B's tid is rejected."""
        _seed_jwks_cache(
            self.sa,
            f'https://login.microsoftonline.com/{MS_TENANT_A}/discovery/v2.0/keys',
            _jwks_for(self.private_key),
        )
        # Same key pair signs both tenants' tokens here (test-only fixture);
        # the tid check is the defense-in-depth layer that still catches this.
        token = _ms_token(self.private_key, tenant_id=MS_TENANT_A, with_tid=True)
        claims = pyjwt.decode(token, options={'verify_signature': False})
        claims['tid'] = MS_TENANT_B
        tampered = pyjwt.encode(claims, self.private_key, algorithm='RS256',
                                headers={'kid': 'test-key'})
        with self.assertRaises(self.sa.TokenVerificationError):
            self.sa.verify_microsoft_token(tampered, tenant_id=MS_TENANT_A)

    def test_no_tenant_uses_global_setting_fallback(self):
        _seed_jwks_cache(
            self.sa,
            'https://login.microsoftonline.com/common/discovery/v2.0/keys',
            _jwks_for(self.private_key),
        )
        token = _ms_token(self.private_key, tenant_id='common')
        with override_settings(SOCIAL_AUTH_MICROSOFT_TENANT_ID='common'):
            claims = self.sa.verify_microsoft_token(token)
        self.assertEqual(claims['oid'], 'ms-1')

    def test_verify_id_token_passes_tenant_only_to_microsoft(self):
        _seed_jwks_cache(
            self.sa,
            f'https://login.microsoftonline.com/{MS_TENANT_A}/discovery/v2.0/keys',
            _jwks_for(self.private_key),
        )
        token = _ms_token(self.private_key, tenant_id=MS_TENANT_A)
        claims = self.sa.verify_id_token('microsoft', token, tenant_id=MS_TENANT_A)
        self.assertEqual(claims['oid'], 'ms-1')


class MicrosoftLoginFlowTests(TestCase):
    """Login/link endpoints resolve the per-foundation tenant BEFORE verification."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan MS Tenant", brand_name="MS Tenant",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA MS", npsn="20419999",
            level=School.LEVEL_SMA,
        )
        set_current_foundation_id(self.foundation.id)
        self.client = APIClient()
        self.staff_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123457001",
            email="guru@sekolah.sch.id",
            full_name="Guru MS",
        )
        assign_role(
            user=self.staff_user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id, tenant_id=MS_TENANT_A,
        )

    def test_login_rejects_token_from_wrong_tenant(self):
        """A token issued by another tenant fails verification at login time."""
        from apps.identity.social_auth import TokenVerificationError
        with unittest.mock.patch(
                'apps.identity.social_auth.verify_id_token') as m_verify:
            m_verify.side_effect = TokenVerificationError(
                "Microsoft ID token was issued for a different tenant than the configured one.")
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'microsoft', 'id_token': 'foreign-tenant-token',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'TOKEN_INVALID')
        m_verify.assert_called_once()

    def test_login_passes_pinned_tenant_to_verification(self):
        """social_login resolves the foundation's pinned tenant before verifying."""
        with unittest.mock.patch(
                'apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch(
                'apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'oid': 'ms-fixed', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('ms-fixed', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'microsoft', 'id_token': 'fake',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 200, res.data)
        m_verify.assert_called_once_with(
            'microsoft', 'fake', tenant_id=MS_TENANT_A)
        self.assertEqual(res.data['user']['id'], self.staff_user.id)

    def test_login_without_config_uses_fallback_tenant(self):
        """Foundations without a pin keep pilot behaviour (global setting)."""
        MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=self.foundation.id).update(deleted_at=timezone.now())
        with unittest.mock.patch(
                'apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch(
                'apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'oid': 'ms-2', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('ms-2', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'microsoft', 'id_token': 'fake',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 200, res.data)
        m_verify.assert_called_once_with(
            'microsoft', 'fake', tenant_id=settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID)

    def test_link_passes_pinned_tenant_to_verification(self):
        self.client.force_authenticate(user=self.staff_user)
        with unittest.mock.patch(
                'apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch(
                'apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'oid': 'ms-3', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('ms-3', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/link/', {
                'provider': 'microsoft', 'id_token': 'fake',
            }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        m_verify.assert_called_once_with(
            'microsoft', 'fake', tenant_id=MS_TENANT_A)

    def test_google_login_is_unaffected_by_ms_config(self):
        with unittest.mock.patch(
                'apps.identity.social_auth.verify_id_token') as m_verify, \
             unittest.mock.patch(
                'apps.identity.social_auth.extract_identity') as m_extract:
            m_verify.return_value = {'sub': 'g-1', 'email': 'guru@sekolah.sch.id'}
            m_extract.return_value = ('g-1', 'guru@sekolah.sch.id')
            res = self.client.post('/api/v1/auth/sso/login/', {
                'provider': 'google', 'id_token': 'fake',
            }, format='json', HTTP_X_FOUNDATION_ID=str(self.foundation.id))
        self.assertEqual(res.status_code, 200, res.data)
        m_verify.assert_called_once_with('google', 'fake', tenant_id=None)


class MicrosoftTenantConfigAPITests(TestCase):
    """GET/PUT/DELETE /api/v1/auth/sso/microsoft-tenant/ — Foundation Admin only."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Config A", brand_name="Config A",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Config B", brand_name="Config B",
        )
        set_current_foundation_id(self.foundation.id)
        self.client = APIClient()
        self.admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123458001",
            email="admin@a.sch.id",
            full_name="Admin A",
        )
        assign_role(
            user=self.admin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )
        self.teacher = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123458002",
            email="guru@a.sch.id",
            full_name="Guru A",
        )
        self.teacher_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA A", npsn="20429999",
            level=School.LEVEL_SMA,
        )
        assign_role(
            user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.teacher_school.id,
            foundation_id=self.foundation.id,
        )

    def test_get_returns_null_when_unset(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/v1/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['tenant_id'])

    def test_put_sets_and_updates_tenant(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_A,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['tenant_id'], MS_TENANT_A)
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id, deleted_at__isnull=True).count(), 1)

        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_B,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id, deleted_at__isnull=True).count(), 1)
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.get(
                foundation_id=self.foundation.id,
                deleted_at__isnull=True).tenant_id, MS_TENANT_B)

    def test_put_accepts_domain_tenant_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': 'sekolah.sch.id',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_put_rejects_invalid_tenant_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': 'bukan tenant!!',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'TENANT_ID_INVALID')

    def test_put_forbidden_for_non_admin(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_A,
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_get_requires_authentication(self):
        res = self.client.get('/api/v1/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 401)

    def test_config_is_foundation_scoped(self):
        """Another foundation's admin sees their own (empty) config, not ours."""
        other_admin = User.objects.create(
            foundation_id=self.other_foundation.id,
            phone_e164="+628123458003",
            email="admin@b.sch.id",
            full_name="Admin B",
        )
        assign_role(
            user=other_admin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.other_foundation.id,
            foundation_id=self.other_foundation.id,
        )
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id, tenant_id=MS_TENANT_A)

        self.client.force_authenticate(user=other_admin)
        set_current_foundation_id(self.other_foundation.id)
        res = self.client.get('/api/v1/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['tenant_id'])

        res = self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_B,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        # Foundation A's pin untouched; exactly one active row per foundation
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id,
                tenant_id=MS_TENANT_A, deleted_at__isnull=True).count(), 1)
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.other_foundation.id,
                tenant_id=MS_TENANT_B, deleted_at__isnull=True).count(), 1)

    def test_delete_removes_pin_and_restores_fallback(self):
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id, tenant_id=MS_TENANT_A)
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete('/api/v1/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=self.foundation.id, deleted_at__isnull=True).exists())

        from apps.identity.social_auth import resolve_microsoft_tenant_id
        self.assertEqual(resolve_microsoft_tenant_id(self.foundation.id),
                         settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID)

    def test_delete_idempotent_when_unset(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete('/api/v1/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 204)

    def test_put_writes_audit_event(self):
        from apps.core.models import AuditEvent
        self.client.force_authenticate(user=self.admin)
        self.client.put('/api/v1/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_A,
        }, format='json')
        self.assertTrue(AuditEvent.objects.filter(
            action='identity.sso.microsoft_tenant.set').exists())
