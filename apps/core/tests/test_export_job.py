"""Tests for the generic async export-job pipeline (ARC-010, RPT-002/003, FND-014)."""
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import ExportJob, TaskQueue
from apps.core.pii import PIIType
from apps.core.services import (
    create_export_job,
    get_export_job_status,
    get_export_notifier,
    get_export_pii_types,
    get_export_renderer,
    is_export_pii,
    register_export_notifier,
    register_export_pii,
    register_export_renderer,
)


class CreateExportJobTests(TestCase):
    def test_creates_pending_job_and_enqueues_task(self):
        job = create_export_job(
            report_key='test.report', export_format=ExportJob.FORMAT_XLSX,
            filters={'from': '2026-09-01'}, foundation_id=101,
            requested_by='7', requested_by_name='Budi',
        )
        self.assertEqual(job.status, ExportJob.STATUS_PENDING)
        self.assertEqual(job.foundation_id, 101)
        self.assertEqual(job.filters, {'from': '2026-09-01'})

        task = TaskQueue.objects.filter(task_type='core.export.run').first()
        self.assertIsNotNone(task)
        self.assertEqual(task.payload, {'export_job_id': job.id})
        self.assertEqual(task.foundation_id, 101)


class RunExportJobTests(TestCase):
    @mock.patch('apps.core.storage._client')
    def test_successful_render_completes_job_and_notifies(self, mock_client):
        notified = []

        @register_export_renderer('test.report.success')
        def fake_renderer(job):
            return b'hello xlsx bytes', 'application/octet-stream', 'out.xlsx'

        @register_export_notifier('test.report.success')
        def fake_notifier(job, download_url):
            notified.append((job.id, download_url))

        job = create_export_job(
            report_key='test.report.success', export_format=ExportJob.FORMAT_XLSX,
            filters={}, foundation_id=202, requested_by='9',
        )

        call_command('drain_tasks', limit=10)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.STATUS_COMPLETED)
        self.assertTrue(job.result_key)
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0][0], job.id)

    @mock.patch('apps.core.storage._client')
    def test_renderer_exception_marks_job_failed(self, mock_client):
        @register_export_renderer('test.report.failing')
        def failing_renderer(job):
            raise RuntimeError("boom")

        job = create_export_job(
            report_key='test.report.failing', export_format=ExportJob.FORMAT_PDF,
            filters={}, foundation_id=303,
        )

        call_command('drain_tasks', limit=10)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.STATUS_FAILED)
        self.assertIn("boom", job.error_text)

    def test_missing_renderer_marks_job_failed_without_raising(self):
        job = create_export_job(
            report_key='test.report.unregistered', export_format=ExportJob.FORMAT_PDF,
            filters={}, foundation_id=404,
        )

        call_command('drain_tasks', limit=10)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.STATUS_FAILED)
        self.assertIn('test.report.unregistered', job.error_text)


class GetExportJobStatusTests(TestCase):
    @mock.patch('apps.core.storage.generate_download_url')
    def test_completed_job_returns_signed_download_url(self, mock_generate_url):
        mock_generate_url.return_value = 'https://signed.example/download'
        job = ExportJob.objects.create(
            foundation_id=505, report_key='test.report', format=ExportJob.FORMAT_XLSX,
            status=ExportJob.STATUS_COMPLETED, result_key='STG/export_test/out.xlsx',
        )
        result = get_export_job_status(job.id, foundation_id=505)
        self.assertEqual(result['status'], ExportJob.STATUS_COMPLETED)
        self.assertEqual(result['download_url'], 'https://signed.example/download')
        mock_generate_url.assert_called_once_with('STG/export_test/out.xlsx', 24 * 60 * 60)

    def test_pending_job_returns_no_download_url(self):
        job = ExportJob.objects.create(
            foundation_id=606, report_key='test.report', format=ExportJob.FORMAT_PDF,
        )
        result = get_export_job_status(job.id, foundation_id=606)
        self.assertEqual(result['status'], ExportJob.STATUS_PENDING)
        self.assertIsNone(result['download_url'])

    def test_cross_tenant_job_returns_none(self):
        job = ExportJob.objects.create(foundation_id=707, report_key='test.report', format=ExportJob.FORMAT_PDF)
        result = get_export_job_status(job.id, foundation_id=999)
        self.assertIsNone(result)

    def test_unknown_job_returns_none(self):
        result = get_export_job_status(999999, foundation_id=101)
        self.assertIsNone(result)


class RegisterExportPiiTests(TestCase):
    """register_export_pii's pii_types param consumes the apps.core.pii canonical
    registry so a new PII type only needs to be added in one place to be
    covered by both live log scrubbing and export audit trails."""

    def test_register_without_pii_types_still_marks_pii(self):
        register_export_pii('test.export_job.no_types')
        self.assertTrue(is_export_pii('test.export_job.no_types'))
        self.assertEqual(get_export_pii_types('test.export_job.no_types'), frozenset())

    def test_register_with_pii_types_records_them(self):
        register_export_pii('test.export_job.typed', {PIIType.NIK, PIIType.NISN})
        self.assertTrue(is_export_pii('test.export_job.typed'))
        self.assertEqual(get_export_pii_types('test.export_job.typed'), {PIIType.NIK, PIIType.NISN})

    def test_unregistered_report_key_has_no_pii_types(self):
        self.assertEqual(get_export_pii_types('test.export_job.never_registered'), frozenset())

    def test_non_pii_type_member_raises(self):
        with self.assertRaises(ValueError):
            register_export_pii('test.export_job.bad_type', {'NIK'})
