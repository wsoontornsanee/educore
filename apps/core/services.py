"""Core service helpers for auditing, asynchronous task enqueueing, and domain events."""
import base64
import hashlib
import logging

try:
    from google.cloud.exceptions import NotFound
except ImportError:
    class NotFound(Exception):
        """Fallback exception when google-cloud-storage is not installed."""
        pass

from django.conf import settings
from django.utils import timezone
from educore.middleware.tenancy import get_current_foundation_id
from educore.middleware.audit import get_current_actor, get_current_ip
from .models import AuditEvent, DomainEvent, StoredFile, TaskQueue
from . import storage

logger = logging.getLogger(__name__)

# Registry for task handlers invoked by drain_tasks
_TASK_HANDLERS = {}

def register_task_handler(task_type):
    """Decorator to register a callable as a handler for task_type."""
    def decorator(fn):
        _TASK_HANDLERS[task_type] = fn
        return fn
    return decorator

def get_task_handler(task_type):
    """Retrieve registered handler for task_type."""
    return _TASK_HANDLERS.get(task_type)

def audit(action, entity_type, entity_id, actor_id=None, role='', foundation_id=None, school_id=None, ip_address=None, diff=None):
    """Write an explicit, immutable audit event (ARC-008, spec/01 §8.5).
    
    All mutating actions in business apps MUST invoke this helper.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if actor_id is None:
        current_actor = get_current_actor()
        if current_actor:
            actor_id = str(getattr(current_actor, 'pk', current_actor))
            if hasattr(current_actor, 'role') and not role:
                role = str(current_actor.role)

    if ip_address is None:
        ip_address = get_current_ip()

    return AuditEvent.objects.create(
        actor_id=actor_id,
        role=role,
        foundation_id=foundation_id,
        school_id=school_id,
        ip_address=ip_address,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        diff=diff or {},
    )

def enqueue_task(task_type, payload=None, foundation_id=None, run_after=None, max_attempts=4):
    """Enqueue an asynchronous task in MySQL TaskQueue (ARC-010).
    
    Safe to call inside transactional atomic blocks.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if run_after is None:
        run_after = timezone.now()

    return TaskQueue.objects.create(
        foundation_id=foundation_id,
        task_type=task_type,
        payload=payload or {},
        run_after=run_after,
        max_attempts=max_attempts,
        status=TaskQueue.STATUS_PENDING,
    )

def record_domain_event(name, payload=None, foundation_id=None):
    """Record a transactional domain event (ARC-010, spec/01 §8.4).
    
    MUST be written in the same transaction as state changes.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if foundation_id is None:
        raise ValueError("Cannot record DomainEvent without an explicit or thread-local foundation_id.")

    return DomainEvent.objects.create(
        foundation_id=foundation_id,
        name=name,
        payload=payload or {},
    )


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
    try:
        metadata = storage.get_blob_metadata(stored_file.key)
    except NotFound:
        raise InvalidUploadError("UPLOAD_NOT_FOUND: the object has not been uploaded to GCS yet.")

    rules = PURPOSE_RULES.get(stored_file.purpose)
    if rules and metadata['size'] > rules['max_size']:
        raise InvalidUploadError("FILE_TOO_LARGE: uploaded object exceeds the limit for its purpose.")

    checksum_hex = base64.b64decode(metadata['md5_hash']).hex() if metadata['md5_hash'] else ''

    stored_file.size = metadata['size']
    stored_file.checksum = checksum_hex
    stored_file.confirmed_at = timezone.now()
    stored_file.save(update_fields=['size', 'checksum', 'confirmed_at', 'updated_at'])
    return stored_file


def write_generated_file(purpose, filename, data, content_type, foundation_id=None, uploaded_by=None, school_id=None, key=None):
    """For server-generated files (PDFs): write bytes directly to GCS and
    create an already-confirmed StoredFile row in one step — no two-phase
    commit needed since the server itself performed the write.

    Pass an explicit `key` (e.g. via storage.build_deterministic_object_key)
    for documents that represent one canonical rendering per entity — a
    re-render then overwrites the same GCS object and updates the same
    StoredFile row (matched by the unique `key`) instead of creating a new
    row and orphaning the previous object. Omit `key` to get the default
    uuid-suffixed key, which always creates a fresh row.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if key is None:
        key = storage.build_object_key(purpose, filename)
    storage.upload_bytes(key, data, content_type)

    # Known race: two concurrent calls for the same deterministic key can both
    # miss on the get() below and collide on StoredFile.key's unique
    # constraint. Same unguarded update_or_create pattern already accepted
    # elsewhere (SchoolQrisConfig.objects.update_or_create) — acceptable
    # while callers are single manual actions per entity.
    stored_file, _created = StoredFile.all_tenants.update_or_create(
        key=key,
        defaults={
            'foundation_id': foundation_id,
            'school_id': school_id,
            'bucket': settings.GCS_BUCKET_NAME,
            'purpose': purpose,
            'content_type': content_type,
            'size': len(data),
            'checksum': hashlib.md5(data, usedforsecurity=False).hexdigest(),
            'confirmed_at': timezone.now(),
            'uploaded_by': uploaded_by or '',
        },
    )
    return stored_file
