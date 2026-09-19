"""Management command: read-only report of SETTLED payments that never reached the books.

Reconciliation used to flip Payment.status to SETTLED without a receipt number, an
invoice allocation or a ledger journal (fixed in the manual-settle and auto-settle
changes). This lists the payments that path already produced so finance can review
them. It never writes: repairing one is a separate, confirmed step.

    python manage.py report_unbooked_settled_payments [--foundation-id N] [--school-id N] [--csv FILE]

Ad-hoc operator tool, not scheduled, so it takes no advisory lock and writes no JobRun.
The report carries payment/student ids and the payment reference, never names or contact data.
"""
import csv

from django.core.management.base import BaseCommand

from apps.finance.services.reconciliation import find_settled_payments_missing_books


class Command(BaseCommand):
    help = "List SETTLED payments missing a ledger journal, invoice allocation or receipt number (read-only)."

    def add_arguments(self, parser):
        parser.add_argument('--foundation-id', type=int, default=None, help="Limit to one foundation.")
        parser.add_argument('--school-id', type=int, default=None, help="Limit to one school.")
        parser.add_argument('--csv', dest='csv_path', default=None, help="Also write the rows to this CSV file.")

    def handle(self, *args, **options):
        rows = find_settled_payments_missing_books(
            foundation_id=options['foundation_id'], school_id=options['school_id'],
        )
        no_journal = sum('NO_JOURNAL' in row['flags'] for row in rows)
        review_only = len(rows) - no_journal

        for row in rows:
            settled_at = f"{row['settled_at']:%Y-%m-%d %H:%M}" if row['settled_at'] else '-'
            self.stdout.write(
                f"{row['reference']}\tpayment={row['payment_id']}\tfoundation={row['foundation_id']}"
                f"\tschool={row['school_id']}\tstudent={row['student_id']}"
                f"\t{row['currency']} {row['amount']}\tsettled_at={settled_at}\tflags={','.join(row['flags'])}"
            )

        if options['csv_path']:
            with open(options['csv_path'], 'w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    'payment_id', 'reference', 'foundation_id', 'school_id', 'student_id', 'channel',
                    'amount', 'currency', 'settled_at', 'flags',
                ])
                for row in rows:
                    writer.writerow([
                        row['payment_id'], row['reference'], row['foundation_id'], row['school_id'],
                        row['student_id'], row['channel'], row['amount'], row['currency'],
                        row['settled_at'].isoformat() if row['settled_at'] else '', ' '.join(row['flags']),
                    ])

        self.stdout.write(self.style.WARNING(
            f"{len(rows)} settled payment(s) flagged: {no_journal} with no ledger journal (definite gap), "
            f"{review_only} flagged for review only (no allocation and/or no receipt). Nothing was changed."
        ))
        if no_journal:
            self.stderr.write("Review each NO_JOURNAL payment with finance before any repair; this command never writes.")
