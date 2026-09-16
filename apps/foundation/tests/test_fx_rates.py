"""Tests for FxRate model, FX conversion service, and FxRate CRUD API (CUR-021, FND-005b)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.foundation.models import FxRate, RptFoundationKPI
from apps.foundation.services import FxRateNotFoundError, convert_currency, get_fx_rate, has_mixed_currencies
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class FxRateModelTests(TestCase):
    """CUR-021: fx_rates(base_currency, quote_currency, rate DECIMAL(18,8), effective_date, source)."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Test",
            brand_name="Test",
            npwp="01.111.222.3-000.000",
        )
        set_current_foundation_id(self.foundation.id)

    def test_create_fx_rate(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD',
            quote_currency='IDR',
            rate=Decimal('15500.00000000'),
            effective_date=date(2026, 9, 1),
        )
        self.assertEqual(str(rate), "USD/IDR = 15500.00000000 (2026-09-01)")
        self.assertEqual(rate.source, FxRate.SOURCE_MANUAL)

    def test_soft_delete(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD',
            quote_currency='IDR',
            rate=Decimal('15500.00000000'),
            effective_date=date(2026, 9, 1),
        )
        rate.delete()
        self.assertIsNotNone(rate.deleted_at)
        self.assertFalse(FxRate.all_tenants.filter(id=rate.id, deleted_at__isnull=True).exists())

    def test_default_source_is_manual(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD',
            quote_currency='IDR',
            rate=Decimal('15500.00000000'),
            effective_date=date(2026, 9, 1),
        )
        self.assertEqual(rate.source, 'MANUAL')

    def test_cross_tenant_isolation(self):
        f2 = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Lain",
            npwp="02.222.333.4-000.000",
        )
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=date(2026, 9, 1),
        )
        # Foundation 2 should see no rates
        count = FxRate.all_tenants.filter(foundation_id=f2.id).count()
        self.assertEqual(count, 0)


class FxConversionServiceTests(TestCase):
    """Tests for get_fx_rate, convert_currency, and has_mixed_currencies."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Test",
            brand_name="Test",
            npwp="01.111.222.3-000.000",
            reporting_currency='IDR',
        )
        set_current_foundation_id(self.foundation.id)
        self.today = timezone.now().date()

        # Seed rate: USD 1 = IDR 15,500 (effective from 2026-09-01)
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'),
            effective_date=date(2026, 9, 1),
        )

    def test_get_fx_rate_found(self):
        rate = get_fx_rate('USD', 'IDR', date(2026, 9, 15), self.foundation.id)
        self.assertIsNotNone(rate)
        self.assertEqual(rate.rate, Decimal('15500.00000000'))

    def test_get_fx_rate_same_currency_returns_none(self):
        rate = get_fx_rate('IDR', 'IDR', date(2026, 9, 15), self.foundation.id)
        self.assertIsNone(rate)

    def test_get_fx_rate_not_found(self):
        rate = get_fx_rate('USD', 'EUR', date(2026, 9, 15), self.foundation.id)
        self.assertIsNone(rate)

    def test_get_fx_rate_before_effective_date(self):
        """A rate effective from 2026-09-01 should NOT be found for dates before that."""
        rate = get_fx_rate('USD', 'IDR', date(2026, 8, 31), self.foundation.id)
        self.assertIsNone(rate)

    def test_convert_same_currency(self):
        result, rate = convert_currency(
            Decimal('1000.00'), 'IDR', 'IDR', date(2026, 9, 15), self.foundation.id,
        )
        self.assertEqual(result, Decimal('1000.00'))
        self.assertIsNone(rate)

    def test_convert_usd_to_idr(self):
        result, rate = convert_currency(
            Decimal('100.00'), 'USD', 'IDR', date(2026, 9, 15), self.foundation.id,
        )
        # 100 USD * 15500 = 1,550,000 IDR
        self.assertEqual(result, Decimal('1550000.00'))
        self.assertIsNotNone(rate)
        self.assertEqual(rate.effective_date, date(2026, 9, 1))

    def test_convert_uses_most_recent_rate(self):
        """Add a newer rate — conversion should use the latest effective_date on or before as_of."""
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15600.00000000'),
            effective_date=date(2026, 10, 1),
        )
        result, rate = convert_currency(
            Decimal('100.00'), 'USD', 'IDR', date(2026, 10, 15), self.foundation.id,
        )
        self.assertEqual(result, Decimal('1560000.00'))
        self.assertEqual(rate.effective_date, date(2026, 10, 1))

    def test_convert_rounding(self):
        """CUR-016: ROUND_HALF_UP applied at persistence."""
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00500000'),
            effective_date=date(2026, 10, 1),  # different date from setUp's rate
        )
        result, rate = convert_currency(
            Decimal('100.00'), 'USD', 'IDR', date(2026, 10, 15), self.foundation.id,
        )
        # 100 * 15500.005 = 1,550,000.50 — stored at 2dp
        self.assertEqual(result, Decimal('1550000.50'))

    def test_convert_missing_rate_raises(self):
        """CUR-024: missing rate MUST surface as an explicit error, never silent zero."""
        with self.assertRaises(FxRateNotFoundError):
            convert_currency(
                Decimal('100.00'), 'USD', 'EUR', date(2026, 9, 15), self.foundation.id,
            )

    def test_has_mixed_currencies_single(self):
        """Single currency foundation returns False."""
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Test",
            npsn="40101001", level=School.LEVEL_SD, base_currency='IDR',
        )
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Test",
            npsn="40101002", level=School.LEVEL_SMP, base_currency='IDR',
        )
        self.assertFalse(has_mixed_currencies(self.foundation.id))

    def test_has_mixed_currencies_mixed(self):
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Test",
            npsn="40101001", level=School.LEVEL_SD, base_currency='IDR',
        )
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Test",
            npsn="40101002", level=School.LEVEL_SMP, base_currency='USD',
        )
        self.assertTrue(has_mixed_currencies(self.foundation.id))


class FxRateApiTests(APITestCase):
    """CRUD API tests for FxRate (CUR-021)."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Test",
            brand_name="Test",
            npwp="01.111.222.3-000.000",
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999999",
            foundation_id=self.foundation.id,
            full_name="Admin Test",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )
        self.client.force_authenticate(user=self.admin)

    def test_list_fx_rates_empty(self):
        response = self.client.get('/api/v1/fx-rates/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])

    def test_create_fx_rate(self):
        payload = {
            'base_currency': 'USD',
            'quote_currency': 'IDR',
            'rate': '15500.00000000',
            'effective_date': '2026-09-01',
        }
        response = self.client.post('/api/v1/fx-rates/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['base_currency'], 'USD')
        self.assertEqual(response.data['rate'], '15500.00000000')

    def test_retrieve_fx_rate(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=date(2026, 9, 1),
        )
        response = self.client.get(f'/api/v1/fx-rates/{rate.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['base_currency'], 'USD')

    def test_update_fx_rate(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=date(2026, 9, 1),
        )
        response = self.client.patch(f'/api/v1/fx-rates/{rate.id}/', {'rate': '15600.00000000'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['rate'], '15600.00000000')

    def test_delete_fx_rate_soft(self):
        rate = FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=date(2026, 9, 1),
        )
        response = self.client.delete(f'/api/v1/fx-rates/{rate.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # Verify soft delete
        rate.refresh_from_db()
        self.assertIsNotNone(rate.deleted_at)

    def test_cross_tenant_returns_empty(self):
        """Foundation A admin should not see Foundation B's rates."""
        f2 = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain",
            npwp="02.222.333.4-000.000",
        )
        FxRate.all_tenants.create(
            foundation_id=f2.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=date(2026, 9, 1),
        )
        response = self.client.get('/api/v1/fx-rates/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)

    def test_unauthenticated_request_denied(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/api/v1/fx-rates/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MultiCurrencyKpiRefreshTests(TestCase):
    """FND-005b: refresh_foundation_kpis multi-currency consolidation."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Multi Currency",
            brand_name="Multi",
            npwp="01.111.222.3-000.000",
            reporting_currency='IDR',
        )
        set_current_foundation_id(self.foundation.id)
        self.now = timezone.now()
        self.month = self.now.date().replace(day=1)
        self.month_end = (self.month.replace(day=28) + timezone.timedelta(days=4)).replace(day=1) - timezone.timedelta(days=1)

        # School A: IDR base
        self.school_idr = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD IDR",
            npsn="40101001", level=School.LEVEL_SD, base_currency='IDR',
        )
        # School B: USD base
        self.school_usd = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD USD",
            npsn="40101002", level=School.LEVEL_SD, base_currency='USD',
        )

        # Create a class_group for attendance rollup
        from apps.identity.models import Person, Student
        from apps.academic.models import ClassGroup, AcademicYear

        self.ay = AcademicYear.objects.create(
            foundation_id=self.foundation.id, school=self.school_idr,
            name="2026/2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )
        self.class_group = ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=self.school_idr,
            academic_year=self.ay, grade_level=10, name="X IPA 1",
        )
        self.student_idr = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_idr,
            person=Person.all_tenants.create(
                foundation_id=self.foundation.id, nik="3471010101010101", full_name="Siswa IDR",
            ),
            nisn="1122334455", nis="X-001", status=Student.STATUS_ACTIVE,
        )
        self.student_usd = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_usd,
            person=Person.all_tenants.create(
                foundation_id=self.foundation.id, nik="3471010101010202", full_name="Siswa USD",
            ),
            nisn="1122334466", nis="X-002", status=Student.STATUS_ACTIVE,
        )

        # Seed rpt_* rows for both schools
        for school, currency, billed, collected in [
            (self.school_idr, 'IDR', Decimal('5000000.00'), Decimal('4000000.00')),
            (self.school_usd, 'USD', Decimal('1000.00'), Decimal('800.00')),
        ]:
            from apps.reporting.models import RptDailyFinance, RptArAging, RptActiveStudent, RptWalletActivity, RptDailyAttendance
            RptDailyFinance.all_tenants.create(
                foundation_id=self.foundation.id, school=school, date=self.month,
                currency=currency, billed=billed, collected=collected,
                outstanding=collected * Decimal('0.25'), computed_at=self.now,
            )
            RptActiveStudent.all_tenants.create(
                foundation_id=self.foundation.id, school=school, month=self.month,
                active_count=100, computed_at=self.now,
            )
            RptWalletActivity.all_tenants.create(
                foundation_id=self.foundation.id, school=school, date=self.month,
                currency=currency, purchases=Decimal('50000.00'), computed_at=self.now,
            )
            RptDailyAttendance.all_tenants.create(
                foundation_id=self.foundation.id, school=school, date=self.month,
                class_group=self.class_group, present=90, late=5, sick=2, permitted=1, absent=2,
                rate_pct=Decimal('95.00'), computed_at=self.now,
            )

        # No FxRate seeded yet — will be added in specific test methods

    def _refresh(self):
        from apps.reporting.services import refresh_foundation_kpis
        return refresh_foundation_kpis(scope='full')

    def _get_agg(self):
        return RptFoundationKPI.objects.get(
            foundation_id=self.foundation.id, school_id=None, period_start=self.month,
        )

    def test_single_currency_per_school_rows_use_school_currency(self):
        """Per-school rows must use the school's base_currency, not hardcoded IDR."""
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=self.month,
        )
        self._refresh()

        idr_row = RptFoundationKPI.objects.get(foundation_id=self.foundation.id, school_id=self.school_idr.id, period_start=self.month)
        self.assertEqual(idr_row.currency, 'IDR')

        usd_row = RptFoundationKPI.objects.get(foundation_id=self.foundation.id, school_id=self.school_usd.id, period_start=self.month)
        self.assertEqual(usd_row.currency, 'USD')

    def test_mixed_currencies_with_fx_rate_consolidates_aggregate(self):
        """With FxRate present, aggregate row should be CONSOLIDATED with converted values."""
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=self.month,
        )
        self._refresh()

        agg = self._get_agg()
        self.assertEqual(agg.multi_currency_status, RptFoundationKPI.MULTI_CURRENCY_CONSOLIDATED)
        self.assertEqual(agg.currency, 'IDR')
        self.assertIsNotNone(agg.fx_rate_date)
        # IDR school: 5,000,000 billed. USD school: 1,000 * 15500 = 15,500,000. Total = 20,500,000
        self.assertEqual(agg.billed, Decimal('20500000.00'))
        # IDR school: 4,000,000 collected. USD school: 800 * 15500 = 12,400,000. Total = 16,400,000
        self.assertEqual(agg.collected, Decimal('16400000.00'))

    def test_mixed_currencies_without_fx_rate_flags_unsupported(self):
        """Without FxRate, aggregate row should be MIXED_CURRENCY_UNSUPPORTED with zero monetary values."""
        self._refresh()

        agg = self._get_agg()
        self.assertEqual(agg.multi_currency_status, RptFoundationKPI.MULTI_CURRENCY_UNSUPPORTED)
        # CUR-024: missing rate surfaces as explicit gap, not silent wrong sum
        self.assertEqual(agg.billed, Decimal('0.00'))
        self.assertEqual(agg.collected, Decimal('0.00'))
        self.assertEqual(agg.outstanding, Decimal('0.00'))
        # Non-monetary values still populated
        self.assertEqual(agg.active_students, 200)
        # fx_rate_date should be None
        self.assertIsNone(agg.fx_rate_date)

    def test_single_currency_foundation_unchanged(self):
        """Foundation with all schools in same currency stays SINGLE_CURRENCY."""
        # Create a new single-currency foundation
        f2 = Foundation.objects.create(
            legal_name="Yayasan Single",
            brand_name="Single",
            npwp="03.333.444.5-000.000",
            reporting_currency='IDR',
        )
        s2 = School.all_tenants.create(
            foundation_id=f2.id, name="SD Single",
            npsn="40101003", level=School.LEVEL_SD, base_currency='IDR',
        )
        from apps.reporting.models import RptDailyFinance
        RptDailyFinance.all_tenants.create(
            foundation_id=f2.id, school=s2, date=self.month,
            currency='IDR', billed=Decimal('10000000.00'), collected=Decimal('8000000.00'),
            outstanding=Decimal('2000000.00'), computed_at=self.now,
        )

        self._refresh()

        agg = RptFoundationKPI.objects.get(foundation_id=f2.id, school_id=None, period_start=self.month)
        self.assertEqual(agg.multi_currency_status, RptFoundationKPI.MULTI_CURRENCY_SINGLE)
        self.assertEqual(agg.billed, Decimal('10000000.00'))
        self.assertIsNone(agg.fx_rate_date)

    def test_partial_conversion_failure_still_flags_unsupported(self):
        """If one school's conversion fails, the entire aggregate is flagged unsupported."""
        FxRate.all_tenants.create(
            foundation_id=self.foundation.id,
            base_currency='USD', quote_currency='IDR',
            rate=Decimal('15500.00000000'), effective_date=self.month,
        )
        # Add a third school with a currency that has no rate
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD EUR",
            npsn="40101004", level=School.LEVEL_SD, base_currency='EUR',
        )

        self._refresh()

        agg = self._get_agg()
        self.assertEqual(agg.multi_currency_status, RptFoundationKPI.MULTI_CURRENCY_UNSUPPORTED)
