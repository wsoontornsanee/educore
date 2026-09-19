import datetime

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Guardian, GuardianLink, Person, Student, User, UserActivityDay
from apps.reporting.management.commands.refresh_reporting import REFRESHERS
from apps.reporting.models import RptParentWeeklyActivity
from apps.reporting.services import _week_start, refresh_parent_weekly_activity


class WeekStartTests(SimpleTestCase):
    def test_monday_maps_to_itself_and_sunday_to_the_previous_monday(self):
        self.assertEqual(_week_start(datetime.date(2026, 9, 14)), datetime.date(2026, 9, 14))  # Monday
        self.assertEqual(_week_start(datetime.date(2026, 9, 20)), datetime.date(2026, 9, 14))  # Sunday


class RefreshParentWeeklyActivityTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.today = timezone.localdate()
        self.week = _week_start(self.today)
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        self.parent = self._parent('+6281200000601', self.fx['student'])

    def _parent(self, phone, student):
        user = User.all_tenants.create_user(phone_e164=phone, full_name='Wali', foundation_id=self.fid)
        person = Person.all_tenants.create(foundation_id=self.fid, full_name='Wali Murid')
        guardian = Guardian.all_tenants.create(foundation_id=self.fid, person=person, user=user)
        GuardianLink.all_tenants.create(foundation_id=self.fid, guardian=guardian, student=student)
        self.guardian = guardian
        return user

    def _active(self, user, day):
        UserActivityDay.all_tenants.create(foundation_id=self.fid, user_id=user.pk, date=day)

    def _row(self, week=None):
        return RptParentWeeklyActivity.all_tenants.get(
            foundation_id=self.fid, school=self.fx['school'], week_start=week or self.week,
        )

    def test_registered_with_refresh_reporting(self):
        self.assertIn(refresh_parent_weekly_activity, [fn for _name, fn in REFRESHERS])

    def test_counts_a_linked_parent_active_this_week(self):
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (1, 1))

    def test_staff_activity_is_not_counted(self):
        self._active(self.fx['teacher_user'], self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        self.assertEqual(self._row().active_parents, 0)

    def test_parent_of_an_inactive_student_is_not_counted_and_denominator_drops(self):
        self.fx['student'].status = Student.STATUS_INACTIVE
        self.fx['student'].save()
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (0, 0))

    def test_one_parent_of_two_children_counts_once(self):
        person = Person.all_tenants.create(foundation_id=self.fid, nik='3471010101019999', full_name='Adik')
        sibling = Student.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], person=person,
            nisn='5544332299', nis='X-009', status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=sibling,
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.fid, guardian=self.guardian, student=sibling,
        )
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (1, 2))

    def test_last_weeks_activity_does_not_count_this_week(self):
        self._active(self.parent, self.week - datetime.timedelta(days=1))
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row().active_parents, 0)
        self.assertEqual(self._row(self.week - datetime.timedelta(weeks=1)).active_parents, 1)

    def test_dashboard_scope_touches_only_the_current_week(self):
        refresh_parent_weekly_activity(scope='dashboard')
        self.assertEqual(RptParentWeeklyActivity.all_tenants.count(), 1)

    def test_full_scope_backfills_the_last_eight_weeks(self):
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(RptParentWeeklyActivity.all_tenants.count(), 8)

    def test_a_week_computed_after_it_ended_is_frozen(self):
        previous = self.week - datetime.timedelta(weeks=1)
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=previous,
            active_parents=99, enrolled_students=99, computed_at=timezone.now(),
        )
        self._active(self.parent, previous)
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row(previous).active_parents, 99)

    def test_a_week_computed_before_it_ended_is_recomputed_once(self):
        previous = self.week - datetime.timedelta(weeks=1)
        computed_mid_week = timezone.make_aware(datetime.datetime.combine(previous + datetime.timedelta(days=2), datetime.time(12)))
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=previous,
            active_parents=0, enrolled_students=1, computed_at=computed_mid_week,
        )
        self._active(self.parent, previous + datetime.timedelta(days=6))
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row(previous).active_parents, 1)
        self.assertGreater(timezone.localdate(self._row(previous).computed_at), previous + datetime.timedelta(days=6))
