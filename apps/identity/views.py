"""API views for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from rest_framework import permissions, status, views, viewsets
from rest_framework.response import Response
from apps.core.services import audit
from educore.middleware.tenancy import get_current_foundation_id
from .models import FoundationEntitlement
from .permissions import IsFoundationAdmin
from .serializers import FoundationEntitlementSerializer, UserProfileSerializer

class CurrentUserView(views.APIView):
    """Returns the authenticated user's profile, roles, accessible schools, and entitlements (spec/02 §7).
    
    GET /api/v1/me -> {user, roles, schools, entitlements}
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

class FoundationEntitlementViewSet(viewsets.ModelViewSet):
    """Manage foundation and per-school module entitlements (spec/02 §6, spec/03 §3, FND-012).
    
    Restricted to Foundation Admins.
    """
    serializer_class = FoundationEntitlementSerializer
    permission_classes = [IsFoundationAdmin]

    def get_queryset(self):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        return FoundationEntitlement.all_tenants.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).order_by('module_key', 'school_id')

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        entitlement = serializer.save(
            foundation_id=foundation_id,
            created_by=str(self.request.user.id),
        )
        audit(
            action="identity.entitlement.created",
            entity_type="FoundationEntitlement",
            entity_id=str(entitlement.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation_id,
            school_id=entitlement.school_id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"module": {"after": entitlement.module_key}, "enabled": {"after": entitlement.enabled}},
        )

    def perform_update(self, serializer):
        before = self.get_object()
        before_enabled = before.enabled
        entitlement = serializer.save(updated_by=str(self.request.user.id))
        audit(
            action="identity.entitlement.updated",
            entity_type="FoundationEntitlement",
            entity_id=str(entitlement.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=entitlement.foundation_id,
            school_id=entitlement.school_id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"enabled": {"before": before_enabled, "after": entitlement.enabled}},
        )


class StaffViewSet(viewsets.ModelViewSet):
    """Staff directory and offboarding API (spec/02 §2, §5, §7).
    
    Enforces:
    - 3-Layer tenancy scoping (TenantManager).
    - RBAC permissions: school_config.read for viewing, school_config.write for management.
    - IAM-021: staff offboarding lifecycle with session revocation and class reassignment event.
    """
    from .models import Staff
    from .permissions import HasRequiredPermission
    from .serializers import StaffSerializer, StaffCreateSerializer, StaffOffboardSerializer

    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'school_config.read',
        'retrieve': 'school_config.read',
        'create': 'school_config.write',
        'update': 'school_config.write',
        'partial_update': 'school_config.write',
        'destroy': 'school_config.write',
        'offboard': 'school_config.write',
    }

    def get_queryset(self):
        from .models import Staff
        qs = Staff.objects.select_related('person', 'user', 'school').all()
        school_id = self.request.query_params.get('school_id')
        if school_id and school_id.isdigit():
            qs = qs.filter(school_id=int(school_id))
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs.order_by('-created_at')

    def get_serializer_class(self):
        from .serializers import StaffSerializer, StaffCreateSerializer, StaffOffboardSerializer
        if self.action == 'create':
            return StaffCreateSerializer
        elif self.action == 'offboard':
            return StaffOffboardSerializer
        return StaffSerializer

    def create(self, request, *args, **kwargs):
        from .serializers import StaffCreateSerializer, StaffSerializer
        from .services import create_user_with_person
        from .models import Staff, School

        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

        school = None
        if data.get('school_id'):
            school = School.objects.get(id=data['school_id'])

        user, person = create_user_with_person(
            foundation_id=foundation_id,
            full_name=data['full_name'],
            phone=data['phone_e164'],
            email=data.get('email'),
            password=data.get('password'),
            nik=data.get('nik'),
            dob=data.get('dob'),
            gender=data.get('gender', ''),
            address=data.get('address', ''),
        )

        staff = Staff.objects.create(
            foundation_id=foundation_id,
            person=person,
            user=user,
            school=school,
            nip=data.get('nip', ''),
            employment_type=data.get('employment_type', Staff.TYPE_PERMANENT),
            join_date=data['join_date'],
            status=Staff.STATUS_ACTIVE,
            created_by=str(request.user.id),
        )

        audit(
            action="identity.staff.created",
            entity_type="Staff",
            entity_id=str(staff.id),
            actor_id=str(request.user.id),
            role=getattr(request.user, 'role', 'school_admin'),
            foundation_id=foundation_id,
            school_id=staff.school_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"nip": {"after": staff.nip}, "full_name": {"after": person.full_name}},
        )

        out_serializer = StaffSerializer(staff)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)

    from rest_framework.decorators import action
    @action(detail=True, methods=['post'], url_path='offboard')
    def offboard(self, request, pk=None):
        """Execute atomic staff offboarding lifecycle per IAM-021."""
        from .serializers import StaffOffboardSerializer, StaffSerializer
        from .services import offboard_staff

        staff = self.get_object()
        serializer = StaffOffboardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from rest_framework.exceptions import ValidationError as DRFValidationError
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            offboarded_staff = offboard_staff(
                staff_id=staff.id,
                actor_id=str(request.user.id),
                role=getattr(request.user, 'role', 'school_admin'),
                reason=data.get('reason', ''),
                resignation_date=data.get('resignation_date'),
                reassign_to_staff_id=data.get('reassign_to_staff_id'),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(detail=exc.messages if hasattr(exc, 'messages') else str(exc))

        return Response(StaffSerializer(offboarded_staff).data, status=status.HTTP_200_OK)


