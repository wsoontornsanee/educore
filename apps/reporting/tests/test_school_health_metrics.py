import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.academic.models import AcademicCalendarEvent, ClassEnrollment, TimetableSlot
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import PeriodAttendance
from apps.finance.models import Invoice, InvoiceStatus, Payment, PaymentAllocation, PaymentStatus
from apps.hardware.models import DeviceUptimeDay
from apps.reporting.models import RptParentWeeklyActivity
from apps.reporting.services import _week_start, refresh_parent_weekly_activity
from apps.wallet.models import Merchant, MerchantType, POSTransaction, POSTransactionStatus

DAY = datetime.timedelta(days=1)
WEEK = datetime.timedelta(weeks=1)


class SchoolHealthMetricsTests(TestCase):
    """RPT-013 columns of rpt_parent_weekly_activity: collection, attendance submission, gate uptime, canteen."""

    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.school = self.fx['school']
        self.student = self.fx['student']
        self.today = timezone.localdate()
        self.week = _week_start(self.today) - WEEK      # a completed week
        self.week_end = self.week + datetime.timedelta(days=6)
        self.fx['term'].__class__.objects.filter(pk=self.fx['term'].pk).update(
            start_date=self.today - 400 * DAY, end_date=self.today + 400 * DAY,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=self.student, class_group=self.fx['class_group'],
            enrolled_at=self.today - 200 * DAY,
        )

    def _row(self, week=None):
        return RptParentWeeklyActivity.all_tenants.get(foundation_id=self.fid, school=self.school, week_start=week or self.week)

    def _refresh(self):
        return refresh_parent_weekly_activity(scope='full')

    def _at(self, day, hour=10):
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)))

    # --- collection rate ---------------------------------------------------------------------------------

    def _invoice(self, suffix, total, due, status=InvoiceStatus.ISSUED, currency='IDR'):
        return Invoice.objects.create(
            foundation_id=self.fid, school=self.school, student=self.student, number=f"INV/RH/{self.fid}/{suffix}",
            period=f"2026-{suffix:>02}", issue_date=due - 14 * DAY, due_date=due, subtotal=Decimal(total),
            total=Decimal(total), currency=currency, status=status,
        )

    def _pay(self, invoice, amount, settled_on, status=PaymentStatus.SETTLED):
        payment = Payment.objects.create(
            foundation_id=self.fid, school=self.school, student=self.student, invoice=invoice, amount=Decimal(amount),
            method='CASH', channel='CASHIER', reference=f"PAY/RH/{self.fid}/{invoice.pk}/{amount}/{settled_on}",
            status=status, settled_at=self._at(settled_on),
        )
        PaymentAllocation.objects.create(
            foundation_id=self.fid, payment=payment, invoice=invoice, amount=Decimal(amount), currency='IDR',
        )

    def test_collection_is_settled_by_week_end_over_invoices_due_in_the_week(self):
        inv = self._invoice('01', '1000000.00', self.week + 2 * DAY, status=InvoiceStatus.PARTIALLY_PAID)
        self._pay(inv, '400000.00', self.week + DAY)
        self._invoice('02', '500000.00', self.week + 4 * DAY)
        self._refresh()
        row = self._row()
        self.assertEqual((row.collection_billed, row.collection_collected), (Decimal('1500000.00'), Decimal('400000.00')))

    def test_a_payment_after_the_week_ended_does_not_count(self):
        inv = self._invoice('01', '1000000.00', self.week + DAY, status=InvoiceStatus.PAID)
        self._pay(inv, '1000000.00', self.week_end + 2 * DAY)
        self._refresh()
        self.assertEqual(self._row().collection_collected, Decimal('0.00'))

    def test_drafts_cancelled_pending_payments_and_other_currencies_are_left_out(self):
        self._invoice('01', '100.00', self.week + DAY, status=InvoiceStatus.DRAFT)
        self._invoice('02', '100.00', self.week + DAY, status=InvoiceStatus.CANCELLED)
        self._invoice('03', '100.00', self.week + DAY, currency='USD')
        inv = self._invoice('04', '200.00', self.week + DAY)
        self._pay(inv, '200.00', self.week + DAY, status=PaymentStatus.PENDING)
        self._refresh()
        row = self._row()
        self.assertEqual((row.collection_billed, row.collection_collected), (Decimal('200.00'), Decimal('0.00')))

    # --- attendance-submission compliance ----------------------------------------------------------------

    def _slot(self, day, period_no=1):
        return TimetableSlot.objects.create(
            foundation_id=self.fid, class_subject=self.fx['class_subject'], day_of_week=day.isoweekday(),
            period_no=period_no, start_time=datetime.time(7, 0), end_time=datetime.time(7, 45),
        )

    def _submit(self, slot, day):
        PeriodAttendance.objects.create(foundation_id=self.fid, student=self.student, slot=slot, date=day)

    def test_compliance_counts_periods_with_any_attendance_submitted(self):
        monday, tuesday = self.week, self.week + DAY
        slot_mon, slot_tue = self._slot(monday), self._slot(tuesday)
        self._submit(slot_mon, monday)
        self._refresh()
        row = self._row()
        self.assertEqual((row.attendance_expected_periods, row.attendance_submitted_periods), (2, 1))
        self.assertIsNotNone(slot_tue)

    def test_a_calendar_holiday_excuses_the_periods(self):
        monday = self.week
        self._slot(monday)
        AcademicCalendarEvent.objects.create(
            foundation_id=self.fid, school=self.school, title='Libur', event_type='HOLIDAY',
            start_at=self._at(monday, 0), end_at=self._at(monday, 23), is_all_day=True, affects_attendance=True,
        )
        self._refresh()
        self.assertEqual(self._row().attendance_expected_periods, 0)

    def test_a_term_that_has_not_started_yet_expects_nothing(self):
        self._slot(self.week)
        self.fx['term'].__class__.objects.filter(pk=self.fx['term'].pk).update(start_date=self.today + DAY)
        self._refresh()
        self.assertEqual(self._row().attendance_expected_periods, 0)

    def test_the_in_progress_week_only_expects_days_before_today(self):
        current = _week_start(self.today)
        for offset in range(7):
            self._slot(current + offset * DAY, period_no=offset + 1)
        self._refresh()
        self.assertEqual(self._row(current).attendance_expected_periods, (self.today - current).days)

    # --- gate uptime -------------------------------------------------------------------------------------

    def test_gate_uptime_parts_are_summed_over_the_week(self):
        for offset, (samples, up) in enumerate([(10, 9), (10, 10)]):
            DeviceUptimeDay.all_tenants.create(
                foundation_id=self.fid, school=self.school, date=self.week + offset * DAY, samples=samples, up_samples=up,
            )
        DeviceUptimeDay.all_tenants.create(
            foundation_id=self.fid, school=self.school, date=self.week_end + DAY, samples=99, up_samples=0,
        )
        self._refresh()
        row = self._row()
        self.assertEqual((row.gate_samples, row.gate_up_samples), (20, 19))

    # --- canteen adoption --------------------------------------------------------------------------------

    def _sale(self, suffix, day, merchant_type=MerchantType.CANTEEN, status=POSTransactionStatus.COMPLETED):
        merchant, _ = Merchant.objects.get_or_create(
            foundation_id=self.fid, school=self.school, name=f"M-{merchant_type}", defaults={'type': merchant_type},
        )
        POSTransaction.objects.create(
            foundation_id=self.fid, merchant=merchant, student=self.student, subtotal=Decimal('5000.00'),
            total=Decimal('5000.00'), occurred_at=self._at(day), status=status, client_transaction_id=suffix,
        )

    def test_a_student_counts_once_however_many_canteen_purchases(self):
        self._sale('a', self.week)
        self._sale('b', self.week + DAY)
        self._refresh()
        self.assertEqual(self._row().canteen_active_students, 1)

    def test_voided_other_merchant_and_out_of_week_sales_do_not_count(self):
        self._sale('a', self.week, status=POSTransactionStatus.VOIDED)
        self._sale('b', self.week, merchant_type=MerchantType.UNIFORM)
        self._sale('c', self.week_end + DAY)
        self._refresh()
        self.assertEqual(self._row().canteen_active_students, 0)

    # --- freezing ----------------------------------------------------------------------------------------

    def test_a_week_computed_after_it_ended_is_frozen(self):
        self._sale('a', self.week)
        self._refresh()
        self.assertEqual(self._row().canteen_active_students, 1)
        self._sale('b', self.week + DAY)
        POSTransaction.objects.filter(client_transaction_id='a').update(status=POSTransactionStatus.VOIDED)
        self._refresh()
        self.assertEqual(self._row().canteen_active_students, 1)

    def test_weeks_computed_before_the_health_columns_existed_get_them_without_touching_the_wau_part(self):
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.school, week_start=self.week, active_parents=7, enrolled_students=99,
            computed_at=self._at(self.week_end + 2 * DAY),
        )
        self._sale('a', self.week)
        self._refresh()
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (7, 99))   # frozen WAU part kept
        self.assertEqual(row.canteen_active_students, 1)                          # health part filled in
        self.assertIsNotNone(row.health_computed_at)

    def test_the_in_progress_week_is_recomputed_every_run(self):
        current = _week_start(self.today)
        self._refresh()
        self.assertEqual(self._row(current).canteen_active_students, 0)
        self._sale('a', self.today)
        self._refresh()
        self.assertEqual(self._row(current).canteen_active_students, 1)
