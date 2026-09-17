import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.academic.models import ClassEnrollment, ClassGroup, ClassSubject, DayOfWeek, Subject, TimetableSlot
from apps.academic.services import (
    PeriodGridMismatchError,
    TimetableConflictError,
    assign_substitution,
    create_timetable_slot,
    get_expected_periods_for_school,
    get_period_grid,
    set_period_grid,
)
from apps.attendance.services import submit_period_attendance
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

    def test_assign_substitution_notifies_substitute(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        sub = assign_substitution(self.slot, datetime.date(2026, 8, 3), self.substitute, reason="Sakit")

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.SUBSTITUTE_ASSIGNED,
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.recipient_user_id, self.substitute.user_id)
        self.assertEqual(intent.payload['class_group'], self.fx['class_group'].name)
        self.assertEqual(intent.payload['original_teacher'], self.fx['teacher'].person.full_name)
        self.assertEqual(intent.payload['type'], 'SUBSTITUTE_ASSIGNED')
        self.assertEqual(intent.payload['substitution_id'], sub.id)
        self.assertEqual(intent.payload['slot_id'], self.slot.id)

    def test_notification_failure_does_not_block_substitution_assignment(self):
        from unittest.mock import patch

        with patch('apps.notifications.services.dispatch_intent', side_effect=RuntimeError("boom")):
            sub = assign_substitution(self.slot, datetime.date(2026, 8, 3), self.substitute, reason="Sakit")
        self.assertEqual(sub.substitute_teacher, self.substitute)


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


def make_second_class_subject(fx):
    """A second class group + subject + teacher in the same school, for school-level rollup tests."""
    other_person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik='3471010101010888', full_name='Bu Wati')
    other_user = User.objects.create(foundation_id=fx['foundation'].id, phone_e164='+628188888880', email='wati@cendekia.sch.id', full_name='Bu Wati')
    other_teacher = Staff.all_tenants.create(
        foundation_id=fx['foundation'].id, person=other_person, user=other_user,
        school=fx['school'], join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
    )
    other_class_group = ClassGroup.objects.create(
        foundation_id=fx['foundation'].id, school=fx['school'], academic_year=fx['academic_year'],
        grade_level=10, name="X IPA 2", homeroom_teacher=other_teacher,
    )
    other_subject = Subject.objects.create(
        foundation_id=fx['foundation'].id, school=fx['school'], code='FIS', name='Fisika',
    )
    return ClassSubject.objects.create(
        foundation_id=fx['foundation'].id, class_group=other_class_group, subject=other_subject,
        teacher=other_teacher, term=fx['term'],
    )


class ExpectedPeriodsForSchoolTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.monday = datetime.date(2026, 8, 3)
        assert self.monday.isoweekday() == DayOfWeek.MONDAY
        self.slot1 = create_timetable_slot(
            class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        other_class_subject = make_second_class_subject(self.fx)
        self.slot2 = create_timetable_slot(
            class_subject=other_class_subject, day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R2',
        )

    def test_includes_all_class_groups_not_just_one_teacher(self):
        expected = get_expected_periods_for_school(self.fx['school'], self.monday)
        self.assertEqual(len(expected), 2)
        class_groups = {e['class_group'] for e in expected}
        self.assertEqual(class_groups, {'X IPA 1', 'X IPA 2'})

    def test_substitution_shows_substitute_teacher(self):
        other_person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3471010101010777', full_name='Pak Eko')
        other_user = User.objects.create(foundation_id=self.fx['foundation'].id, phone_e164='+628177777770', email='eko@cendekia.sch.id', full_name='Pak Eko')
        substitute = Staff.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=other_person, user=other_user,
            school=self.fx['school'], join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
        )
        assign_substitution(self.slot1, self.monday, substitute, reason="Sakit")

        expected = get_expected_periods_for_school(self.fx['school'], self.monday)
        entry = next(e for e in expected if e['slot_id'] == self.slot1.id)
        self.assertTrue(entry['is_substitution'])
        self.assertEqual(entry['teacher_id'], substitute.id)

    def test_attendance_submitted_reflects_real_submission(self):
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        expected = get_expected_periods_for_school(self.fx['school'], self.monday)
        entry = next(e for e in expected if e['slot_id'] == self.slot1.id)
        self.assertFalse(entry['attendance_submitted'])

        submit_period_attendance(self.fx['teacher'], self.slot1, self.monday, {})
        expected = get_expected_periods_for_school(self.fx['school'], self.monday)
        entry = next(e for e in expected if e['slot_id'] == self.slot1.id)
        self.assertTrue(entry['attendance_submitted'])

    def test_missing_only_excludes_submitted(self):
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        submit_period_attendance(self.fx['teacher'], self.slot1, self.monday, {})

        missing = get_expected_periods_for_school(self.fx['school'], self.monday, missing_only=True)
        slot_ids = {e['slot_id'] for e in missing}
        self.assertNotIn(self.slot1.id, slot_ids)
        self.assertIn(self.slot2.id, slot_ids)

    def test_wrong_weekday_empty(self):
        tuesday = self.monday + datetime.timedelta(days=1)
        self.assertEqual(get_expected_periods_for_school(self.fx['school'], tuesday), [])


class ExpectedPeriodsViewTests(TestCase):
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
        self.monday = datetime.date(2026, 8, 3)
        create_timetable_slot(
            class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )

    def test_expected_periods_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/expected-periods/?date={self.monday.isoformat()}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['periods']), 1)

    def test_missing_date_param_returns_400(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/expected-periods/')
        self.assertEqual(res.status_code, 400)
