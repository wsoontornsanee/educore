"""API views for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, views, viewsets
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from apps.core.services import audit
from apps.core.throttling import LoginRateThrottle, OtpRequestIPThrottle
from educore.middleware.tenancy import get_current_foundation_id
from .guardian_access import is_staff_user
from .models import FoundationEntitlement, Guardian, GuardianLink, RoleAssignment, User as UserModel
from .permissions import IsFoundationAdmin
from .rbac import assign_role
from .serializers import (
    EduCoreTokenObtainPairSerializer,
    EduCoreTokenRefreshSerializer,
    FoundationEntitlementSerializer,
    GuardianChildSerializer,
    UserProfileSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    SocialLoginSerializer,
)
from .services import request_phone_otp, verify_phone_otp, normalize_phone_e164

class EduCoreTokenObtainPairView(TokenObtainPairView):
    """Custom JWT token obtain view supporting dual phone/email identifier login (IAM-001)."""
    serializer_class = EduCoreTokenObtainPairSerializer
    throttle_classes = [LoginRateThrottle]
    throttle_scope = 'auth_login'

class EduCoreTokenRefreshView(TokenRefreshView):
    """Tenant-aware JWT token refresh view."""
    serializer_class = EduCoreTokenRefreshSerializer

class CurrentUserView(views.APIView):
    """Returns the authenticated user's profile, roles, accessible schools, and entitlements (spec/02 §7).
    
    GET /api/v1/me -> {user, roles, schools, entitlements}
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

class SpendingPinView(views.APIView):
    """/api/v1/me/pin/ — the guardian's 6-digit spending PIN (spec 18 QRS-029).

    GET  -> {is_set, locked_until, requires_otp_reset}
    POST {pin}                      first-time setup
    PUT  {current_pin, new_pin}     change (the current PIN counts against the attempt limit)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from . import pin as pin_service
        return Response(pin_service.get_pin_status(request.user))

    def post(self, request):
        from . import pin as pin_service
        try:
            pin_service.set_pin(request.user, str(request.data.get('pin', '')))
        except pin_service.PinError as e:
            return pin_service.pin_error_response(e)
        return Response(pin_service.get_pin_status(request.user), status=status.HTTP_201_CREATED)

    def put(self, request):
        from . import pin as pin_service
        try:
            pin_service.change_pin(
                request.user, str(request.data.get('current_pin', '')), str(request.data.get('new_pin', '')),
            )
        except pin_service.PinError as e:
            return pin_service.pin_error_response(e)
        return Response(pin_service.get_pin_status(request.user))


class SpendingPinResetView(views.APIView):
    """POST /api/v1/me/pin/reset/ {challenge_id, code, new_pin} — forgotten or locked-out PIN, proven by a
    fresh OTP to the account's own phone (request one via /auth/otp/request/)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from . import pin as pin_service
        try:
            challenge_id = int(request.data.get('challenge_id'))
        except (TypeError, ValueError):
            return Response({'error': 'OTP_INVALID'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            pin_service.reset_pin_with_otp(
                request.user, challenge_id, str(request.data.get('code', '')), str(request.data.get('new_pin', '')),
            )
        except pin_service.PinError as e:
            return pin_service.pin_error_response(e)
        return Response(pin_service.get_pin_status(request.user))


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
    - School ceiling (IAM-012): a school-scoped holder only sees and manages staff of the
      schools they hold the permission in; foundation-wide staff (no school) and staff whose
      access reaches beyond the actor's schools are foundation-scope territory. Out-of-ceiling
      rows are 404s, exactly as on the web console (console_access.can_manage_staff).
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

    def _foundation_id(self):
        return get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)

    def _ceiling(self, permission=None):
        """None = whole foundation, else the set of school ids the actor holds
        `permission` (default: this action's) in."""
        from .console_access import accessible_school_ids

        permission = permission or self.action_permissions.get(self.action) or 'school_config.read'
        return accessible_school_ids(self.request.user, self._foundation_id(), permission)

    def _require_manageable(self, staff):
        """404 unless the actor may manage `staff` (see can_manage_staff)."""
        from .console_access import can_manage_staff

        assignments = list(RoleAssignment.all_tenants.filter(
            foundation_id=staff.foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
        ))
        if not can_manage_staff(self.request.user, staff, self._ceiling('school_config.write'), assignments):
            raise NotFound()

    def get_queryset(self):
        from .models import Staff
        qs = Staff.objects.select_related('person', 'user', 'school').all()
        ceiling = self._ceiling()
        if ceiling is not None:
            qs = qs.filter(school_id__in=ceiling)
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
        from .services import create_staff_member
        from .models import School

        serializer = StaffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

        ceiling = self._ceiling()
        school = None
        if data.get('school_id'):
            school = School.objects.filter(id=data['school_id'], deleted_at__isnull=True).first()
            if school is None or (ceiling is not None and school.id not in ceiling):
                raise NotFound(_("Sekolah tidak ditemukan."))
        elif ceiling is not None:
            raise PermissionDenied(_("Hanya pemegang wewenang tingkat yayasan yang dapat menambah staf tingkat yayasan."))

        staff = create_staff_member(
            foundation_id=foundation_id, school=school, data=data, actor=request.user,
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        return Response(StaffSerializer(staff).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        ceiling = self._ceiling()
        new_school = serializer.validated_data.get('school', serializer.instance.school)
        if ceiling is not None and (new_school is None or new_school.id not in ceiling):
            raise PermissionDenied(_("Staf tidak dapat dipindahkan ke luar sekolah yang Anda kelola."))
        serializer.save()

    def perform_destroy(self, instance):
        self._require_manageable(instance)
        super().perform_destroy(instance)

    from rest_framework.decorators import action
    @action(detail=True, methods=['post'], url_path='offboard')
    def offboard(self, request, pk=None):
        """Execute atomic staff offboarding lifecycle per IAM-021."""
        from .serializers import StaffOffboardSerializer, StaffSerializer
        from .services import offboard_staff

        staff = self.get_object()
        self._require_manageable(staff)
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


class StaffRoleListView(views.APIView):
    """GET /api/v1/staff/<staff_id>/roles/ — list a staff member's role assignments.
    POST /api/v1/staff/<staff_id>/roles/ — grant a role.

    JSON API parity for the web console's role editing (PR #223): both entry
    points call the exact same `role_admin` service functions, so every
    privilege-escalation rule (self-edit, scope ceiling, no-escalation, last
    foundation-admin) is enforced identically and in one place. Session/JWT-
    authenticated staff only (`HasRequiredPermission` requires
    `request.user.is_authenticated`) — this app registers no API-key
    authentication, so partner integrations cannot reach this endpoint.
    """
    from .permissions import HasRequiredPermission

    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.write'

    def _foundation_id(self, request):
        return get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

    def _load_staff(self, request, staff_id):
        from .console_access import accessible_school_ids
        from .models import Staff

        foundation_id = self._foundation_id(request)
        ceiling = accessible_school_ids(request.user, foundation_id, self.required_permission)
        qs = Staff.all_tenants.filter(id=staff_id, foundation_id=foundation_id, deleted_at__isnull=True)
        if ceiling is not None:
            qs = qs.filter(school_id__in=ceiling)
        staff = qs.first()
        if staff is None:
            raise NotFound()
        return staff, foundation_id

    def get(self, request, staff_id):
        from .serializers import RoleAssignmentSerializer

        staff, foundation_id = self._load_staff(request, staff_id)
        assignments = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
        ).order_by('scope_type', 'role')
        return Response(RoleAssignmentSerializer(assignments, many=True).data)

    def post(self, request, staff_id):
        from .role_admin import RoleChangeError, grant_role
        from .serializers import RoleAssignmentSerializer, RoleGrantSerializer

        staff, foundation_id = self._load_staff(request, staff_id)
        body = RoleGrantSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        try:
            assignment, created = grant_role(
                foundation_id=foundation_id, actor=request.user, target_user_id=staff.user_id,
                ip_address=request.META.get('REMOTE_ADDR'), **body.validated_data,
            )
        except (RoleChangeError, DjangoValidationError) as exc:
            raise DRFValidationError(detail=exc.messages if hasattr(exc, 'messages') else str(exc))

        if not created:
            existing = RoleAssignment.all_tenants.get(
                foundation_id=foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
                **body.validated_data,
            )
            return Response(RoleAssignmentSerializer(existing).data, status=status.HTTP_200_OK)
        return Response(RoleAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)


class StaffRoleDetailView(StaffRoleListView):
    """DELETE /api/v1/staff/<staff_id>/roles/<assignment_id>/ — revoke a role assignment.

    The assignment must belong to this staff member, or it is a 404, exactly
    like the web console (a mismatched pair cannot be used to probe or revoke
    someone else's role).
    """

    def delete(self, request, staff_id, assignment_id):
        from .role_admin import RoleChangeError, revoke_role_assignment

        staff, foundation_id = self._load_staff(request, staff_id)
        if not RoleAssignment.all_tenants.filter(
            id=assignment_id, foundation_id=foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
        ).exists():
            raise NotFound()

        try:
            revoke_role_assignment(
                foundation_id=foundation_id, actor=request.user, assignment_id=assignment_id,
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        except RoleChangeError as exc:
            raise DRFValidationError(detail=exc.messages)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudentViewSet(viewsets.ModelViewSet):
    """Student directory, guardian linking, and bulk import API (spec/02 §2, §5, §7).
    
    Enforces:
    - 3-Layer tenancy scoping (TenantManager).
    - School scoping isolation (IAM-012, spec/02 §8.1): school_admin receives 404 for student of another school.
    - RBAC permissions: student_records.read for viewing, student_records.write for mutations.
    - Status machine transitions (IAM-019).
    - Atomic bulk XLSX/CSV import with dry-run diff preview (IAM-017, IAM-018, spec/02 §8.3).
    """
    from .models import Student
    from .permissions import HasRequiredPermission
    from .serializers import (
        StudentSerializer, StudentCreateSerializer, StudentStatusTransitionSerializer,
        GuardianLinkDetailSerializer, GuardianLinkCreateSerializer, StudentImportSerializer
    )

    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'student_records.read',
        'retrieve': 'student_records.read',
        'guardians': 'student_records.read',
        'create': 'student_records.write',
        'update': 'student_records.write',
        'partial_update': 'student_records.write',
        'destroy': 'student_records.write',
        'status_transition': 'student_records.write',
        'add_guardian': 'student_records.write',
        'bulk_import': 'student_records.write',
    }

    def get_queryset(self):
        from django.db.models import Q
        from .models import Student, RoleAssignment

        user = self.request.user
        foundation_id = get_current_foundation_id() or getattr(user, 'foundation_id', None)

        qs = Student.objects.select_related('person', 'school').all()

        # If user is a school-scoped admin or teacher, scope strictly to their assigned schools
        if not user.is_superuser:
            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role__in=[RoleAssignment.ROLE_FOUNDATION_ADMIN],
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                from .guardian_access import get_guardian_student_ids, STAFF_ROLES

                # User has school-scoped roles or parent role
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)

                if staff_school_ids and parent_student_ids:
                    # Dual role: staff at some schools, parent at others (IAM-009, IAM-014)
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(id__in=parent_student_ids))
                elif staff_school_ids:
                    # Staff only
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif parent_student_ids:
                    # Parent only: strictly linked students (IAM-014)
                    qs = qs.filter(id__in=parent_student_ids)
                else:
                    # Neither staff nor linked students
                    return qs.none()

        # Teacher-only users see just the students of their own classes (plus their own children).
        from apps.academic.class_scope import ClassScope
        qs = qs.filter(ClassScope(user, foundation_id).student_q('id', 'school_id'))

        # Filters
        school_id = self.request.query_params.get('school_id')
        if school_id and school_id.isdigit():
            qs = qs.filter(school_id=int(school_id))

        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        q = self.request.query_params.get('q')
        if q:
            q = q.strip()
            qs = qs.filter(
                Q(person__full_name__icontains=q) |
                Q(nis__icontains=q) |
                Q(nisn__icontains=q)
            )

        return qs.order_by('-created_at')

    def get_serializer_class(self):
        from .serializers import (
            StudentSerializer, StudentCreateSerializer, StudentStatusTransitionSerializer,
            GuardianLinkDetailSerializer, GuardianLinkCreateSerializer, StudentImportSerializer
        )
        if self.action == 'create':
            return StudentCreateSerializer
        elif self.action == 'status_transition':
            return StudentStatusTransitionSerializer
        elif self.action == 'guardians' and self.request.method == 'POST':
            return GuardianLinkCreateSerializer
        elif self.action == 'guardians':
            return GuardianLinkDetailSerializer
        elif self.action == 'bulk_import':
            return StudentImportSerializer
        return StudentSerializer

    def create(self, request, *args, **kwargs):
        from .serializers import StudentCreateSerializer, StudentSerializer
        from .models import Student, Person, School

        serializer = StudentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        school = School.objects.get(id=data['school_id'])

        person = Person.objects.create(
            foundation_id=foundation_id,
            full_name=data['full_name'],
            nik=data.get('nik'),
            dob=data.get('dob'),
            gender=data.get('gender', ''),
            address=data.get('address', ''),
            religion=data.get('religion', ''),
            birth_city=data.get('birth_city', ''),
            birth_certificate_number=data.get('birth_certificate_number', ''),
            mother_name=data.get('mother_name', ''),
            citizenship=data.get('citizenship', 'WNI'),
            rt=data.get('rt', ''),
            rw=data.get('rw', ''),
            dusun=data.get('dusun', ''),
            kelurahan=data.get('kelurahan', ''),
            kecamatan=data.get('kecamatan', ''),
            kabupaten_kota=data.get('kabupaten_kota', ''),
            provinsi=data.get('provinsi', ''),
            postal_code=data.get('postal_code', ''),
            home_latitude=data.get('home_latitude'),
            home_longitude=data.get('home_longitude'),
            created_by=str(request.user.id),
        )

        student = Student.objects.create(
            foundation_id=foundation_id,
            school=school,
            person=person,
            nis=data['nis'],
            nisn=data.get('nisn'),
            photo_key=data.get('photo_key', ''),
            target_grade_level=data.get('target_grade_level'),
            status=Student.STATUS_PROSPECT,
            created_by=str(request.user.id),
        )

        audit(
            action="identity.student.created",
            entity_type="Student",
            entity_id=str(student.id),
            actor_id=str(request.user.id),
            role=getattr(request.user, 'role', 'school_admin'),
            foundation_id=foundation_id,
            school_id=school.id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"nis": {"after": student.nis}, "full_name": {"after": person.full_name}},
        )

        return Response(StudentSerializer(student).data, status=status.HTTP_201_CREATED)

    from rest_framework.decorators import action
    @action(detail=True, methods=['post'], url_path='status')
    def status_transition(self, request, pk=None):
        """Execute validated student lifecycle status transition (IAM-019)."""
        from .serializers import StudentStatusTransitionSerializer, StudentSerializer
        from rest_framework.exceptions import ValidationError as DRFValidationError

        student = self.get_object()
        serializer = StudentStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            student.transition_status(
                new_status=data['status'],
                actor_id=str(request.user.id),
                reason=data.get('reason', '')
            )
        except ValueError as exc:
            raise DRFValidationError(detail=str(exc))

        return Response(StudentSerializer(student).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get', 'post'], url_path='guardians')
    def guardians(self, request, pk=None):
        """List or add linked guardians for student (IAM-014, IAM-015)."""
        from .models import Guardian, GuardianLink, Person, User
        from .serializers import GuardianLinkDetailSerializer, GuardianLinkCreateSerializer
        from rest_framework.exceptions import ValidationError as DRFValidationError

        student = self.get_object()
        foundation_id = student.foundation_id

        if request.method == 'GET':
            links = GuardianLink.objects.filter(student=student).select_related('guardian__person', 'guardian__user')
            serializer = GuardianLinkDetailSerializer(links, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # POST: Link guardian
        serializer = GuardianLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        guardian = None
        if data.get('guardian_id'):
            guardian = Guardian.objects.get(id=data['guardian_id'])
        else:
            # Create new Guardian profile
            if not data.get('full_name'):
                raise DRFValidationError({'full_name': 'Nama wali wajib diisi.'})

            person = Person.objects.create(
                foundation_id=foundation_id,
                full_name=data['full_name'],
                nik=data.get('nik'),
                created_by=str(request.user.id),
            )

            guardian_user = None
            if data.get('phone_e164'):
                guardian_user = User.all_tenants.filter(
                    foundation_id=foundation_id,
                    phone_e164=data['phone_e164']
                ).first()
                if not guardian_user:
                    guardian_user = User.objects.create_user(
                        phone_e164=data['phone_e164'],
                        full_name=person.full_name,
                        foundation_id=foundation_id,
                    )

            guardian = Guardian.objects.create(
                foundation_id=foundation_id,
                person=person,
                user=guardian_user,
                occupation=data.get('occupation', ''),
                created_by=str(request.user.id),
            )

        link = GuardianLink.objects.create(
            foundation_id=foundation_id,
            guardian=guardian,
            student=student,
            relation=data.get('relation', GuardianLink.RELATION_GUARDIAN),
            is_primary=data.get('is_primary', False),
            can_pickup=data.get('can_pickup', True),
            financial_responsible=data.get('financial_responsible', False),
            created_by=str(request.user.id),
        )

        return Response(GuardianLinkDetailSerializer(link).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='import')
    def bulk_import(self, request):
        """Atomic multipart bulk student XLSX/CSV import with dry-run diff preview (IAM-017, IAM-018)."""
        from .serializers import StudentImportSerializer
        from .importers import StudentBulkImporter
        from rest_framework.exceptions import ValidationError as DRFValidationError

        serializer = StudentImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        uploaded_file = data['file']
        school_id = data['school_id']
        dry_run = data.get('dry_run', True)

        # Query param override (?dry_run=true|false)
        if 'dry_run' in request.query_params:
            dry_run = request.query_params['dry_run'].lower() in ('1', 'true', 'yes')

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

        importer = StudentBulkImporter(
            foundation_id=foundation_id,
            school_id=school_id,
            actor_id=str(request.user.id)
        )

        try:
            result = importer.execute(
                file_obj=uploaded_file,
                filename=uploaded_file.name,
                dry_run=dry_run
            )
        except Exception as exc:
            raise DRFValidationError(detail=str(exc))

        if not result['success']:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK if dry_run else status.HTTP_201_CREATED)


class RequestOtpView(views.APIView):
    """POST /api/v1/auth/otp/request/ — guardian OTP login, step 1 (PAR-001, IAM-002)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OtpRequestIPThrottle]
    throttle_scope = 'otp_request_ip'

    def post(self, request):
        try:
            serializer = OtpRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
        except DRFValidationError:
            return Response({'error': 'Nomor HP wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)

        phone = serializer.validated_data['phone_e164']

        # Validate phone format first (may embed raw input in error message)
        try:
            normalize_phone_e164(phone)
        except DjangoValidationError:
            # Return generic message without echoing raw input
            return Response({'error': 'Format nomor telepon tidak valid. Gunakan format Indonesia (+62... atau 08...).'}, status=status.HTTP_400_BAD_REQUEST)

        # Phone format is valid; now attempt OTP request (may fail due to throttle, which is safe to echo)
        try:
            challenge, _raw_code = request_phone_otp(phone)
        except DjangoValidationError as exc:
            # At this point, any ValidationError must be throttling (format already validated above)
            return Response({'error': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'challenge_id': challenge.id}, status=status.HTTP_201_CREATED)


class VerifyOtpView(views.APIView):
    """POST /api/v1/auth/otp/verify/ — guardian OTP login, step 2 (PAR-001, IAM-003)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ok, message = verify_phone_otp(data['challenge_id'], data['code'])
        if not ok:
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)

        from .models import OTPChallenge
        challenge = OTPChallenge.objects.get(id=data['challenge_id'])
        user = UserModel.all_tenants.filter(phone_e164=challenge.phone_e164).first()
        not_registered = Response(
            {'error': 'Nomor HP ini belum terdaftar sebagai wali murid.', 'code': 'GUARDIAN_NOT_REGISTERED'},
            status=status.HTTP_404_NOT_FOUND,
        )
        if not user:
            return not_registered

        # IAM-014: this endpoint grants a privilege (ROLE_PARENT), so the resolved
        # user must actually be a registered guardian. Without this check any staff
        # member owning a phone number could self-grant the parent role via OTP.
        is_guardian = Guardian.all_tenants.filter(
            foundation_id=user.foundation_id, user=user, deleted_at__isnull=True
        ).exists()
        if not is_guardian:
            return not_registered

        assignment = assign_role(
            user=user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=user.foundation_id,
            foundation_id=user.foundation_id,
        )
        audit(
            action="identity.role.parent_granted",
            entity_type="RoleAssignment",
            entity_id=str(assignment.id),
            actor_id=str(user.id),
            role=RoleAssignment.ROLE_PARENT,
            foundation_id=user.foundation_id,
            school_id=None,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={
                "role": {"after": RoleAssignment.ROLE_PARENT},
                "scope_type": {"after": RoleAssignment.SCOPE_FOUNDATION},
                "scope_id": {"after": user.foundation_id},
                "granted_via": {"after": "otp_verify"},
            },
        )

        refresh = RefreshToken.for_user(user)
        roles = list(
            RoleAssignment.all_tenants.filter(
                foundation_id=user.foundation_id, user=user, deleted_at__isnull=True
            ).values('id', 'role', 'scope_type', 'scope_id')
        )
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'phone_e164': user.phone_e164,
                'email': user.email,
                'foundation_id': user.foundation_id,
                'roles': roles,
            },
        }, status=status.HTTP_200_OK)


class GuardianChildrenView(views.APIView):
    """GET /api/v1/me/children/ — child switcher list for the authed guardian (PAR-003, PAR-017)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if is_staff_user(request.user, foundation_id):
            return Response(
                {'error': 'Endpoint ini khusus untuk akun wali murid.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        links = GuardianLink.all_tenants.filter(
            foundation_id=foundation_id,
            guardian__user=request.user,
            guardian__deleted_at__isnull=True,
            deleted_at__isnull=True,
            student__deleted_at__isnull=True,
        ).select_related('student__person', 'student__school')
        links = list(links)
        serializer = GuardianChildSerializer(
            links, many=True, context={'class_names': self._current_class_names(foundation_id, links)},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @staticmethod
    def _current_class_names(foundation_id, links) -> dict:
        """student_id -> name of the newest active rombel enrolment, in one query."""
        from apps.academic.models import ClassEnrollment
        rows = ClassEnrollment.all_tenants.filter(
            foundation_id=foundation_id, student_id__in=[link.student_id for link in links],
            is_active=True, deleted_at__isnull=True,
        ).order_by('enrolled_at', 'id').values_list('student_id', 'class_group__name')
        # Ascending order: a later enrolment overwrites an earlier one.
        return dict(rows)


# ── Third-Party SSO — Google Workspace / Microsoft 365 (spec/14 §6, TASK-036) ──

class SocialLoginView(views.APIView):
    """POST /api/v1/auth/sso/login/ — verify a provider ID token and mint an EduCore JWT.

    The frontend obtains the ID token via the provider's SDK, sends it here along
    with the foundation context (X-Foundation-ID header, like every other authed
    call). Resolution order: existing SocialLogin link → email match against a
    user holding a staff role (auto-link) → 404 AccountNotLinkedError.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from .social_auth import social_login, SocialAuthError, AccountNotLinkedError, TokenVerificationError

        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data['provider']
        id_token = serializer.validated_data['id_token']

        # Foundation context from header (mirrors EduCoreJWTAuthentication's contract)
        foundation_id = request.headers.get('X-Foundation-ID')
        if foundation_id:
            from educore.middleware.tenancy import set_current_foundation_id
            set_current_foundation_id(int(foundation_id))

        try:
            user, claims = social_login(provider, id_token)
        except AccountNotLinkedError as e:
            return Response({'error': str(e), 'code': 'ACCOUNT_NOT_LINKED'},
                            status=status.HTTP_404_NOT_FOUND)
        except (TokenVerificationError, SocialAuthError) as e:
            return Response({'error': str(e), 'code': 'TOKEN_INVALID'},
                            status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response({'error': 'Akun pengguna tidak aktif.', 'code': 'ACCOUNT_INACTIVE'},
                            status=status.HTTP_403_FORBIDDEN)

        refresh = RefreshToken.for_user(user)
        audit(
            action="identity.sso.login",
            entity_type="User",
            entity_id=str(user.id),
            actor_id=str(user.id),
            foundation_id=user.foundation_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"provider": {"after": provider}},
        )
        roles = list(
            RoleAssignment.all_tenants.filter(
                foundation_id=user.foundation_id, user=user, deleted_at__isnull=True
            ).values('id', 'role', 'scope_type', 'scope_id')
        )
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'phone_e164': user.phone_e164,
                'email': user.email,
                'foundation_id': user.foundation_id,
                'roles': roles,
            },
        }, status=status.HTTP_200_OK)


class SocialLinkView(views.APIView):
    """POST /api/v1/auth/sso/link/ — link the authenticated user to an SSO provider.

    DELETE /api/v1/auth/sso/link/ — soft-delete the user's link for a provider.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .social_auth import social_link, SocialAuthError, AccountAlreadyLinkedError, TokenVerificationError

        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data['provider']
        id_token = serializer.validated_data['id_token']

        try:
            social = social_link(request.user, provider, id_token)
        except AccountAlreadyLinkedError as e:
            return Response({'error': str(e), 'code': 'ACCOUNT_ALREADY_LINKED'},
                            status=status.HTTP_409_CONFLICT)
        except (TokenVerificationError, SocialAuthError) as e:
            return Response({'error': str(e), 'code': 'TOKEN_INVALID'},
                            status=status.HTTP_400_BAD_REQUEST)

        audit(
            action="identity.sso.linked",
            entity_type="SocialLogin",
            entity_id=str(social.id),
            actor_id=str(request.user.id),
            foundation_id=request.user.foundation_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"provider": {"after": provider}},
        )
        return Response({
            'provider': social.provider,
            'provider_user_id': social.provider_user_id,
            'email': social.email,
        }, status=status.HTTP_201_CREATED)

    def delete(self, request):
        from .social_auth import social_unlink, SocialAuthError

        provider = request.query_params.get('provider')
        if provider not in ('google', 'microsoft'):
            return Response({'error': 'Provider harus "google" atau "microsoft".'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            social_unlink(request.user, provider)
        except SocialAuthError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        audit(
            action="identity.sso.unlinked",
            entity_type="SocialLogin",
            entity_id=str(request.user.id),
            actor_id=str(request.user.id),
            foundation_id=request.user.foundation_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"provider": {"after": provider}},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SocialLinksListView(views.APIView):
    """GET /api/v1/auth/sso/links/ — list the authenticated user's SSO links."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .models import SocialLogin
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        links = SocialLogin.all_tenants.filter(
            foundation_id=foundation_id, user=request.user, deleted_at__isnull=True,
        )
        return Response({'results': [
            {'provider': s.provider, 'provider_user_id': s.provider_user_id, 'email': s.email}
            for s in links
        ]})


class MicrosoftTenantConfigView(views.APIView):
    """GET/PUT/DELETE /api/v1/auth/sso/microsoft-tenant/ — per-foundation Microsoft
    Entra tenant pinning for Microsoft 365 SSO (deferred from TASK-036 / PR #115).

    Foundation Admins only, scoped to the caller's own foundation (never URL
    parameters). When pinned, SSO for this foundation only accepts Microsoft ID
    tokens issued by the configured tenant; DELETE removes the pin and restores
    the global-setting fallback.
    """
    permission_classes = [permissions.IsAuthenticated, IsFoundationAdmin]

    def _get_config(self, foundation_id: int):
        from .models import MicrosoftTenantConfig
        return MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        config = self._get_config(foundation_id)
        return Response({
            'tenant_id': config.tenant_id if config else None,
            'updated_at': config.updated_at if config else None,
        }, status=status.HTTP_200_OK)

    def put(self, request):
        from .models import MicrosoftTenantConfig, validate_microsoft_tenant_id

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({'error': 'Konteks yayasan tidak ditemukan.'},
                            status=status.HTTP_400_BAD_REQUEST)

        tenant_id = (request.data.get('tenant_id') or '').strip()
        try:
            validate_microsoft_tenant_id(tenant_id)
        except DjangoValidationError as e:
            return Response({'error': '; '.join(e.messages), 'code': 'TENANT_ID_INVALID'},
                            status=status.HTTP_400_BAD_REQUEST)

        config = self._get_config(foundation_id)
        previous_tenant_id = config.tenant_id if config else None
        if config:
            config.tenant_id = tenant_id
            config.save(update_fields=['tenant_id', 'updated_at', 'updated_by'])
        else:
            config = MicrosoftTenantConfig.all_tenants.create(
                foundation_id=foundation_id,
                tenant_id=tenant_id,
            )

        audit(
            action="identity.sso.microsoft_tenant.set",
            entity_type="MicrosoftTenantConfig",
            entity_id=str(config.id),
            actor_id=str(request.user.id),
            foundation_id=foundation_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"tenant_id": {"before": previous_tenant_id, "after": tenant_id}},
        )
        return Response({'tenant_id': config.tenant_id}, status=status.HTTP_200_OK)

    def delete(self, request):
        from .models import MicrosoftTenantConfig
        from django.utils import timezone as dj_timezone

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        deleted = MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).update(deleted_at=dj_timezone.now())

        if deleted:
            audit(
                action="identity.sso.microsoft_tenant.removed",
                entity_type="MicrosoftTenantConfig",
                entity_id=str(foundation_id),
                actor_id=str(request.user.id),
                foundation_id=foundation_id,
                ip_address=request.META.get('REMOTE_ADDR'),
                diff={"tenant_id": {"after": None}},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

