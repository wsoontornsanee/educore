"""Core base models, infrastructure tables, and tenancy primitives.

Adheres strictly to:
- ARC-001, ARC-002, ARC-003 (TenantModel, 3-layer tenancy, audit fields)
- ARC-008 (AuditEvent, JobRun)
- ARC-010, ARC-011, ARC-012 (DomainEvent, TaskQueue)
"""
from django.db import models
from django.utils import timezone
from .managers import TenantManager, AllTenantsManager

class TenantModel(models.Model):
    """Abstract base model for all tenant-scoped entities (ARC-001).
    
    Guarantees foundation_id, timestamps, creator/updater tracking, and soft-delete.
    """
    foundation_id = models.BigIntegerField(db_index=True, help_text="ID of the governing Yayasan (foundation)")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.CharField(max_length=64, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=64, blank=True, null=True)
    deleted_at = models.DateTimeField(blank=True, null=True, db_index=True)

    objects = TenantManager()
    all_tenants = AllTenantsManager()

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['foundation_id', 'deleted_at']),
        ]

    def delete(self, using=None, keep_parents=False):
        """Perform soft delete by marking deleted_at timestamp (spec/appendix §2.3)."""
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])

    def hard_delete(self):
        """Perform physical deletion from database."""
        super().delete()

    def restore(self):
        """Restore a soft-deleted entity."""
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])

    @property
    def is_deleted(self):
        return self.deleted_at is not None

class AuditEvent(models.Model):
    """Append-only audit trail table (ARC-008, spec/01 §8.5)."""
    id = models.BigAutoField(primary_key=True)
    actor_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    role = models.CharField(max_length=64, blank=True, default='')
    foundation_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    school_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    action = models.CharField(max_length=128, db_index=True, help_text="e.g. finance.invoice.issue, academic.grade.update")
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.CharField(max_length=64, db_index=True)
    diff = models.JSONField(blank=True, null=True, help_text="Before/after JSON diff with PII redacted")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'audit_events'
        indexes = [
            models.Index(fields=['foundation_id', 'timestamp']),
            models.Index(fields=['entity_type', 'entity_id']),
            models.Index(fields=['foundation_id', 'action']),  # FND-010: module/action filter
        ]

    def __str__(self):
        return f"{self.timestamp} - {self.actor_id or 'system'} - {self.action} ({self.entity_type}#{self.entity_id})"

class DomainEvent(models.Model):
    """Database-backed transactional domain event model (ARC-010, spec/01 §8.4)."""
    id = models.BigAutoField(primary_key=True)
    foundation_id = models.BigIntegerField(db_index=True)
    name = models.CharField(max_length=128, db_index=True, help_text="module.entity.verb format")
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'domain_events'
        indexes = [
            models.Index(fields=['foundation_id', 'occurred_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.id}) - Foundation: {self.foundation_id}"

class TaskQueue(models.Model):
    """Asynchronous tasks queue stored in MySQL (ARC-010, ARC-011, ARC-012)."""
    STATUS_PENDING = 'PENDING'
    STATUS_RUNNING = 'RUNNING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_DEAD_LETTER = 'DEAD_LETTER'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_DEAD_LETTER, 'Dead Letter'),
    ]

    id = models.BigAutoField(primary_key=True)
    foundation_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    task_type = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict)
    run_after = models.DateTimeField(db_index=True, help_text="Earliest timestamp task can run")
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=4)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=128, null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    error_text = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'task_queue'
        indexes = [
            models.Index(fields=['status', 'run_after']),
        ]

    def __str__(self):
        return f"Task #{self.id} [{self.task_type}] - {self.status}"

class JobRun(models.Model):
    """Scheduled cron execution record (ARC-008, spec/01 §5)."""
    STATUS_RUNNING = 'RUNNING'
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'

    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    id = models.BigAutoField(primary_key=True)
    job_name = models.CharField(max_length=128, db_index=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    items_processed = models.PositiveIntegerField(default=0)
    error_text = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'job_runs'
        indexes = [
            models.Index(fields=['job_name', 'started_at']),
        ]

    def __str__(self):
        return f"JobRun {self.job_name} ({self.status}) at {self.started_at}"

class IdempotencyRecord(models.Model):
    """Idempotency store for API endpoints (spec/01 §8.1)."""
    key = models.CharField(max_length=128, primary_key=True)
    endpoint = models.CharField(max_length=255, db_index=True)
    request_hash = models.CharField(max_length=64)
    response_body = models.TextField(blank=True, default='')
    response_status = models.PositiveIntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'idempotency_records'

    def __str__(self):
        return f"Idempotency {self.key} for {self.endpoint}"

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
    checksum = models.CharField(max_length=64, blank=True, default='')  # stored as a hex digest (e.g. md5 hexdigest)
    uploaded_by = models.CharField(max_length=64, blank=True, default='')
    confirmed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'stored_files'
        indexes = [
            models.Index(fields=['purpose', 'deleted_at']),
            models.Index(fields=['foundation_id', 'purpose'], name='idx_storedfile_fnd_purpose'),
        ]

    def __str__(self):
        return f"{self.purpose}:{self.key}"

class ExportJob(TenantModel):
    """Async report export job (ARC-010, RPT-002/003, FND-014).

    All exports run through TaskQueue regardless of expected row count — the
    RPT-002 "over 10,000 rows" async threshold is trivially satisfied by
    always being async, avoiding a separate sync code path for small exports.
    """
    STATUS_PENDING = 'PENDING'
    STATUS_RUNNING = 'RUNNING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    FORMAT_PDF = 'PDF'
    FORMAT_XLSX = 'XLSX'
    FORMAT_CSV = 'CSV'
    FORMAT_CHOICES = [(FORMAT_PDF, 'PDF'), (FORMAT_XLSX, 'XLSX'), (FORMAT_CSV, 'CSV')]

    report_key = models.CharField(max_length=64, db_index=True, help_text="Registered export-renderer key, e.g. 'foundation_dashboard'")
    format = models.CharField(max_length=16, choices=FORMAT_CHOICES)
    filters = models.JSONField(default=dict)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    result_key = models.CharField(max_length=500, blank=True, default='', help_text="StoredFile key once COMPLETED")
    error_text = models.TextField(blank=True, default='')
    requested_by = models.CharField(max_length=64, blank=True, default='')
    requested_by_name = models.CharField(max_length=128, blank=True, default='', help_text="Actor display name, for the RPT-003/FND-014 export header")

    class Meta:
        db_table = 'export_jobs'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
        ]

    def __str__(self):
        return f"ExportJob#{self.id} {self.report_key} ({self.status})"


ANALYTICS_EVENT_NAMES = (
    'app_open', 'child_switch', 'invoice_view', 'pay_start',
    'pay_method_selected', 'pay_intent_created', 'pay_completed',
    'topup_completed', 'absence_submitted', 'grades_view',
    'report_card_view', 'notification_opened',
)


class AnalyticsEvent(TenantModel):
    """Product analytics event (spec/08 §5, spec/15 RPT-015).

    Deliberately distinct from AuditEvent: no actor_id, ip_address, or diff —
    RPT-015 requires these events never carry PII, only foundation_id,
    school_id, and role.
    """
    EVENT_NAME_CHOICES = [(name, name) for name in ANALYTICS_EVENT_NAMES]

    event_name = models.CharField(max_length=32, choices=EVENT_NAME_CHOICES, db_index=True)
    school_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    role = models.CharField(max_length=32)
    occurred_at = models.DateTimeField(help_text="Client-reported event time; may differ from created_at for offline-queued events")

    class Meta:
        db_table = 'analytics_events'
        indexes = [
            models.Index(fields=['foundation_id', 'event_name', 'occurred_at']),
            models.Index(fields=['foundation_id', 'occurred_at']),
        ]

    def __str__(self):
        return f"{self.event_name} @ {self.occurred_at} (foundation={self.foundation_id})"
