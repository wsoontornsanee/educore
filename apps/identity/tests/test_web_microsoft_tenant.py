"""Tests for Foundation-Admin Microsoft Tenant Settings UI (spec/14 §6, spec/17, PR #117).

Covers:
- Authentication redirect for anonymous users
- RBAC authorization (Foundation Admin only; Teacher and School Admin forbidden 403)
- GET display for default (unpinned fallback) and pinned states
- POST tenant ID validation (GUID and domain valid, invalid string rejected with inline id-ID error)
- POST upserting MicrosoftTenantConfig and writing AuditEvent
- DELETE / unpinning restoring fallback and writing AuditEvent
- HTMX partial fragment swapping (HX-Request header)
- Cross-foundation multi-tenancy isolation
- Alias URL routing (/web/foundation/settings/microsoft-tenant/)
"""
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import (
    Foundation,
    MicrosoftTenantConfig,
    RoleAssignment,
    School,
    User,
)
from apps.identity.rbac import assign_role
from apps.identity.social_auth import resolve_microsoft_tenant_id
from educore.middleware.tenancy import set_current_foundation_id

MS_TENANT_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
MS_TENANT_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


class FoundationMicrosoftTenantWebSettingsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Pendidikan A",
            brand_name="Yayasan A",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Pendidikan B",
            brand_name="Yayasan B",
        )
        set_current_foundation_id(self.foundation.id)

        # Foundation Admin A
        self.admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123456001",
            email="admin@yayasan-a.sch.id",
            full_name="Admin Yayasan A",
            is_active=True,
        )
        self.admin.set_password("SecurePass123!")
        self.admin.save()
        assign_role(
            user=self.admin,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        # School and School Admin A
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA A",
            npsn="20429991",
            level=School.LEVEL_SMA,
        )
        self.school_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123456002",
            email="tu@sma-a.sch.id",
            full_name="Tata Usaha A",
            is_active=True,
        )
        self.school_admin.set_password("SecurePass123!")
        self.school_admin.save()
        assign_role(
            user=self.school_admin,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Teacher A
        self.teacher = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123456003",
            email="guru@sma-a.sch.id",
            full_name="Guru SMA A",
            is_active=True,
        )
        self.teacher.set_password("SecurePass123!")
        self.teacher.save()
        assign_role(
            user=self.teacher,
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Foundation Admin B
        self.other_admin = User.objects.create(
            foundation_id=self.other_foundation.id,
            phone_e164="+628123456004",
            email="admin@yayasan-b.sch.id",
            full_name="Admin Yayasan B",
            is_active=True,
        )
        self.other_admin.set_password("SecurePass123!")
        self.other_admin.save()
        assign_role(
            user=self.other_admin,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.other_foundation.id,
            foundation_id=self.other_foundation.id,
        )

    def test_unauthenticated_redirects_to_login(self):
        res = self.client.get('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.url.startswith('/web/login/?next='))

    def test_teacher_forbidden_403(self):
        self.client.force_login(self.teacher)
        res = self.client.get('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "Hanya Administrator Yayasan yang berwenang", status_code=403)

    def test_school_admin_forbidden_403(self):
        self.client.force_login(self.school_admin)
        res = self.client.get('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "Hanya Administrator Yayasan yang berwenang", status_code=403)

    def test_foundation_admin_get_default_unpinned_state(self):
        self.client.force_login(self.admin)
        res = self.client.get('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Integrasi Microsoft Entra ID")
        self.assertContains(res, "Mode Standar")
        self.assertContains(res, settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID)
        self.assertContains(res, "Belum ada riwayat perubahan pengaturan")
        # Destructive unpin button should NOT be present when unpinned
        self.assertNotContains(res, "Lepas Penyematan")

    def test_foundation_admin_post_valid_guid_pins_tenant(self):
        self.client.force_login(self.admin)
        res = self.client.post('/web/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_A,
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Penyematan tenant Microsoft Entra berhasil disimpan.")
        self.assertContains(res, "Tersemat ke Entra ID Yayasan")
        self.assertContains(res, MS_TENANT_A)
        self.assertContains(res, "Lepas Penyematan")

        # Verify DB model
        config = MicrosoftTenantConfig.all_tenants.get(
            foundation_id=self.foundation.id,
            deleted_at__isnull=True,
        )
        self.assertEqual(config.tenant_id, MS_TENANT_A)
        self.assertEqual(resolve_microsoft_tenant_id(self.foundation.id), MS_TENANT_A)

        # Verify AuditEvent
        evt = AuditEvent.objects.filter(
            foundation_id=self.foundation.id,
            action="identity.sso.microsoft_tenant.set",
        ).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.actor_id, str(self.admin.id))
        self.assertEqual(evt.diff['tenant_id']['after'], MS_TENANT_A)

    def test_foundation_admin_post_valid_domain_pins_tenant(self):
        self.client.force_login(self.admin)
        res = self.client.post('/web/auth/sso/microsoft-tenant/', {
            'tenant_id': 'yayasan-a.sch.id',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "yayasan-a.sch.id")
        self.assertEqual(resolve_microsoft_tenant_id(self.foundation.id), 'yayasan-a.sch.id')

    def test_foundation_admin_post_invalid_tenant_id_returns_400_with_inline_error(self):
        self.client.force_login(self.admin)
        res = self.client.post('/web/auth/sso/microsoft-tenant/', {
            'tenant_id': 'bukan-tenant-yang-valid!!',
        })
        self.assertEqual(res.status_code, 400)
        self.assertContains(
            res,
            "Tenant ID harus berupa GUID Microsoft Entra (Directory ID) atau nama domain terdaftar.",
            status_code=400,
        )
        # Verify no config was created
        self.assertFalse(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id,
                deleted_at__isnull=True,
            ).exists()
        )

    def test_foundation_admin_delete_unpins_tenant(self):
        # First pin
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id,
            tenant_id=MS_TENANT_A,
            created_by=str(self.admin.id),
        )
        self.assertEqual(resolve_microsoft_tenant_id(self.foundation.id), MS_TENANT_A)

        self.client.force_login(self.admin)
        res = self.client.delete('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Penyematan tenant Microsoft Entra berhasil dilepas.")
        self.assertContains(res, "Mode Standar")

        # Verify DB model is soft deleted
        self.assertFalse(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id,
                deleted_at__isnull=True,
            ).exists()
        )
        self.assertEqual(
            resolve_microsoft_tenant_id(self.foundation.id),
            settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID,
        )

        # Verify AuditEvent
        evt = AuditEvent.objects.filter(
            foundation_id=self.foundation.id,
            action="identity.sso.microsoft_tenant.removed",
        ).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.diff['tenant_id']['after'], None)

    def test_post_action_unpin_delegates_to_delete(self):
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id,
            tenant_id=MS_TENANT_A,
            created_by=str(self.admin.id),
        )
        self.client.force_login(self.admin)
        res = self.client.post('/web/auth/sso/microsoft-tenant/', {
            'action': 'unpin',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Penyematan tenant Microsoft Entra berhasil dilepas.")
        self.assertFalse(
            MicrosoftTenantConfig.all_tenants.filter(
                foundation_id=self.foundation.id,
                deleted_at__isnull=True,
            ).exists()
        )

    def test_htmx_header_renders_partial_fragment_only(self):
        self.client.force_login(self.admin)
        res = self.client.get(
            '/web/auth/sso/microsoft-tenant/',
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(res.status_code, 200)
        # Should contain fragment container
        self.assertContains(res, 'id="microsoft-tenant-settings-container"')
        # Should NOT contain full HTML shell tags
        self.assertNotContains(res, '<!DOCTYPE html>')
        self.assertNotContains(res, '<html')
        self.assertNotContains(res, '</html>')

    def test_cross_foundation_isolation(self):
        # Foundation A pins MS_TENANT_A
        MicrosoftTenantConfig.all_tenants.create(
            foundation_id=self.foundation.id,
            tenant_id=MS_TENANT_A,
            created_by=str(self.admin.id),
        )
        AuditEvent.objects.create(
            foundation_id=self.foundation.id,
            action="identity.sso.microsoft_tenant.set",
            entity_type="MicrosoftTenantConfig",
            entity_id="1",
            actor_id=str(self.admin.id),
            diff={"tenant_id": {"before": None, "after": MS_TENANT_A}},
        )

        # Foundation B Admin logs in
        self.client.force_login(self.other_admin)
        set_current_foundation_id(self.other_foundation.id)

        res = self.client.get('/web/auth/sso/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        # Foundation B should see unpinned fallback, not Foundation A's tenant
        self.assertContains(res, "Mode Standar")
        self.assertNotContains(res, MS_TENANT_A)
        # Foundation B should not see Foundation A's audit event
        self.assertContains(res, "Belum ada riwayat perubahan pengaturan")

        # Foundation B pins MS_TENANT_B
        res_post = self.client.post('/web/auth/sso/microsoft-tenant/', {
            'tenant_id': MS_TENANT_B,
        })
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.get(
                foundation_id=self.foundation.id,
                deleted_at__isnull=True,
            ).tenant_id,
            MS_TENANT_A,
        )
        self.assertEqual(
            MicrosoftTenantConfig.all_tenants.get(
                foundation_id=self.other_foundation.id,
                deleted_at__isnull=True,
            ).tenant_id,
            MS_TENANT_B,
        )

    def test_alias_url_resolves(self):
        self.client.force_login(self.admin)
        res = self.client.get('/web/foundation/settings/microsoft-tenant/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Integrasi Microsoft Entra ID")
