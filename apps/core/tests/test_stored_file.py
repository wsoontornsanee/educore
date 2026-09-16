from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.core.models import StoredFile
from apps.core.services import InvalidUploadError, confirm_upload, initiate_upload, write_generated_file
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
        with self.assertRaises(Exception):
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
        mock_blob = MagicMock(size=999, content_type='application/pdf', md5_hash='abc==')
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        with tenant_context(1):
            sf = StoredFile.objects.create(
                foundation_id=1, bucket='b', key='STG/homework_submission/x_tugas.pdf',
                purpose='homework_submission', content_type='application/pdf',
            )
            confirmed = confirm_upload(sf.id)

        self.assertEqual(confirmed.size, 999)
        self.assertEqual(confirmed.checksum, 'abc==')
        self.assertIsNotNone(confirmed.confirmed_at)


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
