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
