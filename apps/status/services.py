"""Domain services for apps.status: heartbeat recording, rollup, and metric queries."""
import datetime
import time

from django.db import connection
from django.db.models import Avg
from django.utils import timezone

from apps.core.services import audit, enqueue_task
from educore.middleware.tenancy import tenant_context

from .models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent, StatusIncident, StatusSubscriber

BAR_COLOR_UNKNOWN = '#D9D2CD'

BAR_COLORS = {
    ServiceComponent.STATUS_OPERATIONAL: '#0E7A4F',
    ServiceComponent.STATUS_DEGRADED: '#B56A00',
    ServiceComponent.STATUS_DOWN: '#B3261E',
}


def _probe_database():
    """Lightweight DB connectivity + round-trip latency probe.

    Design limitation (documented, not a bug to fix in this pass): this probe
    runs in the same Django process, against the same database, that would
    also need to receive the ComponentHeartbeat/JobRun write recording its
    result. If the database is genuinely down, the probe's SELECT 1 AND the
    subsequent write both fail together — so `is_up=False` can essentially
    never be durably persisted by this automated path. In practice, the
    automated half of this feature can only ever report "up"; a real outage
    can only be reported by staff setting a component's manual_status by
    hand via the internal management view. A structural fix (an external
    health-check process with its own write path, decoupled from this
    process's own DB availability) is out of scope for this pass.
    """
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
        if day not in statuses:
            # No DailyComponentStatus row for this day (fresh deploy, or the
            # component didn't exist yet) — render a distinct "unknown"
            # color, not a fabricated OPERATIONAL/green, to match the
            # uptime-percentage metric's own "—" (unknown) treatment of the
            # same missing data.
            color = BAR_COLOR_UNKNOWN
        else:
            color = BAR_COLORS.get(statuses[day], BAR_COLORS[ServiceComponent.STATUS_OPERATIONAL])
        bars.append({'date': day, 'color': color})
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
    """Count of components whose current status is not OPERATIONAL — the "affected components" headline metric."""
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
    # StatusIncident is platform-wide, non-tenant data (see apps/status/models.py
    # module docstring). audit() infers foundation_id from the ambient
    # thread-local when it's omitted OR explicitly None — so without
    # clearing that thread-local, the AuditEvent would silently inherit the
    # acting platform operator's own (unrelated) foundation_id, and that
    # foundation's tenant-scoped audit log would leak platform-wide status
    # mutations. tenant_context(None) clears it for the duration of the call.
    with tenant_context(None):
        audit(
            action='status.incident.created', entity_type='StatusIncident', entity_id=str(incident.id),
            actor_id=str(actor.id) if actor else None, role='platform_operator',
        )
        if published:
            # One TaskQueue row per subscriber (not one row for the whole
            # incident) so a single bad address retries/dead-letters
            # independently instead of blocking the rest of the batch (ARC-012).
            #
            # Also platform-wide, non-tenant data — same leak class as the
            # audit() call above. enqueue_task(foundation_id=None) falls back
            # to the ambient thread-local foundation_id when None is passed
            # (apps/core/services.py), so this loop must stay inside the same
            # tenant_context(None) block or each TaskQueue row would silently
            # inherit the acting operator's own unrelated foundation_id.
            for subscriber_id in StatusSubscriber.objects.values_list('id', flat=True):
                enqueue_task(
                    'status.subscriber_email.send',
                    payload={'incident_id': incident.id, 'subscriber_id': subscriber_id},
                    foundation_id=None,
                )
    return incident


_INCIDENT_UPDATABLE_FIELDS = {
    'severity', 'title_id', 'title_en', 'body_id', 'body_en',
    'occurred_at', 'duration_minutes', 'published',
}


def update_incident(incident, *, actor, **fields):
    """Update a StatusIncident's fields and write an audit event.

    `fields` is restricted to _INCIDENT_UPDATABLE_FIELDS — an explicit
    allowlist — so a caller can never use this to set `created_by`, `id`,
    or any other field not meant to be editable this way.
    """
    disallowed = set(fields) - _INCIDENT_UPDATABLE_FIELDS
    if disallowed:
        raise ValueError(f"update_incident: cannot set field(s) {sorted(disallowed)}")
    for key, value in fields.items():
        setattr(incident, key, value)
    incident.updated_by = actor
    incident.save()
    # See create_incident's comment above: clear the ambient tenant context
    # so this platform-wide AuditEvent never inherits the acting operator's
    # own unrelated foundation_id.
    with tenant_context(None):
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
