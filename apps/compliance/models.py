"""Compliance & statutory reporting models (spec/14 §4, CMP-017..CMP-022)."""
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantModel


class StatutorySystem(models.TextChoices):
    """Government reporting systems (CMP-017, CMP-019)."""
    DAPODIK = 'DAPODIK', _('DAPODIK (Kemendikbudristek)')
    EMIS = 'EMIS', _('EMIS (Kemenag Madrasah)')


class StatutoryExportSchema(TenantModel):
    """
    Statutory export schema version as configurable data, not code (CMP-020).

    Ministry formats change between cycles, so the column mapping lives here.
    `field_map` maps EduCore field paths to the statutory column names for
    each entity sheet, e.g.::

        {
            "students": {
                "nisn": "nisn",
                "person.full_name": "nama",
            },
            ...
        }

    An inactive schema is never used for new exports; a newer version takes
    over by simply being the active row for its system.
    """
    system = models.CharField(
        max_length=16,
        choices=StatutorySystem.choices,
        db_index=True,
        help_text=_("Which ministry reporting system this schema targets"),
    )
    version = models.CharField(
        max_length=32,
        help_text=_("Statutory schema version label, e.g. '2026.1' — data, not code (CMP-020)"),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Only the active schema version is used for new exports"),
    )
    field_map = models.JSONField(
        default=dict,
        help_text=_(
            "Entity sheet -> {EduCore field path -> statutory column name}. "
            "Entity sheets: students, staff, rombel."
        ),
    )
    mandatory_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Optional mandatory field paths per entity sheet for pre-export validation (CMP-018, CMP-020). "
            "E.g., {'students': ['person.mother_name', 'person.home_latitude', 'person.home_longitude']}"
        ),
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'statutory_export_schemas'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'system', 'version'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_active_statutory_schema_per_foundation_system_version',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'system', 'is_active']),
        ]

    def __str__(self):
        return f"{self.system} v{self.version} ({'active' if self.is_active else 'inactive'})"


class PiiExportAccessLog(TenantModel):
    """
    Access audit log for PII-bearing exports (spec/14 §3 CMP-016, spec/15 §2 RPT-004).

    Immutable audit record capturing who exported which personal data, when,
    under what filters, and with what visible watermark.
    """
    export_job = models.ForeignKey(
        'core.ExportJob',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pii_access_logs',
        help_text=_("Associated async export job if generated via ExportJob pipeline"),
    )
    report_key = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Registered report key, e.g. 'statutory_dapodik', 'statutory_emis'"),
    )
    format = models.CharField(
        max_length=16,
        help_text=_("Export format (e.g. XLSX, CSV)"),
    )
    exported_by_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("User ID of the requesting actor"),
    )
    exported_by_name = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text=_("Full name of the requesting actor"),
    )
    school_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Target school ID if the export was school-scoped"),
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Filters applied to the export query"),
    )
    record_count = models.IntegerField(
        default=0,
        help_text=_("Number of records/rows exported"),
    )
    watermark_text = models.TextField(
        blank=True,
        default='',
        help_text=_("Visible watermark text stamped on the document"),
    )
    file_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=_("Generated filename"),
    )
    file_size = models.PositiveIntegerField(
        default=0,
        help_text=_("File size in bytes"),
    )
    download_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of times download link was accessed"),
    )
    last_downloaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time the download link was retrieved"),
    )

    class Meta:
        db_table = 'pii_export_access_logs'
        indexes = [
            models.Index(fields=['foundation_id', '-created_at']),
            models.Index(fields=['foundation_id', 'report_key', '-created_at']),
            models.Index(fields=['foundation_id', 'exported_by_id', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"PiiExportAccessLog#{self.id} {self.report_key} by {self.exported_by_name or self.exported_by_id} at {self.created_at}"


class DataSubjectRequestSubjectType(models.TextChoices):
    """Who the request is about (CMP-011/012)."""
    STUDENT = 'STUDENT', _('Siswa')
    STAFF = 'STAFF', _('Staf')


class DataSubjectRequestStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', _('Selesai')
    REFUSED = 'REFUSED', _('Ditolak')


class ConsentPurpose(models.TextChoices):
    """Per-purpose consent categories (CMP-009): "Consent is per-purpose,
    never bundled" — one purpose never implies another."""
    BIOMETRIC = 'BIOMETRIC', _('Pendaftaran Biometrik')
    PHOTO_MEDIA = 'PHOTO_MEDIA', _('Penggunaan Foto/Media')
    HEALTH_DATA = 'HEALTH_DATA', _('Pemrosesan Data Kesehatan')
    MARKETING = 'MARKETING', _('Komunikasi Pemasaran')


class ConsentRecord(TenantModel):
    """Versioned, timestamped per-purpose consent (CMP-009).

    Append-only: a fresh grant creates a new row with `version` incremented
    for that (subject, purpose) rather than mutating a prior grant, so the
    consent history stays fully auditable. Withdrawal stamps `withdrawn_at`
    on the current (highest-version, not-yet-withdrawn) row for that purpose
    — see `apps.compliance.services.withdraw_biometric_consent`, which is the
    CMP-010 "admin-initiated equivalent" of the (not-yet-built) parent-app
    withdrawal action.
    """
    subject_type = models.CharField(max_length=16, choices=DataSubjectRequestSubjectType.choices, db_index=True)
    subject_id = models.BigIntegerField(help_text="Student.id or Staff.id")
    purpose = models.CharField(max_length=16, choices=ConsentPurpose.choices, db_index=True)
    version = models.PositiveIntegerField(default=1)
    granted_at = models.DateTimeField()
    granted_by = models.CharField(max_length=64, blank=True, default='', help_text=_("User ID of the guardian/admin who recorded the grant"))
    withdrawn_at = models.DateTimeField(null=True, blank=True, db_index=True)
    withdrawn_by = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'consent_records'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'subject_type', 'subject_id', 'purpose', 'version'],
                name='unique_consent_version_per_subject_purpose',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'subject_type', 'subject_id', 'purpose']),
            models.Index(fields=['foundation_id', 'purpose', 'withdrawn_at']),
        ]
        ordering = ['-version']

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None

    def __str__(self):
        state = 'withdrawn' if self.withdrawn_at else 'active'
        return f"{self.subject_type}#{self.subject_id} {self.purpose} v{self.version} ({state})"


class DataSubjectRequest(TenantModel):
    """Right-to-erasure request audit trail (CMP-012): the "admin tool, not a
    manual SQL task" spec/14 §3 requires. Runs synchronously to a terminal
    outcome (COMPLETED or REFUSED) in one call — there is no PENDING state.

    Access-export requests (CMP-011) are NOT tracked here: they reuse the
    existing ExportJob + PiiExportAccessLog pipeline (CMP-016), which already
    is that domain's "admin tool, not manual SQL" audit trail — see
    apps/compliance/exports.py's `dsar_access` report_key (Task 2).
    """
    subject_type = models.CharField(max_length=16, choices=DataSubjectRequestSubjectType.choices, db_index=True)
    subject_id = models.BigIntegerField(
        help_text="Student.id or Staff.id — a plain integer, not an FK, so this row "
                   "survives the subject's Person row being anonymized."
    )
    status = models.CharField(max_length=16, choices=DataSubjectRequestStatus.choices, db_index=True)
    requested_by = models.CharField(max_length=64, blank=True, default='')
    requested_by_name = models.CharField(max_length=128, blank=True, default='')
    refusal_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'data_subject_erasure_requests'
        indexes = [
            models.Index(fields=['foundation_id', 'subject_type', 'subject_id']),
            models.Index(fields=['foundation_id', 'status']),
        ]

    def __str__(self):
        return f"Erasure {self.subject_type}#{self.subject_id} ({self.status})"
