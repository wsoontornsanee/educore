"""NFR-009: rows past retention move to *_archive tables, never dropped, never orphaning a FK."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.archiving import POLICIES, archive_batch, eligible_rows
from apps.core.models import AuditEvent, DomainEvent, JobRun
from apps.wallet.models import (
    Merchant, POSTerminal, POSTransaction, Wallet, WalletTransaction, WalletTransactionStatus, WalletTransactionType,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture

OLD = timezone.now() - timedelta(days=1000)


class ArchiveSchemaTests(SimpleTestCase):
    def test_archive_mirrors_source_columns(self):
        for policy in POLICIES:
            with self.subTest(table=policy.source._meta.db_table):
                source_cols = {f.column for f in policy.source._meta.concrete_fields if not getattr(f, 'generated', False)}
                self.assertEqual({f.column for f in policy.archive._meta.concrete_fields}, source_cols)

    def test_archive_carries_no_constraints_that_could_reject_a_move(self):
        for policy in POLICIES:
            with self.subTest(table=policy.source._meta.db_table):
                fields = policy.archive._meta.concrete_fields
                self.assertFalse([f.name for f in fields if f.is_relation])
                self.assertEqual([f.name for f in fields if f.unique and not f.primary_key], [])
                self.assertEqual(policy.archive._meta.constraints, [])

    def test_all_five_nfr009_tables_are_covered(self):
        self.assertEqual(
            {p.source._meta.db_table for p in POLICIES},
            {'gate_events', 'wallet_transactions', 'pos_transactions', 'audit_events', 'domain_events'},
        )


class ArchiveAuditAndDomainEventTests(TestCase):
    def setUp(self):
        self.old_audit = AuditEvent.objects.create(action='finance.invoice.issue', entity_type='Invoice', entity_id='1', foundation_id=7)
        self.new_audit = AuditEvent.objects.create(action='finance.invoice.issue', entity_type='Invoice', entity_id='2', foundation_id=7)
        AuditEvent.objects.filter(pk=self.old_audit.pk).update(timestamp=OLD)
        self.policy = next(p for p in POLICIES if p.model_label == 'core.AuditEvent')

    def test_old_rows_move_and_recent_rows_stay(self):
        self.assertEqual(archive_batch(self.policy, 100), 1)
        self.assertFalse(AuditEvent.objects.filter(pk=self.old_audit.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(pk=self.new_audit.pk).exists())
        archived = self.policy.archive.objects.get(pk=self.old_audit.pk)
        self.assertEqual((archived.action, archived.entity_id, archived.foundation_id), ('finance.invoice.issue', '1', 7))
        self.assertEqual(archived.timestamp, OLD)

    def test_rerun_is_a_no_op(self):
        archive_batch(self.policy, 100)
        self.assertEqual(archive_batch(self.policy, 100), 0)
        self.assertEqual(self.policy.archive.objects.count(), 1)

    def test_retention_override_widens_the_window(self):
        self.assertEqual(archive_batch(self.policy, 100, retention_days=0), 2)

    def test_domain_events_use_occurred_at(self):
        policy = next(p for p in POLICIES if p.model_label == 'core.DomainEvent')
        old = DomainEvent.objects.create(foundation_id=7, name='a.b.c', payload={'k': 1})
        DomainEvent.objects.create(foundation_id=7, name='a.b.c')
        DomainEvent.objects.filter(pk=old.pk).update(occurred_at=OLD)
        self.assertEqual(archive_batch(policy, 100), 1)
        self.assertEqual(policy.archive.objects.get(pk=old.pk).payload, {'k': 1})


class ArchiveWalletTests(TestCase):
    def setUp(self):
        fx = build_wallet_fixture("Yayasan Arsip")
        self.foundation, self.school, self.student = fx['foundation'], fx['school'], fx['student']
        self.wallet = Wallet.objects.create(foundation_id=self.foundation.id, student=self.student)
        self.wallet_policy = next(p for p in POLICIES if p.model_label == 'wallet.WalletTransaction')
        self.pos_policy = next(p for p in POLICIES if p.model_label == 'wallet.POSTransaction')
        self.merchant = Merchant.objects.create(foundation_id=self.foundation.id, school=self.school, name='Kantin')
        self.terminal = POSTerminal.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, device_id='ARSIP-01',
        )

    def wallet_txn(self, key, *, when=OLD, status=WalletTransactionStatus.COMPLETED):
        return WalletTransaction.objects.create(
            foundation_id=self.foundation.id, wallet=self.wallet, type=WalletTransactionType.TOPUP,
            amount=Decimal('12345.67'), balance_after=Decimal('12345.67'), occurred_at=when,
            status=status, idempotency_key=key,
        )

    def pos_txn(self, key, *, when=OLD, wallet_transaction=None):
        return POSTransaction.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, terminal=self.terminal, student=self.student,
            subtotal=Decimal('5000.00'), total=Decimal('5000.00'), occurred_at=when,
            client_transaction_id=key, wallet_transaction=wallet_transaction,
        )

    def test_money_and_ids_survive_the_move(self):
        txn = self.wallet_txn('a')
        archive_batch(self.wallet_policy, 100)
        self.assertFalse(WalletTransaction.all_tenants.filter(pk=txn.pk).exists())
        archived = self.wallet_policy.archive.objects.get(pk=txn.pk)
        self.assertEqual((archived.amount, archived.balance_after), (Decimal('12345.67'), Decimal('12345.67')))
        self.assertEqual((archived.wallet_id, archived.foundation_id, archived.idempotency_key), (self.wallet.id, self.foundation.id, 'a'))

    def test_open_reconciliation_and_recent_rows_stay_hot(self):
        recon = self.wallet_txn('r', status=WalletTransactionStatus.RECONCILE_REQUIRED)
        recent = self.wallet_txn('n', when=timezone.now())
        self.assertEqual(archive_batch(self.wallet_policy, 100), 0)
        self.assertTrue(WalletTransaction.all_tenants.filter(pk__in=[recon.pk, recent.pk]).count() == 2)

    def test_row_still_referenced_by_a_hot_row_is_not_archived(self):
        linked = self.wallet_txn('linked')
        free = self.wallet_txn('free')
        self.pos_txn('recent-sale', when=timezone.now(), wallet_transaction=linked)
        self.assertEqual(list(eligible_rows(self.wallet_policy).values_list('pk', flat=True)), [free.pk])
        archive_batch(self.wallet_policy, 100)
        self.assertTrue(WalletTransaction.all_tenants.filter(pk=linked.pk).exists())

    def test_soft_deleted_rows_are_archived_too(self):
        txn = self.wallet_txn('gone')
        WalletTransaction.all_tenants.filter(pk=txn.pk).update(deleted_at=timezone.now())
        archive_batch(self.wallet_policy, 100)
        self.assertIsNotNone(self.wallet_policy.archive.objects.get(pk=txn.pk).deleted_at)

    def test_pos_archive_skips_generated_marker_column(self):
        sale = self.pos_txn('old-sale')
        self.assertEqual(archive_batch(self.pos_policy, 100), 1)
        self.assertEqual(self.pos_policy.archive.objects.get(pk=sale.pk).total, Decimal('5000.00'))

    def test_batch_size_bounds_each_move(self):
        for i in range(3):
            self.wallet_txn(f'k{i}')
        self.assertEqual(archive_batch(self.wallet_policy, 2), 2)
        self.assertEqual(archive_batch(self.wallet_policy, 2), 1)


class ArchiveCommandTests(TestCase):
    def setUp(self):
        for i in range(3):
            event = AuditEvent.objects.create(action='x.y.z', entity_type='T', entity_id=str(i), foundation_id=1)
            AuditEvent.objects.filter(pk=event.pk).update(timestamp=OLD)

    def run_command(self, *args):
        out = StringIO()
        call_command('archive_high_write_tables', '--table', 'audit_events', *args, stdout=out)
        return out.getvalue()

    def test_limit_caps_a_run_and_the_next_run_resumes(self):
        archive = next(p for p in POLICIES if p.model_label == 'core.AuditEvent').archive
        self.run_command('--limit', '2', '--batch-size', '1')
        self.assertEqual((AuditEvent.objects.count(), archive.objects.count()), (1, 2))
        run = JobRun.objects.get(job_name='archive_high_write_tables')
        self.assertEqual((run.status, run.items_processed), (JobRun.STATUS_SUCCESS, 2))

        self.run_command('--limit', '2')
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(archive.objects.count(), 3)

    def test_dry_run_moves_nothing(self):
        output = self.run_command('--dry-run')
        self.assertIn('3 row(s) would be archived', output)
        self.assertEqual(AuditEvent.objects.count(), 3)
        self.assertNotIn('archived 0 row(s)', output)  # a dry run reports what it would do, never a fake result

    def test_dry_run_leaves_no_job_run_and_a_real_run_leaves_one(self):
        # A dry-run row would make job_health read a never-scheduled job as healthy (ARC-008).
        self.run_command('--dry-run')
        self.assertFalse(JobRun.objects.filter(job_name='archive_high_write_tables').exists())
        self.run_command()
        run = JobRun.objects.get(job_name='archive_high_write_tables')
        self.assertEqual((run.status, run.items_processed), (JobRun.STATUS_SUCCESS, 3))
        self.assertIsNotNone(run.finished_at)

    def test_a_failing_run_is_recorded_failed_and_the_error_still_reaches_cron(self):
        with mock.patch('apps.core.management.commands.archive_high_write_tables.archive_batch',
                        side_effect=RuntimeError('boom')):
            with self.assertRaisesMessage(RuntimeError, 'boom'):
                self.run_command()
        run = JobRun.objects.get(job_name='archive_high_write_tables')
        self.assertEqual(run.status, JobRun.STATUS_FAILED)
        self.assertIn('boom', run.error_text)
        self.assertIsNotNone(run.finished_at)
