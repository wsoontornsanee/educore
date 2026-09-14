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


class StaffPersonSummarySerializer(serializers.Serializer):
    """Summary of Person PII record for staff details."""
    id = serializers.IntegerField(read_only=True)
    nik = serializers.CharField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    gender = serializers.CharField(read_only=True)


class StaffUserSummarySerializer(serializers.Serializer):
    """Summary of User auth record for staff details."""
    id = serializers.IntegerField(read_only=True)
    phone_e164 = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    status = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class StaffSerializer(serializers.ModelSerializer):
    """Serializer for Staff records (spec/02 §2, §7)."""
    person = StaffPersonSummarySerializer(read_only=True)
    user = StaffUserSummarySerializer(read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        from .models import Staff
        model = Staff
        fields = [
            'id',
            'foundation_id',
            'school',
            'school_name',
            'person',
            'user',
            'nip',
            'employment_type',
            'join_date',
            'resignation_date',
            'resignation_reason',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'foundation_id',
            'resignation_date',
            'resignation_reason',
            'created_at',
            'updated_at',
        ]


class StaffCreateSerializer(serializers.Serializer):
    """Serializer for creating a new staff member with Person PII and User account."""
    school_id = serializers.IntegerField(required=False, allow_null=True)
    nip = serializers.CharField(max_length=32, required=False, allow_blank=True)
    employment_type = serializers.ChoiceField(choices=['PERMANENT', 'CONTRACT', 'HONORARY'], default='PERMANENT')
    join_date = serializers.DateField()
    
    # Person PII
    full_name = serializers.CharField(max_length=128)
    nik = serializers.CharField(max_length=16, required=False, allow_blank=True)
    dob = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=[('L', 'Laki-laki'), ('P', 'Perempuan')], required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)

    # User Auth
    phone_e164 = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)


class StaffOffboardSerializer(serializers.Serializer):
    """Serializer for offboarding a staff member (IAM-021)."""
    resignation_date = serializers.DateField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    reassign_to_staff_id = serializers.IntegerField(required=False, allow_null=True)

