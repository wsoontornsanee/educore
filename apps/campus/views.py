from rest_framework import exceptions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.identity.models import Guardian, School, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id
from .models import BehaviourCase, BehaviourPolicy, BehaviourReason, BehaviourRecord
from .serializers import (
    AcknowledgeRecordInputSerializer,
    BehaviourCaseSerializer,
    BehaviourPolicySerializer,
    BehaviourReasonSerializer,
    BehaviourRecordSerializer,
    RecordBehaviourInputSerializer,
    StudentBehaviourSummarySerializer,
    SupersedeRecordInputSerializer,
)
from .services import (
    acknowledge_behaviour_record,
    get_or_create_behaviour_policy,
    get_student_behaviour_summary,
    record_behaviour,
    supersede_behaviour_record,
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
        if not foundation_id:
            return BehaviourRecord.objects.none()
        qs = BehaviourRecord.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related('reason', 'student__person', 'recorded_by').order_by('-created_at')
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
