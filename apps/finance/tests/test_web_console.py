"""Keuangan web console pages (/web/finance/): RBAC gate, tenancy, school scope, content."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.finance.models import (
    Discount,
    DiscountStatus,
    GatewaySettlementBatch,
    Invoice,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    Payment,
    PaymentDiscrepancy,
    PaymentStatus,
)
from apps.finance.templatetags.finance_extras import money
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, Student, User

PAGES = ['finance-console-billing', 'finance-console-reconciliation', 'finance-console-receivables']


def make_foundation(tag):
    foundation = Foundation.objects.create(legal_name=f'F{tag}', brand_name=f'F{tag}')
    schools = [
        School.objects.create(foundation_id=foundation.id, name=f'Sekolah {tag}{i}', npsn=f'{tag}000000{i}', level=School.LEVEL_SMA)
        for i in (1, 2)
    ]
    return foundation, schools


def make_student(foundation, school, name, nis):
    person = Person.objects.create(foundation_id=foundation.id, full_name=name)
    return Student.objects.create(
        foundation_id=foundation.id, school=school, person=person, nis=nis, status=Student.STATUS_ACTIVE,
    )


def make_invoice(foundation, student, number, total='1500000.00', paid='0.00', status=InvoiceStatus.ISSUED, due_days=-10, period='2026-08'):
    return Invoice.objects.create(
        foundation_id=foundation.id, school=student.school, student=student, number=number, period=period,
        issue_date=timezone.localdate() - datetime.timedelta(days=40),
        due_date=timezone.localdate() + datetime.timedelta(days=due_days),
        total=Decimal(total), paid=Decimal(paid), status=status,
    )


def make_user(foundation, phone, role, scope_type, scope_id, staff_school=None, name='Finance'):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    user.set_password('pw12345')
    user.save()
    RoleAssignment.objects.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    if staff_school:
        person = Person.objects.create(foundation_id=foundation.id, full_name=name)
        Staff.objects.create(
            foundation_id=foundation.id, person=person, user=user, school=staff_school, join_date=timezone.localdate(),
        )
    return user


class FinanceConsoleBase(TestCase):
    def setUp(self):
        self.foundation, (self.school1, self.school2) = make_foundation('A')
        self.s1 = make_student(self.foundation, self.school1, 'Budi Satu', '0001')
        self.s2 = make_student(self.foundation, self.school2, 'Sari Dua', '0002')
        self.inv1 = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        self.inv2 = make_invoice(self.foundation, self.s2, 'INV/A2/2026/000001', total='2000000.00', paid='500000.00',
                                 status=InvoiceStatus.PARTIALLY_PAID)
        self.admin = make_user(
            self.foundation, '+6281300009001', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1,
        )


class FinanceConsoleAccessTests(FinanceConsoleBase):
    def test_anonymous_redirected_to_login(self):
        for name in PAGES:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/web/login/', response['Location'])

    def test_finance_officer_can_open_all_pages(self):
        self.client.force_login(self.admin)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_user_without_finance_permission_gets_403(self):
        teacher = make_user(
            self.foundation, '+6281300009002', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_finance_permission_without_staff_profile_gets_403(self):
        no_staff = make_user(
            self.foundation, '+6281300009003', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, name='Ortu',
        )
        self.client.force_login(no_staff)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)


class BillingConsoleTests(FinanceConsoleBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _billing(self, **params):
        return self.client.get(reverse('finance-console-billing'), params)

    def test_lists_invoices_with_server_side_totals(self):
        response = self._billing()
        self.assertContains(response, 'INV/A1/2026/000001')
        self.assertContains(response, 'INV/A2/2026/000001')
        totals = response.context['totals']
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]['billed'], Decimal('3500000.00'))
        self.assertEqual(totals[0]['collected'], Decimal('500000.00'))
        self.assertEqual(totals[0]['outstanding'], Decimal('3000000.00'))
        self.assertContains(response, 'Rp 3.500.000')

    def test_status_filter(self):
        response = self._billing(status=InvoiceStatus.PARTIALLY_PAID)
        self.assertContains(response, 'INV/A2/2026/000001')
        self.assertNotContains(response, 'INV/A1/2026/000001')

    def test_search_by_student_name(self):
        response = self._billing(q='Budi')
        self.assertContains(response, 'INV/A1/2026/000001')
        self.assertNotContains(response, 'INV/A2/2026/000001')

    def test_bad_filters_are_ignored(self):
        response = self._billing(status='HACK', period='not-a-period')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filters'], {'status': '', 'period': '', 'q': ''})

    def test_overdue_open_invoice_is_flagged(self):
        response = self._billing()
        badges = {row['invoice'].number: row['badge'] for row in response.context['rows']}
        self.assertEqual(badges['INV/A1/2026/000001'], 'JATUH_TEMPO')

    def test_paginates(self):
        for i in range(30):
            make_invoice(self.foundation, self.s1, f'INV/A1/2027/{i:06d}', period=f'{2000 + i // 12}-{i % 12 + 1:02d}')
        response = self._billing()
        self.assertEqual(len(response.context['rows']), 25)
        self.assertEqual(len(self._billing(page=2).context['rows']), 7)

    def test_shows_recent_payments(self):
        Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school1, student=self.s1, invoice=self.inv1,
            amount=Decimal('750000.00'), reference='PAY/A1/2026/000001', channel='CASHIER',
            status=PaymentStatus.SETTLED,
        )
        response = self._billing()
        self.assertContains(response, 'PAY/A1/2026/000001')
        self.assertContains(response, 'Rp 750.000')

    def test_cross_tenant_invoices_never_shown(self):
        other, (other_school, _) = make_foundation('B')
        other_student = make_student(other, other_school, 'Orang Lain', '9001')
        make_invoice(other, other_student, 'INV/B1/2026/000001')
        response = self._billing()
        self.assertNotContains(response, 'INV/B1/2026/000001')
        self.assertNotContains(response, 'Orang Lain')

    def test_school_scoped_officer_sees_only_own_school(self):
        scoped = make_user(
            self.foundation, '+6281300009004', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Scoped',
        )
        self.client.force_login(scoped)
        response = self._billing()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INV/A1/2026/000001')
        self.assertNotContains(response, 'INV/A2/2026/000001')
        self.assertEqual(response.context['totals'][0]['billed'], Decimal('1500000.00'))


class ReconciliationConsoleTests(FinanceConsoleBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
            total_records=10, matched_count=8, missing_count=1, mismatch_count=1,
        )
        PaymentDiscrepancy.objects.create(
            foundation_id=self.foundation.id, batch=self.batch, external_id='EXT-777',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('123000.00'),
        )

    def test_lists_batches(self):
        response = self.client.get(reverse('finance-console-reconciliation'))
        self.assertContains(response, 'XENDIT')
        self.assertNotContains(response, 'EXT-777')

    def test_selected_batch_shows_discrepancies(self):
        response = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertContains(response, 'EXT-777')
        self.assertContains(response, 'Rp 123.000')

    def test_other_tenants_batch_is_not_selectable(self):
        other, _ = make_foundation('B')
        foreign = GatewaySettlementBatch.objects.create(
            foundation_id=other.id, provider='MIDTRANS', settlement_date=datetime.date(2026, 9, 1),
        )
        response = self.client.get(reverse('finance-console-reconciliation'), {'batch': foreign.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected'])
        self.assertNotContains(response, 'MIDTRANS')

    def test_non_numeric_batch_param_ignored(self):
        response = self.client.get(reverse('finance-console-reconciliation'), {'batch': "1' OR 1=1"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected'])

    def test_requires_payment_read_not_just_invoice_read(self):
        # school_admin lacks finance.payment.read per RBAC table; if it ever gains it this test documents the gate key.
        self.assertEqual(
            __import__('apps.finance.web_views', fromlist=['x']).ReconciliationConsoleView.required_permission,
            'finance.payment.read',
        )


class ReceivablesConsoleTests(FinanceConsoleBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_shows_aging_summary_and_top_debtors(self):
        response = self.client.get(reverse('finance-console-receivables'))
        report = response.context['report']
        # 1.5M (10 days overdue) + 1.5M balance on inv2 (2M - 500k)
        self.assertEqual(report['summary']['total_outstanding'], '3000000.00')
        self.assertEqual({d['student_name'] for d in response.context['debtors']}, {'Budi Satu', 'Sari Dua'})
        self.assertContains(response, 'Sari Dua')
        self.assertContains(response, 'Rp 3.000.000')

    def test_pending_discount_and_write_off_listed(self):
        Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s1, type='PERCENT', value=Decimal('50.00'),
            reason='Beasiswa Tahfidz', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=self.inv2, school=self.school2,
            amount=Decimal('1500000.00'), reason='Siswa pindah tanpa jejak', requested_by=self.admin,
        )
        response = self.client.get(reverse('finance-console-receivables'))
        self.assertContains(response, 'Beasiswa Tahfidz')
        self.assertContains(response, 'Siswa pindah tanpa jejak')

    def test_approved_discount_not_listed(self):
        Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s1, type='FIXED', value=Decimal('100000.00'),
            reason='Sudah Disetujui', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.APPROVED,
        )
        response = self.client.get(reverse('finance-console-receivables'))
        self.assertNotContains(response, 'Sudah Disetujui')

    def test_school_scope_limits_report_and_pending_items(self):
        Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s2, type='PERCENT', value=Decimal('10.00'),
            reason='Milik Sekolah Dua', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        scoped = make_user(
            self.foundation, '+6281300009005', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Scoped',
        )
        self.client.force_login(scoped)
        response = self.client.get(reverse('finance-console-receivables'))
        self.assertEqual(response.context['report']['summary']['total_outstanding'], '1500000.00')
        self.assertNotContains(response, 'Sari Dua')
        self.assertNotContains(response, 'Milik Sekolah Dua')

    def test_cross_tenant_debt_never_counted(self):
        other, (other_school, _) = make_foundation('B')
        other_student = make_student(other, other_school, 'Orang Lain', '9001')
        make_invoice(other, other_student, 'INV/B1/2026/000001', total='9000000.00')
        response = self.client.get(reverse('finance-console-receivables'))
        self.assertEqual(response.context['report']['summary']['total_outstanding'], '3000000.00')
        self.assertNotContains(response, 'Orang Lain')


class MoneyFilterTests(TestCase):
    def test_idr_has_no_decimals_and_dot_grouping(self):
        self.assertEqual(money('1500000.00', 'IDR'), 'Rp 1.500.000')
        self.assertEqual(money(Decimal('0'), 'IDR'), 'Rp 0')

    def test_foreign_currency_two_decimals_id_grouping(self):
        self.assertEqual(money('1234.5', 'USD'), 'USD 1.234,50')

    def test_garbage_renders_empty(self):
        self.assertEqual(money('abc'), '')
        self.assertEqual(money(None), '')


class KeuanganNavTests(FinanceConsoleBase):
    def test_finance_officer_sees_all_three_keuangan_items(self):
        from apps.identity.nav import get_nav_for_user
        nav = get_nav_for_user(self.admin, self.foundation.id)
        keuangan = next(group for group in nav if str(group['label']) == 'Keuangan')
        self.assertEqual(
            [item['url_name'] for item in keuangan['items']],
            ['finance-console-billing', 'finance-console-reconciliation', 'finance-console-receivables'],
        )
