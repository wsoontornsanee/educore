"""Tests for apps.core.storage (ARC-026/ARC-030 GCS helpers)."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.storage import build_object_key, generate_download_url, generate_upload_url


class BuildObjectKeyTests(SimpleTestCase):
    @override_settings(GCS_PATH_PREFIX='STG')
    def test_key_uses_configured_prefix(self):
        key = build_object_key('homework_submissions', 'tugas.pdf')
        self.assertTrue(key.startswith('STG/homework_submissions/'))
        self.assertTrue(key.endswith('_tugas.pdf'))

    @override_settings(GCS_PATH_PREFIX='PRD')
    def test_key_uses_production_prefix(self):
        key = build_object_key('homework_submissions', 'tugas.pdf')
        self.assertTrue(key.startswith('PRD/homework_submissions/'))

    def test_key_is_unique_per_call(self):
        first = build_object_key('reports', 'rapor.pdf')
        second = build_object_key('reports', 'rapor.pdf')
        self.assertNotEqual(first, second)


class SignedUrlTests(SimpleTestCase):
    @patch('apps.core.storage._client')
    def test_generate_upload_url_uses_configured_bucket(self, mock_client):
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = 'https://signed.example/put'
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        url = generate_upload_url('STG/homework_submissions/abc_file.pdf', 'application/pdf')

        self.assertEqual(url, 'https://signed.example/put')
        mock_blob.generate_signed_url.assert_called_once_with(
            version='v4', expiration=600, method='PUT', content_type='application/pdf',
        )

    @patch('apps.core.storage._client')
    def test_generate_download_url_uses_configured_bucket(self, mock_client):
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = 'https://signed.example/get'
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        url = generate_download_url('STG/homework_submissions/abc_file.pdf')

        self.assertEqual(url, 'https://signed.example/get')
        mock_blob.generate_signed_url.assert_called_once_with(
            version='v4', expiration=600, method='GET',
        )
