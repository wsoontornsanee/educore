"""School transport: routes, board/alight events, geofence notices and the unaccounted-student check
(spec/05 §6, ATT-019..ATT-022).

A route has stops, and a student is assigned to one stop. A driver starts a run, the handheld reports the bus
position and each student's card tap (board / alight, geotagged, idempotent on `event_uuid`), and the run ends.

* ATT-020: each position report is compared with the run's stops. Guardians are told the bus is coming once the
  estimated arrival at their child's stop is within the route's `approach_minutes`, or the bus is inside the stop's
  geofence. The estimate is straight-line distance over the reported speed (a default when it is missing or the bus
  is nearly still), so it is early on winding roads; a road-aware ETA is a follow-up.
* ATT-021: when a run ends, every student whose last event is BOARD raises an alert to the school's admins and the
  student's guardians. The check runs inline at the end of the run and again from the `check_unaccounted_students`
  cron, which also closes runs the driver forgot to end; it is idempotent (`unaccounted_checked_at`, notice dedupe
  keys), so running it twice alerts once.
* ATT-022: `guardian_live_view` shows a guardian only their own child's stop and the bus position, and only while
  the run is ACTIVE. It is polled (ARC-017, ARC-018), never pushed.

The foundation always comes from the authenticated user or the cron's tenant context, never from a request body.
"""
import logging
import math
from datetime import timedelta
from decimal import Decimal

import dateutil.parser
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.attendance.models import (
    BusBoardingEvent, BusEventKind, BusRoute, BusRun, BusRunDirection, BusRunStatus, BusStop, BusStopAssignment,
)
from apps.attendance.pickup import _linked_guardian_recipients
from apps.core.services import audit
from apps.identity.models import GuardianLink, School, Student
from apps.identity.recipients import get_school_admin_users

logger = logging.getLogger(__name__)

DEFAULT_SPEED_MPS = 7.0  # about 25 km/h: used when the handheld sends no speed or the bus is nearly still
MIN_MOVING_SPEED_MPS = 2.0
STALE_RUN_AFTER = timedelta(hours=4)  # a run with no activity this long was forgotten by the driver
UNACCOUNTED_RETRY_WINDOW = timedelta(hours=24)  # how long the cron keeps retrying an alert that failed to queue
EARTH_RADIUS_M = 6_371_000
MAX_NAMES_IN_ADMIN_NOTICE = 10


class TransportError(ValueError):
    """A transport rule was not met; ``code`` is stable and machine-readable, ``message`` is for the person."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres between two coordinates (haversine)."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ── Setup: routes, stops, assignments ──────────────────────────────────────────────────────────────────

def _audit(user, action, entity, school_id, **diff):
    audit(
        action=action, entity_type=type(entity).__name__, entity_id=entity.id,
        actor_id=str(user.id) if user else '', foundation_id=entity.foundation_id, school_id=school_id, diff=diff,
    )


def _name_taken(foundation_id, school_id, name, exclude_id=None) -> bool:
    qs = BusRoute.all_tenants.filter(
        foundation_id=foundation_id, school_id=school_id, name=name, deleted_at__isnull=True,
    )
    return (qs.exclude(id=exclude_id) if exclude_id else qs).exists()


def create_route(*, user, school, name, approach_minutes=5) -> BusRoute:
    name = (name or '').strip()
    if not name:
        raise TransportError('BUS_NAME_REQUIRED', _("Nama rute wajib diisi."))
    if _name_taken(school.foundation_id, school.id, name):
        raise TransportError('BUS_ROUTE_NAME_TAKEN', _("Sudah ada rute dengan nama itu di sekolah ini."))
    route = BusRoute.all_tenants.create(
        foundation_id=school.foundation_id, school=school, name=name, approach_minutes=approach_minutes,
    )
    _audit(user, 'attendance.bus_route.created', route, school.id, approach_minutes=approach_minutes)
    return route


def update_route(*, user, route, name=None, approach_minutes=None, is_active=None) -> BusRoute:
    changes = {}
    if name is not None and name.strip() != route.name:
        name = name.strip()
        if not name:
            raise TransportError('BUS_NAME_REQUIRED', _("Nama rute wajib diisi."))
        if _name_taken(route.foundation_id, route.school_id, name, exclude_id=route.id):
            raise TransportError('BUS_ROUTE_NAME_TAKEN', _("Sudah ada rute dengan nama itu di sekolah ini."))
        route.name = changes['name'] = name
    if approach_minutes is not None and approach_minutes != route.approach_minutes:
        route.approach_minutes = changes['approach_minutes'] = approach_minutes
    if is_active is not None and is_active != route.is_active:
        route.is_active = changes['is_active'] = is_active
    if changes:
        route.save(update_fields=[*changes, 'updated_at'])
        _audit(user, 'attendance.bus_route.updated', route, route.school_id, fields=sorted(changes))
    return route


def add_stop(*, user, route, name, latitude, longitude, radius_m=150, sequence=None) -> BusStop:
    name = (name or '').strip()
    if not name:
        raise TransportError('BUS_NAME_REQUIRED', _("Nama titik wajib diisi."))
    if sequence is None:
        last = BusStop.all_tenants.filter(route=route, deleted_at__isnull=True).order_by('-sequence').first()
        sequence = last.sequence + 1 if last else 0
    stop = BusStop.all_tenants.create(
        foundation_id=route.foundation_id, route=route, name=name, sequence=sequence,
        latitude=latitude, longitude=longitude, radius_m=radius_m,
    )
    _audit(user, 'attendance.bus_stop.created', stop, route.school_id, route_id=route.id)
    return stop


@transaction.atomic
def remove_stop(*, user, stop) -> None:
    """Soft-delete a stop and the assignments to it, so those students must be assigned to another stop."""
    BusStopAssignment.all_tenants.filter(stop=stop, deleted_at__isnull=True).update(deleted_at=timezone.now())
    stop.delete()
    _audit(user, 'attendance.bus_stop.removed', stop, stop.route.school_id, route_id=stop.route_id)


@transaction.atomic
def assign_student(*, user, stop, student) -> BusStopAssignment:
    """Put the student at `stop`; a student already on this route is moved to it."""
    route = stop.route
    if student.school_id != route.school_id:
        raise TransportError('BUS_STUDENT_OTHER_SCHOOL', _("Siswa bukan dari sekolah rute ini."))
    assignment = BusStopAssignment.all_tenants.select_for_update().filter(
        foundation_id=route.foundation_id, route=route, student=student, deleted_at__isnull=True,
    ).first()
    if assignment is None:
        assignment = BusStopAssignment.all_tenants.create(
            foundation_id=route.foundation_id, route=route, stop=stop, student=student,
        )
    elif assignment.stop_id != stop.id:
        assignment.stop = stop
        assignment.save(update_fields=['stop', 'updated_at'])
    _audit(user, 'attendance.bus_assignment.set', assignment, route.school_id, student_id=student.id, stop_id=stop.id)
    return assignment


@transaction.atomic
def unassign_student(*, user, stop, student) -> bool:
    assignment = BusStopAssignment.all_tenants.filter(
        foundation_id=stop.foundation_id, stop=stop, student=student, deleted_at__isnull=True,
    ).first()
    if assignment is None:
        return False
    assignment.delete()
    _audit(user, 'attendance.bus_assignment.removed', assignment, stop.route.school_id, student_id=student.id)
    return True


# ── Runs ───────────────────────────────────────────────────────────────────────────────────────────────

def start_run(*, user, route, direction, now=None) -> BusRun:
    """Start a trip. A route has at most one ACTIVE run, decided under a lock on the route row."""
    now = now or timezone.now()
    with transaction.atomic():
        BusRoute.all_tenants.select_for_update().get(id=route.id)
        if not route.is_active:
            raise TransportError('BUS_ROUTE_INACTIVE', _("Rute ini tidak aktif."))
        if BusRun.all_tenants.filter(
            foundation_id=route.foundation_id, route=route, status=BusRunStatus.ACTIVE, deleted_at__isnull=True,
        ).exists():
            raise TransportError('BUS_RUN_ACTIVE', _("Rute ini sudah memiliki perjalanan yang sedang berjalan."))
        run = BusRun.all_tenants.create(
            foundation_id=route.foundation_id, school=route.school, route=route, direction=direction,
            started_at=now, started_by=user,
        )
        _audit(user, 'attendance.bus_run.started', run, route.school_id, route_id=route.id, direction=direction)
    return run


def record_position(*, run, latitude, longitude, speed_mps=None, now=None) -> BusRun:
    """Store the bus position and tell the guardians of any stop it is now near (ATT-020)."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = BusRun.all_tenants.select_for_update().select_related('route').get(id=run.id)
        if locked.status != BusRunStatus.ACTIVE:
            raise TransportError('BUS_RUN_NOT_ACTIVE', _("Perjalanan ini sudah selesai."))
        locked.last_latitude, locked.last_longitude = latitude, longitude
        locked.last_speed_mps, locked.last_position_at = speed_mps, now
        locked.save(update_fields=[
            'last_latitude', 'last_longitude', 'last_speed_mps', 'last_position_at', 'updated_at',
        ])
    announce_approaching_stops(locked, now=now)
    return locked


def end_run(*, user, run, now=None) -> BusRun:
    """End a trip (idempotent) and run the unaccounted-student check straight away (ATT-021)."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = BusRun.all_tenants.select_for_update().get(id=run.id)
        if locked.status == BusRunStatus.ACTIVE:
            locked.status, locked.ended_at, locked.ended_by = BusRunStatus.COMPLETED, now, user
            locked.save(update_fields=['status', 'ended_at', 'ended_by', 'updated_at'])
            _audit(user, 'attendance.bus_run.ended', locked, locked.school_id, route_id=locked.route_id)
    check_unaccounted(locked, now=now)
    return locked


def _last_event_kinds(run) -> dict:
    """student_id -> kind of that student's latest event on the run."""
    latest = {}
    events = BusBoardingEvent.all_tenants.filter(
        foundation_id=run.foundation_id, run=run, deleted_at__isnull=True,
    ).order_by('occurred_at', 'id').values_list('student_id', 'kind')
    for student_id, kind in events:
        latest[student_id] = kind
    return latest


# ── Board / alight events (ATT-019) ────────────────────────────────────────────────────────────────────

def _parse_time(value):
    if isinstance(value, str):
        value = dateutil.parser.isoparse(value)
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _decimal(value):
    return None if value is None or value == '' else Decimal(str(value))


def apply_bus_events(*, foundation_id, events, allowed_school_ids=None, actor_id='') -> list:
    """Record a batch of board/alight taps. Each item: `event_uuid`, `run_id`, `occurred_at`, one of `raw_uid`
    (the card) or `student_id`, and optionally `kind` (BOARD / ALIGHT; otherwise it alternates from the student's
    last event on the run), `latitude`, `longitude`, `device_id`, `replayed`. Returns one result per item:
    RECORDED, DUPLICATE (already seen, or the student is already in that state) or REJECTED with a reason, so a
    handheld can tell a bad card from a retry. `allowed_school_ids=None` means every school of the foundation."""
    prepared = [{**item, 'occurred_at': _parse_time(item['occurred_at'])} for item in events]
    prepared.sort(key=lambda item: item['occurred_at'])
    return [_apply_one(foundation_id, item, allowed_school_ids, actor_id) for item in prepared]


def _resolve_student(foundation_id, item, run):
    """(student, reason): the student behind a card tap or an explicit id, only within the run's school."""
    if item.get('raw_uid'):
        from apps.attendance.services import verify_credential
        res = verify_credential(foundation_id=foundation_id, uid=item['raw_uid'])
        if not res['valid']:
            return None, 'CREDENTIAL_INVALID'
        student = res.get('student')
        if student is None or student.school_id != run.school_id or student.deleted_at is not None:
            return None, 'NOT_A_STUDENT_OF_SCHOOL'
        return student, ''
    student = Student.all_tenants.filter(
        id=item.get('student_id'), foundation_id=foundation_id, school_id=run.school_id, deleted_at__isnull=True,
    ).first()
    return (student, '') if student else (None, 'STUDENT_NOT_FOUND')


def _apply_one(foundation_id, item, allowed_school_ids, actor_id) -> dict:
    event_uuid = item['event_uuid']

    def result(status, reason=''):
        return {'event_uuid': str(event_uuid), 'status': status, 'reason': reason}

    if BusBoardingEvent.all_tenants.filter(foundation_id=foundation_id, event_uuid=event_uuid).exists():
        return result('DUPLICATE', 'EVENT_ALREADY_RECORDED')
    run = BusRun.all_tenants.filter(id=item.get('run_id'), foundation_id=foundation_id, deleted_at__isnull=True).first()
    if run is None or (allowed_school_ids is not None and run.school_id not in allowed_school_ids):
        return result('REJECTED', 'RUN_NOT_FOUND')
    device = None
    if item.get('device_id') is not None:
        from apps.hardware.models import Device
        device = Device.all_tenants.filter(
            id=item['device_id'], foundation_id=foundation_id, school_id=run.school_id, deleted_at__isnull=True,
        ).first()
        if device is None:
            return result('REJECTED', 'DEVICE_NOT_FOUND')
    student, reason = _resolve_student(foundation_id, item, run)
    if student is None:
        return result('REJECTED', reason)

    last = _last_event_kinds(run).get(student.id)
    kind = item.get('kind') or (BusEventKind.ALIGHT if last == BusEventKind.BOARD else BusEventKind.BOARD)
    if kind == last:
        return result('DUPLICATE', f'ALREADY_{kind}')
    try:
        with transaction.atomic():
            BusBoardingEvent.all_tenants.create(
                foundation_id=foundation_id, event_uuid=event_uuid, school_id=run.school_id, run=run, student=student,
                kind=kind, occurred_at=item['occurred_at'], latitude=_decimal(item.get('latitude')),
                longitude=_decimal(item.get('longitude')), device=device, replayed=bool(item.get('replayed')),
                created_by=actor_id, updated_by=actor_id,
            )
    except IntegrityError:  # the same event_uuid arrived twice at once
        return result('DUPLICATE', 'EVENT_ALREADY_RECORDED')
    return result('RECORDED')


# ── Geofence approach notice (ATT-020) ─────────────────────────────────────────────────────────────────

def announce_approaching_stops(run, now=None) -> int:
    """Tell the guardians of students at a stop the bus is about to reach. A stop is announced once per run.
    A student is skipped when they have already done what the run is for at this point (boarded on a run to
    school, alighted on a run home). Returns how many notices were queued."""
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    now = now or timezone.now()
    if run.last_latitude is None:
        return 0
    speed = run.last_speed_mps if run.last_speed_mps and run.last_speed_mps >= MIN_MOVING_SPEED_MPS else DEFAULT_SPEED_MPS
    route = run.route
    announced = set(run.announced_stop_ids)
    lead_seconds = route.approach_minutes * 60
    to_school = run.direction == BusRunDirection.TO_SCHOOL
    done_kind = BusEventKind.BOARD if to_school else BusEventKind.ALIGHT
    template_key = 'attendance.bus_approaching_pickup' if to_school else 'attendance.bus_approaching_dropoff'
    kinds = None
    school = None
    local_time = timezone.localtime(now)
    queued = 0
    newly_announced = []

    for stop in BusStop.all_tenants.filter(route=route, deleted_at__isnull=True).exclude(id__in=announced):
        metres = distance_m(run.last_latitude, run.last_longitude, stop.latitude, stop.longitude)
        eta_seconds = metres / speed
        arrived = metres <= stop.radius_m
        if not arrived and eta_seconds > lead_seconds:
            continue
        newly_announced.append(stop.id)
        if kinds is None:
            kinds = _last_event_kinds(run)
            school = School.all_tenants.filter(id=run.school_id).first()
        minutes = 1 if arrived else max(1, math.ceil(eta_seconds / 60))
        assignments = BusStopAssignment.all_tenants.filter(
            foundation_id=run.foundation_id, stop=stop, deleted_at__isnull=True,
        ).select_related('student__person')
        for assignment in assignments:
            student = assignment.student
            if kinds.get(student.id) == done_kind:
                continue
            for guardian, user, phone, email in _linked_guardian_recipients(run.foundation_id, student):
                payload = {
                    'type': NotificationCategory.BUS_APPROACH,
                    'student_id': student.id,
                    'student_name': student.person.full_name if student.person else 'Siswa',
                    'guardian_name': guardian.person.full_name if guardian.person else 'Wali Murid',
                    'school_name': school.name if school else 'Sekolah',
                    'route_name': route.name,
                    'stop_name': stop.name,
                    'minutes': minutes,
                    'time': local_time.strftime('%H:%M'),
                    'date': local_time.strftime('%Y-%m-%d'),
                }
                try:
                    dispatch_intent(
                        foundation_id=run.foundation_id, school_id=run.school_id, recipient_user=user,
                        recipient_phone=phone, recipient_email=email, recipient_name=payload['guardian_name'],
                        category=NotificationCategory.BUS_APPROACH, template_key=template_key, payload=payload,
                        priority=NotificationPriority.HIGH,
                        dedupe_key=f"bus_approach:{run.id}:{stop.id}:{student.id}:{guardian.id}", immediate=True,
                    )
                    queued += 1
                except Exception as exc:  # noqa: BLE001 - one bad recipient must not stop the others or the run
                    logger.warning(
                        "bus approach notice failed for run %s stop %s guardian %s: %s",
                        run.id, stop.id, guardian.id, type(exc).__name__,
                    )
    if newly_announced:
        BusRun.all_tenants.filter(id=run.id).update(announced_stop_ids=sorted(announced | set(newly_announced)))
    return queued


# ── Unaccounted-student check (ATT-021) ────────────────────────────────────────────────────────────────

def check_unaccounted(run, now=None):
    """Alert for every student whose last event on the ended run is BOARD. Idempotent: the notices dedupe and
    `unaccounted_checked_at` is set once every notice was queued, so a partial failure is retried by the cron
    and a repeat run never alerts twice. Returns the unaccounted student ids, or None when the run is still
    active or was already checked."""
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    now = now or timezone.now()
    run = BusRun.all_tenants.select_related('route', 'school').get(id=run.id)
    if run.status != BusRunStatus.COMPLETED or run.unaccounted_checked_at is not None:
        return None
    student_ids = sorted(sid for sid, kind in _last_event_kinds(run).items() if kind == BusEventKind.BOARD)
    students = list(Student.all_tenants.filter(id__in=student_ids).select_related('person').order_by('id'))
    school = run.school
    local_end = timezone.localtime(run.ended_at or now)
    base = {
        'type': NotificationCategory.EMERGENCY,
        'school_name': school.name,
        'route_name': run.route.name,
        'time': local_end.strftime('%H:%M'),
        'date': local_end.strftime('%Y-%m-%d'),
    }
    complete = True

    def send(recipient_kwargs, template_key, payload, dedupe_key):
        nonlocal complete
        try:
            dispatch_intent(
                foundation_id=run.foundation_id, school_id=run.school_id, category=NotificationCategory.EMERGENCY,
                template_key=template_key, payload=payload, priority=NotificationPriority.CRITICAL,
                dedupe_key=dedupe_key, immediate=True, **recipient_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - keep alerting the rest; the cron retries the unchecked run
            complete = False
            logger.warning("bus unaccounted alert failed for run %s: %s", run.id, type(exc).__name__)

    if students:
        names = [s.person.full_name if s.person else 'Siswa' for s in students]
        shown = ', '.join(names[:MAX_NAMES_IN_ADMIN_NOTICE])
        if len(names) > MAX_NAMES_IN_ADMIN_NOTICE:
            shown += f" (+{len(names) - MAX_NAMES_IN_ADMIN_NOTICE})"
        for admin in get_school_admin_users(school):
            send(
                {'recipient_user': admin, 'recipient_name': admin.full_name or ''},
                'attendance.bus_unaccounted_admin', {**base, 'count': len(students), 'student_names': shown},
                f"bus_unaccounted_admin:{run.id}:{admin.id}",
            )
        for student, name in zip(students, names):
            for guardian, user, phone, email in _linked_guardian_recipients(run.foundation_id, student):
                guardian_name = guardian.person.full_name if guardian.person else 'Wali Murid'
                send(
                    {
                        'recipient_user': user, 'recipient_phone': phone, 'recipient_email': email,
                        'recipient_name': guardian_name,
                    },
                    'attendance.bus_unaccounted',
                    {**base, 'student_id': student.id, 'student_name': name, 'guardian_name': guardian_name},
                    f"bus_unaccounted:{run.id}:{student.id}:{guardian.id}",
                )
    if not complete:
        return student_ids
    with transaction.atomic():
        locked = BusRun.all_tenants.select_for_update().get(id=run.id)
        if locked.unaccounted_checked_at is None:
            locked.unaccounted_checked_at = now
            locked.save(update_fields=['unaccounted_checked_at', 'updated_at'])
            if student_ids:
                audit(
                    action='attendance.bus_run.unaccounted', entity_type='BusRun', entity_id=run.id, actor_id='',
                    foundation_id=run.foundation_id, school_id=run.school_id,
                    diff={'priority': 'HIGH', 'route_id': run.route_id, 'student_ids': student_ids},
                )
    return student_ids


def close_stale_runs(foundation_id, now=None) -> int:
    """End ACTIVE runs with no activity for STALE_RUN_AFTER (a forgotten run must still get its check)."""
    now = now or timezone.now()
    closed = 0
    for run in BusRun.all_tenants.filter(
        foundation_id=foundation_id, status=BusRunStatus.ACTIVE, deleted_at__isnull=True,
    ):
        last_event = BusBoardingEvent.all_tenants.filter(run=run).order_by('-occurred_at').values_list(
            'occurred_at', flat=True,
        ).first()
        last_activity = max(t for t in (run.started_at, run.last_position_at, last_event) if t is not None)
        if now - last_activity >= STALE_RUN_AFTER:
            end_run(user=None, run=run, now=now)
            closed += 1
    return closed


def unchecked_completed_runs(foundation_id, now=None):
    """Ended runs whose unaccounted check has not completed, within the retry window."""
    now = now or timezone.now()
    return BusRun.all_tenants.filter(
        foundation_id=foundation_id, status=BusRunStatus.COMPLETED, unaccounted_checked_at__isnull=True,
        ended_at__gte=now - UNACCOUNTED_RETRY_WINDOW, deleted_at__isnull=True,
    )


# ── Guardian live view (ATT-022) ───────────────────────────────────────────────────────────────────────

def guardian_live_view(*, user, route, student_id) -> dict:
    """What a guardian may see of a run: only for their own child assigned to the route, only while a run is
    ACTIVE, and only that child's stop and the bus position. Raises TransportError('BUS_NOT_FOUND') otherwise,
    so a guardian learns nothing about routes their child is not on."""
    linked = GuardianLink.all_tenants.filter(
        foundation_id=route.foundation_id, student_id=student_id, guardian__user=user, deleted_at__isnull=True,
        guardian__deleted_at__isnull=True,
    ).exists()
    assignment = BusStopAssignment.all_tenants.filter(
        foundation_id=route.foundation_id, route=route, student_id=student_id, deleted_at__isnull=True,
    ).select_related('stop').first() if linked else None
    if assignment is None:
        raise TransportError('BUS_NOT_FOUND', _("Rute tidak ditemukan."))
    run = BusRun.all_tenants.filter(
        foundation_id=route.foundation_id, route=route, status=BusRunStatus.ACTIVE, deleted_at__isnull=True,
    ).first()
    if run is None:
        return {'active': False}
    stop = assignment.stop
    kind = _last_event_kinds(run).get(student_id)
    return {
        'active': True,
        'run_id': run.id,
        'route_name': route.name,
        'direction': run.direction,
        'started_at': run.started_at,
        'student_status': {BusEventKind.BOARD: 'ON_BUS', BusEventKind.ALIGHT: 'ALIGHTED'}.get(kind, 'WAITING'),
        'stop': {
            'name': stop.name, 'latitude': float(stop.latitude), 'longitude': float(stop.longitude),
            'radius_m': stop.radius_m,
        },
        'bus': None if run.last_latitude is None else {
            'latitude': float(run.last_latitude), 'longitude': float(run.last_longitude),
            'speed_mps': run.last_speed_mps, 'updated_at': run.last_position_at,
        },
    }
