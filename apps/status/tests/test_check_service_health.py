import io
from unittest.mock import patch, MagicMock

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


class CheckServiceHealthProviderProbesIntegrationTests(TestCase):
    @override_settings(XENDIT_API_KEY='test-key', WHATSAPP_API_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='')
    @patch('apps.status.probes.requests.get')
    def test_command_records_real_payments_signal_end_to_end(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        call_command('check_service_health')
        payments = ServiceComponent.objects.get(key='payments')
        hb = ComponentHeartbeat.objects.filter(component=payments).latest('checked_at')
        self.assertEqual(hb.status, ServiceComponent.STATUS_OPERATIONAL)
        daily = DailyComponentStatus.objects.get(component=payments)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)
        # notifications had no credentials configured -> DB-only fallback, unaffected by the mock.
        notifications = ServiceComponent.objects.get(key='notifications')
        self.assertTrue(
            ComponentHeartbeat.objects.filter(component=notifications).exists()
        )
