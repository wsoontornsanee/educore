from django.db import models
from django.utils.translation import gettext_lazy as _

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

    class Meta(TenantModel.Meta):
        db_table = 'devices'
        verbose_name = _('Device')
        verbose_name_plural = _('Devices')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'device_code'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_active_device_per_school'
            )
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status'], name='idx_dev_fnd_sch_st'),
            models.Index(fields=['foundation_id', 'device_code'], name='idx_dev_fnd_code'),
            models.Index(fields=['foundation_id', 'device_class'], name='idx_dev_fnd_class'),
        ]

    def __str__(self):
        return f"{self.name} ({self.device_code}) - {self.get_device_class_display()}"
