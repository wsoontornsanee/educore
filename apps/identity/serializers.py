"""Serializers for Identity, RBAC, Entitlements, and User Profile (spec/02 §6, §7)."""
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
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

class RoleAssignmentSerializer(serializers.ModelSerializer):
    """A staff member's role assignment, for the role grant/revoke API."""
    class Meta:
        model = RoleAssignment
        fields = ['id', 'user_id', 'role', 'scope_type', 'scope_id', 'created_at']
        read_only_fields = fields

class RoleGrantSerializer(serializers.Serializer):
    """Body of POST /staff/<id>/roles/ — role + scope, the same shape role_admin.grant_role takes."""
    role = serializers.CharField()
    scope_type = serializers.ChoiceField(choices=[RoleAssignment.SCOPE_FOUNDATION, RoleAssignment.SCOPE_SCHOOL])
    scope_id = serializers.IntegerField()

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
    home_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, read_only=True)
    home_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, read_only=True)


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
            'nuptk',
            'employment_type',
            'appointment_type',
            'certification_status',
            'highest_degree',
            'degree_institution',
            'degree_graduation_year',
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
    nuptk = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    employment_type = serializers.ChoiceField(choices=['PERMANENT', 'CONTRACT', 'HONORARY'], default='PERMANENT')
    appointment_type = serializers.ChoiceField(
        choices=['PNS', 'CPNS', 'PPPK', 'GTY', 'PTT', 'TIDAK_ADA'],
        required=False, allow_blank=True, allow_null=True, default='',
    )
    certification_status = serializers.ChoiceField(
        choices=['SERTIFIKAT', 'BELUM', 'SERTIFIKASI_PROSES'],
        required=False, allow_blank=True, allow_null=True, default='',
    )
    highest_degree = serializers.ChoiceField(
        choices=['SMA', 'D1', 'D2', 'D3', 'D4', 'S1', 'S2', 'S3'],
        required=False, allow_blank=True, allow_null=True, default='',
    )
    degree_institution = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    degree_graduation_year = serializers.IntegerField(required=False, allow_null=True)
    join_date = serializers.DateField()

    # Person PII
    full_name = serializers.CharField(max_length=128)
    nik = serializers.CharField(max_length=16, required=False, allow_blank=True)
    dob = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=[('L', 'Laki-laki'), ('P', 'Perempuan')], required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    religion = serializers.ChoiceField(
        choices=['ISLAM', 'KRISTEN', 'KATOLIK', 'HINDU', 'BUDDHA', 'KONGHUCU'],
        required=False, allow_blank=True, allow_null=True, default='',
    )
    birth_city = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    birth_certificate_number = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    citizenship = serializers.CharField(max_length=32, required=False, allow_blank=True, default='WNI')
    rt = serializers.CharField(max_length=8, required=False, allow_blank=True, default='')
    rw = serializers.CharField(max_length=8, required=False, allow_blank=True, default='')
    dusun = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kelurahan = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kecamatan = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kabupaten_kota = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    provinsi = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    postal_code = serializers.CharField(max_length=10, required=False, allow_blank=True, default='')

    # User Auth
    phone_e164 = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)


class StaffOffboardSerializer(serializers.Serializer):
    """Serializer for offboarding a staff member (IAM-021)."""
    resignation_date = serializers.DateField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    reassign_to_staff_id = serializers.IntegerField(required=False, allow_null=True)


class StudentPersonSerializer(serializers.Serializer):
    """Person PII vault representation for Student profiles."""
    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    nik = serializers.CharField(read_only=True)
    dob = serializers.DateField(read_only=True)
    gender = serializers.CharField(read_only=True)
    address = serializers.CharField(read_only=True)
    religion = serializers.CharField(read_only=True)
    birth_city = serializers.CharField(read_only=True)
    birth_certificate_number = serializers.CharField(read_only=True)
    mother_name = serializers.CharField(read_only=True)
    citizenship = serializers.CharField(read_only=True)
    rt = serializers.CharField(read_only=True)
    rw = serializers.CharField(read_only=True)
    dusun = serializers.CharField(read_only=True)
    kelurahan = serializers.CharField(read_only=True)
    kecamatan = serializers.CharField(read_only=True)
    kabupaten_kota = serializers.CharField(read_only=True)
    provinsi = serializers.CharField(read_only=True)
    postal_code = serializers.CharField(read_only=True)
    home_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, read_only=True)
    home_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, read_only=True)


class StudentSerializer(serializers.ModelSerializer):
    """Serializer for Student records (spec/02 §2, §7)."""
    person = StudentPersonSerializer(read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        from .models import Student
        model = Student
        fields = [
            'id',
            'foundation_id',
            'school',
            'school_name',
            'person',
            'nis',
            'nisn',
            'photo_key',
            'status',
            'target_grade_level',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'foundation_id',
            'status',
            'created_at',
            'updated_at',
        ]


class StudentCreateSerializer(serializers.Serializer):
    """Serializer for creating a new student with Person PII."""
    school_id = serializers.IntegerField()
    nis = serializers.CharField(max_length=32)
    nisn = serializers.CharField(max_length=10, required=False, allow_blank=True, allow_null=True)
    photo_key = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    target_grade_level = serializers.IntegerField(required=False, allow_null=True)

    # Person PII
    full_name = serializers.CharField(max_length=128)
    nik = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    dob = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=[('L', 'Laki-laki'), ('P', 'Perempuan')], required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True, default='')
    religion = serializers.ChoiceField(
        choices=['ISLAM', 'KRISTEN', 'KATOLIK', 'HINDU', 'BUDDHA', 'KONGHUCU'],
        required=False, allow_blank=True, allow_null=True, default='',
    )
    birth_city = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    birth_certificate_number = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    mother_name = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    citizenship = serializers.CharField(max_length=32, required=False, allow_blank=True, default='WNI')
    rt = serializers.CharField(max_length=8, required=False, allow_blank=True, default='')
    rw = serializers.CharField(max_length=8, required=False, allow_blank=True, default='')
    dusun = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kelurahan = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kecamatan = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    kabupaten_kota = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    provinsi = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    postal_code = serializers.CharField(max_length=10, required=False, allow_blank=True, default='')
    home_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    home_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)


class StudentStatusTransitionSerializer(serializers.Serializer):
    """Serializer for student status transitions (IAM-019)."""
    status = serializers.ChoiceField(choices=['PROSPECT', 'ACTIVE', 'INACTIVE', 'GRADUATED', 'TRANSFERRED_OUT'])
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class GuardianLinkDetailSerializer(serializers.Serializer):
    """Serializer for guardian and relationship link details."""
    id = serializers.IntegerField(read_only=True)
    guardian_id = serializers.IntegerField(source='guardian.id', read_only=True)
    full_name = serializers.CharField(source='guardian.person.full_name', read_only=True)
    phone_e164 = serializers.CharField(source='guardian.user.phone_e164', read_only=True)
    occupation = serializers.CharField(source='guardian.occupation', read_only=True)
    relation = serializers.CharField(read_only=True)
    is_primary = serializers.BooleanField(read_only=True)
    can_pickup = serializers.BooleanField(read_only=True)
    financial_responsible = serializers.BooleanField(read_only=True)


class GuardianLinkCreateSerializer(serializers.Serializer):
    """Serializer for linking a new or existing guardian to a student (IAM-014, IAM-015)."""
    guardian_id = serializers.IntegerField(required=False, allow_null=True)
    full_name = serializers.CharField(max_length=128, required=False)
    phone_e164 = serializers.CharField(max_length=20, required=False, allow_blank=True)
    nik = serializers.CharField(max_length=16, required=False, allow_blank=True)
    occupation = serializers.CharField(max_length=128, required=False, allow_blank=True)

    relation = serializers.ChoiceField(choices=['FATHER', 'MOTHER', 'GUARDIAN'], default='GUARDIAN')
    is_primary = serializers.BooleanField(default=False)
    can_pickup = serializers.BooleanField(default=True)
    financial_responsible = serializers.BooleanField(default=False)


class StudentImportSerializer(serializers.Serializer):
    """Serializer for multipart bulk student XLSX/CSV file upload (IAM-017)."""
    school_id = serializers.IntegerField()
    file = serializers.FileField()
    dry_run = serializers.BooleanField(default=True)


class EduCoreTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Custom JWT serializer supporting dual phone/email identifier login (IAM-001)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['identifier'] = serializers.CharField(required=False)
        self.fields[self.username_field].required = False

    def validate(self, attrs):
        identifier = attrs.get('identifier') or attrs.get(self.username_field) or attrs.get('email')
        password = attrs.get('password')

        if not identifier or not password:
            raise serializers.ValidationError('Identifier dan password wajib diisi.')

        user = authenticate(
            request=self.context.get('request'),
            username=identifier,
            password=password,
        )

        if not user:
            raise serializers.ValidationError('Kredensial tidak valid atau akun terkunci.')

        if not user.is_active:
            raise serializers.ValidationError('Akun pengguna tidak aktif.')

        refresh = self.get_token(user)

        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'phone_e164': user.phone_e164,
                'email': user.email,
                'foundation_id': user.foundation_id,
                'roles': list(
                    RoleAssignment.all_tenants.filter(
                        foundation_id=user.foundation_id,
                        user=user,
                        deleted_at__isnull=True,
                    ).values('id', 'role', 'scope_type', 'scope_id')
                ),
            },
        }


class EduCoreTokenRefreshSerializer(TokenRefreshSerializer):
    """Custom TokenRefreshSerializer using User.all_tenants for unscoped user lookup."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])

        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM, None)
        if user_id:
            user_model = get_user_model()
            manager = getattr(user_model, 'all_tenants', user_model.objects)
            try:
                user = manager.get(**{api_settings.USER_ID_FIELD: user_id})
            except user_model.DoesNotExist:
                user = None

            if user and not api_settings.USER_AUTHENTICATION_RULE(user):
                raise AuthenticationFailed(
                    self.error_messages["no_active_account"],
                    "no_active_account",
                )

        data = {"access": str(refresh.access_token)}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass

            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()

            data["refresh"] = str(refresh)

        return data


class OtpRequestSerializer(serializers.Serializer):
    phone_e164 = serializers.CharField()


class OtpVerifySerializer(serializers.Serializer):
    challenge_id = serializers.IntegerField()
    code = serializers.CharField(max_length=6, min_length=6)


class GuardianChildSerializer(serializers.Serializer):
    """One child in the guardian's switcher. NIS/NISN are the guardian's own child's identifiers,
    served over the authenticated API only (never logged, AGENTS red line 5). ``class_name`` needs
    ``context['class_names']`` (student_id -> current rombel name); absent means no active enrolment."""
    student_id = serializers.IntegerField(source='student.id')
    full_name = serializers.CharField(source='student.person.full_name')
    photo_key = serializers.CharField(source='student.photo_key')
    financial_responsible = serializers.BooleanField()
    nis = serializers.CharField(source='student.nis')
    nisn = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    school_name = serializers.CharField(source='student.school.name')

    def get_nisn(self, link) -> str:
        return link.student.nisn or ''

    def get_class_name(self, link) -> str:
        return self.context.get('class_names', {}).get(link.student_id, '')


# ── SSO (spec/14 §6, TASK-036) ─────────────────────────────────────

class SocialLoginSerializer(serializers.Serializer):
    """Validate an SSO login/link request."""
    provider = serializers.ChoiceField(choices=['google', 'microsoft'])
    id_token = serializers.CharField()

    def validate_id_token(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("ID token wajib diisi.")
        return value.strip()


class SocialLoginResponseSerializer(serializers.Serializer):
    """JWT response for a successful SSO login."""
    access = serializers.CharField()
    refresh = serializers.CharField()
    user_id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    is_staff = serializers.BooleanField()


class SocialLinkResponseSerializer(serializers.Serializer):
    """Response after linking an SSO account."""
    provider = serializers.CharField()
    provider_user_id = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True)
