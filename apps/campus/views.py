from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import exceptions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.core.services import audit
from apps.identity.models import Guardian, School, Staff, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id
from .models import (
    BehaviourCase,
    BehaviourPolicy,
    BehaviourReason,
    BehaviourRecord,
    ClinicPolicy,
    ClinicVisit,
    CounsellingConfidentiality,
    CounsellingSession,
    HealthProfile,
    LibraryItem,
    Loan,
    LoanBorrowerType,
    MedicationStock,
)
from .serializers import (
    AcknowledgeRecordInputSerializer,
    BehaviourCaseSerializer,
    BehaviourPolicySerializer,
    BehaviourReasonSerializer,
    BehaviourRecordSerializer,
    CheckoutLoanInputSerializer,
    ClinicPolicySerializer,
    ClinicVisitSerializer,
    CounsellingSessionSerializer,
    HealthProfileSerializer,
    LibraryItemSerializer,
    LoanSerializer,
    MarkLoanLostInputSerializer,
    MedicationStockSerializer,
    RecordBehaviourInputSerializer,
    RecordClinicVisitInputSerializer,
    RecordCounsellingSessionInputSerializer,
    ReturnLoanInputSerializer,
    StudentBehaviourSummarySerializer,
    StudentMedicalAlertSerializer,
    SupersedeRecordInputSerializer,
)
from .services import (
    access_counselling_session,
    acknowledge_behaviour_record,
    checkout_library_item,
    get_or_create_behaviour_policy,
    get_overdue_loans,
    get_principal_users,
    get_student_behaviour_summary,
    mark_loan_lost,
    record_behaviour,
    record_counselling_session,
    return_library_item,
    supersede_behaviour_record,
)
from .services_clinic import (
    get_medication_stock_alerts,
    get_or_create_clinic_policy,
    get_or_create_health_profile,
    record_clinic_visit,
)


class BehaviourReasonViewSet(viewsets.ModelViewSet):
    """CRUD viewset for school behaviour reasons catalogue (spec/10 §2, §4, TCH-009)."""
    queryset = BehaviourReason.objects.all()
    serializer_class = BehaviourReasonSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'
    action_permissions = {
        'create': 'behaviour.write',
        'update': 'behaviour.write',
        'partial_update': 'behaviour.write',
        'destroy': 'behaviour.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return BehaviourReason.objects.none()
        qs = BehaviourReason.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ['true', '1'])
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        serializer.save(foundation_id=foundation_id)


class BehaviourPolicyView(APIView):
    """View to retrieve and update school behaviour policy (LIF-009, LIF-014)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'

    def get_required_permission(self):
        if self.request.method in ['PUT', 'PATCH']:
            return 'behaviour.write'
        return 'behaviour.read'

    def get(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_behaviour_policy(school)
        serializer = BehaviourPolicySerializer(policy)
        return Response(serializer.data)

    def put(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_behaviour_policy(school)
        serializer = BehaviourPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BehaviourRecordViewSet(viewsets.ModelViewSet):
    """Viewset for recording and inspecting student behaviour entries (LIF-008 to LIF-013)."""
    queryset = BehaviourRecord.objects.all().select_related('reason', 'student__person', 'recorded_by')
    serializer_class = BehaviourRecordSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'
    action_permissions = {
        'create': 'behaviour.write',
        'supersede': 'behaviour.write',
        'acknowledge': 'behaviour.read',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        qs = BehaviourRecord.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related('reason', 'student__person', 'recorded_by').order_by('-created_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return BehaviourRecord.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids, STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)

                if staff_school_ids and parent_student_ids:
                    from django.db.models import Q
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(student_id__in=parent_student_ids))
                elif staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif parent_student_ids:
                    qs = qs.filter(student_id__in=parent_student_ids)
                else:
                    return BehaviourRecord.objects.none()

        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        term_id = self.request.query_params.get('term_id')
        if term_id:
            qs = qs.filter(term_id=term_id)
        is_superseded = self.request.query_params.get('is_superseded')
        if is_superseded is not None:
            qs = qs.filter(is_superseded=is_superseded.lower() in ['true', '1'])
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(reason__category=category)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = RecordBehaviourInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        try:
            reason = BehaviourReason.objects.get(pk=data['reason_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except BehaviourReason.DoesNotExist:
            raise exceptions.NotFound("Alasan perilaku tidak ditemukan.")

        term = None
        if data.get('term_id'):
            from apps.academic.models import Term
            try:
                term = Term.objects.get(pk=data['term_id'], foundation_id=foundation_id, deleted_at__isnull=True)
            except Term.DoesNotExist:
                raise exceptions.NotFound("Semester tidak ditemukan.")

        try:
            record = record_behaviour(
                foundation_id=foundation_id,
                school=student.school,
                student=student,
                reason=reason,
                recorded_by=request.user,
                term=term,
                points=data.get('points'),
                note=data.get('note', ''),
                occurred_at=data.get('occurred_at'),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = BehaviourRecordSerializer(record)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("DELETE", detail="Catatan perilaku tidak dapat dihapus secara fisik (LIF-013). Gunakan perbaikan catatan.")

    @action(detail=True, methods=['post'], url_path='supersede')
    def supersede(self, request, pk=None):
        record = self.get_object()
        serializer = SupersedeRecordInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            new_reason = BehaviourReason.objects.get(pk=data['reason_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except BehaviourReason.DoesNotExist:
            raise exceptions.NotFound("Alasan perilaku baru tidak ditemukan.")

        try:
            new_record = supersede_behaviour_record(
                original_record=record,
                new_reason=new_reason,
                recorded_by=request.user,
                correction_reason=data['correction_reason'],
                points=data.get('points'),
                note=data.get('note', ''),
                occurred_at=data.get('occurred_at'),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = BehaviourRecordSerializer(new_record)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='acknowledge')
    def acknowledge(self, request, pk=None):
        record = self.get_object()
        serializer = AcknowledgeRecordInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        guardian_id = data.get('guardian_id')
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

        if guardian_id:
            try:
                guardian = Guardian.objects.get(pk=guardian_id, foundation_id=foundation_id, deleted_at__isnull=True)
            except Guardian.DoesNotExist:
                raise exceptions.NotFound("Wali murid tidak ditemukan.")
        else:
            # Infer from user
            guardian = Guardian.objects.filter(user=request.user, foundation_id=foundation_id, deleted_at__isnull=True).first()
            if not guardian:
                raise exceptions.ValidationError("Wali murid harus ditentukan.")

        try:
            acknowledged = acknowledge_behaviour_record(
                record=record,
                guardian=guardian,
                user=request.user,
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = BehaviourRecordSerializer(acknowledged)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


class StudentBehaviourSummaryView(APIView):
    """View to return student positive, negative, and net points summary (LIF-008, LIF-010)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'

    def get(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        term = None
        term_id = request.query_params.get('term_id')
        if term_id:
            from apps.academic.models import Term
            try:
                term = Term.objects.get(pk=term_id, foundation_id=foundation_id, deleted_at__isnull=True)
            except Term.DoesNotExist:
                raise exceptions.NotFound("Semester tidak ditemukan.")

        summary_data = get_student_behaviour_summary(student, term=term)
        serializer = StudentBehaviourSummarySerializer(summary_data)
        return Response(serializer.data)


class BehaviourCaseViewSet(viewsets.ModelViewSet):
    """CRUD and status update for escalated discipline cases (LIF-009)."""
    queryset = BehaviourCase.objects.all().select_related('student__person', 'assigned_counsellor__person')
    serializer_class = BehaviourCaseSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'
    action_permissions = {
        'create': 'behaviour.write',
        'update': 'behaviour.write',
        'partial_update': 'behaviour.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return BehaviourCase.objects.none()
        qs = BehaviourCase.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related('student__person', 'assigned_counsellor__person').order_by('-created_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return BehaviourCase.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids, STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)

                if staff_school_ids and parent_student_ids:
                    from django.db.models import Q
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(student_id__in=parent_student_ids))
                elif staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif parent_student_ids:
                    qs = qs.filter(student_id__in=parent_student_ids)
                else:
                    return BehaviourCase.objects.none()

        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        case_status = self.request.query_params.get('status')
        if case_status:
            qs = qs.filter(status=case_status)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        serializer.save(foundation_id=foundation_id)


class CounsellingSessionViewSet(viewsets.ModelViewSet):
    """Guidance counselling (BK) session recording and confidentiality-aware access
    (spec/10 §5, LIF-015 to LIF-018). Staff-only surface — never guardian-facing,
    regardless of confidentiality level."""
    queryset = CounsellingSession.objects.all().select_related('student__person', 'counsellor__person')
    serializer_class = CounsellingSessionSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'
    action_permissions = {
        'create': 'behaviour.write',
    }
    pagination_class = StandardCursorPagination
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        """Staff-scoped only (LIF-015: never guardian-facing). Confidentiality
        visibility is enforced separately in `list()` (silent omission) and
        `retrieve()` (403 + audit) rather than baked into this base queryset,
        so retrieve can distinguish "not staff at this school" (404) from
        "staff at this school but not authorized for a RESTRICTED note" (403)."""
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return CounsellingSession.objects.none()
        qs = CounsellingSession.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person', 'counsellor__person').order_by('-created_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return CounsellingSession.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                if staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                else:
                    return CounsellingSession.objects.none()

        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    @staticmethod
    def _attach_notes(sessions):
        """Decrypts notes for sessions the caller has ALREADY authorized (list()
        excluded unauthorized RESTRICTED rows before calling this; retrieve()
        raised via `access_counselling_session` first) — no re-check here."""
        for session in sessions:
            session._decrypted_notes = session.notes

    @staticmethod
    def _is_authorized(session, user, principal_ids_by_school):
        """Same rule as `is_counselling_reader_authorized`, but reusing a
        pre-resolved {school_id: {principal_user_id, ...}} map instead of
        re-querying RoleAssignment per row (avoids O(RESTRICTED rows) query
        fan-out in `list()` — each `get_principal_users` call is 2-3 queries)."""
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if session.counsellor.user_id == user.id:
            return True
        return user.id in principal_ids_by_school.get(session.school_id, set())

    def list(self, request, *args, **kwargs):
        """LIF-015: RESTRICTED sessions the requester isn't authorized for are
        silently omitted from listings (no audit — nothing was actually read).
        Unauthorized ids are excluded via `.exclude()` (not a Python list) so
        cursor pagination still gets a real queryset to order and slice."""
        queryset = self.filter_queryset(self.get_queryset())
        restricted = list(queryset.filter(confidentiality=CounsellingConfidentiality.RESTRICTED))

        principal_ids_by_school = {
            school_id: {u.id for u in get_principal_users(session.school)}
            for school_id, session in {s.school_id: s for s in restricted}.items()
        }
        unauthorized_ids = [
            s.id for s in restricted
            if not self._is_authorized(s, request.user, principal_ids_by_school)
        ]
        if unauthorized_ids:
            queryset = queryset.exclude(id__in=unauthorized_ids)

        page = self.paginate_queryset(queryset)
        target = page if page is not None else list(queryset)
        self._attach_notes(target)
        serializer = self.get_serializer(target, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            access_counselling_session(instance, request.user, foundation_id)
        except PermissionDenied as exc:
            raise exceptions.PermissionDenied(str(exc))
        self._attach_notes([instance])
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = RecordCounsellingSessionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        try:
            counsellor = Staff.objects.get(pk=data['counsellor_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Staff.DoesNotExist:
            raise exceptions.NotFound("Konselor tidak ditemukan.")

        case = None
        if data.get('case_id'):
            try:
                case = BehaviourCase.objects.get(pk=data['case_id'], foundation_id=foundation_id, deleted_at__isnull=True)
            except BehaviourCase.DoesNotExist:
                raise exceptions.NotFound("Kasus perilaku tidak ditemukan.")

        try:
            session = record_counselling_session(
                foundation_id=foundation_id,
                school=student.school,
                student=student,
                counsellor=counsellor,
                recorded_by=request.user,
                case=case,
                occurred_at=data.get('occurred_at'),
                session_type=data.get('type', 'INITIAL'),
                notes=data.get('notes', ''),
                follow_up_at=data.get('follow_up_at'),
                confidentiality=data.get('confidentiality', 'NORMAL'),
                is_urgent=data.get('is_urgent', False),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        self._attach_notes([session])
        output_serializer = self.get_serializer(session)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class LibraryItemViewSet(viewsets.ModelViewSet):
    """CRUD viewset for the library/asset catalogue (spec/10 §6)."""
    queryset = LibraryItem.objects.all()
    serializer_class = LibraryItemSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'library.read'
    action_permissions = {
        'create': 'library.write',
        'update': 'library.write',
        'partial_update': 'library.write',
        'destroy': 'library.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return LibraryItem.objects.none()
        qs = LibraryItem.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('title')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        item_type = self.request.query_params.get('type')
        if item_type:
            qs = qs.filter(type=item_type)
        available_only = self.request.query_params.get('available_only')
        if available_only is not None and available_only.lower() in ['true', '1']:
            qs = qs.filter(copies_available__gt=0)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        copies_total = serializer.validated_data.get('copies_total', 1)
        serializer.save(foundation_id=foundation_id, copies_available=copies_total)


class LoanViewSet(viewsets.ModelViewSet):
    """Checkout/return viewset for library loans (LIF-019 to LIF-023)."""
    queryset = Loan.objects.all().select_related('item')
    serializer_class = LoanSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'library.read'
    action_permissions = {
        'create': 'library.write',
        'return_item': 'library.write',
        'mark_lost': 'library.write',
    }
    pagination_class = StandardCursorPagination
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Loan.objects.none()
        qs = Loan.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related(
            'item'
        ).order_by('-borrowed_at', '-id')

        user = self.request.user
        if not user or not user.is_authenticated:
            return Loan.objects.none()

        if not user.is_superuser:
            from django.db.models import Q
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids, STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)
                student_borrower_q = Q(borrower_type=LoanBorrowerType.STUDENT, borrower_id__in=parent_student_ids)

                if staff_school_ids and parent_student_ids:
                    qs = qs.filter(Q(item__school_id__in=staff_school_ids) | student_borrower_q)
                elif staff_school_ids:
                    qs = qs.filter(item__school_id__in=staff_school_ids)
                elif parent_student_ids:
                    qs = qs.filter(student_borrower_q)
                else:
                    return Loan.objects.none()

        borrower_id = self.request.query_params.get('borrower_id')
        if borrower_id:
            qs = qs.filter(borrower_id=borrower_id)
        borrower_type = self.request.query_params.get('borrower_type')
        if borrower_type:
            qs = qs.filter(borrower_type=borrower_type)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(item__school_id=school_id)
        loan_status = self.request.query_params.get('status')
        if loan_status:
            qs = qs.filter(status=loan_status)
        return qs

    def create(self, request, *args, **kwargs):
        """POST /library/loans — checkout by student/staff card tap (LIF-019)."""
        serializer = CheckoutLoanInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            item = LibraryItem.objects.get(pk=data['item_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except LibraryItem.DoesNotExist:
            raise exceptions.NotFound("Barang perpustakaan tidak ditemukan.")

        try:
            loan = checkout_library_item(
                school=item.school,
                item=item,
                borrower_type=data['borrower_type'],
                borrower_id=data['borrower_id'],
                checked_out_by=request.user,
                occurred_at=data.get('occurred_at'),
                condition_on_issue=data.get('condition_on_issue', ''),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = LoanSerializer(loan)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='return')
    def return_item(self, request, pk=None):
        """POST /library/loans/:id/return (spec/10 §7)."""
        loan = self.get_object()
        serializer = ReturnLoanInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            returned = return_library_item(
                loan=loan,
                condition_on_return=data.get('condition_on_return', ''),
                occurred_at=data.get('occurred_at'),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = LoanSerializer(returned)
        return Response(output_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='lost')
    def mark_lost(self, request, pk=None):
        """POST /library/loans/:id/lost — lost item handling with staff approval (LIF-021)."""
        loan = self.get_object()
        serializer = MarkLoanLostInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            approved_by = Staff.objects.get(pk=data['approved_by_staff_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Staff.DoesNotExist:
            raise exceptions.NotFound("Staf yang menyetujui tidak ditemukan.")

        try:
            lost_loan = mark_loan_lost(
                loan=loan,
                approved_by=approved_by,
                occurred_at=data.get('occurred_at'),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = LoanSerializer(lost_loan)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


class OverdueLoansView(APIView):
    """GET /library/overdue (spec/10 §7)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'library.read'

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        school_id = request.query_params.get('school_id')
        overdue = get_overdue_loans(foundation_id=foundation_id, school_id=school_id)
        serializer = LoanSerializer(overdue, many=True)
        return Response(serializer.data)


class ClinicPolicyView(APIView):
    """Retrieve and update school clinic policy (LIF-006)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get_required_permission(self):
        if self.request.method in ['PUT', 'PATCH']:
            return 'clinic.write'
        return 'clinic.read'

    def get(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_clinic_policy(school)
        serializer = ClinicPolicySerializer(policy)
        return Response(serializer.data)

    def put(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_clinic_policy(school)
        old_teacher_sees_allergies = policy.teacher_sees_allergies
        serializer = ClinicPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        audit(
            action='campus.clinic_policy.updated',
            entity_type='ClinicPolicy',
            entity_id=str(policy.id),
            actor_id=str(request.user.id) if request.user else None,
            foundation_id=foundation_id,
            school_id=school.id,
            diff={
                'teacher_sees_allergies': {
                    'old': old_teacher_sees_allergies,
                    'new': policy.teacher_sees_allergies,
                },
            },
        )
        return Response(serializer.data)


class MedicationStockViewSet(viewsets.ModelViewSet):
    """CRUD for UKS medication/first-aid inventory (LIF-004, LIF-005)."""
    queryset = MedicationStock.objects.all()
    serializer_class = MedicationStockSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'
    action_permissions = {
        'create': 'clinic.write',
        'update': 'clinic.write',
        'partial_update': 'clinic.write',
        'destroy': 'clinic.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return MedicationStock.objects.none()
        qs = MedicationStock.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('name')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        instance = serializer.save(foundation_id=foundation_id)
        audit(
            action='campus.medication_stock.created',
            entity_type='MedicationStock',
            entity_id=str(instance.id),
            actor_id=str(self.request.user.id) if self.request.user else None,
            foundation_id=foundation_id,
            school_id=instance.school_id,
            diff={'name': instance.name, 'quantity': instance.quantity},
        )

    def perform_update(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        instance = serializer.save()
        audit(
            action='campus.medication_stock.updated',
            entity_type='MedicationStock',
            entity_id=str(instance.id),
            actor_id=str(self.request.user.id) if self.request.user else None,
            foundation_id=foundation_id,
            school_id=instance.school_id,
            diff={'name': instance.name, 'quantity': instance.quantity},
        )

    def perform_destroy(self, instance):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        audit(
            action='campus.medication_stock.deleted',
            entity_type='MedicationStock',
            entity_id=str(instance.id),
            actor_id=str(self.request.user.id) if self.request.user else None,
            foundation_id=foundation_id,
            school_id=instance.school_id,
            diff={'name': instance.name},
        )
        instance.delete()


class MedicationStockAlertView(APIView):
    """LIF-005: below-reorder-level or near-expiry medication stock for the clinic dashboard."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        alerts = get_medication_stock_alerts(school)
        serializer = MedicationStockSerializer(alerts, many=True)
        return Response(serializer.data)


class StudentHealthProfileView(APIView):
    """Retrieve and update a student's health profile (LIF-002)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get_required_permission(self):
        if self.request.method in ['PUT', 'PATCH']:
            return 'clinic.write'
        return 'clinic.read'

    def get(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        serializer = HealthProfileSerializer(profile)
        return Response(serializer.data)

    def put(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        serializer = HealthProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed_fields = sorted(serializer.validated_data.keys())
        serializer.save()

        audit(
            action='campus.health_profile.updated',
            entity_type='HealthProfile',
            entity_id=str(profile.id),
            actor_id=str(request.user.id) if request.user else None,
            foundation_id=foundation_id,
            school_id=student.school_id,
            diff={
                'student_id': student.id,
                'changed_fields': changed_fields,
            },
        )
        return Response(serializer.data)


class StudentMedicalAlertView(APIView):
    """LIF-006: teacher-facing non-clinical medical alert flag."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'

    def get(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        policy = get_or_create_clinic_policy(student.school)

        data = {'has_medical_alert': profile.has_medical_alert}
        if policy.teacher_sees_allergies:
            data['allergies'] = profile.allergies
        serializer = StudentMedicalAlertSerializer(data)
        return Response(serializer.data)


class ClinicVisitViewSet(viewsets.ModelViewSet):
    """Records and lists UKS clinic visits (LIF-001, LIF-003, LIF-004, LIF-007)."""
    queryset = ClinicVisit.objects.all().select_related('student__person', 'handled_by__person', 'medication_given')
    serializer_class = ClinicVisitSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'
    action_permissions = {
        'create': 'clinic.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return ClinicVisit.objects.none()
        qs = ClinicVisit.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related(
            'student__person', 'handled_by__person', 'medication_given'
        ).order_by('-occurred_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return ClinicVisit.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids, STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES,
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)

                if staff_school_ids and parent_student_ids:
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(student_id__in=parent_student_ids))
                elif staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif parent_student_ids:
                    qs = qs.filter(student_id__in=parent_student_ids)
                else:
                    return ClinicVisit.objects.none()

        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = RecordClinicVisitInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        handled_by = Staff.objects.filter(user=request.user, foundation_id=foundation_id, deleted_at__isnull=True).first()
        if not handled_by:
            raise exceptions.ValidationError("Pengguna saat ini tidak terdaftar sebagai staf.")

        medication = None
        if data.get('medication_id'):
            try:
                medication = MedicationStock.objects.get(pk=data['medication_id'], foundation_id=foundation_id, deleted_at__isnull=True)
            except MedicationStock.DoesNotExist:
                raise exceptions.NotFound("Stok obat tidak ditemukan.")

        try:
            visit = record_clinic_visit(
                foundation_id=foundation_id,
                school=student.school,
                student=student,
                handled_by=handled_by,
                complaint=data['complaint'],
                treatment=data.get('treatment', ''),
                vitals=data.get('vitals') or {},
                outcome=data['outcome'],
                medication=medication,
                medication_quantity=data.get('medication_quantity'),
                guardian_consent_confirmed=data.get('guardian_consent_confirmed', False),
                guardian_consent_note=data.get('guardian_consent_note', ''),
                occurred_at=data.get('occurred_at'),
            )
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.messages if hasattr(exc, 'messages') else str(exc))

        output_serializer = ClinicVisitSerializer(visit)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("PUT", detail="Kunjungan klinik tidak dapat diubah setelah dicatat.")

    def partial_update(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("PATCH", detail="Kunjungan klinik tidak dapat diubah setelah dicatat.")

    def destroy(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("DELETE", detail="Kunjungan klinik tidak dapat dihapus.")
