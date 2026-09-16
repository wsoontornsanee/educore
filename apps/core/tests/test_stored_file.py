from django.test import TestCase

from apps.core.models import StoredFile


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
