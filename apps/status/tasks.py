"""Async task handlers for apps.status (ARC-010/011 — drained by drain_tasks)."""
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.core.services import register_task_handler

from apps.identity.models import User

from .models import StatusIncident, StatusSubscriber


@register_task_handler('status.subscriber_email.send')
def send_subscriber_incident_email(payload: dict):
    """Send one subscriber a plain-text notification for one published
    StatusIncident. One TaskQueue row per subscriber (see create_incident in
    services.py) — a missing incident is a real failure (lets the row
    retry/dead-letter); a missing subscriber means they unsubscribed between
    enqueue and drain, an expected race, not a failure."""
    incident = StatusIncident.objects.get(id=payload['incident_id'])
    try:
        subscriber = StatusSubscriber.objects.get(id=payload['subscriber_id'])
    except StatusSubscriber.DoesNotExist:
        return

    unsubscribe_url = f"{settings.EDUCORE_PUBLIC_BASE_URL}/status/unsubscribe/{subscriber.unsubscribe_token}/"
    subject = f"[EduCore Status] {incident.title_id}"
    body = (
        f"{incident.title_id}\n\n"
        f"{incident.body_id}\n\n"
        f"Tingkat keparahan: {incident.severity}\n"
        f"Waktu kejadian: {timezone.localtime(incident.occurred_at).strftime('%d %B %Y %H:%M')} WIB\n\n"
        f"---\n"
        f"Berhenti berlangganan pembaruan status: {unsubscribe_url}"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [subscriber.email])


@register_task_handler('status.job_alert_email.send')
def send_job_alert_email(payload: dict):
    """Email one platform operator the scheduled jobs that stopped succeeding (ARC-008).

    A missing or address-less user is an expected race (removed since enqueue), not a failure."""
    user = User.all_tenants.filter(pk=payload['user_id']).first()
    if user is None or not user.email:
        return
    lines = payload['lines']
    subject = f"[EduCore Ops] {len(lines)} job terjadwal perlu perhatian"
    body = (
        "Job terjadwal berikut belum berhasil dalam batas waktunya:\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"\n\nDetail dan pesan kesalahan: {settings.EDUCORE_PUBLIC_BASE_URL}/web/status/manage/\n"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
