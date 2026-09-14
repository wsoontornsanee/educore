"""Tests for TenantModel and 3-layer tenancy isolation (ARC-001, ARC-002)."""
from django.db import connection, models
from django.test import TestCase
from apps.core.fields import MoneyField
from apps.core.models import TenantModel
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class DummyTenantModel(TenantModel):
    name = models.CharField(max_length=50)
    amount = MoneyField(default=0)

    class Meta:
        app_label = 'core'
        db_table = 'test_tenant_items'

class TenancyIsolationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_tenant_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    foundation_id BIGINT NOT NULL,
                    created_at DATETIME NOT NULL,
                    created_by VARCHAR(64),
                    updated_at DATETIME NOT NULL,
                    updated_by VARCHAR(64),
                    deleted_at DATETIME,
                    name VARCHAR(50) NOT NULL,
                    amount DECIMAL(18,2) NOT NULL DEFAULT '0.00'
                )
            """)

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS test_tenant_items")
        super().tearDownClass()

    def setUp(self):
        clear_current_foundation_id()

    def tearDown(self):
        clear_current_foundation_id()

    def test_tenant_model_fields(self):
        """Verify TenantModel possesses required fields per ARC-001."""
        fields = {f.name for f in DummyTenantModel._meta.get_fields()}
        self.assertIn('foundation_id', fields)
        self.assertIn('created_at', fields)
        self.assertIn('created_by', fields)
        self.assertIn('updated_at', fields)
        self.assertIn('updated_by', fields)
        self.assertIn('deleted_at', fields)

    def test_tenant_scoping_isolation(self):
        """Verify TenantManager scopes queries strictly to active thread-local foundation (ARC-002)."""
        # Create records under Foundation 1001
        with tenant_context(1001):
            item1 = DummyTenantModel.objects.create(name="School A Item", foundation_id=1001)

        # Create records under Foundation 2002
        with tenant_context(2002):
            item2 = DummyTenantModel.objects.create(name="School B Item", foundation_id=2002)

        # Query under Foundation 1001
        with tenant_context(1001):
            results = list(DummyTenantModel.objects.all())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].pk, item1.pk)

        # Query under Foundation 2002
        with tenant_context(2002):
            results = list(DummyTenantModel.objects.all())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].pk, item2.pk)

        # Query without tenant context: must fail-closed (return empty queryset)
        clear_current_foundation_id()
        self.assertEqual(DummyTenantModel.objects.count(), 0)

        # Unscoped query via all_tenants
        all_items = list(DummyTenantModel.all_tenants.all())
        self.assertEqual(len(all_items), 2)

    def test_soft_delete_and_restore(self):
        """Verify soft deletion lifecycle for tenant models."""
        with tenant_context(1001):
            item = DummyTenantModel.objects.create(name="Soft Delete Item", foundation_id=1001)
            self.assertIsNone(item.deleted_at)
            self.assertFalse(item.is_deleted)

            # Soft delete
            item.delete()
            item.refresh_from_db()
            self.assertIsNotNone(item.deleted_at)
            self.assertTrue(item.is_deleted)

            # Objects manager should not return it
            self.assertEqual(DummyTenantModel.objects.filter(pk=item.pk).count(), 0)

            # all_tenants should not return it by default
            self.assertEqual(DummyTenantModel.all_tenants.filter(pk=item.pk).count(), 0)

            # all_tenants.with_deleted() MUST return it
            self.assertEqual(DummyTenantModel.all_tenants.with_deleted().filter(pk=item.pk).count(), 1)

            # Restore
            item.restore()
            item.refresh_from_db()
            self.assertIsNone(item.deleted_at)
            self.assertEqual(DummyTenantModel.objects.filter(pk=item.pk).count(), 1)
