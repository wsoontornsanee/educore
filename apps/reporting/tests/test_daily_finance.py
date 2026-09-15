import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.finance.models import Invoice, InvoiceStatus
from apps.finance.services.payments import record_cash_payment
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.reporting.models import RptDailyFinance
from apps.reporting.services import refresh_daily_finance
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def build_finance_fixture(foundation_name="Yayasan Reporting Finance"):
    clear_current_foundation_id()
    foundation = Foundation.objects.create(
        legal_name=foundation_name, brand_name=foundation_name, npwp="01.999.888.7-666.000", address="Bandung",
    )
    set_current_foundation_id(foundation.id)
    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id, name="SD Reporting", npsn=f"301{npsn_suffix}", level=School.LEVEL_SD, base_currency="IDR",
    )
    person = Person.all_tenants.create(foundation_id=foundation.id, nik=f"34710101{npsn_suffix}", full_name="Siswa Reporting")
    student = Student.all_tenants.create(
        foundation_id=foundation.id, school=school, person=person, nisn=npsn_suffix, nis=f"RPT-{npsn_suffix}", status=Student.STATUS_ACTIVE,
    )
    finance_user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+6281{npsn_suffix}", email=f"finance.{npsn_suffix}@school.id", full_name="Bendahara",
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=finance_user, role='finance_officer',
        scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
    )
    return {'foundation': foundation, 'school': school, 'student': student, 'finance_user': finance_user}


def make_invoice(fx, total, paid=Decimal('0.00'), status=InvoiceStatus.ISSUED, issue_date=None, number_suffix='1', period='2026-08'):
    return Invoice.objects.create(
        foundation_id=fx['foundation'].id, school=fx['school'], student=fx['student'],
        number=f"INV/RPT/{fx['foundation'].id}/{number_suffix}", period=period,
        issue_date=issue_date or timezone.now().date(), due_date=timezone.now().date() + datetime.timedelta(days=14),
        subtotal=total, total=total, paid=paid, currency='IDR', status=status,
    )


class RefreshDailyFinanceTests(TestCase):
    def setUp(self):
        self.fx = build_finance_fixture()
        self.today = timezone.now().date()

    def test_billed_computed_from_issued_invoices(self):
        make_invoice(self.fx, Decimal('500000.00'))
        refresh_daily_finance(scope='full')

        row = RptDailyFinance.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=self.today)
        self.assertEqual(row.billed, Decimal('500000.00'))

    def test_collected_fees_payments_count_from_settled_payments(self):
        record_cash_payment(self.fx['school'], self.fx['student'], Decimal('200000.00'))
        refresh_daily_finance(scope='full')

        row = RptDailyFinance.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=self.today)
        self.assertEqual(row.collected, Decimal('200000.00'))
        self.assertEqual(row.payments_count, 1)
        self.assertEqual(row.fees, Decimal('0.00'))  # cash payments carry no gateway fee

    def test_outstanding_reflects_open_invoice_balance(self):
        make_invoice(self.fx, Decimal('500000.00'), paid=Decimal('150000.00'), status=InvoiceStatus.PARTIALLY_PAID)
        make_invoice(self.fx, Decimal('100000.00'), status=InvoiceStatus.PAID, paid=Decimal('100000.00'), number_suffix='2', period='2026-09')  # excluded: final status
        refresh_daily_finance(scope='full')

        row = RptDailyFinance.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=self.today)
        self.assertEqual(row.outstanding, Decimal('350000.00'))

    def test_rerun_is_idempotent(self):
        make_invoice(self.fx, Decimal('100000.00'))
        refresh_daily_finance(scope='full')
        refresh_daily_finance(scope='full')

        self.assertEqual(
            RptDailyFinance.all_tenants.filter(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=self.today).count(),
            1,
        )

    def test_dashboard_scope_excludes_old_days_full_scope_includes_them(self):
        old_date = self.today - datetime.timedelta(days=10)
        make_invoice(self.fx, Decimal('100000.00'), issue_date=old_date)

        refresh_daily_finance(scope='dashboard')
        self.assertFalse(RptDailyFinance.all_tenants.filter(foundation_id=self.fx['foundation'].id, date=old_date).exists())

        refresh_daily_finance(scope='full')
        self.assertTrue(RptDailyFinance.all_tenants.filter(foundation_id=self.fx['foundation'].id, date=old_date).exists())


class DailyFinanceViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_finance_fixture()
        make_invoice(self.fx, Decimal('250000.00'))
        refresh_daily_finance(scope='full')

    def test_daily_finance_report_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/reporting/daily-finance/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['billed'], '250000.00')

    def test_cross_tenant_school_returns_403(self):
        fx_b = build_finance_fixture(foundation_name="Yayasan Reporting Finance B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(f'/api/v1/reporting/daily-finance/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 403)
