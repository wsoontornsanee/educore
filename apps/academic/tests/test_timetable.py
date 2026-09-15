import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.academic.models import DayOfWeek, TimetableSlot
from apps.academic.services import (
    TimetableConflictError,
    assign_substitution,
    create_timetable_slot,
)
from apps.academic.tests.base import build_academic_fixture


class TimetableConflictTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_create_slot_succeeds(self):
        slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY,
            period_no=1,
            start_time=datetime.time(7, 0),
            end_time=datetime.time(7, 40),
            room='X-IPA-1',
        )
        self.assertEqual(slot.period_no, 1)

    def test_class_double_booked_rejected(self):
        create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        # Same class_group, different subject but same period -> class double-booked.
        from apps.academic.models import ClassSubject, Subject
        other_subject = Subject.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], code='FIS', name='Fisika',
        )
        other_class_subject = ClassSubject.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_group=self.fx['class_group'],
            subject=other_subject,
            teacher=self.fx['teacher'],
            term=self.fx['term'],
        )
        with self.assertRaises(TimetableConflictError):
            create_timetable_slot(
                class_subject=other_class_subject,
                day_of_week=DayOfWeek.MONDAY, period_no=1,
                start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R2',
            )

    def test_teacher_double_booked_rejected(self):
        create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        from apps.academic.models import ClassGroup, ClassSubject
        other_class_group = ClassGroup.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            academic_year=self.fx['academic_year'],
            grade_level=10,
            name='X IPA 2',
        )
        other_class_subject = ClassSubject.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_group=other_class_group,
            subject=self.fx['subject'],
            teacher=self.fx['teacher'],  # same teacher
            term=self.fx['term'],
        )
        with self.assertRaises(TimetableConflictError):
            create_timetable_slot(
                class_subject=other_class_subject,
                day_of_week=DayOfWeek.MONDAY, period_no=1,
                start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R2',
            )

    def test_room_double_booked_rejected(self):
        create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        from apps.academic.models import ClassGroup, ClassSubject
        other_class_group = ClassGroup.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            academic_year=self.fx['academic_year'],
            grade_level=10,
            name='X IPA 3',
        )
        other_teacher_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101010303', full_name='Pak Budi',
        )
        other_teacher_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+628122222222', email='budi@cendekia.sch.id', full_name='Pak Budi',
        )
        other_teacher = Staff.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=other_teacher_person, user=other_teacher_user,
            school=self.fx['school'], join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
        )
        from apps.academic.models import ClassSubject as CS
        other_class_subject = CS.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_group=other_class_group,
            subject=self.fx['subject'],
            teacher=other_teacher,
            term=self.fx['term'],
        )
        with self.assertRaises(TimetableConflictError):
            create_timetable_slot(
                class_subject=other_class_subject,
                day_of_week=DayOfWeek.MONDAY, period_no=1,
                start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
            )

    def test_different_period_no_conflict(self):
        create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        slot2 = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=2,
            start_time=datetime.time(7, 40), end_time=datetime.time(8, 20), room='R1',
        )
        self.assertEqual(slot2.period_no, 2)


class SubstitutionTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        sub_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik='3471010101010404', full_name='Bu Wati',
        )
        sub_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+628133333333', email='wati@cendekia.sch.id', full_name='Bu Wati',
        )
        self.substitute = Staff.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=sub_person, user=sub_user,
            school=self.fx['school'], join_date=datetime.date(2022, 1, 1), status=Staff.STATUS_ACTIVE,
        )

    def test_assign_substitution(self):
        sub = assign_substitution(self.slot, datetime.date(2026, 8, 3), self.substitute, reason="Sakit")
        self.assertEqual(sub.substitute_teacher, self.substitute)
        self.assertEqual(sub.original_teacher, self.fx['teacher'])


class TimetableViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_create_slot_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/timetable/slots/', {
            'class_subject': self.fx['class_subject'].id,
            'day_of_week': DayOfWeek.MONDAY,
            'period_no': 1,
            'start_time': '07:00:00',
            'end_time': '07:40:00',
            'room': 'X-IPA-1',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)

    def test_conflict_via_api_returns_400(self):
        create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/timetable/slots/', {
            'class_subject': self.fx['class_subject'].id,
            'day_of_week': DayOfWeek.MONDAY,
            'period_no': 1,
            'start_time': '07:00:00',
            'end_time': '07:40:00',
            'room': 'R2',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('CLASS_DOUBLE_BOOKED', res.json()['error'])
