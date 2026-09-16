"""Tests for the Audit Explorer's CSV export (FND-010, RPT-002)."""
import csv
import io
from unittest import mock

from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.models import AuditEvent, ExportJob
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class FoundationAuditExportTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa", brand_name="Bina Bangsa", npwp="01.888.777.6-001.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Bina Bangsa", npsn="40100001", level=School.LEVEL_SMA,
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999999", foundation_id=self.foundation.id, full_name="Ketua Yayasan",
        )
        assign_role(
            user=self.admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )
        self.event = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=self.school.id, actor_id=str(self.admin.id),
            action='finance.invoice.issue', entity_type='Invoice', entity_id='1',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_pdf_format_rejected_for_audit_report(self):
        """render_foundation_audit_export always emits CSV; requesting PDF for
        report=foundation_audit must 400 rather than silently return a CSV
        file while ExportJob.format records 'PDF' (FND-010 review finding)."""
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                '/api/v1/foundation/exports', data={'report': 'foundation_audit', 'format': 'PDF'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch('apps.core.storage._client')
    def test_csv_export_completes_and_contains_rows(self, mock_client):
        mock_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/download.csv'
        )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            post_response = self.client.post(
                '/api/v1/foundation/exports',
                data={'report': 'foundation_audit', 'format': 'csv'}, format='json',
            )
            self.assertEqual(post_response.status_code, status.HTTP_202_ACCEPTED)
            job_id = post_response.data['job_id']

        call_command('drain_tasks', limit=10)

        job = ExportJob.all_tenants.get(id=job_id)
        self.assertEqual(job.status, ExportJob.STATUS_COMPLETED, job.error_text)
        self.assertTrue(job.result_key.endswith('.csv'))

        upload_call = mock_client.return_value.bucket.return_value.blob.return_value.upload_from_string
        data = upload_call.call_args[0][0]
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(rows[0][0], 'ID')
        self.assertEqual(rows[1][6], 'finance.invoice.issue')

    @mock.patch('apps.core.storage._client')
    def test_csv_export_applies_module_filter(self, mock_client):
        mock_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/download.csv'
        )
        AuditEvent.objects.create(
            foundation_id=self.foundation.id, actor_id=str(self.admin.id),
            action='identity.school.updated', entity_type='School', entity_id=str(self.school.id),
        )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            post_response = self.client.post(
                '/api/v1/foundation/exports',
                data={'report': 'foundation_audit', 'format': 'csv', 'filters': {'module': 'identity'}},
                format='json',
            )
            job_id = post_response.data['job_id']

        call_command('drain_tasks', limit=10)

        upload_call = mock_client.return_value.bucket.return_value.blob.return_value.upload_from_string
        data = upload_call.call_args[0][0]
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        data_rows = rows[1:]
        self.assertEqual(len(data_rows), 1)
        self.assertEqual(data_rows[0][6], 'identity.school.updated')

    @mock.patch('apps.core.storage._client')
    def test_csv_export_defuses_formula_injection(self, mock_client):
        """entity_id (or any other requester-influenced field) starting with
        =/+/-/@ must not be interpretable as a formula by Excel/Sheets."""
        mock_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/download.csv'
        )
        AuditEvent.objects.create(
            foundation_id=self.foundation.id, actor_id=str(self.admin.id),
            action='wallet.topup.completed', entity_type='Student', entity_id='=cmd|/c calc',
        )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            post_response = self.client.post(
                '/api/v1/foundation/exports',
                data={'report': 'foundation_audit', 'format': 'csv', 'filters': {'module': 'wallet'}},
                format='json',
            )
            job_id = post_response.data['job_id']

        call_command('drain_tasks', limit=10)

        upload_call = mock_client.return_value.bucket.return_value.blob.return_value.upload_from_string
        data = upload_call.call_args[0][0]
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        entity_id_cell = rows[1][8]
        self.assertTrue(entity_id_cell.startswith("'="))

    @mock.patch('apps.core.storage._client')
    @mock.patch('apps.foundation.services.AUDIT_EXPORT_MAX_ROWS', 3)
    def test_csv_export_enforces_row_cap(self, mock_client):
        """FND-010: CSV export is capped, regardless of how many rows match."""
        mock_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/download.csv'
        )
        for _ in range(5):
            AuditEvent.objects.create(
                foundation_id=self.foundation.id, actor_id=str(self.admin.id),
                action='wallet.topup.completed', entity_type='Student', entity_id='1',
            )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            post_response = self.client.post(
                '/api/v1/foundation/exports',
                data={'report': 'foundation_audit', 'format': 'csv', 'filters': {'module': 'wallet'}},
                format='json',
            )
            job_id = post_response.data['job_id']

        call_command('drain_tasks', limit=10)

        upload_call = mock_client.return_value.bucket.return_value.blob.return_value.upload_from_string
        data = upload_call.call_args[0][0]
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(len(rows) - 1, 3)  # header + capped rows, not the 5 that matched
