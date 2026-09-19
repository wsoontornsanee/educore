"""ARC-008: platform operators are told when a scheduled job stops succeeding."""
from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import JobRun, TaskQueue
from apps.core.job_health import evaluate_job_health
from apps.identity.models import Foundation, PlatformRoleAssignment, User
from apps.status.services import dispatch_job_alerts
from apps.status.tasks import send_job_alert_email


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.foundation = Foundation.objects.create(legal_name='Ops Test', brand_name='Ops Test')

    def operator(self, phone='+6281200000201', email='ops@example.com'):
        user = User.objects.create(
            phone_e164=phone, full_name='Ops', email=email, is_active=True, foundation_id=self.foundation.id,
        )
        PlatformRoleAssignment.objects.create(user=user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        return user

    def stale_job(self, job='send_digests', hours_ago=30):
        when = timezone.now() - timedelta(hours=hours_ago)
        JobRun.objects.create(
            job_name=job, status=JobRun.STATUS_SUCCESS, started_at=when, finished_at=when,
        )

    def alert_tasks(self):
        return TaskQueue.all_tenants.filter(task_type='status.job_alert_email.send') \
            if hasattr(TaskQueue, 'all_tenants') else TaskQueue.objects.filter(task_type='status.job_alert_email.send')


class DispatchJobAlertsTests(_Base):
    def test_nothing_to_alert_when_every_job_is_healthy_or_never_ran(self):
        self.operator()
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 0)
        self.assertEqual(self.alert_tasks().count(), 0)

    def test_stale_job_enqueues_one_email_per_operator_listing_it(self):
        self.operator()
        self.operator(phone='+6281200000202', email='ops2@example.com')
        self.stale_job()
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 1)
        tasks = list(self.alert_tasks())
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(t.foundation_id is None for t in tasks))
        self.assertIn('send_digests', tasks[0].payload['lines'][0])

    def test_the_same_stale_job_alerts_once_not_every_tick(self):
        self.operator()
        self.stale_job()
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 1)
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 0)
        self.assertEqual(self.alert_tasks().count(), 1)

    def test_a_job_that_recovers_and_goes_stale_again_alerts_again(self):
        self.operator()
        self.stale_job(hours_ago=30)
        dispatch_job_alerts(evaluate_job_health())
        self.stale_job(hours_ago=28)  # a newer success, itself now stale: a different (job, last success) key
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 1)

    def test_no_operator_means_nothing_is_marked_alerted(self):
        self.stale_job()
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 0)
        self.operator()  # first operator appears later: the alert must not be delayed by the earlier dedupe
        self.assertEqual(dispatch_job_alerts(evaluate_job_health()), 1)

    def test_email_handler_lists_jobs_without_error_text(self):
        user = self.operator()
        send_job_alert_email({'user_id': user.id, 'lines': ['send_digests [STALE] cadence daily']})
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['ops@example.com'])
        self.assertIn('1 job terjadwal', sent.subject)
        self.assertIn('send_digests', sent.body)
        self.assertIn('/web/status/manage/', sent.body)

    def test_email_handler_ignores_a_user_removed_since_enqueue(self):
        send_job_alert_email({'user_id': 999999, 'lines': ['x']})
        self.assertEqual(len(mail.outbox), 0)


class CheckJobFreshnessCommandTests(_Base):
    def test_command_alerts_and_records_its_own_jobrun(self):
        self.operator()
        self.stale_job()
        out = StringIO()
        call_command('check_job_freshness', '--force', stdout=out)
        self.assertIn('send_digests: STALE', out.getvalue())
        self.assertEqual(self.alert_tasks().count(), 1)
        row = JobRun.objects.get(job_name='check_job_freshness')
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 1)

    def test_command_is_quiet_when_all_healthy(self):
        self.operator()
        call_command('check_job_freshness', '--force', stdout=StringIO())
        self.assertEqual(self.alert_tasks().count(), 0)


class OpsPageJobHealthTests(_Base):
    def test_ops_page_shows_job_health_and_flags_stale_jobs(self):
        user = self.operator()
        self.client.force_login(user)
        self.stale_job()
        response = self.client.get(reverse('status_manage:page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kesehatan job terjadwal')
        self.assertContains(response, 'send_digests')
        self.assertContains(response, 'Terlambat')
