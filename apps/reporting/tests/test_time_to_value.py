import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import GateDirection, GateEvent, GateEventStatus, GateMethod
from apps.finance.models import Invoice, InvoiceStatus
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Guardian, GuardianLink, Person, Student, User, UserActivityDay
from apps.reporting.health import get_health_metrics
from apps.reporting.models import RptParentWeeklyActivity


def day(n):
    return datetime.date(2026, 8, 1) + datetime.timedelta(days=n)


class TimeToValueTests(TestCase):
    """RPT-016: days from contract to first gate scan, first invoice, first parent login, 50% activation."""

    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.fid = self.foundation.id
        self.school = self.fx['school']
        self.foundation.contract_date = day(0)
        self.foundation.save()
        self.device = Device.objects.create(
            foundation_id=self.fid, school=self.school, name='Gerbang', device_code='GT-TTV',
            device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN, status=DeviceStatus.ONLINE,
        )
        self.students = [self.fx['student']]
        self._enrol(self.fx['student'])

    def _enrol(self, student):
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=student, class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def _sibling(self, suffix):
        person = Person.all_tenants.create(foundation_id=self.fid, nik=f'34710101010{suffix}', full_name='Murid')
        student = Student.all_tenants.create(
            foundation_id=self.fid, school=self.school, person=person,
            nisn=f'55443322{suffix}', nis=f'X-{suffix}', status=Student.STATUS_ACTIVE,
        )
        self._enrol(student)
        self.students.append(student)
        return student

    def _parent(self, phone, student):
        user = User.all_tenants.create_user(phone_e164=phone, full_name='Wali', foundation_id=self.fid)
        person = Person.all_tenants.create(foundation_id=self.fid, full_name='Wali Murid')
        guardian = Guardian.all_tenants.create(foundation_id=self.fid, person=person, user=user)
        GuardianLink.all_tenants.create(foundation_id=self.fid, guardian=guardian, student=student)
        return user

    def _seen(self, user, on):
        UserActivityDay.all_tenants.create(foundation_id=self.fid, user_id=user.pk, date=on)

    def _scan(self, at, status=GateEventStatus.ACCEPTED):
        GateEvent.objects.create(
            foundation_id=self.fid, school=self.school, device=self.device, student=self.fx['student'],
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, method=GateMethod.RFID,
            occurred_at=at, status=status,
        )

    def _invoice(self, issued, status=InvoiceStatus.ISSUED, suffix='1'):
        Invoice.objects.create(
            foundation_id=self.fid, school=self.school, student=self.fx['student'],
            number=f'INV/TTV/{self.fid}/{suffix}', period=f'2026-0{suffix}', issue_date=issued,
            due_date=issued + datetime.timedelta(days=14), subtotal=Decimal('100000.00'),
            total=Decimal('100000.00'), currency='IDR', status=status,
        )

    def _week(self):
        RptParentWeeklyActivity.all_tenants.update_or_create(
            foundation_id=self.fid, school=self.school, week_start=datetime.date(2026, 8, 3),
            defaults={'active_parents': 0, 'enrolled_students': len(self.students), 'computed_at': timezone.now()},
        )

    def _ttv(self, **kwargs):
        self._week()
        (row,) = get_health_metrics(today=datetime.date(2026, 9, 16), **kwargs)
        return row['time_to_value']

    def test_a_school_with_no_activity_has_no_milestones(self):
        ttv = self._ttv()
        self.assertEqual(ttv['contract_date'], '2026-08-01')
        for key in ('first_gate_scan', 'first_invoice', 'first_parent_login', 'parent_activation_50'):
            self.assertEqual(ttv[key], {'date': None, 'days': None}, key)

    def test_first_gate_scan_is_the_earliest_accepted_scan_in_school_time(self):
        self._scan(timezone.make_aware(datetime.datetime(2026, 8, 12, 8, 0)))
        self._scan(timezone.make_aware(datetime.datetime(2026, 8, 9, 0, 30)))
        self._scan(timezone.make_aware(datetime.datetime(2026, 8, 5, 7, 0)), status=GateEventStatus.REJECTED)
        # 00:30 Jakarta on the 9th is the 8th in UTC; the milestone is the school's calendar day.
        self.assertEqual(self._ttv()['first_gate_scan'], {'date': '2026-08-09', 'days': 8})

    def test_first_invoice_ignores_drafts(self):
        self._invoice(day(3), status=InvoiceStatus.DRAFT, suffix='1')
        self._invoice(day(10), suffix='2')
        self._invoice(day(6), status=InvoiceStatus.CANCELLED, suffix='3')
        self.assertEqual(self._ttv()['first_invoice'], {'date': '2026-08-07', 'days': 6})

    def test_first_parent_login_ignores_staff_and_unlinked_users(self):
        parent = self._parent('+6281200000801', self.fx['student'])
        self._seen(self.fx['teacher_user'], day(1))
        self._seen(parent, day(9))
        self._seen(parent, day(12))
        self.assertEqual(self._ttv()['first_parent_login'], {'date': '2026-08-10', 'days': 9})

    def test_activation_is_reached_when_half_the_students_have_a_parent_who_logged_in(self):
        for suffix in ('01', '02', '03'):
            self._sibling(suffix)  # 4 enrolled students: 50% is 2 parent accounts
        parents = [self._parent(f'+62812000009{i}', s) for i, s in enumerate(self.students)]
        self._seen(parents[0], day(4))
        self._seen(parents[1], day(20))
        self._seen(parents[0], day(21))  # a repeat visit does not add a second account
        ttv = self._ttv()
        self.assertEqual(ttv['first_parent_login']['days'], 4)
        self.assertEqual(ttv['parent_activation_50'], {'date': '2026-08-21', 'days': 20})

    def test_activation_is_not_reached_below_half(self):
        for suffix in ('01', '02', '03'):
            self._sibling(suffix)
        self._seen(self._parent('+6281200000911', self.students[0]), day(4))
        self.assertEqual(self._ttv()['parent_activation_50'], {'date': None, 'days': None})

    def test_an_odd_roster_rounds_the_threshold_up(self):
        for suffix in ('01', '02'):
            self._sibling(suffix)  # 3 students: 50% rounds up to 2 accounts
        parents = [self._parent(f'+62812000008{i}', s) for i, s in enumerate(self.students)]
        self._seen(parents[0], day(2))
        self.assertEqual(self._ttv()['parent_activation_50']['date'], None)
        self._seen(parents[1], day(5))
        self.assertEqual(self._ttv()['parent_activation_50'], {'date': '2026-08-06', 'days': 5})

    def test_without_a_contract_date_the_dates_show_and_the_days_do_not(self):
        self.foundation.contract_date = None
        self.foundation.save()
        self._invoice(day(6))
        ttv = self._ttv()
        self.assertIsNone(ttv['contract_date'])
        self.assertEqual(ttv['first_invoice'], {'date': '2026-08-07', 'days': None})

    def test_another_foundations_activity_is_not_counted(self):
        other = build_academic_fixture('Yayasan Lain')
        Invoice.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], student=other['student'],
            number='INV/TTV/OTHER/1', period='2026-08', issue_date=day(1), due_date=day(15),
            total=Decimal('1.00'), currency='IDR', status=InvoiceStatus.ISSUED,
        )
        self.assertEqual(self._ttv(foundation_id=self.fid)['first_invoice'], {'date': None, 'days': None})
