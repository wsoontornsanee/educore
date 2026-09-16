"""Tests for Foundation settings and KPI dashboard views (spec/03 §4, §5)."""
from datetime import date, timedelta
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.foundation.models import RptFoundationKPI
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class FoundationKPIAndSettingsTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa",
            brand_name="Bina Bangsa",
            npwp="01.888.777.6-001.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Bina Bangsa",
            npsn="40100001",
            level=School.LEVEL_SMA,
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999999",
            foundation_id=self.foundation.id,
            full_name="Ketua Yayasan Bina Bangsa",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        # Seed KPI rollup row (FND-001, FND-005)
        self.kpi = RptFoundationKPI.objects.create(
            foundation_id=self.foundation.id,
            school_id=self.school.id,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            billed=Decimal('50000000.00'),
            collected=Decimal('42500000.00'),
            outstanding=Decimal('7500000.00'),
            ar_0_30=Decimal('5000000.00'),
            ar_31_60=Decimal('2500000.00'),
            campus_spend=Decimal('8200000.00'),
            active_students=450,
            avg_attendance_pct=Decimal('96.50'),
            currency='IDR',
            reporting_currency='IDR',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_get_foundation_settings(self):
        """Verify GET /api/v1/foundation/settings returns foundation metadata."""
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/settings')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['brand_name'], "Bina Bangsa")
            self.assertEqual(response.data['reporting_currency'], "IDR")

    def test_update_foundation_settings(self):
        """Verify PATCH /api/v1/foundation/settings updates settings with audit."""
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            response = self.client.patch('/api/v1/foundation/settings', data={"address": "Jl. Merdeka No. 10"})
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['address'], "Jl. Merdeka No. 10")
            self.foundation.refresh_from_db()
            self.assertEqual(self.foundation.address, "Jl. Merdeka No. 10")

    def test_get_foundation_kpis(self):
        """Verify GET /api/v1/foundation/kpis returns read-model rollup figures."""
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/kpis')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            results = response.data['results']
            self.assertEqual(len(results), 1)
            row = results[0]
            self.assertEqual(row['billed'], "50000000.00")
            self.assertEqual(row['collected'], "42500000.00")
            self.assertEqual(row['collection_rate_pct'], "85.00")
            self.assertEqual(row['active_students'], 450)
            self.assertEqual(row['avg_attendance_pct'], "96.50")

    def test_get_foundation_kpis_filters_by_school_ids_and_date_range(self):
        """FoundationKPIView.get delegates to filter_foundation_kpis (shared with
        the dashboard export renderer) — pin down its school_ids/from/to contract
        directly, not just the single-row default case."""
        other_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Bina Bangsa", npsn="40100002", level=School.LEVEL_SMP,
        )
        other_period_row = RptFoundationKPI.objects.create(
            foundation_id=self.foundation.id, school_id=other_school.id,
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            billed=Decimal('10000000.00'), collected=Decimal('9000000.00'),
            active_students=100, avg_attendance_pct=Decimal('90.00'),
            currency='IDR', reporting_currency='IDR',
        )
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            response = self.client.get(f'/api/v1/foundation/kpis?school_ids={self.school.id}')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual([r['school_id'] for r in response.data['results']], [self.school.id])

            response = self.client.get('/api/v1/foundation/kpis?from=2026-09-01&to=2026-09-30')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data['results']), 1)
            self.assertEqual(response.data['results'][0]['school_id'], self.school.id)

            response = self.client.get('/api/v1/foundation/kpis?from=2026-08-01&to=2026-08-31')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual([r['school_id'] for r in response.data['results']], [other_school.id])

    def test_foundation_kpis_freshness_flag(self):
        """FND-006: freshness must be surfaced and flagged stale beyond 15 minutes."""
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/kpis')
            freshness = response.data['freshness']
            self.assertIsNotNone(freshness['computed_at'])
            self.assertFalse(freshness['stale'])

            # RptFoundationKPI.computed_at is auto_now=True, so .save() would
            # overwrite it back to "now" -- use a queryset update to bypass that.
            RptFoundationKPI.objects.filter(pk=self.kpi.pk).update(computed_at=timezone.now() - timedelta(minutes=30))

            response = self.client.get('/api/v1/foundation/kpis')
            self.assertTrue(response.data['freshness']['stale'])

    def test_foundation_kpis_compare_prev_period(self):
        """FND-003: compare=prev_period returns absolute + percentage delta."""
        self.client.force_authenticate(user=self.admin)

        RptFoundationKPI.objects.create(
            foundation_id=self.foundation.id,
            school_id=self.school.id,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            billed=Decimal('40000000.00'),
            collected=Decimal('30000000.00'),
            active_students=400,
            avg_attendance_pct=Decimal('95.00'),
            currency='IDR',
            reporting_currency='IDR',
        )

        with tenant_context(self.foundation.id):
            response = self.client.get(
                '/api/v1/foundation/kpis?from=2026-09-01&to=2026-09-30&compare=prev_period'
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            compare = response.data['compare']
            self.assertIsNotNone(compare)
            self.assertEqual(compare['prior_period'], {'from': '2026-08-01', 'to': '2026-08-31'})
            self.assertEqual(compare['delta']['billed'], '10000000.00')
            self.assertEqual(compare['delta_pct']['billed'], '25.00')
