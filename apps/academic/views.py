from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.identity.permissions import HasRequiredPermission
from apps.identity.models import Staff, Student
from educore.middleware.tenancy import get_current_foundation_id

from apps.academic.models import (
    AcademicYear,
    Assessment,
    AssessmentScore,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    LearningObjective,
    Subject,
    Term,
    TimetableSlot,
    TimetableSubstitution,
)
from apps.academic.serializers import (
    AcademicYearSerializer,
    AssessmentScoreSerializer,
    AssessmentSerializer,
    BulkScoreEntrySerializer,
    ClassEnrollmentSerializer,
    ClassGroupSerializer,
    ClassSubjectSerializer,
    LearningObjectiveSerializer,
    SubjectSerializer,
    TermSerializer,
    TimetableSlotSerializer,
    TimetableSubstitutionSerializer,
)
from apps.academic.services import (
    ReasonRequiredError,
    ScoreOutOfRangeError,
    TimetableConflictError,
    WeightConfigError,
    assign_substitution,
    compute_term_grade,
    create_timetable_slot,
    publish_assessment,
    set_assessment_score,
)


class TenantScopedModelViewSet(viewsets.ModelViewSet):
    """Common tenancy-scoped queryset behaviour for academic reference data."""
    model = None
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return self.model.objects.none()
        qs = self.model.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        for param, field in getattr(self, 'filter_params', {}).items():
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        return qs

    def perform_create(self, serializer):
        serializer.save(foundation_id=get_current_foundation_id())


class AcademicYearViewSet(TenantScopedModelViewSet):
    model = AcademicYear
    serializer_class = AcademicYearSerializer
    filter_params = {'school_id': 'school_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class TermViewSet(TenantScopedModelViewSet):
    model = Term
    serializer_class = TermSerializer
    filter_params = {'academic_year_id': 'academic_year_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class SubjectViewSet(TenantScopedModelViewSet):
    model = Subject
    serializer_class = SubjectSerializer
    filter_params = {'school_id': 'school_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class ClassGroupViewSet(TenantScopedModelViewSet):
    model = ClassGroup
    serializer_class = ClassGroupSerializer
    filter_params = {'school_id': 'school_id', 'academic_year_id': 'academic_year_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class ClassEnrollmentViewSet(TenantScopedModelViewSet):
    model = ClassEnrollment
    serializer_class = ClassEnrollmentSerializer
    filter_params = {'class_group_id': 'class_group_id', 'student_id': 'student_id'}
    action_permissions = {
        'list': 'student_records.read', 'retrieve': 'student_records.read',
        'create': 'student_records.write', 'update': 'student_records.write',
        'partial_update': 'student_records.write', 'destroy': 'student_records.write',
    }


class ClassSubjectViewSet(TenantScopedModelViewSet):
    model = ClassSubject
    serializer_class = ClassSubjectSerializer
    filter_params = {'class_group_id': 'class_group_id', 'term_id': 'term_id', 'teacher_id': 'teacher_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class LearningObjectiveViewSet(TenantScopedModelViewSet):
    model = LearningObjective
    serializer_class = LearningObjectiveSerializer
    filter_params = {'subject_id': 'subject_id'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
    }


class AssessmentViewSet(TenantScopedModelViewSet):
    model = Assessment
    serializer_class = AssessmentSerializer
    filter_params = {'class_subject_id': 'class_subject_id', 'type': 'type'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'publish': 'grades.write', 'scores': 'grades.write',
    }

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        assessment = self.get_object()
        try:
            published = publish_assessment(assessment)
        except WeightConfigError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(published).data)

    @action(detail=True, methods=['put'], url_path='scores')
    def scores(self, request, pk=None):
        """Bulk score entry for this assessment (ACD-004, ACD-005)."""
        assessment = self.get_object()
        payload = BulkScoreEntrySerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        foundation_id = get_current_foundation_id()
        results = []
        for row in payload.validated_data['scores']:
            student = Student.objects.filter(id=row['student_id'], foundation_id=foundation_id).first()
            if not student:
                return Response(
                    {'error': _("Siswa tidak ditemukan: %(id)s") % {'id': row['student_id']}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            try:
                record = set_assessment_score(
                    assessment=assessment,
                    student=student,
                    score=row.get('score'),
                    feedback=row.get('feedback', ''),
                    reason=row.get('reason'),
                    actor=request.user,
                )
            except (ScoreOutOfRangeError, ReasonRequiredError) as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            results.append(AssessmentScoreSerializer(record).data)

        return Response({'scores': results}, status=status.HTTP_200_OK)


class TimetableSlotViewSet(TenantScopedModelViewSet):
    model = TimetableSlot
    serializer_class = TimetableSlotSerializer
    filter_params = {'class_subject_id': 'class_subject_id', 'day_of_week': 'day_of_week'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        class_subject = serializer.validated_data['class_subject']
        try:
            slot = create_timetable_slot(
                class_subject=class_subject,
                day_of_week=serializer.validated_data['day_of_week'],
                period_no=serializer.validated_data['period_no'],
                start_time=serializer.validated_data['start_time'],
                end_time=serializer.validated_data['end_time'],
                room=serializer.validated_data.get('room', ''),
            )
        except TimetableConflictError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(slot).data, status=status.HTTP_201_CREATED)


class TimetableSubstitutionViewSet(TenantScopedModelViewSet):
    model = TimetableSubstitution
    serializer_class = TimetableSubstitutionSerializer
    filter_params = {'slot_id': 'slot_id', 'date': 'date'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        slot = TimetableSlot.objects.filter(id=request.data.get('slot'), foundation_id=foundation_id).first()
        if not slot:
            return Response({'error': _("Slot jadwal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        substitute_teacher = Staff.objects.filter(
            id=request.data.get('substitute_teacher'), foundation_id=foundation_id
        ).first()
        if not substitute_teacher:
            return Response({'error': _("Guru pengganti tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        substitution = assign_substitution(
            slot=slot,
            date=request.data.get('date'),
            substitute_teacher=substitute_teacher,
            reason=request.data.get('reason', ''),
        )
        return Response(self.get_serializer(substitution).data, status=status.HTTP_201_CREATED)


class GradebookView(APIView):
    """GET /gradebook?class_subject_id&term_id -> matrix {students, assessments, scores} (spec/04 §8)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        class_subject_id = request.query_params.get('class_subject_id')
        if not class_subject_id:
            return Response({'error': _("Parameter class_subject_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        class_subject = ClassSubject.objects.filter(id=class_subject_id, foundation_id=foundation_id).first()
        if not class_subject:
            return Response({'error': _("Kelas/mapel tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        students = list(
            Student.objects.filter(
                foundation_id=foundation_id,
                class_enrollments__class_group=class_subject.class_group,
                class_enrollments__is_active=True,
                class_enrollments__deleted_at__isnull=True,
                deleted_at__isnull=True,
            ).distinct().order_by('nis')
        )
        assessments = list(
            Assessment.objects.filter(class_subject=class_subject, deleted_at__isnull=True).order_by('due_at', 'id')
        )
        scores = AssessmentScore.objects.filter(
            assessment__in=assessments, student__in=students
        ).select_related('assessment', 'student')

        score_map = {(s.assessment_id, s.student_id): s for s in scores}

        return Response({
            'students': [{'id': s.id, 'nis': s.nis, 'name': s.person.full_name} for s in students],
            'assessments': AssessmentSerializer(assessments, many=True).data,
            'scores': [
                {
                    'assessment_id': a.id,
                    'student_id': s.id,
                    'score': str(score_map[(a.id, s.id)].score) if (a.id, s.id) in score_map and score_map[(a.id, s.id)].score is not None else None,
                    'descriptor': score_map[(a.id, s.id)].descriptor if (a.id, s.id) in score_map else '',
                }
                for a in assessments for s in students
            ],
        })


class StudentAttainmentView(APIView):
    """GET /students/:id/attainment?class_subject_id -> weighted final grade (ACD-008/009)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        class_subject_id = request.query_params.get('class_subject_id')
        class_subject = ClassSubject.objects.filter(id=class_subject_id, foundation_id=foundation_id).first()
        if not class_subject:
            return Response({'error': _("Kelas/mapel tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = compute_term_grade(student, class_subject)
        except WeightConfigError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result)
