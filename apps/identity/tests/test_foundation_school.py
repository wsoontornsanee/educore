"""Tests for Foundation and School models and seeding (spec/02, spec/03)."""
from django.core.management import call_command
from django.test import TestCase
from apps.identity.models import Foundation, School
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class FoundationAndSchoolModelTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()

    def tearDown(self):
        clear_current_foundation_id()

    def test_foundation_creation_and_defaults(self):
        """Verify Foundation model attributes and defaults."""
        f = Foundation.objects.create(
            legal_name="Yayasan Nurul Fikri Indonesia",
            brand_name="Nurul Fikri",
            npwp="01.999.888.7-001.000",
        )
        self.assertEqual(f.timezone, "Asia/Jakarta")
        self.assertEqual(f.reporting_currency, "IDR")
        self.assertEqual(f.plan_tier, Foundation.PLAN_STANDARD)
        self.assertEqual(f.status, Foundation.STATUS_ACTIVE)
        self.assertIn("Nurul Fikri", str(f))

    def test_school_tenancy_isolation(self):
        """Verify School models enforce 3-layer tenancy isolation via TenantManager."""
        f_alpha = Foundation.objects.create(legal_name="Yayasan Alpha", brand_name="Alpha")
        f_beta = Foundation.objects.create(legal_name="Yayasan Beta", brand_name="Beta")

        # Create schools under Alpha
        with tenant_context(f_alpha.id):
            s_alpha = School.objects.create(
                foundation_id=f_alpha.id,
                name="SD Alpha Nusantara",
                npsn="10100001",
                level=School.LEVEL_SD,
            )

        # Create schools under Beta
        with tenant_context(f_beta.id):
            s_beta = School.objects.create(
                foundation_id=f_beta.id,
                name="SMP Beta Nusantara",
                npsn="10100002",
                level=School.LEVEL_SMP,
            )

        # Query under Alpha
        with tenant_context(f_alpha.id):
            schools = list(School.objects.all())
            self.assertEqual(len(schools), 1)
            self.assertEqual(schools[0].pk, s_alpha.pk)
            self.assertEqual(schools[0].base_currency, "IDR")

        # Query under Beta
        with tenant_context(f_beta.id):
            schools = list(School.objects.all())
            self.assertEqual(len(schools), 1)
            self.assertEqual(schools[0].pk, s_beta.pk)

        # Query without tenant context: must fail-closed (return empty queryset)
        clear_current_foundation_id()
        self.assertEqual(School.objects.count(), 0)

        # Unscoped query via all_tenants
        all_schools = list(School.all_tenants.all())
        self.assertEqual(len(all_schools), 2)

    def test_school_soft_delete(self):
        """Verify soft deletion lifecycle for schools."""
        f = Foundation.objects.create(legal_name="Yayasan Gamma", brand_name="Gamma")
        with tenant_context(f.id):
            school = School.objects.create(
                foundation_id=f.id,
                name="SMA Gamma",
                npsn="10100003",
                level=School.LEVEL_SMA,
            )
            self.assertFalse(school.is_deleted)

            school.delete()
            school.refresh_from_db()
            self.assertTrue(school.is_deleted)
            self.assertIsNotNone(school.deleted_at)

            # Standard manager should not return it
            self.assertEqual(School.objects.filter(pk=school.pk).count(), 0)
            self.assertEqual(School.all_tenants.filter(pk=school.pk).count(), 0)

            # all_tenants.with_deleted() returns it
            self.assertEqual(School.all_tenants.with_deleted().filter(pk=school.pk).count(), 1)

            # Restore
            school.restore()
            school.refresh_from_db()
            self.assertFalse(school.is_deleted)
            self.assertEqual(School.objects.filter(pk=school.pk).count(), 1)

    def test_seed_demo_foundation_idempotency(self):
        """Verify seed_demo_foundation seeds Yayasan and 3 schools cleanly and idempotently."""
        # 1st run
        call_command('seed_demo_foundation')
        self.assertEqual(Foundation.objects.filter(brand_name="Yayasan Al-Hikmah Nusantara").count(), 1)
        self.assertEqual(School.all_tenants.count(), 3)

        foundation = Foundation.objects.get(brand_name="Yayasan Al-Hikmah Nusantara")
        with tenant_context(foundation.id):
            schools = School.objects.all()
            self.assertEqual(schools.count(), 3)
            levels = {s.level for s in schools}
            self.assertEqual(levels, {School.LEVEL_SD, School.LEVEL_SMP, School.LEVEL_SMA})

            # Check demo admin user
            from apps.identity.models import User, Person, RoleAssignment, FoundationEntitlement
            admin_user = User.objects.get(phone_e164="+6281234567890")
            self.assertTrue(admin_user.is_superuser)
            self.assertEqual(admin_user.email, "admin@alhikmah.sch.id")
            person = Person.objects.get(foundation_id=foundation.id)
            self.assertEqual(person.nik, "3171012345670001")
            role_assignment = RoleAssignment.objects.get(user=admin_user)
            self.assertEqual(role_assignment.role, RoleAssignment.ROLE_FOUNDATION_ADMIN)
            self.assertEqual(role_assignment.scope_type, RoleAssignment.SCOPE_FOUNDATION)
            self.assertEqual(role_assignment.scope_id, foundation.id)
            self.assertEqual(FoundationEntitlement.objects.filter(foundation_id=foundation.id).count(), 8)

        # 2nd run: assert idempotency (counts do not duplicate)
        call_command('seed_demo_foundation')
        self.assertEqual(Foundation.objects.filter(brand_name="Yayasan Al-Hikmah Nusantara").count(), 1)
        self.assertEqual(School.all_tenants.count(), 3)
        self.assertEqual(User.all_tenants.filter(phone_e164="+6281234567890").count(), 1)
        self.assertEqual(RoleAssignment.all_tenants.filter(user__phone_e164="+6281234567890").count(), 1)
        self.assertEqual(FoundationEntitlement.all_tenants.filter(foundation_id=foundation.id).count(), 8)
