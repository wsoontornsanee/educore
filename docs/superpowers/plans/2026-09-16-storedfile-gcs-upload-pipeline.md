# StoredFile + Signed GCS Upload Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw `MEDIA_ROOT` filesystem writes with a `core.StoredFile` catalog backed by GCS, and add a generic signed-upload/confirm API for future client-direct uploads.

**Architecture:** `apps/core` gains a `StoredFile` model, two new GCS write/read helpers in `apps/core/storage.py`, and three service functions (`initiate_upload`, `confirm_upload`, `write_generated_file`) plus a small two-endpoint API. Three existing call sites (`store_homework_submission_file`, `render_report_card_pdf`, `generate_settlement_statement_pdf`) are migrated onto `write_generated_file` in place, with no change to their external return values or the DB columns that store their keys.

**Tech Stack:** Django 5.1, DRF, MySQL 8, `google-cloud-storage` (already a dependency — used by `apps/core/storage.py` today).

## Global Constraints

- Money: N/A to this feature (no monetary fields).
- `snake_case`, plural table names, explicit `db_table` on every model (spec/01 §8.2) — `StoredFile` uses `db_table = 'stored_files'`.
- Soft delete via `deleted_at`, never hard-deleted — `StoredFile` extends `TenantModel`, inherits this.
- Every FK `on_delete=PROTECT` for financial/academic references, `CASCADE` only for genuinely owned child rows — N/A, `StoredFile` has no FKs (uses plain `foundation_id`/`school_id` integers like `AuditEvent`, since a `StoredFile` outlives any one referencing entity).
- Fail-closed permission checking (IAM-010): every view must declare `required_permission` or access is denied — the new upload endpoints resolve it dynamically per `purpose` via `get_required_permission()`.
- `apps/core` never imports from other `apps/*` business apps (verified: no `from apps.` import anywhere in `apps/core/*.py` today) — the new `PURPOSE_RULES` dict in `apps/core/services.py` must **not** import `apps.academic.models.ALLOWED_SUBMISSION_CONTENT_TYPES`/`MAX_SUBMISSION_FILE_SIZE`; duplicate the values with a comment cross-referencing the source of truth.
- Tests mock `apps.core.storage._client` (never hit real GCS), matching the existing pattern in `apps/core/tests/test_storage.py`.

---

### Task 1: `StoredFile` model

**Files:**
- Modify: `apps/core/models.py` (append after `IdempotencyRecord`, which ends at line 174)
- Create: `apps/core/migrations/0002_storedfile.py` (via `makemigrations`)
- Test: `apps/core/tests/test_stored_file.py`

**Interfaces:**
- Produces: `apps.core.models.StoredFile` with fields `foundation_id, school_id, created_at, created_by, updated_at, updated_by, deleted_at` (inherited from `TenantModel`) plus `bucket, key, purpose, content_type, size, checksum, uploaded_by, confirmed_at`.

- [ ] **Step 1: Write the failing test**

```python
# apps/core/tests/test_stored_file.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.core.tests.test_stored_file -v 2`
Expected: FAIL with `ImportError: cannot import name 'StoredFile'`

- [ ] **Step 3: Add the model**

Append to `apps/core/models.py` (after the `IdempotencyRecord` class, which currently ends at line 174):

```python
class StoredFile(TenantModel):
    """Catalog of every object uploaded to GCS — no domain model references a
    raw path string; every reference is a StoredFile row (ARC-026, ARC-030).

    school_id is a plain BigIntegerField (not an FK), following the same
    pattern as AuditEvent — a StoredFile can outlive the school-scoped row
    that referenced it.
    """
    school_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    bucket = models.CharField(max_length=255)
    key = models.CharField(max_length=500, unique=True)
    purpose = models.CharField(max_length=64, db_index=True)
    content_type = models.CharField(max_length=128)
    size = models.BigIntegerField(blank=True, null=True)
    checksum = models.CharField(max_length=64, blank=True, default='')
    uploaded_by = models.CharField(max_length=64, blank=True, default='')
    confirmed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'stored_files'
        indexes = [
            models.Index(fields=['purpose', 'deleted_at']),
        ]

    def __str__(self):
        return f"{self.purpose}:{self.key}"
```

Then generate the migration:

```bash
python manage.py makemigrations apps.core
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.core.tests.test_stored_file -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/core/models.py apps/core/migrations/0002_storedfile.py apps/core/tests/test_stored_file.py
git commit -m "feat(core): add StoredFile model (ARC-026)"
```

---

### Task 2: `apps/core/storage.py` write/read helpers

**Files:**
- Modify: `apps/core/storage.py` (append after `generate_download_url`, which ends at line 47)
- Test: `apps/core/tests/test_storage.py` (append after `SignedUrlTests`, which ends at line 53)

**Interfaces:**
- Consumes: `apps.core.storage._client()` (existing, line 18).
- Produces: `upload_bytes(key: str, data: bytes, content_type: str) -> None`, `get_blob_metadata(key: str) -> dict` (keys: `size`, `content_type`, `md5_hash`).

- [ ] **Step 1: Write the failing test**

Append to `apps/core/tests/test_storage.py`:

```python
class UploadBytesTests(SimpleTestCase):
    @patch('apps.core.storage._client')
    def test_upload_bytes_writes_to_configured_bucket(self, mock_client):
        from apps.core.storage import upload_bytes

        mock_blob = MagicMock()
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        upload_bytes('STG/report_card_pdf/abc.pdf', b'%PDF-1.4', 'application/pdf')

        mock_client.return_value.bucket.return_value.blob.assert_called_once_with('STG/report_card_pdf/abc.pdf')
        mock_blob.upload_from_string.assert_called_once_with(b'%PDF-1.4', content_type='application/pdf')


class GetBlobMetadataTests(SimpleTestCase):
    @patch('apps.core.storage._client')
    def test_get_blob_metadata_reloads_and_returns_fields(self, mock_client):
        from apps.core.storage import get_blob_metadata

        mock_blob = MagicMock(size=2048, content_type='application/pdf', md5_hash='abc123==')
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        result = get_blob_metadata('STG/homework_submission/abc_tugas.pdf')

        mock_blob.reload.assert_called_once()
        self.assertEqual(result, {'size': 2048, 'content_type': 'application/pdf', 'md5_hash': 'abc123=='})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.core.tests.test_storage -v 2`
Expected: FAIL with `ImportError: cannot import name 'upload_bytes'`

- [ ] **Step 3: Add the helpers**

Append to `apps/core/storage.py`:

```python
def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    """Write bytes directly to GCS. Used for server-generated files (PDFs) —
    the server already has the bytes, so no signed-upload round trip is needed.
    """
    bucket = _client().bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(key)
    blob.upload_from_string(data, content_type=content_type)


def get_blob_metadata(key: str) -> dict:
    """Fetch real (server-verified) size/content-type/checksum for a
    previously client-uploaded object, used by confirm_upload."""
    bucket = _client().bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(key)
    blob.reload()
    return {'size': blob.size, 'content_type': blob.content_type, 'md5_hash': blob.md5_hash}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.core.tests.test_storage -v 2`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add apps/core/storage.py apps/core/tests/test_storage.py
git commit -m "feat(core): add upload_bytes/get_blob_metadata GCS helpers"
```

---

### Task 3: `apps/core/services.py` upload service functions

**Files:**
- Modify: `apps/core/services.py` (append after `record_domain_event`, which ends at line 90)
- Test: `apps/core/tests/test_stored_file.py` (append after Task 1's classes)

**Interfaces:**
- Consumes: `apps.core.models.StoredFile` (Task 1); `apps.core.storage.build_object_key`, `generate_upload_url`, `upload_bytes`, `get_blob_metadata` (existing + Task 2); `educore.middleware.tenancy.get_current_foundation_id` (existing, imported at line 4).
- Produces:
  - `PURPOSE_RULES: dict[str, dict]` — each entry `{'max_size': int, 'allowed_content_types': set[str], 'required_permission': str}`.
  - `initiate_upload(purpose: str, filename: str, content_type: str, size: int, foundation_id=None, school_id=None, uploaded_by=None) -> tuple[StoredFile, str]` — raises `InvalidUploadError` on unknown purpose, oversized, or disallowed content type.
  - `confirm_upload(stored_file_id, actor=None) -> StoredFile` — raises `StoredFile.DoesNotExist` if not found.
  - `write_generated_file(purpose: str, filename: str, data: bytes, content_type: str, foundation_id=None) -> StoredFile` — always returns an already-confirmed `StoredFile`.
  - `InvalidUploadError(ValueError)` exception class.

- [ ] **Step 1: Write the failing test**

Append to `apps/core/tests/test_stored_file.py`:

```python
from unittest.mock import MagicMock, patch

from apps.core.services import InvalidUploadError, confirm_upload, initiate_upload, write_generated_file


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.core.tests.test_stored_file -v 2`
Expected: FAIL with `ImportError: cannot import name 'initiate_upload'`

- [ ] **Step 3: Add the service functions**

Add `import hashlib` at the top of `apps/core/services.py` (line 2, alongside `import logging`), add `from django.conf import settings` (line 3, alongside the existing `from django.utils import timezone`), and change the existing model import on line 6 plus add a storage import:

```python
from .models import AuditEvent, DomainEvent, StoredFile, TaskQueue
from . import storage
```

```python
class InvalidUploadError(ValueError):
    """Raised when an upload request violates its purpose's rules."""


# Rules per upload purpose. Size/content-type values for 'homework_submission'
# mirror apps.academic.models.ALLOWED_SUBMISSION_CONTENT_TYPES /
# MAX_SUBMISSION_FILE_SIZE — duplicated here (not imported) because apps/core
# must not depend on business apps.
PURPOSE_RULES = {
    'homework_submission': {
        'max_size': 20 * 1024 * 1024,
        'allowed_content_types': {
            'application/pdf', 'image/jpeg', 'image/png',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        },
        'required_permission': 'grades.write',
    },
}


def initiate_upload(purpose, filename, content_type, size, foundation_id=None, school_id=None, uploaded_by=None):
    """Two-phase commit, phase 1: validate, create a pending StoredFile, and
    return a signed PUT URL for the client to upload directly to GCS."""
    rules = PURPOSE_RULES.get(purpose)
    if rules is None:
        raise InvalidUploadError(f"UNKNOWN_PURPOSE: '{purpose}' is not a registered upload purpose.")
    if size > rules['max_size']:
        raise InvalidUploadError(f"FILE_TOO_LARGE: '{filename}' exceeds the limit for purpose '{purpose}'.")
    if content_type not in rules['allowed_content_types']:
        raise InvalidUploadError(f"UNSUPPORTED_FILE_TYPE: '{content_type}' is not accepted for purpose '{purpose}'.")

    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    key = storage.build_object_key(purpose, filename)
    stored_file = StoredFile.objects.create(
        foundation_id=foundation_id,
        school_id=school_id,
        bucket=settings.GCS_BUCKET_NAME,
        key=key,
        purpose=purpose,
        content_type=content_type,
        size=size,
        uploaded_by=uploaded_by or '',
    )
    upload_url = storage.generate_upload_url(key, content_type)
    return stored_file, upload_url


def confirm_upload(stored_file_id, actor=None):
    """Two-phase commit, phase 2: verify the object landed in GCS and record
    its real (server-verified) size/checksum."""
    stored_file = StoredFile.objects.get(id=stored_file_id)
    metadata = storage.get_blob_metadata(stored_file.key)
    stored_file.size = metadata['size']
    stored_file.checksum = metadata['md5_hash'] or ''
    stored_file.confirmed_at = timezone.now()
    stored_file.save(update_fields=['size', 'checksum', 'confirmed_at', 'updated_at'])
    return stored_file


def write_generated_file(purpose, filename, data, content_type, foundation_id=None):
    """For server-generated files (PDFs): write bytes directly to GCS and
    create an already-confirmed StoredFile row in one step — no two-phase
    commit needed since the server itself performed the write."""
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    key = storage.build_object_key(purpose, filename)
    storage.upload_bytes(key, data, content_type)

    return StoredFile.objects.create(
        foundation_id=foundation_id,
        bucket=settings.GCS_BUCKET_NAME,
        key=key,
        purpose=purpose,
        content_type=content_type,
        size=len(data),
        checksum=hashlib.md5(data).hexdigest(),
        confirmed_at=timezone.now(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.core.tests.test_stored_file -v 2`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add apps/core/services.py apps/core/tests/test_stored_file.py
git commit -m "feat(core): add initiate_upload/confirm_upload/write_generated_file"
```

---

### Task 4: Signed-upload API endpoints

**Files:**
- Create: `apps/core/serializers.py`
- Create: `apps/core/views.py`
- Create: `apps/core/urls.py`
- Modify: `educore/urls.py` (add one `include()` line after the existing `path('api/v1/', include('apps.foundation.urls'))` on line 8)
- Test: `apps/core/tests/test_upload_views.py`

**Interfaces:**
- Consumes: `apps.core.services.initiate_upload`, `confirm_upload`, `InvalidUploadError`, `PURPOSE_RULES` (Task 3); `apps.identity.permissions.HasRequiredPermission` (existing).
- Produces: `POST /api/v1/files/uploads/` and `POST /api/v1/files/uploads/<id>/confirm/`.

- [ ] **Step 1: Write the failing test**

```python
# apps/core/tests/test_upload_views.py
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
        self.assertTrue(StoredFile.objects.filter(id=body['id']).exists())

    def test_unknown_purpose_returns_400(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/files/uploads/', {
            'purpose': 'not_a_real_purpose', 'filename': 'x.pdf',
            'content_type': 'application/pdf', 'size': 10,
        }, format='json')
        self.assertEqual(res.status_code, 400)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.core.tests.test_upload_views -v 2`
Expected: FAIL with 404 (no such URL) — `apps/core/urls.py` doesn't exist yet.

- [ ] **Step 3: Implement serializers, views, urls**

```python
# apps/core/serializers.py
from rest_framework import serializers

from apps.core.models import StoredFile


class InitiateUploadSerializer(serializers.Serializer):
    purpose = serializers.CharField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    size = serializers.IntegerField(min_value=1)


class StoredFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoredFile
        fields = [
            'id', 'purpose', 'key', 'content_type', 'size', 'checksum',
            'uploaded_by', 'confirmed_at', 'created_at',
        ]
```

```python
# apps/core/views.py
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import StoredFile
from apps.core.serializers import InitiateUploadSerializer, StoredFileSerializer
from apps.core.services import PURPOSE_RULES, InvalidUploadError, confirm_upload, initiate_upload
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.audit import get_current_actor
from educore.middleware.tenancy import get_current_foundation_id


class InitiateUploadView(APIView):
    """POST /api/v1/files/uploads/ — phase 1 of the two-phase commit."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        purpose = self.request.data.get('purpose')
        rules = PURPOSE_RULES.get(purpose)
        return rules['required_permission'] if rules else None

    def post(self, request):
        payload = InitiateUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        actor = get_current_actor()
        uploaded_by = str(getattr(actor, 'pk', actor)) if actor else ''

        try:
            stored_file, upload_url = initiate_upload(
                foundation_id=get_current_foundation_id(), uploaded_by=uploaded_by,
                **payload.validated_data,
            )
        except InvalidUploadError as e:
            return Response({'error': str(e)}, status=400)

        data = StoredFileSerializer(stored_file).data
        data['upload_url'] = upload_url
        return Response(data, status=201)


class ConfirmUploadView(APIView):
    """POST /api/v1/files/uploads/<id>/confirm/ — phase 2 of the two-phase commit."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        try:
            stored_file = StoredFile.objects.get(id=self.kwargs['pk'])
        except StoredFile.DoesNotExist:
            return None
        rules = PURPOSE_RULES.get(stored_file.purpose)
        return rules['required_permission'] if rules else None

    def post(self, request, pk):
        foundation_id = get_current_foundation_id()
        try:
            stored_file = StoredFile.objects.get(id=pk, foundation_id=foundation_id, deleted_at__isnull=True)
        except StoredFile.DoesNotExist:
            raise PermissionDenied("Akses ditolak.")

        confirmed = confirm_upload(stored_file.id)
        return Response(StoredFileSerializer(confirmed).data, status=200)
```

```python
# apps/core/urls.py
from django.urls import path

from apps.core.views import ConfirmUploadView, InitiateUploadView

urlpatterns = [
    path('uploads/', InitiateUploadView.as_view(), name='files-initiate-upload'),
    path('uploads/<int:pk>/confirm/', ConfirmUploadView.as_view(), name='files-confirm-upload'),
]
```

In `educore/urls.py`, add after line 8 (`path('api/v1/', include('apps.foundation.urls')),`):

```python
    path('api/v1/files/', include('apps.core.urls')),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.core.tests.test_upload_views -v 2`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/core/serializers.py apps/core/views.py apps/core/urls.py apps/core/tests/test_upload_views.py educore/urls.py
git commit -m "feat(core): add signed-upload/confirm API endpoints"
```

---

### Task 5: Migrate `store_homework_submission_file`

**Files:**
- Modify: `apps/academic/services.py:542-572` (the function body), plus its imports (`Path`, `uuid4`, `settings` become unused — remove `from pathlib import Path` at line 8, `from uuid import uuid4` at line 9, and `from django.conf import settings` at line 12; add `from apps.core.services import write_generated_file` next to the existing `from apps.core.services import audit` at line 17)
- Modify: `apps/academic/tests/test_homework.py:397-412` (`test_valid_pdf_upload_stored_on_disk`)

**Interfaces:**
- Consumes: `apps.core.services.write_generated_file(purpose, filename, data, content_type, foundation_id=None) -> StoredFile` (Task 3).
- Produces: `store_homework_submission_file(homework, uploaded_file) -> dict` — same `{key, filename, size, content_type}` shape as before (unchanged, so `submit_homework` and `HomeworkViewSet.upload_file` need no changes).

- [ ] **Step 1: Update the test first (TDD for the behavior change)**

Replace `test_valid_pdf_upload_stored_on_disk` (lines 397-412 of `apps/academic/tests/test_homework.py`) with:

```python
    @mock.patch('apps.core.storage._client')
    def test_valid_pdf_upload_written_to_gcs(self, mock_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        mock_blob = mock.MagicMock()
        mock_client.return_value.bucket.return_value.blob.return_value = mock_blob

        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        meta = store_homework_submission_file(self.homework, upload)

        self.assertEqual(meta['filename'], 'tugas.pdf')
        self.assertEqual(meta['content_type'], 'application/pdf')
        self.assertTrue(meta['key'].startswith(f'STG/homework_submission/'))
        mock_blob.upload_from_string.assert_called_once_with(b'%PDF-1.4 fake content', content_type='application/pdf')

        from apps.core.models import StoredFile
        stored = StoredFile.objects.get(key=meta['key'])
        self.assertEqual(stored.purpose, 'homework_submission')
        self.assertIsNotNone(stored.confirmed_at)
```

Add `from unittest import mock` to the imports at the top of `apps/academic/tests/test_homework.py` (line 1, alongside `import datetime`).

The other three tests in `HomeworkFileUploadTests` (`test_oversized_file_rejected`, `test_disallowed_content_type_rejected`, `test_returned_key_round_trips_through_submit_homework`) call `store_homework_submission_file` with `validate_submission_files` still enforced first (unchanged), so they need no GCS mock — `InvalidSubmissionFilesError` is raised before any storage call. `test_returned_key_round_trips_through_submit_homework` (line 431) does reach the storage call, so add the same `@mock.patch('apps.core.storage._client')` decorator to it too.

Also update `HomeworkFileUploadViewTests.test_upload_file_via_api` (line 451) and `test_cross_tenant_upload_returns_404` (line 462), since both go through the real `upload_file` view action which will now call GCS:

```python
    @mock.patch('apps.core.storage._client')
    def test_upload_file_via_api(self, mock_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=self.fx['teacher_user'])
        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        res = self.client.post(
            f'/api/v1/academic/homework/{self.homework.id}/upload-file/', {'file': upload}, format='multipart',
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['filename'], 'tugas.pdf')

    @mock.patch('apps.core.storage._client')
    def test_cross_tenant_upload_returns_404(self, mock_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fx_b = build_academic_fixture(foundation_name="Yayasan Upload B")
        RoleAssignment.all_tenants.create(
            foundation_id=fx_b['foundation'].id, user=fx_b['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=fx_b['school'].id,
        )
        self.client.force_authenticate(user=fx_b['teacher_user'])
        upload = SimpleUploadedFile('tugas.pdf', b'%PDF-1.4', content_type='application/pdf')
        res = self.client.post(
            f'/api/v1/academic/homework/{self.homework.id}/upload-file/', {'file': upload}, format='multipart',
        )
        self.assertEqual(res.status_code, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.academic.tests.test_homework -v 2`
Expected: FAIL — `test_valid_pdf_upload_written_to_gcs` fails because the implementation still writes to disk; `assert_called_once` on `upload_from_string` fails (never called).

- [ ] **Step 3: Migrate the implementation**

Replace `store_homework_submission_file` (lines 542-572 of `apps/academic/services.py`):

```python
def store_homework_submission_file(homework: Homework, uploaded_file) -> dict:
    """ACD-027: validate and persist one uploaded homework attachment, returning the
    {key, filename, size, content_type} dict submit_homework's `files` list expects.

    Reuses validate_submission_files (wrapped as a one-item list) rather than
    duplicating its size/content-type rules. Written directly to GCS via
    core.write_generated_file, catalogued as a core.StoredFile row (ARC-026,
    ARC-030) — no raw filesystem write.
    """
    file_meta = {
        'filename': uploaded_file.name,
        'size': uploaded_file.size,
        'content_type': uploaded_file.content_type,
    }
    validate_submission_files([file_meta])

    data = b''.join(uploaded_file.chunks())
    stored_file = write_generated_file(
        purpose='homework_submission', filename=uploaded_file.name,
        data=data, content_type=uploaded_file.content_type,
        foundation_id=homework.foundation_id,
    )

    return {
        'key': stored_file.key,
        'filename': uploaded_file.name,
        'size': uploaded_file.size,
        'content_type': uploaded_file.content_type,
    }
```

Remove the now-unused imports at the top of `apps/academic/services.py`: `from pathlib import Path` (line 8) and `from uuid import uuid4` (line 9). **Do not** remove `from django.conf import settings` (line 12) yet — `render_report_card_pdf` (Task 6) still uses it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.academic.tests.test_homework -v 2`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add apps/academic/services.py apps/academic/tests/test_homework.py
git commit -m "feat(academic): migrate store_homework_submission_file off MEDIA_ROOT onto StoredFile"
```

---

### Task 6: Migrate `render_report_card_pdf`

**Files:**
- Modify: `apps/academic/services.py:1544-1560` (the function body), plus imports: remove `from pathlib import Path` reference is already gone (Task 5); now remove `from django.conf import settings` (line 12, now unused) since this is the last user of `settings.MEDIA_ROOT` in the file
- Modify: `apps/academic/tests/test_report_cards.py` — add a `_client` patch to `setUp` of `ReportCardStateMachineTests` (line 81), `ArrearsGateTests` (line 114), `SetReportCardContentTests` (line 236), `ReportCardContentViewTests` (line 336)

**Interfaces:**
- Consumes: `apps.core.services.write_generated_file` (Task 3, already imported in Task 5).
- Produces: `render_report_card_pdf(report_card) -> str` — same return type (the GCS key string) as before; `ReportCard.pdf_key` column format unchanged from the consumer's point of view (still an opaque key string).

- [ ] **Step 1: Update tests first**

For each of the four test classes below, add a `setUp`-level patch so `publish_report_card` (which calls `render_report_card_pdf`) doesn't hit real GCS. Example for `ReportCardStateMachineTests` (currently at line 81-86 of `apps/academic/tests/test_report_cards.py`):

```python
class ReportCardStateMachineTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
```

Apply the identical two-line addition (`patcher = mock.patch(...)`, `self.mock_gcs_client = patcher.start()`, `self.addCleanup(patcher.stop)`) to the `setUp` methods of `ArrearsGateTests` (line 114), `SetReportCardContentTests` (line 236), and `ReportCardContentViewTests` (line 336) — each already has a `setUp` that builds a report card before calling `publish_report_card` later in individual test methods.

Add `from unittest import mock` to the top of `apps/academic/tests/test_report_cards.py` if not already present.

No new assertions are needed in the existing test bodies — `self.assertTrue(published.pdf_key)` (line 96) already passes with a GCS-backed key, since `write_generated_file` still returns a non-empty key string.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.academic.tests.test_report_cards -v 2`
Expected: PASS still (the mock is additive and doesn't change behavior yet) — this step confirms the test file itself has no syntax errors before the implementation changes. Then run once more after Step 3 to confirm the real behavior switch.

- [ ] **Step 3: Migrate the implementation**

Replace `render_report_card_pdf` (lines 1544-1560 of `apps/academic/services.py`):

```python
def render_report_card_pdf(report_card: ReportCard) -> str:
    """Render and persist the report card document. Falls back to HTML if weasyprint's
    native libraries are unavailable in this environment (see memory/01_PROJECT.md).
    Written to GCS via core.write_generated_file, catalogued as a core.StoredFile
    row (ARC-026, ARC-030) — no raw filesystem write.
    """
    html_content = render_report_card_html(report_card)

    try:
        from weasyprint import HTML
        filename = f"{report_card.id}_v{report_card.version}.pdf"
        data = HTML(string=html_content).write_pdf()
        content_type = 'application/pdf'
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML rapor output", exc_info=True)
        filename = f"{report_card.id}_v{report_card.version}.html"
        data = html_content.encode('utf-8')
        content_type = 'text/html'

    stored_file = write_generated_file(
        purpose='report_card_pdf', filename=filename, data=data,
        content_type=content_type, foundation_id=report_card.foundation_id,
    )
    return stored_file.key
```

Remove the now-unused `from django.conf import settings` (line 12 of `apps/academic/services.py`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.academic.tests.test_report_cards -v 2`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add apps/academic/services.py apps/academic/tests/test_report_cards.py
git commit -m "feat(academic): migrate render_report_card_pdf off MEDIA_ROOT onto StoredFile"
```

---

### Task 7: Migrate `generate_settlement_statement_pdf`

**Files:**
- Modify: `apps/wallet/services.py:630-647` (the function body), plus imports: remove `from pathlib import Path` (line 4) and `from django.conf import settings` (line 6, both unused after this change — verified no other `Path(`/`settings.` usage exists in this file); add `from apps.core.services import write_generated_file` next to the existing `from apps.core.services import audit` (line 12)
- Modify: `apps/wallet/tests/test_settlement.py` — add a `_client` patch to `RunSettlementTests.setUp` (line 22)

**Interfaces:**
- Consumes: `apps.core.services.write_generated_file` (Task 3).
- Produces: `generate_settlement_statement_pdf(settlement) -> str` — same return type (GCS key string); `MerchantSettlement.statement_pdf_key` column format unchanged.

- [ ] **Step 1: Update the test first**

Modify `RunSettlementTests.setUp` (lines 22-28 of `apps/wallet/tests/test_settlement.py`):

```python
class RunSettlementTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')
        self.today = timezone.localdate()
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
```

Add `from unittest import mock` to the top of `apps/wallet/tests/test_settlement.py` (line 1, alongside `import datetime`).

`test_generate_statement_produces_pdf_key` (lines 66-72) needs no other change — it already only asserts `key` is truthy and round-trips onto `settlement.statement_pdf_key`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.wallet.tests.test_settlement -v 2`
Expected: PASS still (mock is additive, confirms no syntax errors before the implementation change).

- [ ] **Step 3: Migrate the implementation**

Replace `generate_settlement_statement_pdf` (lines 630-647 of `apps/wallet/services.py`):

```python
def generate_settlement_statement_pdf(settlement: MerchantSettlement) -> str:
    """Falls back to HTML if weasyprint's native libraries are unavailable in
    this environment. Written to GCS via core.write_generated_file, catalogued
    as a core.StoredFile row (ARC-026, ARC-030) — no raw filesystem write."""
    html_content = render_settlement_statement_html(settlement)

    try:
        from weasyprint import HTML
        filename = f"{settlement.id}.pdf"
        data = HTML(string=html_content).write_pdf()
        content_type = 'application/pdf'
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML settlement statement", exc_info=True)
        filename = f"{settlement.id}.html"
        data = html_content.encode('utf-8')
        content_type = 'text/html'

    stored_file = write_generated_file(
        purpose='settlement_statement', filename=filename, data=data,
        content_type=content_type, foundation_id=settlement.foundation_id,
    )
    settlement.statement_pdf_key = stored_file.key
    settlement.save(update_fields=['statement_pdf_key', 'updated_at'])
    return stored_file.key
```

Remove the now-unused imports at the top of `apps/wallet/services.py`: `from pathlib import Path` (line 4) and `from django.conf import settings` (line 6).

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.wallet.tests.test_settlement -v 2`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add apps/wallet/services.py apps/wallet/tests/test_settlement.py
git commit -m "feat(wallet): migrate generate_settlement_statement_pdf off MEDIA_ROOT onto StoredFile"
```

---

### Task 8: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python manage.py test`
Expected: PASS, zero failures, zero errors.

- [ ] **Step 2: Grep for any remaining raw MEDIA_ROOT writes tied to this feature**

Run: `grep -rn "MEDIA_ROOT" apps/`
Expected: no matches in `apps/academic/services.py` or `apps/wallet/services.py`. (`MEDIA_URL`/`MEDIA_ROOT` may still appear in `educore/settings/base.py` — that's the Django setting definition itself, out of scope.)

- [ ] **Step 3: Commit (only if Steps 1-2 required fixes)**

```bash
git add -A
git commit -m "fix: address regressions found in full suite run"
```
