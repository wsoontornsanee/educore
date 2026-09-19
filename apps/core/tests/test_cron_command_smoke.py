"""Every line of deploy/crontab runs, exactly as cron runs it, against a tenant that has a school.

`reconcile_payments` shipped calling a field that does not exist (`foundation.name`) and crashed on its
first output line for every active foundation, because no test ever executed the command. The
service functions were tested; the command wrappers, their real arguments and their per-foundation /
per-school loops were not. This runs each scheduled invocation once so that cannot recur unnoticed.

It only proves each job starts, walks an (empty) tenant and exits without raising; what each job
does with real data belongs in that job's own tests.
"""
import shlex
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase

from apps.identity.models import Foundation, School
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

CRONTAB = Path(settings.BASE_DIR) / 'deploy' / 'crontab'

# Invocations that need something a unit-test run does not have. Each needs a reason; a stale entry
# (no longer in the crontab) fails `test_skip_list_has_no_stale_entries`.
SKIPPED = {
    'backup_database': 'refuses to run without DATABASE_BACKUP_KEY and shells out to mysqldump; see test_backup_database',
}


def cron_invocations():
    """The argv of every `manage.py ...` line in deploy/crontab (trailing comments stripped)."""
    invocations = []
    for line in CRONTAB.read_text(encoding='utf-8').splitlines():
        if line.lstrip().startswith('#') or 'manage.py' not in line:
            continue
        invocations.append(shlex.split(line.split('manage.py', 1)[1].split('#', 1)[0]))
    return invocations


class CronCommandSmokeTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cron Smoke", brand_name="Cron Smoke", npwp="01.555.444.3-222.000", address="Yogyakarta",
        )
        set_current_foundation_id(self.foundation.id)
        School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Cron Smoke", npsn="70155501", level=School.LEVEL_SD,
            base_currency="IDR",
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_crontab_yields_a_plausible_number_of_invocations(self):
        # Guards the parser: an empty list would make the real test pass vacuously.
        self.assertGreater(len(cron_invocations()), 20)

    def test_every_scheduled_invocation_runs_without_raising(self):
        for argv in cron_invocations():
            if argv[0] in SKIPPED:
                continue
            with self.subTest(command=' '.join(argv)):
                # A savepoint per invocation: one command's DB error must not poison the rest.
                with transaction.atomic():
                    call_command(*argv, '--force', stdout=StringIO(), stderr=StringIO())

    def test_skip_list_has_no_stale_entries(self):
        scheduled = {argv[0] for argv in cron_invocations()}
        self.assertEqual(sorted(set(SKIPPED) - scheduled), [], "SKIPPED names commands no longer in deploy/crontab")
