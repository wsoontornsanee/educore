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
