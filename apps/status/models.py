"""Public service status page models (platform-wide, non-tenant).

None of these models are TenantModel — status data has no foundation_id,
matching apps.core.AuditEvent/DomainEvent/JobRun/IdempotencyRecord and
apps.identity.OTPChallenge. See
docs/superpowers/specs/2026-09-18-service-status-page-design.md.
"""
import uuid
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


class StatusIncident(models.Model):
    SEVERITY_MINOR = 'MINOR'
    SEVERITY_MAJOR = 'MAJOR'
    SEVERITY_MAINTENANCE = 'MAINTENANCE'
    SEVERITY_CHOICES = [
        (SEVERITY_MINOR, 'Minor'),
        (SEVERITY_MAJOR, 'Major'),
        (SEVERITY_MAINTENANCE, 'Maintenance'),
    ]

    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    title_id = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200)
    body_id = models.TextField()
    body_en = models.TextField()
    occurred_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField()
    affected_components = models.ManyToManyField(ServiceComponent, related_name='incidents', blank=True)
    published = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'status_incidents'
        ordering = ['-occurred_at']
        verbose_name = 'Insiden Status'
        verbose_name_plural = 'Daftar Insiden Status'

    def __str__(self):
        return f"[{self.severity}] {self.title_id}"


class StatusSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        db_table = 'status_subscribers'
        verbose_name = 'Pelanggan Pembaruan Status'
        verbose_name_plural = 'Daftar Pelanggan Pembaruan Status'

    def __str__(self):
        return self.email
