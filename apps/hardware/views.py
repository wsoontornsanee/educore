from django.utils import timezone
from rest_framework import status, views, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.pagination import StandardCursorPagination
from apps.hardware.models import Device, DeviceEventStaging, DeviceStatus
from apps.hardware.serializers import (
    DeviceCreateSerializer,
    DeviceEventBatchSerializer,
    DeviceHeartbeatSerializer,
    DeviceSerializer,
)
from apps.identity.permissions import HasRequiredPermission
from apps.identity.models import RoleAssignment


class DeviceViewSet(viewsets.ModelViewSet):
    """
    CRUD and heartbeat lifecycle management for campus hardware devices (spec/12 §4).
    """
    pagination_class = StandardCursorPagination
    required_permission = 'school_config.read'

    def get_serializer_class(self):
        if self.action == 'create':
            return DeviceCreateSerializer
        if self.action == 'heartbeat':
            return DeviceHeartbeatSerializer
        return DeviceSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'retire']:
            self.required_permission = 'school_config.write'
        elif self.action == 'heartbeat':
            # Heartbeats can be authenticated via device token or admin permission
            self.required_permission = 'school_config.read'
        else:
            self.required_permission = 'school_config.read'
        return [HasRequiredPermission()]

    def get_queryset(self):
        foundation_id = getattr(self.request, 'foundation_id', None)
        if not foundation_id:
            return Device.objects.none()

        qs = Device.objects.filter(foundation_id=foundation_id).select_related('school')

        # School-scoped isolation check
        user = self.request.user
        if not user.is_authenticated:
            return Device.objects.none()

        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            qs = qs.filter(school_id__in=school_ids)

        school_param = self.request.query_params.get('school_id')
        if school_param:
            qs = qs.filter(school_id=school_param)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        device_class_param = self.request.query_params.get('device_class')
        if device_class_param:
            qs = qs.filter(device_class=device_class_param)

        return qs.order_by('-created_at')

    def get_object(self):
        foundation_id = getattr(self.request, 'foundation_id', None)
        obj_id = self.kwargs.get('pk')
        try:
            device = Device.objects.select_related('school').get(id=obj_id, foundation_id=foundation_id)
        except (Device.DoesNotExist, ValueError):
            raise NotFound("Perangkat tidak ditemukan.")

        # If user has only school-scoped roles, verify device belongs to their authorized school(s)
        user = self.request.user
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            if device.school_id not in user_school_ids:
                raise NotFound("Perangkat tidak ditemukan.")

        self.check_object_permissions(self.request, device)
        return device

    def perform_create(self, serializer):
        foundation_id = getattr(self.request, 'foundation_id', None)
        actor_id = str(self.request.user.id) if self.request.user and self.request.user.is_authenticated else ''
        serializer.save(
            foundation_id=foundation_id,
            created_by=actor_id,
            updated_by=actor_id,
        )

    @action(detail=True, methods=['post'], url_path='heartbeat')
    def heartbeat(self, request, pk=None):
        """
        Record device self-reported heartbeat metrics (spec/12 §3 HW-007).
        """
        device = self.get_object()
        serializer = DeviceHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        device.last_heartbeat_at = timezone.now()
        device.status = data.get('status', DeviceStatus.ONLINE)

        if 'firmware_version' in data and data['firmware_version']:
            device.firmware_version = data['firmware_version']
        if 'today_event_count' in data:
            device.today_event_count = data['today_event_count']
        if 'ip_address' in data and data['ip_address']:
            device.ip_address = data['ip_address']
        if 'mac_address' in data and data['mac_address']:
            device.mac_address = data['mac_address']
        if 'metrics' in data:
            config = dict(device.config)
            config['latest_metrics'] = data['metrics']
            device.config = config

        device.save(update_fields=[
            'last_heartbeat_at',
            'status',
            'firmware_version',
            'today_event_count',
            'ip_address',
            'mac_address',
            'config',
            'updated_at',
        ])

        return Response({
            'status': 'ACK',
            'device_id': str(device.id),
            'server_time': timezone.now().isoformat(),
        })

    @action(detail=True, methods=['post'], url_path='retire')
    def retire(self, request, pk=None):
        """
        Retire a device immediately (spec/12 §4 HW-015).
        """
        device = self.get_object()
        device.status = DeviceStatus.RETIRED
        device.save(update_fields=['status', 'updated_at'])
        return Response({
            'status': 'RETIRED',
            'device_id': str(device.id),
        })

    @action(detail=False, methods=['get'], url_path='sync')
    def sync(self, request):
        """
        Delta sync endpoint for on-premise campus edge gateways (spec/12 §3, §7).
        Returns roster_delta, credentials_delta, rules_delta, and next_cursor.
        """
        foundation_id = getattr(request, 'foundation_id', None)
        school_id = request.query_params.get('school_id')
        cursor = request.query_params.get('cursor')

        if not school_id:
            raise ValidationError("school_id wajib diisi pada parameter query.")

        user = request.user
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            if int(school_id) not in user_school_ids:
                raise NotFound("Sekolah tidak ditemukan.")

        from apps.attendance.services import get_device_sync_payload
        data = get_device_sync_payload(
            foundation_id=foundation_id,
            school_id=school_id,
            since_cursor=cursor,
        )
        return Response(data, status=status.HTTP_200_OK)


class DeviceEventIngestView(views.APIView):
    """
    POST /device/events per spec/12 §7 (HW-005): stages a raw event batch from
    an edge gateway for async application by the `ingest_device_events` cron,
    rather than applying it inline — the fast path for large offline-reconnect
    bursts. Small real-time batches keep using `POST /gate/events/` unchanged.
    """
    required_permission = 'school_config.read'
    permission_classes = [HasRequiredPermission]

    def post(self, request):
        foundation_id = getattr(request, 'foundation_id', None)
        serializer = DeviceEventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        school_id = data['school_id']
        user = request.user
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            if school_id not in user_school_ids:
                raise NotFound("Sekolah tidak ditemukan.")

        actor_id = str(user.id) if user and user.is_authenticated else ''
        staged = 0
        duplicates = 0

        for item in data['batch']:
            device_id = item.pop('device_id')
            event_uuid = item['event_uuid']
            try:
                device = Device.objects.get(id=device_id, foundation_id=foundation_id, school_id=school_id)
            except Device.DoesNotExist:
                continue

            if DeviceEventStaging.objects.filter(foundation_id=foundation_id, event_uuid=event_uuid).exists():
                duplicates += 1
                continue

            payload = {
                'event_uuid': str(item['event_uuid']),
                'device_id': device_id,
                'occurred_at': item['occurred_at'].isoformat(),
                'raw_uid': item.get('raw_uid', ''),
                'student_id': item.get('student_id'),
                'staff_id': item.get('staff_id'),
                'direction': item.get('direction'),
                'method': item.get('method', 'RFID'),
                'confidence': str(item['confidence']) if item.get('confidence') is not None else None,
                'photo_key': item.get('photo_key', ''),
                'replayed': item.get('replayed', False),
            }
            if item.get('bus_run_id') is not None:
                payload.update({
                    'bus_run_id': item['bus_run_id'],
                    'kind': item.get('kind'),
                    'latitude': str(item['latitude']) if item.get('latitude') is not None else None,
                    'longitude': str(item['longitude']) if item.get('longitude') is not None else None,
                })
            DeviceEventStaging.objects.create(
                foundation_id=foundation_id,
                school_id=school_id,
                device=device,
                event_uuid=event_uuid,
                payload=payload,
                created_by=actor_id,
                updated_by=actor_id,
            )
            staged += 1

        return Response({
            'staged': staged,
            'duplicates_skipped': duplicates,
            'total': len(data['batch']),
        }, status=status.HTTP_202_ACCEPTED)


class DeviceSyncView(views.APIView):
    """
    Direct API endpoint for GET /api/v1/device/sync per spec/12 §7.
    """
    required_permission = 'school_config.read'
    permission_classes = [HasRequiredPermission]

    def get(self, request):
        foundation_id = getattr(request, 'foundation_id', None)
        school_id = request.query_params.get('school_id')
        cursor = request.query_params.get('cursor')

        if not school_id:
            raise ValidationError("school_id wajib diisi pada parameter query.")

        user = request.user
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            user=user,
            foundation_id=foundation_id,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()
        if not has_fnd_admin:
            user_school_ids = RoleAssignment.all_tenants.filter(
                user=user,
                foundation_id=foundation_id,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            if int(school_id) not in user_school_ids:
                raise NotFound("Sekolah tidak ditemukan.")

        from apps.attendance.services import get_device_sync_payload
        data = get_device_sync_payload(
            foundation_id=foundation_id,
            school_id=school_id,
            since_cursor=cursor,
        )
        return Response(data, status=status.HTTP_200_OK)

