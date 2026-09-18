"""Core service helpers for auditing, asynchronous task enqueueing, and domain events."""
import base64
import datetime
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
from .models import AuditEvent, DomainEvent, ExportJob, StoredFile, TaskQueue
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

# Registries for export jobs (RPT-002/003, FND-014). A business app registers a
# renderer (and optionally a notifier) under its own report_key; core never
# imports business apps or apps.notifications — it only invokes what they hand it,
# the same inversion used by _TASK_HANDLERS above.
_EXPORT_RENDERERS = {}
_EXPORT_NOTIFIERS = {}

def register_export_renderer(report_key):
    """Decorator registering a callable(job: ExportJob) -> (data: bytes, content_type: str, filename: str)."""
    def decorator(fn):
        _EXPORT_RENDERERS[report_key] = fn
        return fn
    return decorator

def get_export_renderer(report_key):
    return _EXPORT_RENDERERS.get(report_key)

def register_export_notifier(report_key):
    """Decorator registering an optional callable(job: ExportJob, download_url: str) -> None,
    invoked once the export completes. A report_key with no registered notifier is silently skipped."""
    def decorator(fn):
        _EXPORT_NOTIFIERS[report_key] = fn
        return fn
    return decorator

def get_export_notifier(report_key):
    return _EXPORT_NOTIFIERS.get(report_key)

_EXPORT_FORMATS = {}
_EXPORT_PERMISSIONS = {}
_EXPORT_PII = set()
_EXPORT_PII_LOGGERS = []

def register_export_formats(report_key, formats):
    """Restrict which ExportJob.FORMAT_* values a report_key accepts (e.g. a CSV-only
    audit export must reject a PDF request instead of silently rendering CSV anyway).
    A report_key with no registration accepts any format registered on ExportJob."""
    _EXPORT_FORMATS[report_key] = set(formats)

def get_export_allowed_formats(report_key):
    """Returns the registered format set for report_key, or None if unregistered
    (caller should then accept any of ExportJob.FORMAT_CHOICES)."""
    return _EXPORT_FORMATS.get(report_key)

def register_export_permission(report_key, permission_key):
    """Register the RBAC permission key required to enqueue an export of report_key,
    when it differs from the export endpoint's own default (e.g. an audit-trail
    export needs 'audit_log.read', not the generic 'school_config.read')."""
    _EXPORT_PERMISSIONS[report_key] = permission_key

def get_export_permission(report_key, default):
    return _EXPORT_PERMISSIONS.get(report_key, default)

def register_export_pii(report_key):
    """Mark a report_key as PII-bearing (spec/14 §3 CMP-016, spec/15 §2 RPT-004).
    PII-bearing exports are automatically watermarked and audited upon generation."""
    _EXPORT_PII.add(report_key)

def is_export_pii(report_key) -> bool:
    return report_key in _EXPORT_PII

def register_pii_export_logger(fn):
    """Register a callback fn(job: ExportJob, filename: str, data: bytes, watermark_text: str, record_count: int) -> None."""
    if fn not in _EXPORT_PII_LOGGERS:
        _EXPORT_PII_LOGGERS.append(fn)
    return fn

def get_pii_export_loggers():
    return list(_EXPORT_PII_LOGGERS)

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


def build_signed_download(key, expires_seconds=600) -> dict:
    """Resolve a GCS object key to a short-lived signed GET URL, for any
    caller exposing a StoredFile-backed key to a client (report card PDFs,
    settlement statements, etc.)."""
    return {
        'download_url': storage.generate_download_url(key, expires_seconds),
        'expires_at': timezone.now() + datetime.timedelta(seconds=expires_seconds),
    }


EXPORT_DOWNLOAD_EXPIRES_SECONDS = 24 * 60 * 60  # RPT-002: 24h-expiring download link


def create_export_job(report_key, export_format, filters=None, foundation_id=None, requested_by=None, requested_by_name=''):
    """Create an ExportJob and enqueue its async render (ARC-010, RPT-002).

    Always async regardless of expected row count — see ExportJob's docstring.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    job = ExportJob.objects.create(
        foundation_id=foundation_id,
        report_key=report_key,
        format=export_format,
        filters=filters or {},
        requested_by=requested_by or '',
        requested_by_name=requested_by_name or '',
    )
    enqueue_task(task_type='core.export.run', payload={'export_job_id': job.id}, foundation_id=foundation_id)
    return job


def get_export_job_status(job_id, foundation_id=None) -> dict:
    """Status payload for GET /foundation/exports/:job_id (and future report-scoped equivalents).

    Returns None if no job with that id exists for the caller's foundation.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    job = ExportJob.all_tenants.filter(id=job_id, foundation_id=foundation_id).first()
    if not job:
        return None

    result = {'job_id': job.id, 'status': job.status, 'download_url': None}
    if job.status == ExportJob.STATUS_COMPLETED and job.result_key:
        signed = build_signed_download(job.result_key, EXPORT_DOWNLOAD_EXPIRES_SECONDS)
        result['download_url'] = signed['download_url']
        result['expires_at'] = signed['expires_at'].isoformat()
    elif job.status == ExportJob.STATUS_FAILED:
        result['error'] = job.error_text
    return result


@register_task_handler('core.export.run')
def run_export_job(payload: dict):
    """Generic export-job worker: looks up the renderer registered for the job's
    report_key, writes its output through the StoredFile pipeline, and notifies
    the requester via that report_key's registered notifier, if any."""
    job = ExportJob.all_tenants.filter(id=payload.get('export_job_id')).first()
    if not job:
        logger.warning("core.export.run: ExportJob #%s not found.", payload.get('export_job_id'))
        return

    renderer = get_export_renderer(job.report_key)
    if not renderer:
        job.status = ExportJob.STATUS_FAILED
        job.error_text = f"No export renderer registered for report_key '{job.report_key}'."
        job.save(update_fields=['status', 'error_text', 'updated_at'])
        return

    job.status = ExportJob.STATUS_RUNNING
    job.save(update_fields=['status', 'updated_at'])

    try:
        data, content_type, filename = renderer(job)
        if is_export_pii(job.report_key):
            from .watermark import watermark_export_data
            data, watermark_text, record_count = watermark_export_data(
                data=data,
                content_type=content_type,
                filename=filename,
                job=job,
            )
            for pii_logger in get_pii_export_loggers():
                try:
                    pii_logger(
                        job=job,
                        filename=filename,
                        data=data,
                        watermark_text=watermark_text,
                        record_count=record_count,
                    )
                except Exception:
                    logger.exception(
                        "core.export.run: PII export logger %s failed for ExportJob #%s",
                        getattr(pii_logger, '__name__', str(pii_logger)),
                        job.id,
                    )

            audit(
                action='EXPORT_PII',
                entity_type='ExportJob',
                entity_id=str(job.id),
                actor_id=job.requested_by or None,
                foundation_id=job.foundation_id,
                diff={
                    'report_key': job.report_key,
                    'format': job.format,
                    'record_count': record_count,
                    'filename': filename,
                },
            )

        stored_file = write_generated_file(
            purpose=f'export_{job.report_key}', filename=filename, data=data,
            content_type=content_type, foundation_id=job.foundation_id, uploaded_by=job.requested_by,
        )
        job.result_key = stored_file.key
        job.status = ExportJob.STATUS_COMPLETED
        job.error_text = ''  # clear any error_text left over from an earlier failed attempt on this job
        job.save(update_fields=['result_key', 'status', 'error_text', 'updated_at'])
    except Exception as exc:
        job.status = ExportJob.STATUS_FAILED
        job.error_text = str(exc)
        job.save(update_fields=['status', 'error_text', 'updated_at'])
        raise

    notifier = get_export_notifier(job.report_key)
    if notifier:
        # The export itself already succeeded and is durably COMPLETED above — a
        # notification failure must not turn this into a retried/dead-lettered
        # task (which would silently re-render and orphan the result_key just written).
        try:
            download_url = build_signed_download(job.result_key, EXPORT_DOWNLOAD_EXPIRES_SECONDS)['download_url']
            notifier(job, download_url)
        except Exception:
            logger.exception("core.export.run: notifier failed for ExportJob #%s (export itself succeeded).", job.id)
