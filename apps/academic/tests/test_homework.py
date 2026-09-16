import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import Guardian, GuardianLink, Person, RoleAssignment, User
from apps.academic.models import ClassEnrollment, Homework, HomeworkSubmissionStatus
from apps.academic.services import (
    HomeworkSubmissionStateError,
    InvalidSubmissionFilesError,
    ReasonRequiredError,
    ReminderRateLimitedError,
    assign_homework,
    get_homework_completion,
    grade_homework_submission,
    remind_unsubmitted,
    return_homework_submission,
    store_homework_submission_file,
    submit_homework,
)
from apps.academic.tests.base import build_academic_fixture


def attach_guardian(fx, with_user=True, nik='3471010101015555', full_name='Bu Guardian'):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=full_name)
    user = None
    if with_user:
        user = User.objects.create(
            foundation_id=fx['foundation'].id, phone_e164=f"+62819{nik[-7:]}", email=f"{nik}@parent.id", full_name=full_name,
        )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
    )
    return guardian


def make_homework(fx, due_delta_hours):
    return Homework.objects.create(
        foundation_id=fx['foundation'].id,
        class_subject=fx['class_subject'],
        title="Latihan Aljabar",
        instructions="Kerjakan soal 1-10",
        assigned_at=timezone.now(),
        due_at=timezone.now() + datetime.timedelta(hours=due_delta_hours),
    )


class SubmitHomeworkTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_on_time_submission_status_submitted(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        sub = submit_homework(hw, self.fx['student'], text="Selesai")
        self.assertEqual(sub.status, HomeworkSubmissionStatus.SUBMITTED)

    def test_late_submission_status_late(self):
        hw = make_homework(self.fx, due_delta_hours=-1)
        sub = submit_homework(hw, self.fx['student'], text="Terlambat")
        self.assertEqual(sub.status, HomeworkSubmissionStatus.LATE)

    def test_too_many_files_rejected(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        files = [{'filename': f'f{i}.pdf', 'size': 100, 'content_type': 'application/pdf'} for i in range(6)]
        with self.assertRaises(InvalidSubmissionFilesError):
            submit_homework(hw, self.fx['student'], files=files)

    def test_oversized_file_rejected(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        files = [{'filename': 'big.pdf', 'size': 21 * 1024 * 1024, 'content_type': 'application/pdf'}]
        with self.assertRaises(InvalidSubmissionFilesError):
            submit_homework(hw, self.fx['student'], files=files)

    def test_unsupported_content_type_rejected(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        files = [{'filename': 'x.exe', 'size': 100, 'content_type': 'application/x-msdownload'}]
        with self.assertRaises(InvalidSubmissionFilesError):
            submit_homework(hw, self.fx['student'], files=files)

    def test_resubmission_updates_same_row(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        submit_homework(hw, self.fx['student'], text="v1")
        sub2 = submit_homework(hw, self.fx['student'], text="v2")
        self.assertEqual(sub2.text, "v2")
        self.assertEqual(sub2.homework.submissions.count(), 1)


class GradeHomeworkTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.hw = make_homework(self.fx, due_delta_hours=24)
        self.sub = submit_homework(self.hw, self.fx['student'], text="Selesai")

    def test_grade_sets_status_graded(self):
        graded = grade_homework_submission(self.sub, score=Decimal('90'), feedback="Bagus")
        self.assertEqual(graded.status, HomeworkSubmissionStatus.GRADED)
        self.assertEqual(graded.score, Decimal('90.00'))


class ReturnHomeworkSubmissionTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.hw = make_homework(self.fx, due_delta_hours=24)
        self.sub = submit_homework(self.hw, self.fx['student'], text="Selesai")

    def test_returns_ungraded_submission(self):
        returned = return_homework_submission(self.sub, feedback="Tolong perbaiki soal nomor 3.")
        self.assertEqual(returned.status, HomeworkSubmissionStatus.RETURNED)
        self.assertEqual(returned.feedback, "Tolong perbaiki soal nomor 3.")
        self.assertIsNone(returned.score)

    def test_returns_graded_submission_and_clears_score(self):
        grade_homework_submission(self.sub, score=Decimal('60'), feedback="Kurang lengkap")
        self.sub.refresh_from_db()
        returned = return_homework_submission(self.sub, feedback="Kerjakan ulang bagian B.")
        self.assertEqual(returned.status, HomeworkSubmissionStatus.RETURNED)
        self.assertIsNone(returned.score)

    def test_requires_feedback(self):
        with self.assertRaises(ReasonRequiredError):
            return_homework_submission(self.sub, feedback="")

    def test_cannot_return_already_returned(self):
        return_homework_submission(self.sub, feedback="Perbaiki.")
        self.sub.refresh_from_db()
        with self.assertRaises(HomeworkSubmissionStateError):
            return_homework_submission(self.sub, feedback="Lagi.")


class ResubmitAfterReturnTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.hw = make_homework(self.fx, due_delta_hours=24)
        self.sub = submit_homework(self.hw, self.fx['student'], text="Versi 1")

    def test_resubmit_allowed_after_returned(self):
        return_homework_submission(self.sub, feedback="Perbaiki.")
        self.sub.refresh_from_db()
        resubmitted = submit_homework(self.hw, self.fx['student'], text="Versi 2")
        self.assertEqual(resubmitted.status, HomeworkSubmissionStatus.SUBMITTED)
        self.assertEqual(resubmitted.text, "Versi 2")

    def test_resubmit_blocked_after_graded(self):
        grade_homework_submission(self.sub, score=Decimal('80'))
        self.sub.refresh_from_db()
        with self.assertRaises(HomeworkSubmissionStateError):
            submit_homework(self.hw, self.fx['student'], text="Versi 2")

    def test_resubmit_allowed_while_still_submitted(self):
        resubmitted = submit_homework(self.hw, self.fx['student'], text="Versi 1 edited")
        self.assertEqual(resubmitted.id, self.sub.id)
        self.assertEqual(resubmitted.text, "Versi 1 edited")


class CompletionAndReminderTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.hw = make_homework(self.fx, due_delta_hours=24)
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_completion_counts(self):
        result = get_homework_completion(self.hw)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(result['not_started'], 1)
        self.assertEqual(result['missing'], 1)
        self.assertEqual(result['graded'], 0)
        self.assertEqual(result['late'], 0)

        submit_homework(self.hw, self.fx['student'])
        result = get_homework_completion(self.hw)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['submitted'], 1)
        self.assertEqual(result['not_started'], 0)
        self.assertEqual(result['missing'], 0)

    def test_reminder_rate_limited(self):
        result = remind_unsubmitted(self.hw)
        self.assertEqual(result['count'], 1)

        with self.assertRaises(ReminderRateLimitedError):
            remind_unsubmitted(self.hw)


class HomeworkViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_create_and_submit_flow(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/homework/', {
            'class_subject': self.fx['class_subject'].id,
            'title': 'PR Bab 3',
            'instructions': 'Kerjakan halaman 40-42',
            'assigned_at': timezone.now().isoformat(),
            'due_at': (timezone.now() + datetime.timedelta(days=2)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        hw_id = res.json()['id']

        res_submit = self.client.post(f'/api/v1/academic/homework/{hw_id}/submissions/', {
            'student_id': self.fx['student'].id,
            'text': 'Sudah selesai',
        }, format='json')
        self.assertEqual(res_submit.status_code, 201, res_submit.content)

        res_list = self.client.get(f'/api/v1/academic/homework/{hw_id}/submissions/')
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.json()), 1)

        res_completion = self.client.get(f'/api/v1/academic/homework/{hw_id}/completion/')
        self.assertEqual(res_completion.json()['total'], 1)
        self.assertEqual(res_completion.json()['submitted'], 1)
        self.assertEqual(res_completion.json()['not_started'], 0)

    def test_remind_rate_limited_returns_429(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res1 = self.client.post(f'/api/v1/academic/homework/{hw.id}/remind/')
        self.assertEqual(res1.status_code, 200)
        res2 = self.client.post(f'/api/v1/academic/homework/{hw.id}/remind/')
        self.assertEqual(res2.status_code, 429)

    def test_grade_submission_via_api(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        sub = submit_homework(hw, self.fx['student'], text="Selesai")
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/academic/homework-submissions/{sub.id}/grade/', {
            'score': '88', 'feedback': 'Baik',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], HomeworkSubmissionStatus.GRADED)

    def test_return_submission_via_api(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        sub = submit_homework(hw, self.fx['student'], text="Selesai")
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/academic/homework-submissions/{sub.id}/return/', {
            'feedback': 'Tolong perbaiki bagian analisis.',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], HomeworkSubmissionStatus.RETURNED)

    def test_return_without_feedback_returns_400(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        sub = submit_homework(hw, self.fx['student'], text="Selesai")
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/academic/homework-submissions/{sub.id}/return/', {
            'feedback': '',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_resubmit_after_grade_returns_400_via_api(self):
        hw = make_homework(self.fx, due_delta_hours=24)
        sub = submit_homework(hw, self.fx['student'], text="Selesai")
        grade_homework_submission(sub, score=Decimal('75'))
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/academic/homework/{hw.id}/submissions/', {
            'student_id': self.fx['student'].id,
            'text': 'Versi baru',
        }, format='json')
        self.assertEqual(res.status_code, 400)


class HomeworkNotificationTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        self.guardian = attach_guardian(self.fx)

    def test_assign_homework_notifies_guardian(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        homework = assign_homework(
            class_subject=self.fx['class_subject'], title="Latihan Aljabar", instructions="Soal 1-10",
            assigned_at=timezone.now(), due_at=timezone.now() + datetime.timedelta(hours=48),
        )

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.HOMEWORK,
            template_key='academic.homework.assigned',
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.recipient_user_id, self.guardian.user_id)
        self.assertEqual(intent.payload['title'], homework.title)

    def test_guardian_without_user_not_notified(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        no_user_guardian = attach_guardian(self.fx, with_user=False, nik='3471010101016666', full_name='Pak Tanpa Akun')

        assign_homework(
            class_subject=self.fx['class_subject'], title="Latihan Aljabar", instructions="",
            assigned_at=timezone.now(), due_at=timezone.now() + datetime.timedelta(hours=48),
        )

        intents = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.HOMEWORK,
            template_key='academic.homework.assigned',
        )
        # Only the guardian WITH a user account gets notified; the accountless one is
        # silently skipped, and no duplicate/extra intent is created for them.
        self.assertEqual(intents.count(), 1)

    def test_reminder_only_notifies_unsubmitted_students_guardians(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        homework = make_homework(self.fx, due_delta_hours=24)
        submit_homework(homework, self.fx['student'], text="Selesai")

        remind_unsubmitted(homework)

        intents = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.HOMEWORK,
            template_key='academic.homework.reminder',
        )
        self.assertEqual(intents.count(), 0)

    def test_reminder_notifies_guardian_of_unsubmitted_student(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        homework = make_homework(self.fx, due_delta_hours=24)
        remind_unsubmitted(homework)

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.HOMEWORK,
            template_key='academic.homework.reminder',
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.recipient_user_id, self.guardian.user_id)

    def test_notification_failure_does_not_block_homework_creation(self):
        from unittest.mock import patch

        with patch('apps.notifications.services.dispatch_intent', side_effect=RuntimeError("boom")):
            homework = assign_homework(
                class_subject=self.fx['class_subject'], title="Latihan Aljabar", instructions="",
                assigned_at=timezone.now(), due_at=timezone.now() + datetime.timedelta(hours=48),
            )
        self.assertIsNotNone(homework.id)


class HomeworkCreateViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        self.guardian = attach_guardian(self.fx)

    def test_create_homework_via_api_notifies_guardian(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/homework/', {
            'class_subject': self.fx['class_subject'].id,
            'title': 'Latihan Aljabar',
            'instructions': 'Soal 1-10',
            'assigned_at': timezone.now().isoformat(),
            'due_at': (timezone.now() + datetime.timedelta(hours=48)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.HOMEWORK,
            template_key='academic.homework.assigned',
        ).first()
        self.assertIsNotNone(intent)


class HomeworkFileUploadTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.homework = make_homework(self.fx, due_delta_hours=48)

    def test_valid_pdf_upload_stored_on_disk(self):
        from django.conf import settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        from pathlib import Path

        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        meta = store_homework_submission_file(self.homework, upload)

        self.assertEqual(meta['filename'], 'tugas.pdf')
        self.assertEqual(meta['content_type'], 'application/pdf')
        self.assertTrue(meta['key'].startswith(f'homework_submissions/{self.homework.id}/'))

        stored_path = Path(settings.MEDIA_ROOT) / meta['key']
        self.assertTrue(stored_path.exists())
        self.assertEqual(stored_path.read_bytes(), b'%PDF-1.4 fake content')
        stored_path.unlink()

    def test_oversized_file_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.academic.services import MAX_SUBMISSION_FILE_SIZE

        upload = SimpleUploadedFile('big.pdf', b'x' * 10, content_type='application/pdf')
        upload.size = MAX_SUBMISSION_FILE_SIZE + 1  # simulate an oversized file without allocating it

        with self.assertRaises(InvalidSubmissionFilesError):
            store_homework_submission_file(self.homework, upload)

    def test_disallowed_content_type_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/x-msdownload')
        with self.assertRaises(InvalidSubmissionFilesError):
            store_homework_submission_file(self.homework, upload)

    def test_returned_key_round_trips_through_submit_homework(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        meta = store_homework_submission_file(self.homework, upload)

        submission = submit_homework(self.homework, self.fx['student'], text="Selesai", files=[meta])
        self.assertEqual(submission.files, [meta])


class HomeworkFileUploadViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.homework = make_homework(self.fx, due_delta_hours=48)

    def test_upload_file_via_api(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=self.fx['teacher_user'])
        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        res = self.client.post(
            f'/api/v1/academic/homework/{self.homework.id}/upload-file/', {'file': upload}, format='multipart',
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['filename'], 'tugas.pdf')

    def test_cross_tenant_upload_returns_404(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fx_b = build_academic_fixture(foundation_name="Yayasan Upload B")
        RoleAssignment.all_tenants.create(
            foundation_id=fx_b['foundation'].id, user=fx_b['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=fx_b['school'].id,
        )
        self.client.force_authenticate(user=fx_b['teacher_user'])
        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4', content_type='application/pdf')
        res = self.client.post(
            f'/api/v1/academic/homework/{self.homework.id}/upload-file/', {'file': upload}, format='multipart',
        )
        self.assertEqual(res.status_code, 404)
