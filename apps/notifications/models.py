import datetime
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, soft_delete_uniqueness_marker
from apps.core.models import TenantModel
from apps.identity.models import School, User


class ChannelType(models.TextChoices):
    WHATSAPP = 'WHATSAPP', _('WhatsApp')
    PUSH = 'PUSH', _('Push Notification')
    SMS = 'SMS', _('SMS')
    EMAIL = 'EMAIL', _('Email')
    IN_APP = 'IN_APP', _('In-App')


class NotificationCategory(models.TextChoices):
    ARRIVAL = 'ARRIVAL', _('Kedatangan (Arrival)')
    DEPARTURE = 'DEPARTURE', _('Kepulangan (Departure)')
    EMERGENCY = 'EMERGENCY', _('Darurat (Emergency)')
    PAYMENT_DUE = 'PAYMENT_DUE', _('Tagihan Pembayaran (Payment Due)')
    PAYMENT_RECEIVED = 'PAYMENT_RECEIVED', _('Pembayaran Diterima (Payment Received)')
    GRADE_PUBLISHED = 'GRADE_PUBLISHED', _('Nilai Diumumkan (Grade Published)')
    REPORT_CARD = 'REPORT_CARD', _('Buku Rapor (Report Card)')
    HOMEWORK = 'HOMEWORK', _('Tugas Sekolah (Homework)')
    BEHAVIOUR_MAJOR = 'BEHAVIOUR_MAJOR', _('Pelanggaran Berat (Major Behaviour)')
    BEHAVIOUR_MINOR = 'BEHAVIOUR_MINOR', _('Catatan Ringan (Minor Behaviour)')
    CANTEEN = 'CANTEEN', _('Transaksi Kantin (Canteen)')
    ANNOUNCEMENT = 'ANNOUNCEMENT', _('Pengumuman Sekolah (Announcement)')
    WALLET_RECONCILIATION = 'WALLET_RECONCILIATION', _('Rekonsiliasi Dompet (Wallet Reconciliation)')
    SUBSTITUTE_ASSIGNED = 'SUBSTITUTE_ASSIGNED', _('Penugasan Guru Pengganti (Substitute Assigned)')
    SUBSTITUTE_DECLINED = 'SUBSTITUTE_DECLINED', _('Penolakan Guru Pengganti (Substitute Declined)')
    EXPORT_READY = 'EXPORT_READY', _('Ekspor Laporan Siap (Export Ready)')
    ABSENCE = 'ABSENCE', _('Ketidakhadiran (Absence)')
    DEVICE_OFFLINE = 'DEVICE_OFFLINE', _('Perangkat Offline (Device Offline)')
    DAILY_DIGEST = 'DAILY_DIGEST', _('Ringkasan Aktivitas Harian (Daily Digest)')
    LIBRARY_LOAN_DUE = 'LIBRARY_LOAN_DUE', _('Peminjaman Perpustakaan Jatuh Tempo (Library Loan Due)')
    COUNSELLING_URGENT = 'COUNSELLING_URGENT', _('Eskalasi BK Mendesak (Urgent Counselling Escalation)')
    COUNSELLING_FOLLOW_UP = 'COUNSELLING_FOLLOW_UP', _('Pengingat Tindak Lanjut BK (Counselling Follow-up)')


class NotificationPriority(models.TextChoices):
    CRITICAL = 'CRITICAL', _('Critical')
    HIGH = 'HIGH', _('High')
    NORMAL = 'NORMAL', _('Normal')
    LOW = 'LOW', _('Low')


class IntentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    PROCESSING = 'PROCESSING', _('Processing')
    DISPATCHED = 'DISPATCHED', _('Dispatched')
    CANCELLED = 'CANCELLED', _('Cancelled')
    FAILED = 'FAILED', _('Failed')
    DIGESTED = 'DIGESTED', _('Digested')  # NTF-007: folded into a recipient's daily digest instead of sent individually


class DeliveryStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    SENT = 'SENT', _('Sent')
    DELIVERED = 'DELIVERED', _('Delivered')
    READ = 'READ', _('Read')
    FAILED = 'FAILED', _('Failed')


class TemplateApprovalStatus(models.TextChoices):
    APPROVED = 'APPROVED', _('Approved')
    PENDING = 'PENDING', _('Pending')
    REJECTED = 'REJECTED', _('Rejected')


CATEGORY_CONFIG = {
    NotificationCategory.ARRIVAL: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
    NotificationCategory.DEPARTURE: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
    NotificationCategory.EMERGENCY: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH, ChannelType.SMS],
        'priority': NotificationPriority.CRITICAL,
        'quiet_hours_respected': False,
        'opt_out_allowed': False,  # NTF-013: EMERGENCY cannot be opted out
    },
    NotificationCategory.PAYMENT_DUE: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH, ChannelType.SMS],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': False,  # NTF-013: Statutory financial notices not opt-outable
        # NTF-004: re-evaluated at send time via process_intent's generic dotted-path lookup (FIN-027)
        'send_time_validator': 'apps.finance.services.is_invoice_reminder_still_needed',
        'send_time_cancelled_reason': _("Tagihan telah lunas sebelum pengingat terkirim (FIN-027)"),
    },
    NotificationCategory.PAYMENT_RECEIVED: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.GRADE_PUBLISHED: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.REPORT_CARD: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.HOMEWORK: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.LOW,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
        # NTF-007: held for the evening digest rather than sent individually (spec/13 §3).
        'digest_only': True,
    },
    NotificationCategory.BEHAVIOUR_MAJOR: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
    NotificationCategory.BEHAVIOUR_MINOR: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.LOW,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
        # NTF-007: held for the evening digest rather than sent individually (spec/13 §3).
        'digest_only': True,
    },
    NotificationCategory.CANTEEN: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.LOW,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
        # NTF-007: held for the evening digest rather than sent individually (spec/13 §3).
        'digest_only': True,
    },
    NotificationCategory.LIBRARY_LOAN_DUE: {
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.LOW,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
        # NTF-007: held for the evening digest rather than sent individually (spec/13 §3).
        'digest_only': True,
    },
    NotificationCategory.ANNOUNCEMENT: {
        'default_channels': [ChannelType.PUSH, ChannelType.IN_APP],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.WALLET_RECONCILIATION: {
        # spec/17 §2: debt notice, not a CANTEEN digest. Never opt-outable, never
        # digested, exempt from the NTF-012 20/day cap, no SMS fallback (REC-019).
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': False,
        # NTF-004: re-evaluated at send time via process_intent's generic dotted-path
        # lookup, not a hardcoded category check (spec/17 REC-008).
        'send_time_validator': 'apps.wallet.services.is_reconciliation_notice_still_needed',
        'send_time_cancelled_reason': _("Saldo telah diselesaikan sebelum notifikasi terkirim (REC-008)"),
    },
    NotificationCategory.SUBSTITUTE_ASSIGNED: {
        # ACD-019: an operational heads-up, not a statutory or emergency notice.
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.SUBSTITUTE_DECLINED: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': True,
        'opt_out_allowed': False,
    },
    NotificationCategory.EXPORT_READY: {
        # RPT-002: an operational heads-up that a requested export finished, not a
        # statutory notice. PUSH only — IN_APP has no registered provider yet
        # (see apps/notifications/providers.py's _PROVIDERS registry).
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
    NotificationCategory.ABSENCE: {
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
    NotificationCategory.DEVICE_OFFLINE: {
        # HW-013: alert the school admin when a critical gate goes offline
        # during operational hours. Ops-critical, staff must not silently miss
        # it, and it fires at most once per device per day (dedupe_key).
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.CRITICAL,
        'quiet_hours_respected': False,
        'opt_out_allowed': False,
    },
    NotificationCategory.DAILY_DIGEST: {
        # NTF-007: the aggregated evening digest itself. Not quiet-hours-gated —
        # the school-configured digest hour (default 17:00, deploy/crontab) IS
        # the delivery time, not a candidate for further deferral. Opt-out reuses
        # the existing per-category NotificationPreference.enabled toggle.
        'default_channels': [ChannelType.WHATSAPP, ChannelType.EMAIL],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
    NotificationCategory.COUNSELLING_URGENT: {
        # LIF-017: safeguarding escalation direct to the principal, bypassing
        # normal queues — same urgency posture as EMERGENCY (quiet-hours
        # exempt, not opt-outable), scoped to a single staff recipient.
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.CRITICAL,
        'quiet_hours_respected': False,
        'opt_out_allowed': False,
    },
    NotificationCategory.COUNSELLING_FOLLOW_UP: {
        # LIF-018: an internal counsellor task reminder, not a guardian
        # notice — normal-priority, quiet-hours respected, opt-out allowed
        # like any other staff operational heads-up.
        'default_channels': [ChannelType.PUSH],
        'priority': NotificationPriority.NORMAL,
        'quiet_hours_respected': True,
        'opt_out_allowed': True,
    },
}


class NotificationTemplate(TenantModel):
    """Template definition for multi-channel messaging (NTF-005, NTF-008)."""
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, blank=True, related_name='notification_templates')
    key = models.CharField(max_length=128, db_index=True, help_text=_("e.g. attendance.arrival"))
    channel = models.CharField(max_length=32, choices=ChannelType.choices, default=ChannelType.WHATSAPP)
    locale = models.CharField(max_length=10, default='id-ID', help_text=_("Default id-ID per NTF-008"))
    subject = models.CharField(max_length=255, blank=True, default='')
    body = models.TextField(help_text=_("Message template with {variable} placeholders"))
    variables = models.JSONField(default=list, help_text=_("Declared variable keys"))
    version = models.PositiveIntegerField(default=1)
    approval_status = models.CharField(
        max_length=32,
        choices=TemplateApprovalStatus.choices,
        default=TemplateApprovalStatus.APPROVED,
        help_text=_("Approval tracking for WhatsApp templates (NTF-005)")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'notification_templates'
        indexes = [
            models.Index(fields=['foundation_id', 'key', 'channel', 'locale']),
        ]

    def __str__(self):
        return f"[{self.channel}][{self.locale}] {self.key} v{self.version}"


class NotificationPreference(TenantModel):
    """User preferences for categories, channels, and quiet hours (NTF-001, NTF-002, NTF-013)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_preferences')
    category = models.CharField(max_length=64, choices=NotificationCategory.choices)
    channels = models.JSONField(default=list, help_text=_("Ordered list of preferred channels"))
    quiet_hours_start = models.TimeField(default=datetime.time(21, 0), help_text=_("Default 21:00 local"))
    quiet_hours_end = models.TimeField(default=datetime.time(6, 0), help_text=_("Default 06:00 local"))
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = 'notification_preferences'
        constraints = [
            models.UniqueConstraint(fields=['foundation_id', 'user', 'category'], name='unique_user_notification_preference'),
        ]

    def __str__(self):
        return f"Preference {self.user.username} - {self.category} ({'ON' if self.enabled else 'OFF'})"


class NotificationIntent(TenantModel):
    """Intent record representing an outbound message to be dispatched (spec/13 §2)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, null=True, blank=True, related_name='notification_intents')
    recipient_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_intents')
    recipient_phone = models.CharField(max_length=32, blank=True, default='', db_index=True)
    recipient_email = models.EmailField(blank=True, default='')
    recipient_name = models.CharField(max_length=128, blank=True, default='')
    category = models.CharField(max_length=64, choices=NotificationCategory.choices)
    template_key = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict)
    priority = models.CharField(max_length=32, choices=NotificationPriority.choices, default=NotificationPriority.NORMAL, db_index=True)
    scheduled_for = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=32, choices=IntentStatus.choices, default=IntentStatus.PENDING, db_index=True)
    dedupe_key = models.CharField(max_length=255, null=True, blank=True, db_index=True, help_text=_("Idempotency dedupe key (NTF-003)"))
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'notification_intents'
        indexes = [
            models.Index(fields=['foundation_id', 'status', 'scheduled_for']),
            models.Index(fields=['foundation_id', 'recipient_phone']),
            models.Index(fields=['foundation_id', 'dedupe_key']),
        ]

    def __str__(self):
        return f"Intent #{self.id} [{self.category}] {self.template_key} -> {self.recipient_phone or self.recipient_email or self.recipient_user_id} ({self.status})"


class NotificationDelivery(TenantModel):
    """Individual delivery attempt across a channel (spec/13 §2, NTF-010, NTF-011)."""
    intent = models.ForeignKey(NotificationIntent, on_delete=models.PROTECT, related_name='deliveries')
    channel = models.CharField(max_length=32, choices=ChannelType.choices)
    provider = models.CharField(max_length=64, default='mock')
    provider_message_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    recipient_target = models.CharField(max_length=128, blank=True, default='')
    rendered_subject = models.CharField(max_length=255, blank=True, default='')
    rendered_body = models.TextField(blank=True, default='')
    status = models.CharField(max_length=32, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING, db_index=True)
    error_code = models.CharField(max_length=64, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    cost = MoneyField(default=Decimal('0.00'), help_text=_("Per-channel provider cost per NTF-011"))
    cost_currency = models.CharField(max_length=3, default='IDR')

    class Meta:
        db_table = 'notification_deliveries'
        indexes = [
            models.Index(fields=['foundation_id', 'status', 'created_at']),
            models.Index(fields=['provider', 'provider_message_id']),
        ]

    def __str__(self):
        return f"Delivery #{self.id} [{self.channel}][{self.provider}] status={self.status} (Intent #{self.intent_id})"


class DevicePushPlatform(models.TextChoices):
    ANDROID = 'ANDROID', _('Android')
    IOS = 'IOS', _('iOS')
    WEB = 'WEB', _('Web')


class DevicePushToken(TenantModel):
    """A mobile or web device push token registered for a user (spec/13, docs/frontend-plan.md M4)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_tokens')
    token = models.CharField(max_length=255)
    platform = models.CharField(max_length=16, choices=DevicePushPlatform.choices, default=DevicePushPlatform.ANDROID)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'device_push_tokens'
        indexes = [
            models.Index(fields=['foundation_id', 'user_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'user', 'token', 'active_uniq_marker'],
                name='unique_user_device_push_token',
            ),
        ]

    def __str__(self):
        return f"PushToken [{self.platform}] user={self.user_id} active={self.is_active}"
