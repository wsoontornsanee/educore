"""Public service status page models (platform-wide, non-tenant).

None of these models are TenantModel — status data has no foundation_id,
matching apps.core.AuditEvent/DomainEvent/JobRun/IdempotencyRecord and
apps.identity.OTPChallenge. See
docs/superpowers/specs/2026-09-18-service-status-page-design.md.
"""
from django.db import models


class ServiceComponent(models.Model):
    STATUS_OPERATIONAL = 'OPERATIONAL'
    STATUS_DEGRADED = 'DEGRADED'
    STATUS_DOWN = 'DOWN'
    STATUS_CHOICES = [
        (STATUS_OPERATIONAL, 'Operational'),
        (STATUS_DEGRADED, 'Degraded'),
        (STATUS_DOWN, 'Down'),
    ]

    key = models.SlugField(max_length=64, unique=True)
    name_id = models.CharField(max_length=128)
    name_en = models.CharField(max_length=128)
    note_id = models.CharField(max_length=255, blank=True, default='')
    note_en = models.CharField(max_length=255, blank=True, default='')
    display_order = models.PositiveIntegerField(default=0)
    manual_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, null=True, blank=True,
        help_text="Staff override. Null means derive status from the daily heartbeat rollup.",
    )

    class Meta:
        db_table = 'status_components'
        ordering = ['display_order', 'id']
        verbose_name = 'Komponen Layanan'
        verbose_name_plural = 'Daftar Komponen Layanan'

    def __str__(self):
        return self.name_id


class ComponentHeartbeat(models.Model):
    component = models.ForeignKey(ServiceComponent, on_delete=models.CASCADE, related_name='heartbeats')
    checked_at = models.DateTimeField(db_index=True)
    is_up = models.BooleanField()
    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'status_component_heartbeats'
        indexes = [models.Index(fields=['component', 'checked_at'])]
        verbose_name = 'Denyut Nadi Komponen'
        verbose_name_plural = 'Daftar Denyut Nadi Komponen'

    def __str__(self):
        return f"{self.component.key} @ {self.checked_at} ({'up' if self.is_up else 'down'})"


class DailyComponentStatus(models.Model):
    component = models.ForeignKey(ServiceComponent, on_delete=models.CASCADE, related_name='daily_statuses')
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=ServiceComponent.STATUS_CHOICES)

    class Meta:
        db_table = 'status_daily_component_status'
        constraints = [
            models.UniqueConstraint(fields=['component', 'date'], name='unique_component_date')
        ]
        indexes = [models.Index(fields=['date'])]
        verbose_name = 'Status Harian Komponen'
        verbose_name_plural = 'Daftar Status Harian Komponen'

    def __str__(self):
        return f"{self.component.key} {self.date} = {self.status}"
