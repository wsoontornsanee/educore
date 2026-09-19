"""School transport endpoints (spec/05 §6, §8: `POST /bus/events`, `GET /bus/routes/:id/live`).

Setup (routes, stops, student assignments) needs `school_config.read` / `school_config.write`, so it is school
staff only. Running a trip (start, position, end, board/alight events) needs `attendance.write`: the driver's
handheld signs in as a staff user, since there is no driver role yet. The guardians' live view needs
`attendance.read` and is limited to their own child by `guardian_live_view`. Every lookup is inside the
authenticated user's foundation and, for staff, inside the schools the permission covers; a row outside that reads
as not found.
"""
from decimal import Decimal

from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance import transport
from apps.attendance.models import BusRoute, BusRun, BusRunDirection, BusStop, BusStopAssignment, BusEventKind
from apps.attendance.transport import TransportError
from apps.identity.console_access import accessible_school_ids
from apps.identity.models import School, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

MAX_EVENTS_PER_BATCH = 500

_HTTP_STATUS = {
    'BUS_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'BUS_RUN_ACTIVE': status.HTTP_409_CONFLICT,
    'BUS_ROUTE_NAME_TAKEN': status.HTTP_409_CONFLICT,
}


def _error(exc: TransportError) -> Response:
    return Response({'error': exc.message, 'code': exc.code}, status=_HTTP_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST))


def _not_found() -> Response:
    return _error(TransportError('BUS_NOT_FOUND', _("Data tidak ditemukan.")))


def _may_act_at_school(user, foundation_id, permission, school_id) -> bool:
    ceiling = accessible_school_ids(user, foundation_id, permission)
    return ceiling is None or school_id in ceiling


class _MethodPermissionView(APIView):
    """A view whose permission depends on the HTTP method: `read_permission` for GET, `write_permission` else."""
    permission_classes = [HasRequiredPermission]
    read_permission = 'school_config.read'
    write_permission = 'school_config.write'

    def get_required_permission(self):
        return self.read_permission if self.request.method == 'GET' else self.write_permission


def _route_for(user, route_id, permission):
    foundation_id = get_current_foundation_id()
    route = BusRoute.objects.filter(id=route_id, foundation_id=foundation_id, deleted_at__isnull=True).select_related('school').first()
    if route is None or not _may_act_at_school(user, foundation_id, permission, route.school_id):
        return None
    return route


def _stop_for(user, stop_id, permission):
    foundation_id = get_current_foundation_id()
    stop = BusStop.objects.filter(id=stop_id, foundation_id=foundation_id, deleted_at__isnull=True).select_related('route').first()
    if stop is None or not _may_act_at_school(user, foundation_id, permission, stop.route.school_id):
        return None
    return stop


def _route_data(route, detail=False) -> dict:
    data = {
        'id': route.id, 'school_id': route.school_id, 'name': route.name,
        'approach_minutes': route.approach_minutes, 'is_active': route.is_active,
    }
    if detail:
        students = {}
        for assignment in BusStopAssignment.objects.filter(
            foundation_id=route.foundation_id, route=route, deleted_at__isnull=True,
        ).select_related('student__person'):
            person = assignment.student.person
            students.setdefault(assignment.stop_id, []).append(
                {'id': assignment.student_id, 'name': person.full_name if person else ''},
            )
        data['stops'] = [
            {
                'id': stop.id, 'name': stop.name, 'sequence': stop.sequence, 'latitude': float(stop.latitude),
                'longitude': float(stop.longitude), 'radius_m': stop.radius_m, 'students': students.get(stop.id, []),
            }
            for stop in BusStop.objects.filter(route=route, foundation_id=route.foundation_id, deleted_at__isnull=True)
        ]
    return data


def _run_data(run) -> dict:
    return {
        'id': run.id, 'route_id': run.route_id, 'direction': run.direction, 'status': run.status,
        'started_at': run.started_at, 'ended_at': run.ended_at, 'last_position_at': run.last_position_at,
    }


# ── Setup ──────────────────────────────────────────────────────────────────────────────────────────────

class _RouteCreateSerializer(serializers.Serializer):
    school_id = serializers.IntegerField()
    name = serializers.CharField(max_length=120)
    approach_minutes = serializers.IntegerField(min_value=1, max_value=60, required=False, default=5)


class _RoutePatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    approach_minutes = serializers.IntegerField(min_value=1, max_value=60, required=False)
    is_active = serializers.BooleanField(required=False)


class BusRouteListView(_MethodPermissionView):
    """GET /bus/routes/?school_id= lists routes; POST creates one."""

    def get(self, request):
        foundation_id = get_current_foundation_id()
        routes = BusRoute.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('name')
        ceiling = accessible_school_ids(request.user, foundation_id, self.read_permission)
        if ceiling is not None:
            routes = routes.filter(school_id__in=ceiling)
        school_id = request.query_params.get('school_id', '')
        if school_id.isdigit():
            routes = routes.filter(school_id=int(school_id))
        return Response([_route_data(route) for route in routes])

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _RouteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        school = School.objects.filter(id=data['school_id'], foundation_id=foundation_id, deleted_at__isnull=True).first()
        if school is None or not _may_act_at_school(request.user, foundation_id, self.write_permission, school.id):
            return _not_found()
        try:
            route = transport.create_route(
                user=request.user, school=school, name=data['name'], approach_minutes=data['approach_minutes'],
            )
        except TransportError as exc:
            return _error(exc)
        return Response(_route_data(route, detail=True), status=status.HTTP_201_CREATED)


class BusRouteDetailView(_MethodPermissionView):
    """GET /bus/routes/<id>/ (with stops and their students); PATCH edits name, lead time or active flag."""

    def get(self, request, route_id):
        route = _route_for(request.user, route_id, self.read_permission)
        return _not_found() if route is None else Response(_route_data(route, detail=True))

    def patch(self, request, route_id):
        route = _route_for(request.user, route_id, self.write_permission)
        if route is None:
            return _not_found()
        serializer = _RoutePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            route = transport.update_route(user=request.user, route=route, **serializer.validated_data)
        except TransportError as exc:
            return _error(exc)
        return Response(_route_data(route, detail=True))


class _StopSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=Decimal('-90'), max_value=Decimal('90'))
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=Decimal('-180'), max_value=Decimal('180'))
    radius_m = serializers.IntegerField(min_value=10, max_value=2000, required=False, default=150)
    sequence = serializers.IntegerField(min_value=0, required=False)


class BusRouteStopsView(_MethodPermissionView):
    """POST /bus/routes/<id>/stops/ adds a stop to the end of the route (or at `sequence`)."""

    def post(self, request, route_id):
        route = _route_for(request.user, route_id, self.write_permission)
        if route is None:
            return _not_found()
        serializer = _StopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            transport.add_stop(user=request.user, route=route, **serializer.validated_data)
        except TransportError as exc:
            return _error(exc)
        return Response(_route_data(route, detail=True), status=status.HTTP_201_CREATED)


class BusStopDetailView(_MethodPermissionView):
    """DELETE /bus/stops/<id>/ removes a stop and its student assignments."""

    def delete(self, request, stop_id):
        stop = _stop_for(request.user, stop_id, self.write_permission)
        if stop is None:
            return _not_found()
        transport.remove_stop(user=request.user, stop=stop)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _StudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()


class BusStopStudentsView(_MethodPermissionView):
    """POST /bus/stops/<id>/students/ {student_id} puts the student at the stop (moving them if already on the route)."""

    def post(self, request, stop_id):
        stop = _stop_for(request.user, stop_id, self.write_permission)
        if stop is None:
            return _not_found()
        serializer = _StudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = Student.objects.filter(
            id=serializer.validated_data['student_id'], foundation_id=stop.foundation_id, deleted_at__isnull=True,
        ).first()
        if student is None:
            return _not_found()
        try:
            transport.assign_student(user=request.user, stop=stop, student=student)
        except TransportError as exc:
            return _error(exc)
        return Response(_route_data(stop.route, detail=True), status=status.HTTP_201_CREATED)


class BusStopStudentDetailView(_MethodPermissionView):
    """DELETE /bus/stops/<id>/students/<student_id>/ takes the student off the stop."""

    def delete(self, request, stop_id, student_id):
        stop = _stop_for(request.user, stop_id, self.write_permission)
        student = Student.objects.filter(id=student_id, foundation_id=stop.foundation_id).first() if stop else None
        if stop is None or student is None or not transport.unassign_student(user=request.user, stop=stop, student=student):
            return _not_found()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Running a trip ─────────────────────────────────────────────────────────────────────────────────────

class _RunPermissionView(APIView):
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.write'


def _run_for(user, run_id):
    foundation_id = get_current_foundation_id()
    run = BusRun.objects.filter(id=run_id, foundation_id=foundation_id, deleted_at__isnull=True).first()
    if run is None or not _may_act_at_school(user, foundation_id, 'attendance.write', run.school_id):
        return None
    return run


class _StartRunSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=BusRunDirection.choices)


class BusRunStartView(_RunPermissionView):
    """POST /bus/routes/<id>/runs/ {direction} starts a trip; a route has one active trip at a time."""

    def post(self, request, route_id):
        route = _route_for(request.user, route_id, self.required_permission)
        if route is None:
            return _not_found()
        serializer = _StartRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            run = transport.start_run(user=request.user, route=route, direction=serializer.validated_data['direction'])
        except TransportError as exc:
            return _error(exc)
        return Response(_run_data(run), status=status.HTTP_201_CREATED)


class _PositionSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=Decimal('-90'), max_value=Decimal('90'))
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=Decimal('-180'), max_value=Decimal('180'))
    speed_mps = serializers.FloatField(min_value=0, max_value=100, required=False, allow_null=True, default=None)


class BusRunPositionView(_RunPermissionView):
    """POST /bus/runs/<id>/position/ {latitude, longitude, speed_mps?}: the handheld reports the bus position."""

    def post(self, request, run_id):
        run = _run_for(request.user, run_id)
        if run is None:
            return _not_found()
        serializer = _PositionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            run = transport.record_position(run=run, **serializer.validated_data)
        except TransportError as exc:
            return _error(exc)
        return Response(_run_data(run))


class BusRunEndView(_RunPermissionView):
    """POST /bus/runs/<id>/end/ ends the trip and runs the unaccounted-student check (ATT-021). Idempotent."""

    def post(self, request, run_id):
        run = _run_for(request.user, run_id)
        if run is None:
            return _not_found()
        return Response(_run_data(transport.end_run(user=request.user, run=run)))


class _EventSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField()
    run_id = serializers.IntegerField()
    occurred_at = serializers.DateTimeField()
    raw_uid = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    student_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    kind = serializers.ChoiceField(choices=BusEventKind.choices, required=False, allow_null=True, default=None)
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal('-90'), max_value=Decimal('90'), required=False, allow_null=True, default=None,
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal('-180'), max_value=Decimal('180'), required=False, allow_null=True, default=None,
    )
    device_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    replayed = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs['raw_uid'] and attrs['student_id'] is None:
            raise serializers.ValidationError(_("Kirim raw_uid (kartu) atau student_id."))
        return attrs


class _EventBatchSerializer(serializers.Serializer):
    events = _EventSerializer(many=True, allow_empty=False, max_length=MAX_EVENTS_PER_BATCH)


class BusEventsView(_RunPermissionView):
    """POST /bus/events/ {events: [...]}: board/alight taps from the driver's handheld (ATT-019), idempotent on
    `event_uuid`. The answer lists what happened to each event, so a bad card is told apart from a retry."""

    def post(self, request):
        foundation_id = get_current_foundation_id()
        serializer = _EventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = transport.apply_bus_events(
            foundation_id=foundation_id, events=[dict(item) for item in serializer.validated_data['events']],
            allowed_school_ids=accessible_school_ids(request.user, foundation_id, self.required_permission),
            actor_id=str(request.user.id),
        )
        counts = {}
        for result in results:
            counts[result['status']] = counts.get(result['status'], 0) + 1
        return Response({'results': results, 'counts': counts})


# ── Guardian live view (ATT-022) ───────────────────────────────────────────────────────────────────────

class BusLiveView(APIView):
    """GET /bus/routes/<id>/live/?student_id=: a guardian polls where the bus is, for their own child only and only
    while the trip is active (ARC-017, ARC-018: polling, never push)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.read'

    def get(self, request, route_id):
        foundation_id = get_current_foundation_id()
        raw = request.query_params.get('student_id', '')
        route = BusRoute.objects.filter(id=route_id, foundation_id=foundation_id, deleted_at__isnull=True).first()
        if route is None or not raw.isdigit():
            return _not_found()
        try:
            return Response(transport.guardian_live_view(user=request.user, route=route, student_id=int(raw)))
        except TransportError as exc:
            return _error(exc)
