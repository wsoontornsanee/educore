"""Calendar sync models: per-staff OAuth connections and normalized external events.

spec/14 §6 lists "calendar sync" as the second purpose of the Google
Workspace / Microsoft 365 integration (the first, Staff SSO, shipped in
PR #115). This slice builds the connection layer and a normalized
event store; auto-populating academic models (timetable, exams) is a
separate, not-yet-designed follow-up — no dated-event academic model
exists to receive them (TimetableSlot is a weekly grid, not events).
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import soft_delete_uniqueness_marker
from apps.core.models import TenantModel


class CalendarConnection(TenantModel):
    """A staff member's OAuth connection to an external calendar provider.

    One active connection per (foundation, user, provider). OAuth access and
    refresh tokens are stored Fernet-encrypted at rest (apps.calendar_sync.crypto)
    and are never returned by any API after the exchange completes.
    """
    PROVIDER_GOOGLE = 'google'
    PROVIDER_MICROSOFT = 'microsoft'
    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE, 'Google Workspace'),
        (PROVIDER_MICROSOFT, 'Microsoft 365'),
    ]

    STATUS_CONNECTED = 'CONNECTED'
    STATUS_ERROR = 'ERROR'
    STATUS_REVOKED = 'REVOKED'
    STATUS_CHOICES = [
        (STATUS_CONNECTED, 'Connected'),
        (STATUS_ERROR, 'Error'),
        (STATUS_REVOKED, 'Revoked'),
    ]

    user = models.ForeignKey(
        'identity.User', on_delete=models.PROTECT, related_name='calendar_connections',
    )
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, db_index=True)
    provider_email = models.EmailField(max_length=255, blank=True, default='')

    access_token_encrypted = models.TextField(help_text=_('Fernet-encrypted OAuth access token'))
    refresh_token_encrypted = models.TextField(
        blank=True, default='',
        help_text=_('Fernet-encrypted OAuth refresh token; blank when the provider did not issue one'),
    )
    token_expires_at = models.DateTimeField(
        null=True, blank=True, help_text=_('Expiry of the stored access token (tz-aware)'),
    )

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_CONNECTED)
    last_error = models.CharField(max_length=255, blank=True, default='')

    calendar_id = models.CharField(
        max_length=255, default='primary',
        help_text=_('Provider calendar identifier being synced'),
    )
    sync_cursor = models.CharField(
        max_length=2048, blank=True, default='',
        help_text=_('Provider incremental-sync cursor (Google syncToken / Graph deltaLink); '
                    'reserved — the current pull uses a plain time window'),
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'calendar_connections'
        verbose_name = _('Koneksi Kalender Eksternal')
        verbose_name_plural = _('Daftar Koneksi Kalender Eksternal')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'user', 'provider', 'active_uniq_marker'],
                name='unique_active_calendar_connection_per_provider',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
            models.Index(fields=['foundation_id', 'user']),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_email or self.user_id} ({self.status})"


class ExternalCalendarEvent(TenantModel):
    """A calendar event pulled from a provider, normalized for EduCore consumption.

    Idempotent per (connection, provider_event_id): re-running the sync cron
    never duplicates rows. Cancelled/deleted provider events are kept with
    is_cancelled=True rather than deleted (red line 2: no hard deletion).
    """
    connection = models.ForeignKey(
        CalendarConnection, on_delete=models.PROTECT, related_name='events',
    )
    provider_event_id = models.CharField(max_length=255, db_index=True)
    title = models.CharField(max_length=512, blank=True, default='')
    description = models.TextField(blank=True, default='')
    location = models.CharField(max_length=512, blank=True, default='')
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    meeting_url = models.URLField(
        max_length=1024, blank=True, default='',
        help_text=_('Video-conference link (Google hangoutLink / Microsoft Teams joinUrl)'),
    )
    is_cancelled = models.BooleanField(default=False)

    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'external_calendar_events'
        verbose_name = _('Acara Kalender Eksternal')
        verbose_name_plural = _('Daftar Acara Kalender Eksternal')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'connection', 'provider_event_id', 'active_uniq_marker'],
                name='unique_active_external_event_per_connection',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'connection', 'start_at']),
        ]

    def __str__(self):
        return f"{self.title or self.provider_event_id} ({self.start_at:%Y-%m-%d})"
