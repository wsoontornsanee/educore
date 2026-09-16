"""API views for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, views, viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from apps.core.services import audit
from educore.middleware.tenancy import get_current_foundation_id
from .guardian_access import is_staff_user
from .models import FoundationEntitlement, GuardianLink, RoleAssignment, User as UserModel
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
)
from .services import request_phone_otp, verify_phone_otp, normalize_phone_e164

class EduCoreTokenObtainPairView(TokenObtainPairView):
    """Custom JWT token obtain view supporting dual phone/email identifier login (IAM-001)."""
    serializer_class = EduCoreTokenObtainPairSerializer

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
        if not user:
            return Response(
                {'error': 'Nomor HP ini belum terdaftar sebagai wali murid.', 'code': 'GUARDIAN_NOT_REGISTERED'},
                status=status.HTTP_404_NOT_FOUND,
            )

        assign_role(
            user=user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=user.foundation_id,
            foundation_id=user.foundation_id,
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
        ).select_related('student__person')
        serializer = GuardianChildSerializer(links, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

