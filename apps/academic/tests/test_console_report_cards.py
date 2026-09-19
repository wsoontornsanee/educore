"""Web console: Rapor pages (report card list, detail, print view). Spec:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md (PR 4)."""
import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import AcademicYear, ClassGroup, ReportCard, ReportCardStatus, Term
from apps.academic.services import generate_report_cards
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.academic.tests.test_report_cards import enroll_and_grade
from apps.identity.models import Person, RoleAssignment, Student
from educore.middleware.tenancy import set_current_foundation_id

LIST_URL = '/web/academic/report-cards/'


class ReportCardConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Rapor Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.card = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628133000005", "wali@rapor.test", "Wali Rapor",
            "3471010101022005", student=self.fx['student'],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher.user)

    def _set_status(self, status):
        ReportCard.all_tenants.filter(id=self.card.id).update(status=status)

    # --- list -------------------------------------------------------

    def test_list_shows_card_and_status_counts(self):
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Andi Wijaya')
        self.assertContains(res, 'X-001')
        self.assertContains(res, f'/web/academic/report-cards/{self.card.id}/')
        counts = {c['status']: c['count'] for c in res.context['status_counts']}
        self.assertEqual(counts[ReportCardStatus.DRAFT], 1)
        self.assertEqual(counts[ReportCardStatus.PUBLISHED], 0)
        self.assertEqual(res.context['total_count'], 1)

    def test_status_counts_follow_card_status(self):
        self._set_status(ReportCardStatus.PUBLISHED)
        res = self.client.get(LIST_URL)
        counts = {c['status']: c['count'] for c in res.context['status_counts']}
        self.assertEqual(counts[ReportCardStatus.PUBLISHED], 1)
        self.assertEqual(counts[ReportCardStatus.DRAFT], 0)

    def _old_term_card(self):
        old_year = AcademicYear.objects.create(
            foundation_id=self.foundation.id, school=self.school, name='2025/2026',
            start_date=datetime.date(2025, 7, 1), end_date=datetime.date(2026, 6, 30), is_active=False,
        )
        old_term = Term.objects.create(
            foundation_id=self.foundation.id, academic_year=old_year, name='Semester Lama', term_no=1,
            start_date=datetime.date(2025, 7, 1), end_date=datetime.date(2025, 12, 20),
        )
        old_class = ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=self.school, academic_year=old_year,
            grade_level=9, name='IX Lama', homeroom_teacher=self.teacher,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Siswa Lama')
        student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person, nis='L-001',
            status=Student.STATUS_ACTIVE,
        )
        return old_term, old_class, ReportCard.objects.create(
            foundation_id=self.foundation.id, student=student, term=old_term, class_group=old_class,
        )

    def test_default_term_is_active_year_and_empty_term_means_all(self):
        old_term, _cls, _card = self._old_term_card()
        res = self.client.get(LIST_URL)
        self.assertContains(res, 'Andi Wijaya')
        self.assertNotContains(res, 'Siswa Lama')
        res = self.client.get(LIST_URL, {'term': ''})
        self.assertContains(res, 'Andi Wijaya')
        self.assertContains(res, 'Siswa Lama')
        res = self.client.get(LIST_URL, {'term': old_term.id})
        self.assertNotContains(res, 'Andi Wijaya')
        self.assertContains(res, 'Siswa Lama')

    def test_class_filter(self):
        _term, old_class, _card = self._old_term_card()
        res = self.client.get(LIST_URL, {'term': '', 'class_group': old_class.id})
        self.assertContains(res, 'Siswa Lama')
        self.assertNotContains(res, 'Andi Wijaya')
        self.assertEqual(self.client.get(LIST_URL, {'class_group': 'abc'}).status_code, 200)

    def test_superseded_and_soft_deleted_cards_excluded(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(is_current=False)
        self.assertNotContains(self.client.get(LIST_URL), 'Andi Wijaya')
        ReportCard.all_tenants.filter(id=self.card.id).update(is_current=True, deleted_at=timezone.now())
        self.assertNotContains(self.client.get(LIST_URL), 'Andi Wijaya')

    def test_empty_state(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(deleted_at=timezone.now())
        res = self.client.get(LIST_URL)
        self.assertContains(res, 'Belum ada rapor')

    def test_row_cap_shows_truncation_note(self):
        self._old_term_card()
        with mock.patch('apps.academic.console_views.REPORT_CARD_PAGE_SIZE', 1):
            res = self.client.get(LIST_URL, {'term': ''})
        self.assertEqual(len(res.context['report_cards']), 1)
        self.assertContains(res, 'Menampilkan 1 rapor dari 2.')

    def test_list_query_count_does_not_grow_with_cards(self):
        self.client.get(LIST_URL)

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(LIST_URL)
            return len(ctx.captured_queries)

        before = count()
        for i in range(4):
            person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=f'Siswa {i}')
            student = Student.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school, person=person, nis=f'Z-{i}',
                status=Student.STATUS_ACTIVE,
            )
            ReportCard.objects.create(
                foundation_id=self.foundation.id, student=student, term=self.fx['term'],
                class_group=self.fx['class_group'],
            )
        self.assertEqual(count(), before)

    # --- detail / print ---------------------------------------------

    def test_detail_shows_snapshot_grades_attendance_and_print_link(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(
            narrative='Anak yang rajin.', promotion_decision='NAIK KE KELAS XI',
            attendance_summary={'HADIR': 90, 'SAKIT': 2},
        )
        res = self.client.get(f'{LIST_URL}{self.card.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Andi Wijaya')
        self.assertContains(res, 'Matematika')
        self.assertContains(res, '85')
        self.assertContains(res, 'Baik')  # descriptor for 85
        self.assertContains(res, 'Anak yang rajin.')
        self.assertContains(res, 'NAIK KE KELAS XI')
        self.assertContains(res, '90 hari')
        self.assertContains(res, f'/web/academic/report-cards/{self.card.id}/print/')
        # PII never rendered
        self.assertNotContains(res, '1122334455')
        self.assertNotContains(res, '3471010101010202')

    def test_detail_attendance_in_fixed_order_with_indonesian_labels(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(
            attendance_summary={'ALPA': 1, 'SAKIT': 2, 'HADIR': 88},
        )
        res = self.client.get(f'{LIST_URL}{self.card.id}/')
        self.assertEqual(
            [(str(r['label']), r['days']) for r in res.context['attendance_rows']],
            [('Hadir', 88), ('Sakit', 2), ('Tanpa keterangan', 1)],
        )
        self.assertNotContains(res, '(Present)')

    def test_detail_incomplete_grade_row(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(
            grades_snapshot=[{'subject': 'Fisika', 'subject_code': 'FIS', 'status': 'INCOMPLETE', 'grade': None}],
        )
        res = self.client.get(f'{LIST_URL}{self.card.id}/')
        self.assertContains(res, 'Fisika')
        self.assertContains(res, 'Belum lengkap')

    def test_print_view_returns_branded_html(self):
        res = self.client.get(f'{LIST_URL}{self.card.id}/print/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('text/html', res['Content-Type'])
        self.assertContains(res, 'Andi Wijaya')

    def test_detail_and_print_404_for_unknown_and_cross_tenant(self):
        other = build_academic_fixture("Yayasan Lain Rapor")
        set_current_foundation_id(other['foundation'].id)
        enroll_and_grade(other)
        generate_report_cards(other['class_group'], other['term'])
        other_card = ReportCard.all_tenants.get(foundation_id=other['foundation'].id)
        set_current_foundation_id(self.foundation.id)
        for suffix in ('', 'print/'):
            self.assertEqual(self.client.get(f'{LIST_URL}999999/{suffix}').status_code, 404)
            self.assertEqual(self.client.get(f'{LIST_URL}{other_card.id}/{suffix}').status_code, 404)

    def test_other_foundation_cards_never_leak_into_list(self):
        other = build_academic_fixture("Yayasan Lain Rapor 2")
        set_current_foundation_id(other['foundation'].id)
        enroll_and_grade(other)
        generate_report_cards(other['class_group'], other['term'])
        Person.all_tenants.filter(foundation_id=other['foundation'].id, full_name='Andi Wijaya').update(full_name='RAHASIA-LAIN')
        set_current_foundation_id(self.foundation.id)
        self.assertNotContains(self.client.get(LIST_URL, {'term': ''}), 'RAHASIA-LAIN')

    # --- gating -----------------------------------------------------

    def test_pages_reject_guardian_without_staff_profile_and_anonymous(self):
        urls = [LIST_URL, f'{LIST_URL}{self.card.id}/', f'{LIST_URL}{self.card.id}/print/']
        self.client.force_authenticate(user=self.guardian_user)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.client.force_authenticate(user=None)
        for url in urls:
            self.assertIn(self.client.get(url).status_code, (401, 403), url)
