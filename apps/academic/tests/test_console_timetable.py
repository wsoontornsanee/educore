"""Web console: Jadwal page (weekly timetable grid). Spec:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md (PR 2)."""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.console_views import build_timetable_grid
from apps.academic.models import ClassGroup, ClassSubject, DayOfWeek, Subject, TimetableSlot
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import set_current_foundation_id

URL = '/web/academic/timetable/'


class TimetableConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Jadwal Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.class_group = self.fx['class_group']
        self.class_subject = self.fx['class_subject']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628133000003", "wali@jadwal.test", "Wali Jadwal",
            "3471010101022003", student=self.fx['student'],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher.user)

    def _slot(self, class_subject, day, period, start='07:00', end='07:45', room=''):
        return TimetableSlot.objects.create(
            foundation_id=self.foundation.id, class_subject=class_subject, day_of_week=day,
            period_no=period, start_time=datetime.time.fromisoformat(start),
            end_time=datetime.time.fromisoformat(end), room=room,
        )

    def _other_class(self, name='X IPA 2'):
        class_group = ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=self.school,
            academic_year=self.fx['academic_year'], grade_level=10, name=name,
        )
        subject = Subject.objects.create(
            foundation_id=self.foundation.id, school=self.school, code='FIS', name='Fisika', credit_hours=3,
        )
        class_subject = ClassSubject.objects.create(
            foundation_id=self.foundation.id, class_group=class_group, subject=subject,
            teacher=self.teacher, term=self.fx['term'],
        )
        return class_group, class_subject

    def test_default_lens_is_first_class_and_renders_grid(self):
        self._slot(self.class_subject, DayOfWeek.MONDAY, 1, room='R-101')
        self._slot(self.class_subject, DayOfWeek.WEDNESDAY, 2, '07:45', '08:30')
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'X IPA 1')
        self.assertContains(res, 'Matematika')
        self.assertContains(res, 'Bu Siti Rahayu')
        self.assertContains(res, 'R-101')
        self.assertContains(res, '07:00–07:45')
        self.assertContains(res, 'Senin')
        self.assertNotContains(res, 'Minggu')

    def test_class_lens_excludes_other_classes(self):
        other_class, other_subject = self._other_class()
        self._slot(self.class_subject, DayOfWeek.MONDAY, 1)
        self._slot(other_subject, DayOfWeek.TUESDAY, 1)
        res = self.client.get(URL, {'lens': f'class:{other_class.id}'})
        self.assertContains(res, 'Fisika')
        self.assertNotContains(res, 'Matematika')

    def test_teacher_lens_shows_class_and_subject_across_classes(self):
        other_class, other_subject = self._other_class()
        self._slot(self.class_subject, DayOfWeek.MONDAY, 1)
        self._slot(other_subject, DayOfWeek.TUESDAY, 2, '07:45', '08:30')
        res = self.client.get(URL, {'lens': f'teacher:{self.teacher.id}'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'X IPA 1')
        self.assertContains(res, 'X IPA 2')
        self.assertContains(res, 'Matematika')
        self.assertContains(res, 'Fisika')

    def test_malformed_lens_falls_back_to_default(self):
        self._slot(self.class_subject, DayOfWeek.MONDAY, 1)
        res = self.client.get(URL, {'lens': 'bogus'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Matematika')

    def test_empty_state_when_class_has_no_slots(self):
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Belum ada jadwal')

    def test_soft_deleted_slot_hidden(self):
        slot = self._slot(self.class_subject, DayOfWeek.MONDAY, 1)
        TimetableSlot.all_tenants.filter(id=slot.id).update(deleted_at=timezone.now())
        self.assertContains(self.client.get(URL), 'Belum ada jadwal')

    def test_unknown_and_cross_tenant_lens_return_404(self):
        other = build_academic_fixture("Yayasan Lain Jadwal")
        set_current_foundation_id(self.foundation.id)
        self.assertEqual(self.client.get(URL, {'lens': 'class:999999'}).status_code, 404)
        self.assertEqual(self.client.get(URL, {'lens': f'class:{other["class_group"].id}'}).status_code, 404)
        self.assertEqual(self.client.get(URL, {'lens': f'teacher:{other["teacher"].id}'}).status_code, 404)

    def test_other_foundation_slots_never_leak(self):
        other = build_academic_fixture("Yayasan Lain Jadwal 2")
        TimetableSlot.all_tenants.create(
            foundation_id=other['foundation'].id, class_subject=other['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 45), room='RAHASIA-LAIN',
        )
        set_current_foundation_id(self.foundation.id)
        self.assertNotContains(self.client.get(URL), 'RAHASIA-LAIN')

    def test_rejects_guardian_without_staff_profile_and_anonymous(self):
        self.client.force_authenticate(user=self.guardian_user)
        self.assertEqual(self.client.get(URL).status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))


class BuildTimetableGridTests(TestCase):
    """Pure grid assembly, no DB rows needed beyond unsaved instances."""

    def _slot(self, day, period, start, end):
        return TimetableSlot(
            day_of_week=day, period_no=period,
            start_time=datetime.time.fromisoformat(start), end_time=datetime.time.fromisoformat(end),
        )

    def test_rows_ordered_by_period_and_cells_aligned_to_days(self):
        mon_p2 = self._slot(DayOfWeek.MONDAY, 2, '07:45', '08:30')
        fri_p1 = self._slot(DayOfWeek.FRIDAY, 1, '07:00', '07:30')
        mon_p1 = self._slot(DayOfWeek.MONDAY, 1, '07:00', '07:45')
        grid = build_timetable_grid([mon_p2, fri_p1, mon_p1])
        self.assertEqual([d.value for d in grid['days']], [1, 2, 3, 4, 5, 6])
        self.assertEqual([r['period_no'] for r in grid['rows']], [1, 2])
        row1 = grid['rows'][0]
        self.assertEqual(row1['cells'][0], [mon_p1])   # Monday
        self.assertEqual(row1['cells'][4], [fri_p1])   # Friday
        self.assertEqual(row1['cells'][1], [])
        # Period 1 spans the widest window across its slots
        self.assertEqual(row1['start_time'], datetime.time(7, 0))
        self.assertEqual(row1['end_time'], datetime.time(7, 45))

    def test_sunday_column_only_when_a_slot_exists(self):
        self.assertEqual(len(build_timetable_grid([])['days']), 6)
        grid = build_timetable_grid([self._slot(DayOfWeek.SUNDAY, 1, '08:00', '09:00')])
        self.assertEqual(len(grid['days']), 7)
