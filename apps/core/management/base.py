"""Shared base class for every cron-scheduled management command (spec/01 §7, ARC-013).

Production runs exactly one dedicated cron host, identified by EDUCORE_CRON_HOST=1.
A command refuses to run anywhere else unless --force is passed — this prevents the
same job firing redundantly on every gunicorn app-server host if cron is ever
misconfigured to run there too. Off in local/staging (EDUCORE_CRON_HOST_ENFORCED is
False there) since both are single-VM per spec/01 §7's own environment table, where
the constraint is trivially satisfied.
"""
import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class CronHostCommand(BaseCommand):
    """Every cron-scheduled command should subclass this instead of BaseCommand
    directly. Subclasses that define their own `add_arguments` MUST call
    `super().add_arguments(parser)` first to keep the `--force` flag.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help="Run even if this host is not the designated EDUCORE_CRON_HOST.",
        )

    def execute(self, *args, **options):
        if getattr(settings, 'EDUCORE_CRON_HOST_ENFORCED', False) and not options.get('force'):
            if os.environ.get('EDUCORE_CRON_HOST') != '1':
                raise CommandError(
                    "Refusing to run: this host is not the designated cron host "
                    "(EDUCORE_CRON_HOST=1 not set, spec/01 §7 ARC-013). Pass --force to override."
                )
        return super().execute(*args, **options)
