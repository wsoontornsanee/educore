"""GCS object storage helpers (ARC-026/ARC-030).

Every uploaded object is namespaced under settings.GCS_PATH_PREFIX ('STG' for
local/staging, 'PRD' for production — see educore/settings/base.py and
production.py) so one shared bucket safely holds every environment's objects
without key collisions.

The client authenticates via GCS_CREDENTIALS_PATH (a service-account JSON kept
outside the repo, referenced only through an env var) when set, falling back to
Application Default Credentials otherwise. Never hardcode or commit credentials
here.
"""
from uuid import uuid4

from django.conf import settings


def _client():
    from google.cloud import storage

    if settings.GCS_CREDENTIALS_PATH:
        return storage.Client.from_service_account_json(settings.GCS_CREDENTIALS_PATH)
    return storage.Client()


def build_object_key(purpose: str, filename: str) -> str:
    """Namespaced GCS object key: '<STG|PRD>/<purpose>/<uuid>_<filename>'."""
    return f"{settings.GCS_PATH_PREFIX}/{purpose}/{uuid4().hex}_{filename}"


def generate_upload_url(key: str, content_type: str, expires_seconds: int = 600) -> str:
    """Signed PUT URL a client uploads directly to, bypassing the server (ARC-030)."""
    bucket = _client().bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(key)
    return blob.generate_signed_url(
        version='v4',
        expiration=expires_seconds,
        method='PUT',
        content_type=content_type,
    )


def generate_download_url(key: str, expires_seconds: int = 600) -> str:
    """Signed GET URL for reading back a previously confirmed object."""
    bucket = _client().bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(key)
    return blob.generate_signed_url(version='v4', expiration=expires_seconds, method='GET')


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
