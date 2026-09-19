"""Check that someone can receive stale-job alerts; optionally send each of them a real test email (ARC-008)."""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

from apps.status.services import classify_platform_operators


class Command(BaseCommand):
    help = (
        "List platform operators and who can receive job alerts. Fails when nobody can. "
        "--send-test emails each reachable operator directly (not through the task queue), "
        "so a broken mail configuration shows up here as an error."
    )

    def add_arguments(self, parser):
        parser.add_argument('--send-test', action='store_true', help="Send one test alert email to each reachable operator.")

    def handle(self, *args, **options):
        reachable, unreachable = classify_platform_operators()
        for user in reachable:
            self.stdout.write(f"  can receive alerts: {user.full_name}")
        for user in unreachable:
            self.stdout.write(self.style.WARNING(f"  cannot (no email or inactive): {user.full_name}"))
        if not reachable:
            raise CommandError(
                "No platform operator can receive job alerts. Grant one with "
                "`manage.py grant_platform_operator --email ...` (the user needs an email address)."
            )
        if options['send_test']:
            for user in reachable:
                send_mail(
                    "[EduCore Ops] Uji peringatan job terjadwal",
                    "Ini email uji. Bila Anda menerimanya, peringatan job terjadwal yang macet akan sampai ke alamat ini.\n",
                    settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
                )
                self.stdout.write(self.style.SUCCESS(f"  test email sent to {user.full_name}"))
        self.stdout.write(self.style.SUCCESS(f"{len(reachable)} operator(s) can receive job alerts."))
