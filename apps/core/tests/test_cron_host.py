import os
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


class CronHostCommandTests(TestCase):
    """sample_cron_job.py is the canonical CronHostCommand subclass used as the
    exercise target here — the mechanism itself lives in apps.core.management.base.
    """

    def test_runs_normally_when_enforcement_is_off(self):
        # Default test settings: EDUCORE_CRON_HOST_ENFORCED is False.
        call_command('sample_cron_job')  # must not raise

    @override_settings(EDUCORE_CRON_HOST_ENFORCED=True)
    @patch.dict(os.environ, {}, clear=False)
    def test_refuses_when_enforced_and_env_var_unset(self):
        os.environ.pop('EDUCORE_CRON_HOST', None)
        with self.assertRaises(CommandError):
            call_command('sample_cron_job')

    @override_settings(EDUCORE_CRON_HOST_ENFORCED=True)
    @patch.dict(os.environ, {'EDUCORE_CRON_HOST': '1'})
    def test_runs_when_enforced_and_env_var_set(self):
        call_command('sample_cron_job')  # must not raise

    @override_settings(EDUCORE_CRON_HOST_ENFORCED=True)
    @patch.dict(os.environ, {}, clear=False)
    def test_force_flag_overrides_enforcement(self):
        os.environ.pop('EDUCORE_CRON_HOST', None)
        call_command('sample_cron_job', '--force')  # must not raise

    @override_settings(EDUCORE_CRON_HOST_ENFORCED=True)
    @patch.dict(os.environ, {'EDUCORE_CRON_HOST': '0'})
    def test_wrong_env_var_value_still_refuses(self):
        with self.assertRaises(CommandError):
            call_command('sample_cron_job')
