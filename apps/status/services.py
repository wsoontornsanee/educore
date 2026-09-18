"""Domain services for apps.status: heartbeat recording, rollup, and metric queries."""
import datetime
import time

from django.db import connection
from django.db.models import Avg
from django.utils import timezone

from apps.core.services import audit

from .models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent, StatusIncident, StatusSubscriber

BAR_COLORS = {
    ServiceComponent.STATUS_OPERATIONAL: '#0E7A4F',
    ServiceComponent.STATUS_DEGRADED: '#B56A00',
    ServiceComponent.STATUS_DOWN: '#B3261E',
}


def _probe_database():
    """Lightweight DB connectivity + round-trip latency probe."""
    start = time.monotonic()
    is_up = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        is_up = False
    latency_ms = int((time.monotonic() - start) * 1000)
    return is_up, latency_ms


def record_heartbeats():
    """Probe the platform once and write one ComponentHeartbeat per ServiceComponent.

    A manual_status=DOWN override forces that component's heartbeat down
    regardless of the shared DB probe result; other overrides don't affect
    the heartbeat itself (they're applied at rollup/read time instead).
    """
    is_up, latency_ms = _probe_database()
    checked_at = timezone.now()
    created = 0
    for component in ServiceComponent.objects.all():
        component_is_up = False if component.manual_status == ServiceComponent.STATUS_DOWN else is_up
        ComponentHeartbeat.objects.create(
            component=component, checked_at=checked_at, is_up=component_is_up, latency_ms=latency_ms,
        )
        created += 1
    return created


def rollup_daily_status(date=None):
    """Upsert today's (or `date`'s) DailyComponentStatus per component from its heartbeats.

    Idempotent — safe to call repeatedly within the same day. manual_status
    always wins; otherwise DOWN if any heartbeat that day was down, else
    OPERATIONAL (no automatic DEGRADED signal exists without a manual override).
    """
    date = date or timezone.localdate()
    day_start = timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))
    day_end = timezone.make_aware(datetime.datetime.combine(date, datetime.time.max))
    updated = 0
    for component in ServiceComponent.objects.all():
        if component.manual_status:
            status = component.manual_status
        else:
            heartbeats = ComponentHeartbeat.objects.filter(
                component=component, checked_at__gte=day_start, checked_at__lte=day_end,
            )
            status = ServiceComponent.STATUS_DOWN if heartbeats.filter(is_up=False).exists() else ServiceComponent.STATUS_OPERATIONAL
        DailyComponentStatus.objects.update_or_create(
            component=component, date=date, defaults={'status': status},
        )
        updated += 1
    return updated


def get_component_status(component, date=None):
    """Current status for one component: manual override, else today's rollup, else OPERATIONAL default."""
    if component.manual_status:
        return component.manual_status
    date = date or timezone.localdate()
    daily = DailyComponentStatus.objects.filter(component=component, date=date).first()
    return daily.status if daily else ServiceComponent.STATUS_OPERATIONAL


def get_component_bars(component, days=30, as_of=None):
    """List of {'date': date, 'color': '#hex'} for the last `days`, oldest first."""
    as_of = as_of or timezone.localdate()
    start = as_of - datetime.timedelta(days=days - 1)
    statuses = {
        row.date: row.status
        for row in DailyComponentStatus.objects.filter(component=component, date__gte=start, date__lte=as_of)
    }
    bars = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        status = statuses.get(day, ServiceComponent.STATUS_OPERATIONAL)
        bars.append({'date': day, 'color': BAR_COLORS[status]})
    return bars


def get_uptime_percentage(days=90, as_of=None):
    """% of component-days OPERATIONAL in the last `days`, or None if there's no data yet."""
    as_of = as_of or timezone.localdate()
    start = as_of - datetime.timedelta(days=days - 1)
    qs = DailyComponentStatus.objects.filter(date__gte=start, date__lte=as_of)
    total = qs.count()
    if total == 0:
        return None
    operational = qs.filter(status=ServiceComponent.STATUS_OPERATIONAL).count()
    return round((operational / total) * 100, 2)


def get_average_latency_ms(days=90, as_of=None):
    """Average heartbeat latency_ms in the last `days`, or None if there's no data yet."""
    as_of = as_of or timezone.now()
    start = as_of - datetime.timedelta(days=days)
    agg = ComponentHeartbeat.objects.filter(
        checked_at__gte=start, checked_at__lte=as_of, latency_ms__isnull=False,
    ).aggregate(avg=Avg('latency_ms'))
    return round(agg['avg']) if agg['avg'] is not None else None


def get_open_component_count(as_of=None):
    """Count of components whose current status is not OPERATIONAL — the "open incidents" headline metric."""
    as_of = as_of or timezone.localdate()
    return sum(
        1 for component in ServiceComponent.objects.all()
        if get_component_status(component, as_of) != ServiceComponent.STATUS_OPERATIONAL
    )


def create_incident(*, severity, title_id, title_en, body_id, body_en, occurred_at,
                     duration_minutes, affected_component_ids, published, actor):
    """Create a StatusIncident and write an audit event (AGENTS.md red line #4)."""
    incident = StatusIncident.objects.create(
        severity=severity, title_id=title_id, title_en=title_en,
        body_id=body_id, body_en=body_en, occurred_at=occurred_at,
        duration_minutes=duration_minutes, published=published,
        created_by=actor, updated_by=actor,
    )
    incident.affected_components.set(affected_component_ids)
    audit(
        action='status.incident.created', entity_type='StatusIncident', entity_id=str(incident.id),
        actor_id=str(actor.id) if actor else None, role='platform_operator',
    )
    return incident


def update_incident(incident, *, actor, **fields):
    """Update a StatusIncident's fields and write an audit event."""
    for key, value in fields.items():
        setattr(incident, key, value)
    incident.updated_by = actor
    incident.save()
    audit(
        action='status.incident.updated', entity_type='StatusIncident', entity_id=str(incident.id),
        actor_id=str(actor.id) if actor else None, role='platform_operator',
    )
    return incident


def list_published_incidents():
    """Published incidents, newest occurred_at first (StatusIncident.Meta.ordering)."""
    return StatusIncident.objects.filter(published=True).prefetch_related('affected_components')


def subscribe_email(email):
    """Idempotently capture an email for incident-update notifications (no delivery built yet)."""
    subscriber, _created = StatusSubscriber.objects.get_or_create(email=email)
    return subscriber
