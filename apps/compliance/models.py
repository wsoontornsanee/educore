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
