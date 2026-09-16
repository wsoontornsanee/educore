# StoredFile + Signed GCS Upload Pipeline — Design

Source: Notion [Open Item] core.StoredFile + Signed GCS Upload Pipeline Missing
(ARC-026, ARC-030) — https://app.notion.com/p/3dd347a6659481d8970fc6291e52ad7b

## Citation correction

The Notion item and `docs/frontend-plan.md` cited `ARC-026`/`ARC-030` and
`specs/01-platform-architecture.md:181-185` as the spec source for this
requirement. Verified against the actual repo: no `ARC-026`/`ARC-030` label
exists anywhere under `spec/`, and `spec/01-platform-architecture.md:181-185`
covers §8.1 idempotency, not file storage or StoredFile. The citation was
fabricated. `docs/frontend-plan.md:52` has been corrected to remove the false
citation.

This build proceeds anyway as an **engineering-driven gap**, not a spec
violation: raw `MEDIA_ROOT` writes are a real risk on any deployment without a
persistent local disk (e.g. Cloud Run–style compute), and
`apps/core/storage.py` already has signed-URL helpers half-built toward this
exact design, suggesting the direction was already underway independent of
the fabricated citation.

## Current state

- `apps/core/storage.py` already has `build_object_key`, `generate_upload_url`
  (signed v4 PUT), `generate_download_url` (signed v4 GET), backed by
  `GCS_BUCKET_NAME` / `GCS_PATH_PREFIX` / `GCS_CREDENTIALS_PATH` in
  `educore/settings/base.py:163-171`.
- No `core.StoredFile` model exists.
- No upload/confirm API endpoint calls the existing signed-URL helpers.
- Three call sites still write raw bytes to `MEDIA_ROOT` via `pathlib`:
  - `apps/academic/services.py::store_homework_submission_file` (server
    receives a multipart upload from the client, then writes to disk).
  - `apps/academic/services.py::render_report_card_pdf` (server-generated
    PDF/HTML, writes to disk, key stored on `ReportCard.pdf_key`).
  - `apps/wallet/services.py::generate_settlement_statement_pdf`
    (server-generated PDF/HTML, writes to disk, key stored on
    `MerchantSettlement.statement_pdf_key`).

## Design

### 1. Model — `apps.core.models.StoredFile`

Extends `TenantModel` (reuses `foundation_id`, `created_at`, `updated_at`,
`deleted_at`, soft-delete). Adds a plain `school_id` `BigIntegerField`
(nullable, indexed) following the same non-FK pattern as `AuditEvent`.

Fields: `bucket`, `key` (unique), `purpose`, `content_type`, `size` (nullable
until confirmed), `checksum`, `uploaded_by` (actor id string, matching
`created_by` convention), `confirmed_at` (null while pending).

`db_table = 'stored_files'`. Indexes: `(purpose, deleted_at)`, unique on
`key`.

### 2. `apps/core/storage.py` — new helpers

- `upload_bytes(key, data: bytes, content_type)` — direct server-side write
  via `blob.upload_from_string`. Used by server-generated files (PDFs).
- `get_blob_metadata(key) -> dict` — `blob.reload()`, returns
  `{size, content_type, md5_hash}`. Used by the confirm step to record the
  real (not client-declared) size/checksum.

### 3. `apps/core/services.py` — new functions

- `initiate_upload(purpose, filename, content_type, size, foundation_id=None, school_id=None, uploaded_by=None) -> (StoredFile, upload_url)`
  Validates against a `PURPOSE_RULES` dict (starts with one entry,
  `homework_submission`, reusing `apps.academic.constants.ALLOWED_SUBMISSION_CONTENT_TYPES`
  / `MAX_SUBMISSION_FILE_SIZE`). Creates a pending `StoredFile` row
  (`confirmed_at=None`), calls `build_object_key` + `generate_upload_url`.
- `confirm_upload(stored_file_id, actor=None) -> StoredFile` — fetches real
  GCS metadata via `get_blob_metadata`, sets `size`, `checksum`,
  `confirmed_at=now()`. Raises if the object doesn't exist in GCS yet.
- `write_generated_file(purpose, filename, data: bytes, content_type, foundation_id=None) -> StoredFile`
  — for server-generated files: builds the key, calls `upload_bytes`,
  creates an **already-confirmed** `StoredFile` row (server did the write
  itself, no two-phase commit needed).

New purposes (e.g. for future upload types) are added by extending
`PURPOSE_RULES` — no schema change.

### 4. New generic API — `apps/core/{views,serializers,urls}.py`

Mounted at `api/v1/files/` in `educore/urls.py`. Additive only — does not
change any existing endpoint's wire contract.

- `POST /uploads/` — body `{purpose, filename, content_type, size}` → calls
  `initiate_upload`, returns `{id, key, upload_url, expires_at}`.
- `POST /uploads/{id}/confirm/` — calls `confirm_upload`, returns the
  serialized `StoredFile`.

This is for future client-direct-upload UIs (mobile/web uploading without
proxying bytes through the server). No current caller needs it yet, but it's
the two-phase-commit primitive the Notion item asked for. Permission: any
authenticated user (`IsAuthenticated`) may call it — no per-`purpose` RBAC
yet, since `PURPOSE_RULES` itself is the gate on what can be uploaded. Add
per-purpose permission checks when a purpose needs one.

### 5. Migrate the three existing writers (no API contract changes)

- `store_homework_submission_file` — replace the `pathlib` disk write with
  `upload_bytes` + create a confirmed `StoredFile` row (`purpose='homework_submission'`).
  Returns the same `{key, filename, size, content_type}` shape callers
  already expect.
- `render_report_card_pdf` — replace the disk write with `write_generated_file`
  (`purpose='report_card_pdf'`). `ReportCard.pdf_key` keeps storing the same
  opaque key string — no serializer or consumer change.
- `generate_settlement_statement_pdf` — same pattern
  (`purpose='settlement_statement'`), `MerchantSettlement.statement_pdf_key`
  unchanged.

The weasyprint-unavailable HTML fallback in both PDF functions is preserved;
only the destination of the write changes (GCS instead of disk), and the
`content_type` passed to `write_generated_file` reflects whichever format
was actually produced (`application/pdf` or `text/html`).

### 6. Testing

- Mock `apps.core.storage._client`, matching the existing pattern in
  `apps/core/tests/test_storage.py`.
- New tests: `StoredFile` model basics, `initiate_upload`/`confirm_upload`
  (including the purpose-validation failure path), the two new upload/confirm
  endpoints, and `upload_bytes`/`get_blob_metadata`.
- Updated tests for the three migrated writers: assert a GCS call happens and
  no file is written under `MEDIA_ROOT`, and that a confirmed `StoredFile` row
  is created with the right `purpose`.

## Out of scope (logged separately)

- Signed-GET download endpoints for `pdf_key` / `statement_pdf_key` — logged
  as a new Notion Open Item:
  https://app.notion.com/p/3dd347a6659481e5818cefe9caef33ad
- Authorization/size/content-type rules for any `purpose` beyond
  `homework_submission`, `report_card_pdf`, `settlement_statement` — add to
  `PURPOSE_RULES` when a new upload type actually needs one.
