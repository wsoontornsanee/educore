"""Serializers for Identity, RBAC, Entitlements, and User Profile (spec/02 §6, §7)."""
from rest_framework import serializers
from .models import FoundationEntitlement, RoleAssignment, School, User
from .entitlements import get_active_entitlements

class FoundationEntitlementSerializer(serializers.ModelSerializer):
    """Serializer for Foundation module entitlement configurations."""
    class Meta:
        model = FoundationEntitlement
        fields = [
            'id',
            'foundation_id',
            'school_id',
            'module_key',
            'enabled',
            'limits',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']

class UserRoleSerializer(serializers.ModelSerializer):
    """Serializer for assigned user roles."""
    class Meta:
        model = RoleAssignment
        fields = ['id', 'role', 'scope_type', 'scope_id']

class UserProfileSerializer(serializers.Serializer):
    """Profile serializer for GET /me (spec/02 §7)."""
    user = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    schools = serializers.SerializerMethodField()
    entitlements = serializers.SerializerMethodField()

    def get_user(self, obj: User) -> dict:
        return {
            'id': obj.id,
            'phone_e164': obj.phone_e164,
            'email': obj.email,
            'full_name': obj.full_name,
            'status': obj.status,
            'foundation_id': obj.foundation_id,
        }

    def get_roles(self, obj: User) -> list[dict]:
        assignments = RoleAssignment.all_tenants.filter(
            foundation_id=obj.foundation_id,
            user=obj,
            deleted_at__isnull=True,
        )
        return UserRoleSerializer(assignments, many=True).data

    def get_schools(self, obj: User) -> list[dict]:
        # If superuser or foundation_admin, can see all foundation schools
        has_fnd_admin = RoleAssignment.all_tenants.filter(
            foundation_id=obj.foundation_id,
            user=obj,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            deleted_at__isnull=True,
        ).exists()

        if obj.is_superuser or has_fnd_admin:
            schools = School.all_tenants.filter(
                foundation_id=obj.foundation_id,
                deleted_at__isnull=True,
            )
        else:
            # Only schools assigned to user
            school_ids = RoleAssignment.all_tenants.filter(
                foundation_id=obj.foundation_id,
                user=obj,
                scope_type=RoleAssignment.SCOPE_SCHOOL,
                deleted_at__isnull=True,
            ).values_list('scope_id', flat=True)
            schools = School.all_tenants.filter(
                id__in=school_ids,
                foundation_id=obj.foundation_id,
                deleted_at__isnull=True,
            )

        return [
            {
                'id': s.id,
                'name': s.name,
                'npsn': s.npsn,
                'level': s.level,
            }
            for s in schools
        ]

    def get_entitlements(self, obj: User) -> dict[str, bool]:
        return get_active_entitlements(obj.foundation_id)
