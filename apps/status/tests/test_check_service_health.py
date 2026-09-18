import io
from django.core.management import call_command
from django.test import TestCase, override_settings
from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.status.models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent


@override_settings(EDUCORE_CRON_HOST_ENFORCED=False)
class CheckServiceHealthCommandTests(TestCase):
    def setUp(self):
        # Clear seed components from migration 0002 so tests see only 2 components.
        ServiceComponent.objects.all().delete()
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2')

    def test_command_writes_heartbeats_and_rollup_and_job_run(self):
        out = io.StringIO()
        expected_count = ServiceComponent.objects.count()
        call_command('check_service_health', stdout=out)
        self.assertEqual(ComponentHeartbeat.objects.count(), expected_count)
        self.assertEqual(DailyComponentStatus.objects.count(), expected_count)
        job_run = JobRun.objects.filter(job_name='check_service_health').latest('id')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, expected_count)

    def test_command_skips_when_lock_held(self):
        out = io.StringIO()
        with advisory_lock('check_service_health', timeout=0):
            call_command('check_service_health', stdout=out)
        self.assertEqual(ComponentHeartbeat.objects.count(), 0)
        self.assertIn('already held', out.getvalue())
