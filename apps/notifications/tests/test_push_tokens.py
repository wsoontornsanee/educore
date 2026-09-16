from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Foundation, School, User
from apps.notifications.models import DevicePushPlatform, DevicePushToken
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class DevicePushTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Darul Ilmi Nusantara",
            brand_name="Darul Ilmi",
            npwp="01.234.567.8-888.000",
            address="Surabaya",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Darul Ilmi",
            npsn="20400001",
            level=School.LEVEL_SMA,
        )
        self.user_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628122334455",
            email="guru.a@darul.sch.id",
            full_name="Guru A",
        )
        self.user_b = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628199887766",
            email="guru.b@darul.sch.id",
            full_name="Guru B",
        )

    def test_register_push_token_success(self):
        self.client.force_authenticate(user=self.user_a)
        res = self.client.post(
            '/api/v1/me/push-tokens/',
            {'token': 'fcm_token_abc_123', 'platform': 'ANDROID'},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['token'], 'fcm_token_abc_123')
        self.assertEqual(data['platform'], DevicePushPlatform.ANDROID)
        self.assertTrue(data['is_active'])

        token_obj = DevicePushToken.all_tenants.get(id=data['id'])
        self.assertEqual(token_obj.user_id, self.user_a.id)
        self.assertEqual(token_obj.foundation_id, self.foundation.id)

    def test_register_push_token_upsert_idempotent(self):
        self.client.force_authenticate(user=self.user_a)
        # Register first time
        res1 = self.client.post(
            '/api/v1/me/push-tokens/',
            {'token': 'fcm_token_same_device', 'platform': 'ANDROID'},
            format='json',
        )
        self.assertEqual(res1.status_code, 201)

        # Register same token with updated platform
        res2 = self.client.post(
            '/api/v1/me/push-tokens/',
            {'token': 'fcm_token_same_device', 'platform': 'WEB'},
            format='json',
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()['platform'], 'WEB')

        # Only one active token row exists for this token
        tokens = DevicePushToken.all_tenants.filter(user=self.user_a, token='fcm_token_same_device')
        self.assertEqual(tokens.count(), 1)

    def test_register_push_token_missing_token_returns_400(self):
        self.client.force_authenticate(user=self.user_a)
        res = self.client.post(
            '/api/v1/me/push-tokens/',
            {'token': '', 'platform': 'ANDROID'},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('Token perangkat wajib diisi', res.json().get('error', ''))

    def test_list_my_push_tokens(self):
        with tenant_context(self.foundation.id):
            DevicePushToken.objects.create(
                foundation_id=self.foundation.id,
                user=self.user_a,
                token='token_active_1',
                platform=DevicePushPlatform.ANDROID,
                is_active=True,
            )
            DevicePushToken.objects.create(
                foundation_id=self.foundation.id,
                user=self.user_a,
                token='token_inactive_2',
                platform=DevicePushPlatform.IOS,
                is_active=False,
            )
            DevicePushToken.objects.create(
                foundation_id=self.foundation.id,
                user=self.user_b,
                token='token_user_b',
                platform=DevicePushPlatform.ANDROID,
                is_active=True,
            )

        self.client.force_authenticate(user=self.user_a)
        res = self.client.get('/api/v1/me/push-tokens/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['token'], 'token_active_1')

    def test_deactivate_push_token(self):
        with tenant_context(self.foundation.id):
            dt = DevicePushToken.objects.create(
                foundation_id=self.foundation.id,
                user=self.user_a,
                token='token_to_delete',
                platform=DevicePushPlatform.ANDROID,
                is_active=True,
            )

        self.client.force_authenticate(user=self.user_a)
        res = self.client.delete(
            '/api/v1/me/push-tokens/',
            {'token': 'token_to_delete'},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'deactivated')

        dt.refresh_from_db()
        self.assertFalse(dt.is_active)

    def test_cross_tenant_isolation(self):
        foundation_b = Foundation.objects.create(
            legal_name="Yayasan Luar",
            brand_name="Luar",
            npwp="01.999.999.9-999.000",
            address="Jakarta",
        )
        user_c = User.objects.create(
            foundation_id=foundation_b.id,
            phone_e164="+628111222333",
            email="guru.c@luar.sch.id",
            full_name="Guru C Luar",
        )

        with tenant_context(self.foundation.id):
            DevicePushToken.objects.create(
                foundation_id=self.foundation.id,
                user=self.user_a,
                token='secret_token_tenant_a',
                platform=DevicePushPlatform.ANDROID,
                is_active=True,
            )

        set_current_foundation_id(foundation_b.id)
        self.client.force_authenticate(user=user_c)
        res = self.client.get('/api/v1/me/push-tokens/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])
