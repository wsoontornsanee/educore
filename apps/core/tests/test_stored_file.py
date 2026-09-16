import base64
from unittest.mock import MagicMock, patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academic.models import ALLOWED_SUBMISSION_CONTENT_TYPES, MAX_SUBMISSION_FILE_SIZE
from apps.core.models import StoredFile
from apps.core.services import (
    PURPOSE_RULES,
    InvalidUploadError,
    confirm_upload,
    initiate_upload,
    write_generated_file,
)
from educore.middleware.tenancy import tenant_context


class StoredFileModelTests(TestCase):
    def test_create_pending_stored_file(self):
        sf = StoredFile.objects.create(
            foundation_id=1,
            bucket='educore-e46aa.firebasestorage.app',
            key='STG/homework_submission/abc_tugas.pdf',
            purpose='homework_submission',
            content_type='application/pdf',
            uploaded_by='42',
        )
        self.assertIsNone(sf.size)
        self.assertIsNone(sf.confirmed_at)
        self.assertEqual(sf.checksum, '')
        self.assertFalse(sf.is_deleted)

    def test_key_is_unique(self):
        StoredFile.objects.create(
            foundation_id=1, bucket='b', key='dup-key', purpose='x', content_type='application/pdf',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StoredFile.objects.create(
                    foundation_id=1, bucket='b', key='dup-key', purpose='x', content_type='application/pdf',
                )

    def test_confirmed_stored_file(self):
        from django.utils import timezone
        sf = StoredFile.objects.create(
            foundation_id=1, bucket='b', key='k2', purpose='report_card_pdf',
            content_type='application/pdf', size=1234, checksum='deadbeef',
            confirmed_at=timezone.now(),
        )
        self.assertEqual(sf.size, 1234)
        self.assertIsNotNone(sf.confirmed_at)


class InitiateUploadTests(TestCase):
    @patch('apps.core.storage._client')
    def test_initiate_upload_creates_pending_row_and_returns_url(self, mock_client):
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = 'https://signed.example/put'
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        sf, url = initiate_upload(
            purpose='homework_submission', filename='tugas.pdf',
            content_type='application/pdf', size=1000, foundation_id=1, uploaded_by='42',
        )

        self.assertEqual(url, 'https://signed.example/put')
        self.assertIsNone(sf.confirmed_at)
        self.assertEqual(sf.purpose, 'homework_submission')
        self.assertTrue(sf.key.endswith('_tugas.pdf'))

    def test_unknown_purpose_rejected(self):
        with self.assertRaises(InvalidUploadError):
            initiate_upload(purpose='not_a_real_purpose', filename='x.pdf', content_type='application/pdf', size=10, foundation_id=1)

    def test_oversized_file_rejected(self):
        with self.assertRaises(InvalidUploadError):
            initiate_upload(
                purpose='homework_submission', filename='big.pdf',
                content_type='application/pdf', size=21 * 1024 * 1024, foundation_id=1,
            )

    def test_disallowed_content_type_rejected(self):
        with self.assertRaises(InvalidUploadError):
            initiate_upload(
                purpose='homework_submission', filename='virus.exe',
                content_type='application/x-msdownload', size=10, foundation_id=1,
            )


class ConfirmUploadTests(TestCase):
    @patch('apps.core.storage._client')
    def test_confirm_upload_sets_real_metadata(self, mock_client):
        md5_b64 = 'XrY7u+Ae7tCTyyK7j1rNww=='
        mock_blob = MagicMock(size=999, content_type='application/pdf', md5_hash=md5_b64)
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        with tenant_context(1):
            sf = StoredFile.objects.create(
                foundation_id=1, bucket='b', key='STG/homework_submission/x_tugas.pdf',
                purpose='homework_submission', content_type='application/pdf',
            )
            confirmed = confirm_upload(sf.id)

        self.assertEqual(confirmed.size, 999)
        self.assertEqual(confirmed.checksum, base64.b64decode(md5_b64).hex())
        self.assertIsNotNone(confirmed.confirmed_at)

    @patch('apps.core.storage._client')
    def test_confirm_upload_raises_when_object_not_found_yet(self, mock_client):
        from google.cloud.exceptions import NotFound

        mock_blob = MagicMock()
        mock_blob.reload.side_effect = NotFound('not found')
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        with tenant_context(1):
            sf = StoredFile.objects.create(
                foundation_id=1, bucket='b', key='STG/homework_submission/y_tugas.pdf',
                purpose='homework_submission', content_type='application/pdf',
            )
            with self.assertRaises(InvalidUploadError):
                confirm_upload(sf.id)

    @patch('apps.core.storage._client')
    def test_confirm_upload_rejects_oversized_object(self, mock_client):
        over_limit = PURPOSE_RULES['homework_submission']['max_size'] + 1
        mock_blob = MagicMock(size=over_limit, content_type='application/pdf', md5_hash='XrY7u+Ae7tCTyyK7j1rNww==')
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        with tenant_context(1):
            sf = StoredFile.objects.create(
                foundation_id=1, bucket='b', key='STG/homework_submission/z_tugas.pdf',
                purpose='homework_submission', content_type='application/pdf',
            )
            with self.assertRaises(InvalidUploadError):
                confirm_upload(sf.id)

            sf.refresh_from_db()
            self.assertIsNone(sf.confirmed_at)


class WriteGeneratedFileTests(TestCase):
    @patch('apps.core.storage._client')
    def test_write_generated_file_uploads_and_confirms_immediately(self, mock_client):
        mock_blob = MagicMock()
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        sf = write_generated_file(
            purpose='report_card_pdf', filename='rapor.pdf',
            data=b'%PDF-1.4 fake', content_type='application/pdf', foundation_id=1,
        )

        mock_blob.upload_from_string.assert_called_once_with(b'%PDF-1.4 fake', content_type='application/pdf')
        self.assertIsNotNone(sf.confirmed_at)
        self.assertEqual(sf.size, len(b'%PDF-1.4 fake'))
        self.assertTrue(sf.checksum)
        self.assertTrue(sf.key.startswith('STG/report_card_pdf/'))

    @patch('apps.core.storage._client')
    def test_write_generated_file_with_explicit_key_overwrites_in_place(self, mock_client):
        mock_blob = MagicMock()
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        first = write_generated_file(
            purpose='report_card_pdf', filename='rapor.pdf', key='STG/report_card_pdf/5_v1.pdf',
            data=b'first render', content_type='application/pdf', foundation_id=1,
        )
        second = write_generated_file(
            purpose='report_card_pdf', filename='rapor.pdf', key='STG/report_card_pdf/5_v1.pdf',
            data=b'second render, longer', content_type='application/pdf', foundation_id=1,
        )

        self.assertEqual(first.id, second.id, "re-render must reuse the same StoredFile row, not create a new one")
        self.assertEqual(StoredFile.all_tenants.filter(key='STG/report_card_pdf/5_v1.pdf').count(), 1)
        self.assertEqual(second.size, len(b'second render, longer'))
        self.assertEqual(mock_blob.upload_from_string.call_count, 2)


class BuildSignedDownloadTests(TestCase):
    @patch('apps.core.storage.generate_download_url')
    def test_returns_url_and_expiry(self, mock_generate_download_url):
        from apps.core.services import build_signed_download

        mock_generate_download_url.return_value = 'https://signed.example/get'

        result = build_signed_download('STG/report_card_pdf/5_v1.pdf')

        mock_generate_download_url.assert_called_once_with('STG/report_card_pdf/5_v1.pdf', 600)
        self.assertEqual(result['download_url'], 'https://signed.example/get')
        self.assertIn('expires_at', result)


class PurposeRulesDriftTests(TestCase):
    """Guards apps.core.services.PURPOSE_RULES['homework_submission'] against
    silently drifting from apps.academic.models' independently-declared
    constants — apps/core intentionally duplicates these values by hand
    (rather than importing apps.academic) to keep apps/core free of business-app
    dependencies, so this test is the tripwire that catches future desync.
    """

    def test_homework_submission_rules_match_academic_constants(self):
        rules = PURPOSE_RULES['homework_submission']
        self.assertEqual(rules['max_size'], MAX_SUBMISSION_FILE_SIZE)
        self.assertEqual(rules['allowed_content_types'], ALLOWED_SUBMISSION_CONTENT_TYPES)
