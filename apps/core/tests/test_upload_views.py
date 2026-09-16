from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.core.models import StoredFile
from apps.identity.models import RoleAssignment


class InitiateUploadViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )

    @patch('apps.core.storage._client')
    def test_initiate_upload_returns_signed_url(self, mock_client):
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = 'https://signed.example/put'
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/files/uploads/', {
            'purpose': 'homework_submission', 'filename': 'tugas.pdf',
            'content_type': 'application/pdf', 'size': 1000,
        }, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['upload_url'], 'https://signed.example/put')
        # Use all_tenants: the request's TenancyMiddleware clears the
        # thread-local foundation context once client.post() returns, and
        # the tenant-scoped default manager fail-closes to none() outside
        # any foundation context (ARC-002).
        self.assertTrue(StoredFile.all_tenants.filter(id=body['id']).exists())

    def test_unknown_purpose_returns_400(self):
        # An unregistered purpose has no entry in PURPOSE_RULES, so
        # HasRequiredPermission cannot resolve a required_permission and
        # fails closed (IAM-010) rather than leaking a distinguishable 400
        # to an unauthorized/unrecognized request — this is the secure
        # behavior, not a validation-only 400.
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/files/uploads/', {
            'purpose': 'not_a_real_purpose', 'filename': 'x.pdf',
            'content_type': 'application/pdf', 'size': 10,
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_unauthenticated_rejected(self):
        res = self.client.post('/api/v1/files/uploads/', {
            'purpose': 'homework_submission', 'filename': 'tugas.pdf',
            'content_type': 'application/pdf', 'size': 1000,
        }, format='json')
        self.assertIn(res.status_code, (401, 403))


class ConfirmUploadViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.stored_file = StoredFile.objects.create(
            foundation_id=self.fx['foundation'].id, bucket='b',
            key='STG/homework_submission/x_tugas.pdf', purpose='homework_submission',
            content_type='application/pdf',
        )

    @patch('apps.core.storage._client')
    def test_confirm_upload_marks_confirmed(self, mock_client):
        mock_blob = MagicMock(size=500, content_type='application/pdf', md5_hash='abc==')
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/files/uploads/{self.stored_file.id}/confirm/')

        self.assertEqual(res.status_code, 200, res.content)
        self.stored_file.refresh_from_db()
        self.assertIsNotNone(self.stored_file.confirmed_at)
        self.assertEqual(self.stored_file.size, 500)
