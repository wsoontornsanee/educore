import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.academic.models import DayOfWeek, TimetableSlot
from apps.academic.services import (
    PeriodGridMismatchError,
    TimetableConflictError,
    assign_substitution,
    create_timetable_slot,
    get_period_grid,
    set_period_grid,
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


class PeriodGridTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_no_grid_configured_allows_any_period(self):
        self.assertEqual(get_period_grid(self.fx['school'], DayOfWeek.MONDAY), [])
        slot = create_timetable_slot(
            class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=7,
            start_time=datetime.time(12, 0), end_time=datetime.time(12, 40), room='R1',
        )
        self.assertEqual(slot.period_no, 7)

    def test_set_period_grid_replaces_whole_set(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 40)},
            {'period_no': 2, 'start_time': datetime.time(7, 40), 'end_time': datetime.time(8, 20)},
        ])
        grid = get_period_grid(self.fx['school'], DayOfWeek.MONDAY)
        self.assertEqual([p.period_no for p in grid], [1, 2])

        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(6, 30), 'end_time': datetime.time(7, 10)},
        ])
        grid = get_period_grid(self.fx['school'], DayOfWeek.MONDAY)
        self.assertEqual(len(grid), 1)
        self.assertEqual(grid[0].start_time, datetime.time(6, 30))

    def test_set_period_grid_scoped_per_day(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 40)},
        ])
        self.assertEqual(get_period_grid(self.fx['school'], DayOfWeek.FRIDAY), [])

    def test_slot_matching_grid_period_succeeds(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 40)},
            {'period_no': 2, 'start_time': datetime.time(7, 40), 'end_time': datetime.time(8, 0), 'is_break': True, 'label': 'Istirahat'},
        ])
        slot = create_timetable_slot(
            class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        self.assertEqual(slot.period_no, 1)

    def test_slot_on_break_period_rejected(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 20), 'is_break': True, 'label': 'Istirahat'},
        ])
        with self.assertRaises(PeriodGridMismatchError):
            create_timetable_slot(
                class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
                start_time=datetime.time(7, 0), end_time=datetime.time(7, 20), room='R1',
            )

    def test_slot_on_unconfigured_period_rejected(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 40)},
        ])
        with self.assertRaises(PeriodGridMismatchError):
            create_timetable_slot(
                class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=9,
                start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
            )

    def test_slot_with_mismatched_time_rejected(self):
        set_period_grid(self.fx['school'], DayOfWeek.MONDAY, [
            {'period_no': 1, 'start_time': datetime.time(7, 0), 'end_time': datetime.time(7, 40)},
        ])
        with self.assertRaises(PeriodGridMismatchError):
            create_timetable_slot(
                class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
                start_time=datetime.time(7, 5), end_time=datetime.time(7, 45), room='R1',
            )


class PeriodGridViewsTests(TestCase):
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

    def test_put_then_get_period_grid_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.put(
            f'/api/v1/academic/schools/{self.fx["school"].id}/period-grid/?day_of_week={DayOfWeek.MONDAY}',
            {'periods': [
                {'period_no': 1, 'start_time': '07:00:00', 'end_time': '07:40:00'},
                {'period_no': 2, 'start_time': '07:40:00', 'end_time': '08:00:00', 'is_break': True, 'label': 'Istirahat'},
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['periods']), 2)

        res2 = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/period-grid/?day_of_week={DayOfWeek.MONDAY}')
        self.assertEqual(res2.status_code, 200, res2.content)
        self.assertEqual(len(res2.json()['periods']), 2)

    def test_missing_day_of_week_returns_400(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/period-grid/')
        self.assertEqual(res.status_code, 400)

    def test_cross_tenant_period_grid_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan Grid B")
        RoleAssignment.all_tenants.create(
            foundation_id=fx_b['foundation'].id,
            user=fx_b['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=fx_b['school'].id,
        )
        self.client.force_authenticate(user=fx_b['teacher_user'])
        res = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/period-grid/?day_of_week={DayOfWeek.MONDAY}')
        # HasRequiredPermission's own school-scope check catches this before the view's
        # own foundation-filtered lookup runs — 403, not 404 (matches the permission
        # class's fail-closed behavior for any schools/:school_id/ config endpoint).
        self.assertEqual(res.status_code, 403)
