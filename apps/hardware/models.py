from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import soft_delete_uniqueness_marker
from apps.core.models import TenantModel


class DeviceClass(models.TextChoices):
    GATE_READER = 'GATE_READER', _('Gate Reader (Turnstile)')
    FACE_TERMINAL = 'FACE_TERMINAL', _('Face Terminal')
    POS_TERMINAL = 'POS_TERMINAL', _('POS Terminal')
    KIOSK = 'KIOSK', _('Kiosk')
    HANDHELD = 'HANDHELD', _('Handheld')
    GATEWAY = 'GATEWAY', _('Campus Gateway')


class DeviceDirection(models.TextChoices):
    IN = 'IN', _('Masuk (In)')
    OUT = 'OUT', _('Keluar (Out)')
    BIDIRECTIONAL = 'BIDIRECTIONAL', _('Dua Arah (Bidirectional)')


class DeviceStatus(models.TextChoices):
    ONLINE = 'ONLINE', _('Online')
    OFFLINE = 'OFFLINE', _('Offline')
    DEGRADED = 'DEGRADED', _('Degraded')
    RETIRED = 'RETIRED', _('Retired')


class Device(TenantModel):
    """
    Physical hardware device deployed on campus (spec/12 §2, §4).
    Turnstiles, face cameras, POS terminals, kiosks, and campus gateways.
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='devices',
        help_text=_('School where the device is deployed')
    )
    device_code = models.CharField(
        max_length=64,
        help_text=_('Unique hardware serial number or device identifier')
    )
    name = models.CharField(
        max_length=128,
        help_text=_('Friendly label, e.g. Gerbang Utama Masuk')
    )
    device_class = models.CharField(
        max_length=32,
        choices=DeviceClass.choices,
        default=DeviceClass.GATE_READER,
        db_index=True
    )
    direction = models.CharField(
        max_length=16,
        choices=DeviceDirection.choices,
        default=DeviceDirection.BIDIRECTIONAL
    )
    location = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text=_('Physical campus location')
    )
    status = models.CharField(
        max_length=16,
        choices=DeviceStatus.choices,
        default=DeviceStatus.OFFLINE,
        db_index=True
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text=_('Local IP address on campus LAN')
    )
    mac_address = models.CharField(
        max_length=32,
        blank=True,
        default=''
    )
    firmware_version = models.CharField(
        max_length=64,
        blank=True,
        default=''
    )
    last_heartbeat_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True
    )
    today_event_count = models.PositiveIntegerField(
        default=0
    )
    api_key_hash = models.CharField(
        max_length=128,
        blank=True,
        default=''
    )
    config = models.JSONField(
        default=dict,
        blank=True
    )
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'devices'
        verbose_name = _('Device')
        verbose_name_plural = _('Devices')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'device_code', 'active_uniq_marker'],
                name='unique_active_device_per_school',)
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status'], name='idx_dev_fnd_sch_st'),
            models.Index(fields=['foundation_id', 'device_code'], name='idx_dev_fnd_code'),
            models.Index(fields=['foundation_id', 'device_class'], name='idx_dev_fnd_class'),
        ]

    def __str__(self):
        return f"{self.name} ({self.device_code}) - {self.get_device_class_display()}"


class DeviceUptimeDay(TenantModel):
    """Gate availability samples for one school and day (RPT-013 gate uptime %).

    `check_device_health` (every 10 minutes) adds one sample per gate/face device during the
    school's operational hours: `samples` counts every device sampled, `up_samples` those that were
    reachable (ONLINE or DEGRADED). Uptime % = `up_samples / samples` summed over a week. The repo
    keeps only the latest heartbeat per device, so without these counters no past week's uptime can
    be reconstructed. Counts only, no PII.
    """
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='device_uptime_days')
    date = models.DateField(help_text=_("School-local calendar date"))
    samples = models.PositiveIntegerField(default=0)
    up_samples = models.PositiveIntegerField(default=0)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'device_uptime_days'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'date'], name='idx_devup_fnd_sch_date'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'active_uniq_marker'],
                name='unique_device_uptime_day_per_school_date',
            ),
        ]

    def __str__(self):
        return f"{self.school_id} {self.date}: {self.up_samples}/{self.samples}"


class BiometricSubjectType(models.TextChoices):
    """Mirrors apps.compliance.DataSubjectRequestSubjectType — not imported
    directly to avoid a hardware -> compliance model dependency; compliance
    is the leaf that reads/writes these rows via apps.hardware.services."""
    STUDENT = 'STUDENT', _('Siswa')
    STAFF = 'STAFF', _('Staf')


class BiometricTemplateStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    WITHDRAWN = 'WITHDRAWN', _('Ditarik (Consent Withdrawn)')
    PURGED = 'PURGED', _('Dihapus (Purged)')


class BiometricTemplate(TenantModel):
    """Enrolled face-recognition template (spec/12 §6 HW-021/024, spec/14
    CMP-009/010/012). `apps.hardware` owns this — enrollment is a
    face-terminal-device concern, mirroring where `GateEvent.method=FACE`
    already lives in `apps.attendance`.

    `template_ciphertext` is the encrypted template payload
    (`apps.hardware.crypto`); the raw biometric vector never touches
    plaintext at rest (HW-010, CMP-003). Deletion (CMP-010/012) blanks
    `template_ciphertext` and flips `status` immediately — it does not wait
    for a retention sweeper, though `purge_biometric_templates` (cron)
    exists as the "not by hope" (CMP-013) safety net for any template that
    misses the immediate path (e.g. exit without going through the
    withdrawal service call).
    """
    subject_type = models.CharField(max_length=16, choices=BiometricSubjectType.choices, db_index=True)
    subject_id = models.BigIntegerField(help_text="Student.id or Staff.id")
    device_class = models.CharField(
        max_length=32,
        choices=DeviceClass.choices,
        default=DeviceClass.FACE_TERMINAL,
        help_text=_("Enrollment device class — always FACE_TERMINAL today; kept general per spec/12 §2"),
    )
    consent_id = models.BigIntegerField(
        help_text=_("apps.compliance.ConsentRecord.id this enrollment relies on (CMP-009: no bundling, no template without a purpose-matched consent)"),
    )
    template_ciphertext = models.TextField(
        blank=True,
        default='',
        help_text=_("Fernet-encrypted template payload; blanked on withdrawal/purge"),
    )
    status = models.CharField(max_length=16, choices=BiometricTemplateStatus.choices, default=BiometricTemplateStatus.ACTIVE, db_index=True)
    enrolled_at = models.DateTimeField()
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        db_table = 'biometric_templates'
        verbose_name = _('Biometric Template')
        verbose_name_plural = _('Biometric Templates')
        indexes = [
            models.Index(fields=['foundation_id', 'subject_type', 'subject_id', 'status'], name='idx_biotpl_fnd_subj_st'),
            models.Index(fields=['foundation_id', 'status'], name='idx_biotpl_fnd_st'),
        ]

    def __str__(self):
        return f"BiometricTemplate {self.subject_type}#{self.subject_id} ({self.status})"


class DeviceEventStagingStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    APPLIED = 'APPLIED', _('Applied')
    FAILED = 'FAILED', _('Failed')


class DeviceEventStaging(TenantModel):
    """
    Raw batched device events uploaded by an edge gateway, staged for async
    domain application by the `ingest_device_events` cron (spec/12 §3 HW-005,
    §7 `POST /device/events`).

    Kept separate from `attendance.GateEvent` (the applied domain record) so a
    large offline-reconnect-burst upload (HW-004: up to 72h buffered) can be
    accepted quickly without blocking on attendance/notification processing
    inline in the HTTP request — that inline path stays the primary route for
    normal small, real-time batches via `POST /gate/events/` (ATT-006's <5s
    notification SLA depends on that request finishing fast).
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='device_event_staging_rows',
    )
    device = models.ForeignKey(
        'hardware.Device',
        on_delete=models.PROTECT,
        related_name='staged_events',
    )
    event_uuid = models.UUIDField(
        unique=True,
        db_index=True,
        help_text=_('Client-generated idempotent UUID from edge gateway (HW-005)'),
    )
    payload = models.JSONField(
        help_text=_('Raw event fields as uploaded: occurred_at, raw_uid, direction, method, etc.'),
    )
    status = models.CharField(
        max_length=16,
        choices=DeviceEventStagingStatus.choices,
        default=DeviceEventStagingStatus.PENDING,
        db_index=True,
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    error_text = models.CharField(max_length=500, blank=True, default='')

    class Meta(TenantModel.Meta):
        db_table = 'device_event_staging'
        verbose_name = _('Device Event Staging Row')
        verbose_name_plural = _('Device Event Staging Rows')
        indexes = [
            models.Index(fields=['foundation_id', 'status', 'created_at'], name='idx_devevtstg_fnd_st_crt'),
            models.Index(fields=['foundation_id', 'school', 'status'], name='idx_devevtstg_fnd_sch_st'),
        ]

    def __str__(self):
        return f"{self.event_uuid} [{self.status}]"
