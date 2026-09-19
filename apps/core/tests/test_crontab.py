"""deploy/crontab is the only place schedules live (ARC-013): keep it in step with the commands on disk."""
import re
from pathlib import Path

from django.conf import settings
from django.core.management import get_commands, load_command_class
from django.test import SimpleTestCase

from apps.core.management.base import CronHostCommand

CRONTAB = Path(settings.BASE_DIR) / 'deploy' / 'crontab'

# Cron-class commands that are deliberately not scheduled.
NOT_SCHEDULED = {
    'sample_cron_job': 'exercise target for the CronHostCommand tests, not a real job',
}


def scheduled_commands():
    return set(re.findall(r'manage\.py\s+(\w+)', CRONTAB.read_text(encoding='utf-8')))


class CrontabTests(SimpleTestCase):
    def test_every_scheduled_command_exists(self):
        missing = sorted(scheduled_commands() - set(get_commands()))
        self.assertEqual(missing, [], f"deploy/crontab schedules commands that do not exist: {missing}")

    def test_every_cron_command_is_scheduled_or_allowlisted(self):
        scheduled = scheduled_commands()
        unscheduled = []
        for name, app in get_commands().items():
            if not app.startswith(('apps.', 'educore')) or name in scheduled or name in NOT_SCHEDULED:
                continue
            if isinstance(load_command_class(app, name), CronHostCommand):
                unscheduled.append(name)
        self.assertEqual(
            sorted(unscheduled), [],
            f"CronHostCommand subclasses missing from deploy/crontab (schedule them, or add to NOT_SCHEDULED with a reason): {sorted(unscheduled)}",
        )

    def test_stagger_offsets_are_unique(self):
        offsets = re.findall(r'sleep\s+(\d+);', CRONTAB.read_text(encoding='utf-8'))
        duplicates = sorted({o for o in offsets if offsets.count(o) > 1}, key=int)
        self.assertEqual(duplicates, [], f"duplicate sleep offsets in deploy/crontab: {duplicates}")
