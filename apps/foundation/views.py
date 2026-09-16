"""Views for Foundation portal and School management (spec/02 §7, spec/03 §5)."""
from rest_framework import generics, status, views, viewsets
from rest_framework.response import Response
from apps.core.services import audit
from apps.identity.models import Foundation, School
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id
from .models import RptFoundationKPI
from .serializers import FoundationKPISerializer, FoundationSettingsSerializer, SchoolSerializer

class SchoolViewSet(viewsets.ModelViewSet):
    """CRUD API for schools under the authenticated user's foundation (spec/02 §7).
    
    Enforces:
    - 3-Layer tenancy isolation: TenantManager filters by thread-local foundation_id.
    - RBAC permissions: school_config.read for reads, school_config.write for mutations.
    - Soft deletion: never hard-delete school rows (TenantModel.deleted_at).
    - Audit logging: writes immutable AuditEvent on every mutating action.
    """
    serializer_class = SchoolSerializer
    permission_classes = [HasRequiredPermission]
    queryset = School.objects.all()
    action_permissions = {
        'list': 'school_config.read',
        'retrieve': 'school_config.read',
        'create': 'school_config.write',
        'update': 'school_config.write',
        'partial_update': 'school_config.write',
        'destroy': 'school_config.write',
    }

    def get_queryset(self):
        # Explicit ordering for CursorPagination
        return School.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        school = serializer.save(
            foundation_id=foundation_id,
            created_by=str(self.request.user.id),
        )
        audit(
            action="identity.school.created",
            entity_type="School",
            entity_id=str(school.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation_id,
            school_id=school.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"name": {"after": school.name}, "npsn": {"after": school.npsn}},
        )

    def perform_update(self, serializer):
        before_instance = self.get_object()
        before_name = before_instance.name
        school = serializer.save(updated_by=str(self.request.user.id))
        audit(
            action="identity.school.updated",
            entity_type="School",
            entity_id=str(school.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=school.foundation_id,
            school_id=school.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"name": {"before": before_name, "after": school.name}},
        )

    def perform_destroy(self, instance):
        school_id = instance.id
        foundation_id = instance.foundation_id
        # Soft delete
        instance.delete()
        audit(
            action="identity.school.deleted",
            entity_type="School",
            entity_id=str(school_id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation_id,
            school_id=school_id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"is_deleted": {"before": False, "after": True}},
        )

class FoundationSettingsView(generics.RetrieveUpdateAPIView):
    """View and configure Foundation-level governance settings (spec/03 §2, §5)."""
    serializer_class = FoundationSettingsSerializer
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self) -> str:
        if self.request.method in ['GET', 'HEAD', 'OPTIONS']:
            return 'school_config.read'
        return 'school_config.write'

    def get_object(self):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        return Foundation.objects.get(id=foundation_id)

    def perform_update(self, serializer):
        foundation = serializer.save()
        audit(
            action="foundation.settings.updated",
            entity_type="Foundation",
            entity_id=str(foundation.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={
                "legal_name": {"after": foundation.legal_name},
                "approval_threshold": {"after": str(foundation.approval_threshold)},
            },
        )

class FoundationKPIView(views.APIView):
    """Dashboard KPIs for the Foundation portal (spec/03 §4, §5).
    
    Reads from rpt_foundation_kpis rollup table (FND-005).
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.read'

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        queryset = RptFoundationKPI.objects.filter(foundation_id=foundation_id)

        school_id = request.query_params.get('school_id')
        if school_id:
            queryset = queryset.filter(school_id=school_id)

        from_date = request.query_params.get('from')
        if from_date:
            queryset = queryset.filter(period_start__gte=from_date)

        to_date = request.query_params.get('to')
        if to_date:
            queryset = queryset.filter(period_end__lte=to_date)

        serializer = FoundationKPISerializer(queryset, many=True)
        return Response(serializer.data)
