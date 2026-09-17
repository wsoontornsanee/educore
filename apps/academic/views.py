from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.idempotency import IdempotentViewMixin
from apps.core.pagination import StandardCursorPagination
from apps.core.services import build_signed_download
from apps.identity.permissions import HasRequiredPermission
from apps.identity.models import School, Staff, Student
from educore.middleware.tenancy import get_current_foundation_id

from apps.academic.models import (
    AcademicYear,
    Assessment,
    AssessmentScore,
    Broadcast,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamAttemptStatus,
    ExamQuestion,
    ExamQuestionType,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    LearningObjective,
    LessonPlan,
    PermissionSlip,
    PermissionSlipAcknowledgement,
    ReportCard,
    Subject,
    SubstitutionStatus,
    Term,
    TimetableSlot,
    TimetableSubstitution,
)
from apps.academic.serializers import (
    AcademicYearSerializer,
    AssessmentScoreSerializer,
    AssessmentSerializer,
    BroadcastCreateSerializer,
    BroadcastPolicySerializer,
    BroadcastSerializer,
    BulkScoreEntrySerializer,
    ScoreCsvImportSerializer,
    ClassEnrollmentSerializer,
    ClassGroupSerializer,
    ClassSubjectSerializer,
    ExamAnswerSerializer,
    ExamAttemptSerializer,
    ExamQuestionPublicSerializer,
    ExamQuestionSerializer,
    ExamSerializer,
    GradeEssaySerializer,
    HomeworkFileUploadSerializer,
    HomeworkGradeSerializer,
    HomeworkGradingQueueItemSerializer,
    HomeworkReturnSerializer,
    HomeworkSerializer,
    HomeworkSubmissionSerializer,
    HomeworkSubmitSerializer,
    LearningObjectiveSerializer,
    LessonPlanDuplicateSerializer,
    LessonPlanSerializer,
    PeriodGridSetSerializer,
    PeriodGridSlotSerializer,
    ReportCardContentUpdateSerializer,
    ReportCardGenerateSerializer,
    ReportCardPolicySerializer,
    ReportCardSerializer,
    SaveAnswerSerializer,
    SubjectSerializer,
    TermSerializer,
    TimetableSlotSerializer,
    TimetableSubstitutionSerializer,
)
from apps.academic.services import (
    AttemptAlreadySubmittedError,
    BroadcastNotAllowedError,
    BroadcastRateLimitedError,
    ExamWindowError,
    HomeworkSubmissionStateError,
    InvalidSubmissionFilesError,
    PeriodGridMismatchError,
    ReasonRequiredError,
    ReminderRateLimitedError,
    ReportCardStateError,
    ScoreConflictError,
    ScoreCsvError,
    ScoreOutOfRangeError,
    TimetableConflictError,
    WeightConfigError,
    apply_bulk_score_import,
    approve_report_card,
    assign_homework,
    assign_substitution,
    auto_submit_if_expired,
    compute_remaining_seconds,
    compute_term_grade,
    create_timetable_slot,
    duplicate_lesson_plan,
    generate_report_cards,
    preview_bulk_score_import,
    get_homework_completion,
    get_homework_remind_status,
    get_expected_periods_for_school,
    get_or_create_broadcast_policy,
    get_or_create_report_card_policy,
    get_period_grid,
    get_visible_report_card,
    grade_essay_answer,
    grade_homework_submission,
    return_homework_submission,
    publish_assessment,
    publish_report_card,
    record_focus_loss,
    remind_unsubmitted,
    revise_report_card,
    save_answer,
    send_broadcast,
    acknowledge_permission_slip,
    create_permission_slip,
    get_slip_consent_tally,
    GuardianNotLinkedError,
    PermissionSlipClosedError,
    StudentNotEnrolledError,
    set_arrears_gate,
    set_assessment_score,
    set_report_card_content,
    suggest_objective_narrative,
    set_broadcast_policy,
    set_period_grid,
    start_attempt,
    submit_attempt,
    store_homework_submission_file,
    submit_homework,
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
        'publish': 'grades.write', 'scores': 'grades.write', 'import_scores_csv': 'grades.write',
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
                    expected_version=row.get('expected_version'),
                )
            except (ScoreOutOfRangeError, ReasonRequiredError) as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            except ScoreConflictError as e:
                return Response({
                    'error': str(e),
                    'conflict': {
                        'student_id': row['student_id'],
                        'current_score': str(e.current_score) if e.current_score is not None else None,
                        'current_version': e.current_version,
                        'current_feedback': e.current_feedback,
                        'current_descriptor': e.current_descriptor,
                        'current_graded_by': e.current_graded_by,
                        'current_graded_at': e.current_graded_at,
                    },
                }, status=status.HTTP_409_CONFLICT)
            results.append(AssessmentScoreSerializer(record).data)

        return Response({'scores': results}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='import-scores-csv', parser_classes=[MultiPartParser])
    def import_scores_csv(self, request, pk=None):
        """ACD-007: CSV bulk score import. dry_run=true (default) returns a
        per-row diff without writing; dry_run=false commits via
        set_assessment_score, matching the JSON bulk endpoint's semantics."""
        assessment = self.get_object()
        payload = ScoreCsvImportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        csv_content = payload.validated_data['file'].read().decode('utf-8-sig')
        try:
            if payload.validated_data['dry_run']:
                preview = preview_bulk_score_import(assessment, csv_content)
                return Response({'dry_run': True, 'rows': preview})

            result = apply_bulk_score_import(
                assessment, csv_content, actor=request.user,
                reason=payload.validated_data.get('reason'),
            )
            return Response({'dry_run': False, **result})
        except ScoreCsvError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TimetableSlotViewSet(TenantScopedModelViewSet):
    model = TimetableSlot
    serializer_class = TimetableSlotSerializer
    filter_params = {'class_subject_id': 'class_subject_id', 'day_of_week': 'day_of_week'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'attendance.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }

    def retrieve(self, request, *args, **kwargs):
        slot = self.get_object()
        user_staff = Staff.objects.filter(user=request.user, foundation_id=slot.foundation_id).first()
        school_id = getattr(getattr(slot, 'class_group', None), 'school_id', None)
        from apps.identity.rbac import has_permission
        is_admin = has_permission(request.user, 'school_config.read', slot.foundation_id, school_id=school_id) or \
                   has_permission(request.user, 'school_config.write', slot.foundation_id, school_id=school_id)
        is_slot_teacher = user_staff and slot.class_subject and slot.class_subject.teacher_id == user_staff.id
        is_substitute = user_staff and TimetableSubstitution.objects.filter(
            foundation_id=slot.foundation_id,
            slot=slot,
            substitute_teacher=user_staff,
            deleted_at__isnull=True,
        ).exists()
        if not (is_admin or is_slot_teacher or is_substitute):
            return Response(
                {'error': _("Hanya guru pengampu, guru pengganti, atau admin yang dapat melihat rincian slot jadwal ini.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(self.get_serializer(slot).data, status=status.HTTP_200_OK)

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
        except (TimetableConflictError, PeriodGridMismatchError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(slot).data, status=status.HTTP_201_CREATED)


class TimetableSubstitutionViewSet(TenantScopedModelViewSet):
    model = TimetableSubstitution
    serializer_class = TimetableSubstitutionSerializer
    filter_params = {'slot_id': 'slot_id', 'date': 'date'}
    action_permissions = {
        'list': 'attendance.read', 'retrieve': 'attendance.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
        'accept': 'attendance.write',
        'decline': 'attendance.write',
        'slot': 'attendance.read',
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

    @action(detail=True, methods=['post'], url_path='accept')
    def accept(self, request, pk=None):
        substitution = self.get_object()
        user_staff = Staff.objects.filter(user=request.user, foundation_id=substitution.foundation_id).first()
        is_substitute = user_staff and user_staff.id == substitution.substitute_teacher_id
        school_id = getattr(getattr(substitution.slot, 'class_group', None), 'school_id', None)
        from apps.identity.rbac import has_permission
        is_admin = has_permission(request.user, 'school_config.write', substitution.foundation_id, school_id=school_id)
        if not (is_substitute or is_admin):
            return Response(
                {'error': _("Hanya guru pengganti terkait atau admin yang dapat merespons penugasan ini.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.academic.services import accept_substitution
        substitution = accept_substitution(substitution, actor=request.user)
        return Response(self.get_serializer(substitution).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='decline')
    def decline(self, request, pk=None):
        substitution = self.get_object()
        user_staff = Staff.objects.filter(user=request.user, foundation_id=substitution.foundation_id).first()
        is_substitute = user_staff and user_staff.id == substitution.substitute_teacher_id
        school_id = getattr(getattr(substitution.slot, 'class_group', None), 'school_id', None)
        from apps.identity.rbac import has_permission
        is_admin = has_permission(request.user, 'school_config.write', substitution.foundation_id, school_id=school_id)
        if not (is_substitute or is_admin):
            return Response(
                {'error': _("Hanya guru pengganti terkait atau admin yang dapat merespons penugasan ini.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        reason = request.data.get('reason', '')
        from apps.academic.services import SubstitutionDeclineReasonRequiredError, decline_substitution
        try:
            substitution = decline_substitution(substitution, reason=reason, actor=request.user)
        except (SubstitutionDeclineReasonRequiredError, ValueError) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(substitution).data, status=status.HTTP_200_OK)

    def _check_substitution_access(self, request, substitution):
        user_staff = Staff.objects.filter(user=request.user, foundation_id=substitution.foundation_id).first()
        is_involved = user_staff and (
            user_staff.id == substitution.substitute_teacher_id
            or user_staff.id == substitution.original_teacher_id
        )
        school_id = getattr(getattr(substitution.slot, 'class_group', None), 'school_id', None)
        from apps.identity.rbac import has_permission
        is_admin = has_permission(request.user, 'school_config.read', substitution.foundation_id, school_id=school_id) or \
                   has_permission(request.user, 'school_config.write', substitution.foundation_id, school_id=school_id)
        return is_involved or is_admin

    def retrieve(self, request, *args, **kwargs):
        substitution = self.get_object()
        if not self._check_substitution_access(request, substitution):
            return Response(
                {'error': _("Hanya guru pengganti terkait, guru utama, atau admin yang dapat melihat rincian penugasan ini.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(self.get_serializer(substitution).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='slot')
    def slot(self, request, pk=None):
        substitution = self.get_object()
        if not self._check_substitution_access(request, substitution):
            return Response(
                {'error': _("Hanya guru pengganti terkait, guru utama, atau admin yang dapat melihat rincian penugasan ini.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(substitution)
        return Response(serializer.data.get('slot_item', {}), status=status.HTTP_200_OK)


class HomeworkViewSet(TenantScopedModelViewSet):
    model = Homework
    serializer_class = HomeworkSerializer
    filter_params = {'class_subject_id': 'class_subject_id'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'submissions': 'grades.read',
        'remind': 'grades.write', 'completion': 'grades.read',
        'remind_status': 'grades.read',
        'upload_file': 'grades.write',
        'grading_queue': 'grades.read',
        'homework_grading_queue': 'grades.read',
    }

    @action(detail=False, methods=['get'], url_path='grading-queue')
    def grading_queue(self, request):
        """TCH-014: Aggregated cross-homework grading queue."""
        class_subject_id = request.query_params.get('class_subject_id')
        homework_id = request.query_params.get('homework_id')
        include_graded = request.query_params.get('include_graded', '').lower() in ('1', 'true')

        qs = HomeworkSubmission.objects.filter(deleted_at__isnull=True)
        if class_subject_id:
            qs = qs.filter(homework__class_subject_id=class_subject_id)
        if homework_id:
            qs = qs.filter(homework_id=homework_id)
        if not include_graded:
            qs = qs.filter(status__in=[HomeworkSubmissionStatus.SUBMITTED, HomeworkSubmissionStatus.LATE])

        qs = qs.select_related(
            'homework', 'homework__class_subject', 'homework__class_subject__subject',
            'homework__class_subject__class_group', 'student', 'student__person', 'graded_by'
        ).order_by('submitted_at')

        serializer = HomeworkGradingQueueItemSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='grading-queue')
    def homework_grading_queue(self, request, pk=None):
        """TCH-014: Grading queue for a specific homework assignment."""
        homework = self.get_object()
        include_graded = request.query_params.get('include_graded', '').lower() in ('1', 'true')

        qs = HomeworkSubmission.objects.filter(homework=homework, deleted_at__isnull=True)
        if not include_graded:
            qs = qs.filter(status__in=[HomeworkSubmissionStatus.SUBMITTED, HomeworkSubmissionStatus.LATE])

        qs = qs.select_related(
            'homework', 'homework__class_subject', 'homework__class_subject__subject',
            'homework__class_subject__class_group', 'student', 'student__person', 'graded_by'
        ).order_by('submitted_at')

        serializer = HomeworkGradingQueueItemSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='upload-file', parser_classes=[MultiPartParser])
    def upload_file(self, request, pk=None):
        homework = self.get_object()
        payload = HomeworkFileUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            file_meta = store_homework_submission_file(
                homework, payload.validated_data['file'], uploaded_by=str(request.user.pk),
            )
        except InvalidSubmissionFilesError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(file_meta, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        homework = assign_homework(
            class_subject=serializer.validated_data['class_subject'],
            title=serializer.validated_data['title'],
            instructions=serializer.validated_data.get('instructions', ''),
            assigned_at=serializer.validated_data['assigned_at'],
            due_at=serializer.validated_data['due_at'],
        )
        serializer.instance = homework

    @action(detail=True, methods=['get', 'post'], url_path='submissions')
    def submissions(self, request, pk=None):
        homework = self.get_object()

        if request.method == 'GET':
            subs = HomeworkSubmission.objects.filter(homework=homework, deleted_at__isnull=True).order_by('-submitted_at')
            return Response(HomeworkSubmissionSerializer(subs, many=True).data)

        student_id = request.data.get('student_id')
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = HomeworkSubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            submission = submit_homework(
                homework=homework,
                student=student,
                text=payload.validated_data.get('text', ''),
                files=payload.validated_data.get('files', []),
            )
        except (InvalidSubmissionFilesError, HomeworkSubmissionStateError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(HomeworkSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remind')
    def remind(self, request, pk=None):
        homework = self.get_object()
        try:
            result = remind_unsubmitted(homework)
        except ReminderRateLimitedError as e:
            return Response({'error': str(e)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(result)

    @action(detail=True, methods=['get'], url_path='completion')
    def completion(self, request, pk=None):
        homework = self.get_object()
        return Response(get_homework_completion(homework))

    @action(detail=True, methods=['get'], url_path='remind-status')
    def remind_status(self, request, pk=None):
        homework = self.get_object()
        return Response(get_homework_remind_status(homework))


class HomeworkSubmissionViewSet(TenantScopedModelViewSet):
    model = HomeworkSubmission
    serializer_class = HomeworkSubmissionSerializer
    filter_params = {'homework_id': 'homework_id', 'student_id': 'student_id', 'status': 'status'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'grade': 'grades.write', 'return_submission': 'grades.write',
    }

    @action(detail=True, methods=['post'], url_path='grade')
    def grade(self, request, pk=None):
        submission = self.get_object()
        payload = HomeworkGradeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        graded = grade_homework_submission(
            submission,
            score=payload.validated_data.get('score'),
            feedback=payload.validated_data.get('feedback', ''),
            actor=request.user,
        )
        return Response(self.get_serializer(graded).data)

    @action(detail=True, methods=['post'], url_path='return')
    def return_submission(self, request, pk=None):
        """ACD-028: return a submission to the student for revision."""
        submission = self.get_object()
        payload = HomeworkReturnSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            returned = return_homework_submission(
                submission, feedback=payload.validated_data['feedback'], actor=request.user,
            )
        except (HomeworkSubmissionStateError, ReasonRequiredError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(returned).data)


class ExamViewSet(TenantScopedModelViewSet):
    model = Exam
    serializer_class = ExamSerializer
    filter_params = {'class_subject_id': 'class_subject_id'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'publish': 'grades.write', 'attempts': 'grades.read', 'grading_queue': 'grades.read',
        'proctor': 'grades.read',
    }

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        exam = self.get_object()
        exam.published = True
        exam.save(update_fields=['published', 'updated_at'])
        return Response(self.get_serializer(exam).data)

    @action(detail=True, methods=['post'], url_path='attempts')
    def attempts(self, request, pk=None):
        exam = self.get_object()
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=request.data.get('student_id'), foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            attempt = start_attempt(exam, student)
        except ExamWindowError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        attempt = auto_submit_if_expired(attempt)
        questions_by_id = {q.id: q for q in exam.questions.filter(deleted_at__isnull=True)}
        ordered_questions = [questions_by_id[qid] for qid in attempt.question_order if qid in questions_by_id]

        return Response({
            'attempt': ExamAttemptSerializer(attempt).data,
            'questions': ExamQuestionPublicSerializer(ordered_questions, many=True).data,
            'remaining_seconds': compute_remaining_seconds(attempt),
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='grading-queue')
    def grading_queue(self, request, pk=None):
        exam = self.get_object()
        pending = ExamAnswer.objects.filter(
            attempt__exam=exam,
            question__type=ExamQuestionType.ESSAY,
            points_awarded__isnull=True,
            deleted_at__isnull=True,
        ).select_related('attempt', 'question')
        return Response(ExamAnswerSerializer(pending, many=True).data)

    @action(detail=True, methods=['get'], url_path='proctor')
    def proctor(self, request, pk=None):
        exam = self.get_object()
        now = timezone.now()

        # Questions
        questions = exam.questions.filter(deleted_at__isnull=True)
        total_questions = questions.count()

        # Roster: Active enrollments in exam's class group
        enrollments = ClassEnrollment.objects.filter(
            class_group=exam.class_subject.class_group,
            is_active=True,
            deleted_at__isnull=True,
        ).select_related('student', 'student__person')

        enrolled_students = {e.student_id: e.student for e in enrollments}

        # Attempts for this exam
        attempts = ExamAttempt.objects.filter(
            exam=exam,
            deleted_at__isnull=True,
        ).select_related('student', 'student__person').prefetch_related('answers')

        attempts_by_student = {att.student_id: att for att in attempts}

        # Combine enrolled students and any students who attempted
        all_student_ids = set(enrolled_students.keys()) | set(attempts_by_student.keys())
        all_students = []
        for sid in all_student_ids:
            student = enrolled_students.get(sid) or (attempts_by_student[sid].student if sid in attempts_by_student else None)
            if student:
                all_students.append(student)

        # Sort students by full_name, nis
        all_students.sort(key=lambda s: (getattr(getattr(s, 'person', None), 'full_name', '') or '', s.nis))

        student_rows = []
        in_progress_count = 0
        submitted_count = 0
        not_started_count = 0
        flagged_count = 0

        for student in all_students:
            attempt = attempts_by_student.get(student.id)
            if attempt:
                attempt = auto_submit_if_expired(attempt)
                status_val = attempt.status
                active_answers = [a for a in attempt.answers.all() if a.deleted_at is None]
                answered_count = len(active_answers)

                if active_answers:
                    latest_ans = max(active_answers, key=lambda a: a.answered_at)
                    last_saved_at = latest_ans.answered_at
                else:
                    last_saved_at = attempt.started_at

                if status_val == ExamAttemptStatus.IN_PROGRESS:
                    rem_sec = compute_remaining_seconds(attempt)
                    in_progress_count += 1
                else:
                    rem_sec = 0
                    submitted_count += 1

                focus_losses = attempt.focus_loss_count
                attempt_id = attempt.id
            else:
                status_val = 'NOT_STARTED'
                answered_count = 0
                last_saved_at = None
                rem_sec = None
                focus_losses = 0
                attempt_id = None
                not_started_count += 1

            progress_pct = int(round((answered_count / total_questions) * 100)) if total_questions > 0 else 0
            needs_attention = (focus_losses >= 3)
            if needs_attention:
                flagged_count += 1

            student_name = student.person.full_name if hasattr(student, 'person') and student.person else student.nis

            student_rows.append({
                'student_id': student.id,
                'student_name': student_name,
                'nis': student.nis,
                'attempt_id': attempt_id,
                'status': status_val,
                'answered_count': answered_count,
                'total_questions': total_questions,
                'progress_pct': progress_pct,
                'focus_loss_count': focus_losses,
                'last_saved_at': last_saved_at,
                'remaining_seconds': rem_sec,
                'needs_attention': needs_attention,
            })

        is_active = (exam.window_start <= now <= exam.window_end)

        return Response({
            'exam': {
                'id': exam.id,
                'title': exam.title,
                'duration_min': exam.duration_min,
                'window_start': exam.window_start,
                'window_end': exam.window_end,
                'total_questions': total_questions,
                'is_active': is_active,
            },
            'summary': {
                'total_students': len(student_rows),
                'in_progress': in_progress_count,
                'submitted': submitted_count,
                'not_started': not_started_count,
                'flagged_focus_loss': flagged_count,
            },
            'students': student_rows,
        })


class ExamQuestionViewSet(TenantScopedModelViewSet):
    model = ExamQuestion
    serializer_class = ExamQuestionSerializer
    filter_params = {'exam_id': 'exam_id'}
    action_permissions = {
        'list': 'grades.write', 'retrieve': 'grades.write',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
    }


class ExamAttemptViewSet(IdempotentViewMixin, TenantScopedModelViewSet):
    model = ExamAttempt
    serializer_class = ExamAttemptSerializer
    filter_params = {'exam_id': 'exam_id', 'student_id': 'student_id', 'status': 'status'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'answers': 'grades.read', 'submit': 'grades.read',
        'focus_loss': 'grades.read', 'grade_answer': 'grades.write',
    }

    @action(detail=True, methods=['patch', 'post'], url_path='answers')
    def answers(self, request, pk=None):
        attempt = auto_submit_if_expired(self.get_object())
        payload = SaveAnswerSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        question = ExamQuestion.objects.filter(
            id=payload.validated_data['question_id'], exam=attempt.exam, foundation_id=attempt.foundation_id,
        ).first()
        if not question:
            return Response({'error': _("Soal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            record = save_answer(attempt, question, payload.validated_data['answer'])
        except AttemptAlreadySubmittedError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'answer': ExamAnswerSerializer(record).data,
            'remaining_seconds': compute_remaining_seconds(attempt),
        })

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        attempt = auto_submit_if_expired(self.get_object())
        attempt = submit_attempt(attempt, auto=False)
        return Response(self.get_serializer(attempt).data)

    @action(detail=True, methods=['post'], url_path='focus-loss')
    def focus_loss(self, request, pk=None):
        attempt = record_focus_loss(self.get_object())
        return Response(self.get_serializer(attempt).data)

    @action(detail=True, methods=['post'], url_path='grade-answer')
    def grade_answer(self, request, pk=None):
        attempt = self.get_object()
        question_id = request.data.get('question_id')
        exam_answer = ExamAnswer.objects.filter(attempt=attempt, question_id=question_id, foundation_id=attempt.foundation_id).first()
        if not exam_answer:
            return Response({'error': _("Jawaban tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        grade_payload = GradeEssaySerializer(data=request.data)
        grade_payload.is_valid(raise_exception=True)
        graded = grade_essay_answer(exam_answer, grade_payload.validated_data['points'], actor=request.user)
        return Response(ExamAnswerSerializer(graded).data)


class ReportCardViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read + state-transition endpoints. Content is generated via `generate`,
    edited via `content`, not raw create/update."""
    serializer_class = ReportCardSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'generate': 'grades.write', 'approve': 'school_config.write',
        'publish': 'school_config.write', 'revise': 'grades.write',
        'content': 'grades.write', 'suggest_narratives': 'grades.read',
        'download': 'grades.read',
    }

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return ReportCard.objects.none()
        qs = ReportCard.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        for param, field in {
            'student_id': 'student_id', 'term_id': 'term_id', 'class_group_id': 'class_group_id',
        }.items():
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        if self.request.query_params.get('include_superseded') != 'true':
            qs = qs.filter(is_current=True)
        return qs

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        foundation_id = get_current_foundation_id()
        payload = ReportCardGenerateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        class_group = ClassGroup.objects.filter(id=payload.validated_data['class_group_id'], foundation_id=foundation_id).first()
        term = Term.objects.filter(id=payload.validated_data['term_id'], foundation_id=foundation_id).first()
        if not class_group or not term:
            return Response({'error': _("Kelas atau semester tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        report = generate_report_cards(class_group, term, triggered_by=request.user)
        return Response(report)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        report_card = self.get_object()
        try:
            approved = approve_report_card(report_card, actor=request.user)
        except ReportCardStateError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(approved).data)

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        report_card = self.get_object()
        try:
            published = publish_report_card(report_card, actor=request.user)
        except ReportCardStateError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(published).data)

    @action(detail=True, methods=['post'], url_path='revise')
    def revise(self, request, pk=None):
        report_card = self.get_object()
        try:
            revised = revise_report_card(report_card, actor=request.user)
        except ReportCardStateError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(revised).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch'], url_path='content')
    def content(self, request, pk=None):
        """ACD-011: edit narrative, extracurricular notes, per-subject objective
        narrative, and promotion decision — only while DRAFT/PENDING_REVIEW."""
        report_card = self.get_object()
        payload = ReportCardContentUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            updated = set_report_card_content(
                report_card,
                narrative=payload.validated_data.get('narrative'),
                extracurricular_notes=payload.validated_data.get('extracurricular_notes'),
                promotion_decision=payload.validated_data.get('promotion_decision'),
                subject_narratives=payload.validated_data.get('subject_narratives'),
                actor=request.user,
            )
        except ReportCardStateError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """Signed-GET download URL for the published report card's PDF."""
        report_card = self.get_object()
        if not report_card.pdf_key:
            return Response({'error': _("Belum ada dokumen rapor untuk diunduh.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(build_signed_download(report_card.pdf_key))

    @action(detail=True, methods=['get'], url_path='suggest-narratives')
    def suggest_narratives(self, request, pk=None):
        """ACD-015: draft per-subject objective narrative suggestions from each
        subject's grade/descriptor. Read-only — nothing is saved here; the
        teacher reviews and submits their edits via `content`."""
        report_card = self.get_object()
        suggestions = [
            {
                'subject_code': g.get('subject_code'),
                'subject': g.get('subject'),
                'grade': g.get('grade'),
                'suggestion': suggest_objective_narrative(g.get('subject', ''), g.get('grade')),
            }
            for g in report_card.grades_snapshot
        ]
        return Response({'suggestions': suggestions})


class ReportCardPolicyView(APIView):
    """GET/PATCH a school's rapor arrears-gate policy (ACD-014)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'school_config.write' if self.request.method == 'PATCH' else 'school_config.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        policy = get_or_create_report_card_policy(school)
        return Response(ReportCardPolicySerializer(policy).data)

    def patch(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        enabled = request.data.get('block_rapor_on_arrears')
        policy = set_arrears_gate(school, bool(enabled), actor=request.user)
        return Response(ReportCardPolicySerializer(policy).data)


class StudentReportCardView(APIView):
    """GET /students/:id/report-cards?term_id -> the visible (PUBLISHED, non-arrears-blocked) card (ACD-013/014)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        term_id = request.query_params.get('term_id')
        if not term_id:
            cards = ReportCard.objects.filter(
                student=student, is_current=True, foundation_id=foundation_id,
            ).select_related('term', 'term__academic_year').order_by('-term__start_date')
            results = []
            for rc in cards:
                visibility = get_visible_report_card(rc)
                if not visibility['visible']:
                    results.append({
                        'id': rc.id,
                        'term_id': rc.term_id,
                        'term_name': rc.term.name if rc.term else '',
                        'academic_year_name': rc.term.academic_year.name if rc.term and rc.term.academic_year else '',
                        'status': rc.status,
                        'visible': False,
                        'reason': visibility['reason'],
                        'download_url': None,
                    })
                else:
                    data = ReportCardSerializer(rc).data
                    download_url = None
                    if rc.pdf_key:
                        try:
                            dl_info = build_signed_download(rc.pdf_key)
                            u = dl_info.get('download_url')
                            if isinstance(u, str):
                                download_url = u
                        except Exception:
                            pass
                    data['download_url'] = download_url
                    data['visible'] = True
                    data['term_name'] = rc.term.name if rc.term else ''
                    data['academic_year_name'] = rc.term.academic_year.name if rc.term and rc.term.academic_year else ''
                    results.append(data)
            return Response({'results': results})

        report_card = ReportCard.objects.filter(
            student=student, term_id=term_id, is_current=True, foundation_id=foundation_id,
        ).first()
        if not report_card:
            return Response({'error': _("Rapor tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        visibility = get_visible_report_card(report_card)
        if not visibility['visible']:
            return Response({'visible': False, 'reason': visibility['reason']}, status=status.HTTP_403_FORBIDDEN)

        data = ReportCardSerializer(report_card).data
        if report_card.pdf_key:
            try:
                dl_info = build_signed_download(report_card.pdf_key)
                u = dl_info.get('download_url')
                if isinstance(u, str):
                    data['download_url'] = u
            except Exception:
                pass
        return Response({'visible': True, 'report_card': data})


class LessonPlanViewSet(TenantScopedModelViewSet):
    model = LessonPlan
    serializer_class = LessonPlanSerializer
    filter_params = {'class_subject_id': 'class_subject_id', 'week_start_date': 'week_start_date'}
    action_permissions = {
        'list': 'grades.read', 'retrieve': 'grades.read',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
        'duplicate': 'grades.write',
    }

    def perform_create(self, serializer):
        serializer.save(foundation_id=get_current_foundation_id(), created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate(self, request, pk=None):
        lesson_plan = self.get_object()
        payload = LessonPlanDuplicateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        duplicated = duplicate_lesson_plan(
            lesson_plan, payload.validated_data['target_week_start_date'], actor=request.user,
        )
        return Response(self.get_serializer(duplicated).data, status=status.HTTP_201_CREATED)


class BroadcastViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """POST creates+sends; content is immutable once sent (spec/09 TCH-011)."""
    serializer_class = BroadcastSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    action_permissions = {'list': 'grades.read', 'create': 'grades.write'}

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return Broadcast.objects.none()
        qs = Broadcast.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-sent_at')
        class_group_id = self.request.query_params.get('class_group_id')
        if class_group_id:
            qs = qs.filter(class_group_id=class_group_id)
        return qs

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        teacher = Staff.objects.filter(user=request.user, foundation_id=foundation_id).first()
        if not teacher:
            return Response({'error': _("Akun ini tidak terhubung ke profil staf.")}, status=status.HTTP_404_NOT_FOUND)

        payload = BroadcastCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        class_group = ClassGroup.objects.filter(id=payload.validated_data['class_group_id'], foundation_id=foundation_id).first()
        if not class_group:
            return Response({'error': _("Kelas tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            broadcast = send_broadcast(
                teacher, class_group,
                payload.validated_data['title'], payload.validated_data['body'],
                actor=request.user,
            )
        except (BroadcastNotAllowedError, BroadcastRateLimitedError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(BroadcastSerializer(broadcast).data, status=status.HTTP_201_CREATED)


class BroadcastPolicyView(APIView):
    """GET/PATCH a school's teacher-broadcast policy (TCH-011)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'school_config.write' if self.request.method == 'PATCH' else 'school_config.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        policy = get_or_create_broadcast_policy(school)
        return Response(BroadcastPolicySerializer(policy).data)

    def patch(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        enabled = request.data.get('teacher_can_broadcast')
        policy = set_broadcast_policy(school, bool(enabled), actor=request.user)
        return Response(BroadcastPolicySerializer(policy).data)


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
                    'version': score_map[(a.id, s.id)].version if (a.id, s.id) in score_map else 0,
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

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
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


class PeriodGridView(APIView):
    """GET/PUT /schools/:school_id/period-grid/?day_of_week= (ACD-018)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'school_config.write' if self.request.method == 'PUT' else 'school_config.read'

    def _get_day_of_week(self, request):
        raw = request.query_params.get('day_of_week') or request.data.get('day_of_week')
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        day_of_week = self._get_day_of_week(request)
        if day_of_week is None:
            return Response({'error': _("Parameter day_of_week wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        grid = get_period_grid(school, day_of_week)
        return Response({'day_of_week': day_of_week, 'periods': PeriodGridSlotSerializer(grid, many=True).data})

    def put(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        day_of_week = self._get_day_of_week(request)
        if day_of_week is None:
            return Response({'error': _("Parameter day_of_week wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        payload = PeriodGridSetSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        grid = set_period_grid(school, day_of_week, payload.validated_data['periods'])
        return Response({'day_of_week': day_of_week, 'periods': PeriodGridSlotSerializer(grid, many=True).data})


class ExpectedPeriodsView(APIView):
    """GET /schools/:school_id/expected-periods/?date=&missing_only= (ACD-020 school-level rollup)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.read'

    def get(self, request, school_id):
        foundation_id = get_current_foundation_id()
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        date_param = request.query_params.get('date')
        if not date_param:
            return Response({'error': _("Parameter date wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        import datetime as _dt
        try:
            date = _dt.date.fromisoformat(date_param)
        except ValueError:
            return Response({'error': _("Format date tidak valid (YYYY-MM-DD).")}, status=status.HTTP_400_BAD_REQUEST)

        missing_only = request.query_params.get('missing_only', '').lower() in ('1', 'true', 'yes')
        periods = get_expected_periods_for_school(school, date, missing_only=missing_only)
        return Response({'date': date_param, 'periods': periods})


class StudentGradesView(APIView):
    """GET /api/v1/academic/students/:student_id/grades/ -> published assessment scores and attainment (PAR-010)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        enrollments = ClassEnrollment.objects.filter(
            student=student,
            is_active=True,
            foundation_id=foundation_id,
        ).select_related('class_group', 'class_group__academic_year')

        class_group_ids = [e.class_group_id for e in enrollments]
        class_subjects = ClassSubject.objects.filter(
            class_group_id__in=class_group_ids,
            foundation_id=foundation_id,
        ).select_related('subject', 'teacher__person', 'term', 'class_group').order_by('subject__name')

        subjects_data = []
        for cs in class_subjects:
            # PAR-010: strictly published=True only
            assessments = Assessment.objects.filter(
                class_subject=cs,
                published=True,
                foundation_id=foundation_id,
            ).order_by('-due_at', 'id')

            scores = AssessmentScore.objects.filter(
                assessment__in=assessments,
                student=student,
                foundation_id=foundation_id,
            )
            score_map = {s.assessment_id: s for s in scores}

            term_grade = None
            try:
                term_grade = compute_term_grade(student, cs)
            except Exception:
                pass

            subjects_data.append({
                'class_subject_id': cs.id,
                'class_group_id': cs.class_group_id,
                'class_group_name': cs.class_group.name,
                'subject_id': cs.subject_id,
                'subject_name': cs.subject.name,
                'subject_code': cs.subject.code,
                'teacher_name': cs.teacher.person.full_name if cs.teacher and cs.teacher.person else '',
                'term_id': cs.term_id,
                'term_name': cs.term.name if cs.term else '',
                'final_grade': str(term_grade['final_score']) if term_grade and term_grade.get('final_score') is not None else None,
                'final_descriptor': term_grade.get('descriptor', '') if term_grade else '',
                'is_complete': term_grade.get('is_complete', False) if term_grade else False,
                'assessments': [
                    {
                        'id': a.id,
                        'title': a.title,
                        'type': a.type,
                        'type_display': a.get_type_display(),
                        'max_score': str(a.max_score),
                        'weight': str(a.weight),
                        'due_at': a.due_at.isoformat() if a.due_at else None,
                        'score': str(score_map[a.id].score) if a.id in score_map and score_map[a.id].score is not None else None,
                        'descriptor': score_map[a.id].descriptor if a.id in score_map else '',
                        'feedback': score_map[a.id].feedback if a.id in score_map else '',
                        'graded_at': score_map[a.id].graded_at.isoformat() if a.id in score_map and score_map[a.id].graded_at else None,
                    }
                    for a in assessments
                ],
            })

        return Response({
            'student_id': student.id,
            'subjects': subjects_data,
        })


class StudentHomeworkView(APIView):
    """GET /api/v1/academic/students/:student_id/homework/ -> homework & submission status for student (spec/08)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        enrollments = ClassEnrollment.objects.filter(
            student=student,
            is_active=True,
            foundation_id=foundation_id,
        ).values_list('class_group_id', flat=True)

        homework_qs = Homework.objects.filter(
            class_subject__class_group_id__in=enrollments,
            foundation_id=foundation_id,
        ).select_related(
            'class_subject__subject',
            'class_subject__teacher__person',
            'class_subject__class_group',
        ).order_by('-due_at')

        submissions = HomeworkSubmission.objects.filter(
            homework__in=homework_qs,
            student=student,
            foundation_id=foundation_id,
        )
        sub_map = {s.homework_id: s for s in submissions}

        results = []
        for hw in homework_qs:
            sub = sub_map.get(hw.id)
            results.append({
                'id': hw.id,
                'title': hw.title,
                'instructions': hw.instructions,
                'subject_name': hw.class_subject.subject.name,
                'subject_code': hw.class_subject.subject.code,
                'teacher_name': hw.class_subject.teacher.person.full_name if hw.class_subject.teacher and hw.class_subject.teacher.person else '',
                'class_group_name': hw.class_subject.class_group.name,
                'assigned_at': hw.assigned_at.isoformat() if hw.assigned_at else None,
                'due_at': hw.due_at.isoformat() if hw.due_at else None,
                'submission_status': sub.status if sub else 'NOT_STARTED',
                'submitted_at': sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
                'score': str(sub.score) if sub and sub.score is not None else None,
                'feedback': sub.feedback if sub else '',
                'files_count': len(sub.files) if sub and isinstance(sub.files, list) else 0,
            })

        return Response({'results': results})


class StudentTimetableView(APIView):
    """GET /api/v1/academic/students/:student_id/timetable/?date= -> weekly timetable for student (spec/08)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        enrollments = ClassEnrollment.objects.filter(
            student=student,
            is_active=True,
            foundation_id=foundation_id,
        ).values_list('class_group_id', flat=True)

        slots = TimetableSlot.objects.filter(
            class_subject__class_group_id__in=enrollments,
            foundation_id=foundation_id,
        ).select_related(
            'class_subject__subject',
            'class_subject__teacher__person',
            'class_subject__class_group',
        ).order_by('day_of_week', 'period_no', 'start_time')

        import datetime as _dt
        date_param = request.query_params.get('date')
        target_date = _dt.date.today()
        if date_param:
            try:
                target_date = _dt.date.fromisoformat(date_param)
            except ValueError:
                pass

        subs = TimetableSubstitution.objects.filter(
            slot__in=slots,
            date=target_date,
            status=SubstitutionStatus.ACCEPTED,
            foundation_id=foundation_id,
        ).select_related('substitute_teacher__person')
        sub_map = {s.slot_id: s for s in subs}

        results = []
        for slot in slots:
            sub = sub_map.get(slot.id)
            results.append({
                'id': slot.id,
                'day_of_week': slot.day_of_week,
                'day_name': slot.get_day_of_week_display(),
                'period_no': slot.period_no,
                'start_time': slot.start_time.strftime('%H:%M'),
                'end_time': slot.end_time.strftime('%H:%M'),
                'room': slot.room,
                'subject_name': slot.class_subject.subject.name,
                'subject_code': slot.class_subject.subject.code,
                'teacher_name': slot.class_subject.teacher.person.full_name if slot.class_subject.teacher and slot.class_subject.teacher.person else '',
                'class_group_name': slot.class_subject.class_group.name,
                'is_substituted': bool(sub),
                'substitute_teacher_name': sub.substitute_teacher.person.full_name if sub and sub.substitute_teacher and sub.substitute_teacher.person else None,
            })

        return Response({
            'date': target_date.isoformat(),
            'slots': results,
        })


def _serialize_permission_slip(slip, tally=None):
    """Compact JSON shape shared by staff and parent endpoints (PAR-012)."""
    data = {
        'id': slip.id,
        'title': slip.title,
        'description': slip.description,
        'event_date': slip.event_date.isoformat() if slip.event_date else None,
        'location': slip.location,
        'due_at': slip.due_at.isoformat() if slip.due_at else None,
        'is_closed': bool(slip.due_at and timezone.now() > slip.due_at),
        'class_group_id': slip.class_group_id,
        'class_group_name': slip.class_group.name,
        'school_id': slip.class_group.school_id,
        'created_by_name': slip.created_by.person.full_name if slip.created_by and slip.created_by.person else '',
        'created_at': slip.created_at.isoformat() if slip.created_at else None,
    }
    if tally is not None:
        data['tally'] = {
            'total_enrolled': tally['total_enrolled'],
            'approved': tally['approved'],
            'declined': tally['declined'],
            'pending': tally['pending'],
        }
    return data


class TeacherPermissionSlipView(APIView):
    """GET (list) + POST (create) /api/v1/academic/teacher/permission-slips/
    School-side permission slips with live consent tally (spec/08 PAR-012)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'grades.write' if self.request.method == 'POST' else 'grades.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        qs = PermissionSlip.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group', 'created_by__person').order_by('-created_at')

        class_group_id = request.query_params.get('class_group_id')
        if class_group_id:
            qs = qs.filter(class_group_id=class_group_id)

        results = []
        for slip in qs[:100]:
            tally = get_slip_consent_tally(slip)
            results.append(_serialize_permission_slip(slip, tally))
        return Response({'results': results})

    def post(self, request):
        foundation_id = get_current_foundation_id()
        staff = Staff.objects.filter(user=request.user, foundation_id=foundation_id).first()
        if not staff:
            return Response({'error': _("Akun ini tidak terhubung ke profil staf.")}, status=status.HTTP_404_NOT_FOUND)

        class_group_id = request.data.get('class_group_id')
        class_group = ClassGroup.objects.filter(id=class_group_id, foundation_id=foundation_id).first()
        if not class_group:
            return Response({'error': _("Kelas tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'error': _("Judul izin wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        description = request.data.get('description') or ''
        location = request.data.get('location') or ''

        import datetime as _dt
        event_date = None
        if request.data.get('event_date'):
            try:
                event_date = _dt.date.fromisoformat(request.data['event_date'])
            except ValueError:
                return Response({'error': _("Format tanggal acara tidak valid (YYYY-MM-DD).")}, status=status.HTTP_400_BAD_REQUEST)

        due_at = None
        if request.data.get('due_at'):
            parsed = parse_datetime(request.data['due_at'])
            if parsed is None:
                return Response({'error': _("Format batas waktu tidak valid (ISO 8601).")}, status=status.HTTP_400_BAD_REQUEST)
            due_at = parsed
            if timezone.is_naive(due_at):
                due_at = timezone.make_aware(due_at)

        try:
            slip = create_permission_slip(
                staff, class_group, title,
                description=description, event_date=event_date,
                location=location, due_at=due_at, actor=request.user,
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_permission_slip(slip, get_slip_consent_tally(slip)), status=status.HTTP_201_CREATED)


class TeacherPermissionSlipTallyView(APIView):
    """GET /api/v1/academic/teacher/permission-slips/:id/consent-tally/
    Live per-student consent roster + roll-up (PAR-012: school sees a live consent tally)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, slip_id):
        foundation_id = get_current_foundation_id()
        slip = PermissionSlip.objects.filter(
            id=slip_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group').first()
        if not slip:
            return Response({'error': _("Izin tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        tally = get_slip_consent_tally(slip)

        from apps.identity.guardian_access import can_guardian_access_student  # noqa: F401 (staff path below)
        enrollments = ClassEnrollment.objects.filter(
            class_group_id=slip.class_group_id, is_active=True,
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person')

        acks = list(PermissionSlipAcknowledgement.objects.filter(
            permission_slip=slip, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('guardian__person').order_by('responded_at', 'id'))
        latest_by_pair = {}
        for ack in acks:
            latest_by_pair[(ack.student_id, ack.guardian_id)] = ack

        roster = []
        for enrollment in enrollments:
            student = enrollment.student
            student_acks = [a for (sid, _gid), a in latest_by_pair.items() if sid == student.id]
            effective = max(student_acks, key=lambda a: (a.responded_at, a.id)) if student_acks else None
            roster.append({
                'student_id': student.id,
                'student_name': student.person.full_name if student.person else '',
                'nis': student.nis,
                'response': effective.response if effective else 'PENDING',
                'responded_at': effective.responded_at.isoformat() if effective else None,
                'signature': effective.signature if effective else None,
            })

        return Response({
            'permission_slip': _serialize_permission_slip(slip),
            'tally': {
                'total_enrolled': tally['total_enrolled'],
                'approved': tally['approved'],
                'declined': tally['declined'],
                'pending': tally['pending'],
            },
            'roster': roster,
        })


class StudentPermissionSlipView(APIView):
    """GET /api/v1/academic/students/:student_id/permission-slips/
    Parent list of the child's class permission slips with the guardian's own
    effective response (spec/08 PAR-012, IAM-014 guardian scoping)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.models import Guardian, GuardianLink
        enrollments = ClassEnrollment.objects.filter(
            student=student, is_active=True, foundation_id=foundation_id, deleted_at__isnull=True,
        ).values_list('class_group_id', flat=True)

        slips = PermissionSlip.objects.filter(
            class_group_id__in=enrollments, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group', 'created_by__person').order_by('-created_at')

        guardian = Guardian.objects.filter(user=request.user, foundation_id=foundation_id).first()

        results = []
        for slip in slips:
            tally = get_slip_consent_tally(slip)
            item = _serialize_permission_slip(slip, tally)
            my_response = None
            my_responded_at = None
            if guardian:
                acks = list(PermissionSlipAcknowledgement.objects.filter(
                    permission_slip=slip, student=student, guardian=guardian,
                    foundation_id=foundation_id, deleted_at__isnull=True,
                ).order_by('responded_at', 'id'))
                if acks:
                    effective = acks[-1]
                    my_response = effective.response
                    my_responded_at = effective.responded_at.isoformat()
            item['my_response'] = my_response
            item['my_responded_at'] = my_responded_at
            item['my_pending'] = guardian is not None and my_response is None and not item['is_closed']
            results.append(item)

        return Response({'results': results})


class StudentBroadcastView(APIView):
    """GET /api/v1/academic/students/:student_id/broadcasts/
    Guardian list of school announcements for the child's class groups
    (spec/08 §2 Messages tab, IAM-014 guardian scoping)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        class_group_ids = ClassEnrollment.objects.filter(
            student=student, is_active=True, foundation_id=foundation_id, deleted_at__isnull=True,
        ).values_list('class_group_id', flat=True)

        broadcasts = Broadcast.objects.filter(
            class_group_id__in=class_group_ids, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('sender__person', 'class_group').order_by('-sent_at')

        from apps.academic.serializers import GuardianBroadcastSerializer
        serializer = GuardianBroadcastSerializer(broadcasts, many=True)
        return Response({'results': serializer.data})


class PermissionSlipAcknowledgeView(APIView):
    """POST /api/v1/academic/permission-slips/:id/acknowledge/
    Guardian signs a digital acknowledgement with timestamp (PAR-012).
    Body: {student_id, response: APPROVED|DECLINED, signature: "<typed full name>"}."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'grades.read'

    def post(self, request, slip_id):
        foundation_id = get_current_foundation_id()
        slip = PermissionSlip.objects.filter(
            id=slip_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group').first()
        if not slip:
            return Response({'error': _("Izin tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from apps.identity.models import Guardian
        guardian = Guardian.objects.filter(user=request.user, foundation_id=foundation_id).first()
        if not guardian:
            return Response({'error': _("Akun ini tidak terhubung ke profil wali murid.")}, status=status.HTTP_404_NOT_FOUND)

        student_id = request.data.get('student_id')
        student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        response = (request.data.get('response') or '').strip().upper()
        signature = (request.data.get('signature') or '').strip()

        try:
            ack = acknowledge_permission_slip(slip, guardian, student, response, signature, actor=request.user)
        except GuardianNotLinkedError as e:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        except StudentNotEnrolledError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except PermissionSlipClosedError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'id': ack.id,
            'permission_slip_id': slip.id,
            'student_id': student.id,
            'response': ack.response,
            'responded_at': ack.responded_at.isoformat(),
            'signature': ack.signature,
        }, status=status.HTTP_201_CREATED)


# ── Permission Slip School Console (web/HTMX, spec/08 PAR-012, spec/17) ──────
# Session-authenticated page + HTML-fragment routes under /web/academic/.
# Distinct from the /api/v1/ JSON endpoints above; both share the same
# services (create_permission_slip / get_slip_consent_tally) and permission keys.

CONSOLE_SLIP_PAGE_SIZE = 50


class PermissionSlipWebAccessMixin:
    """Shared gating for the HTMX console views: authenticated staff only.

    Uses DRF's permission pipeline (SessionAuthentication is a default auth
    class) plus the same grades.read/grades.write keys the JSON endpoints use,
    then additionally requires a Staff profile — a guardian must never reach
    the school-side console even if a role mistake ever grants them grades.*.
    """

    permission_classes = [HasRequiredPermission]

    def _resolve_staff(self, request):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return None
        return Staff.objects.filter(
            user=request.user, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('person', 'school').first()


class PermissionSlipConsolePageView(PermissionSlipWebAccessMixin, APIView):
    """GET /web/academic/permission-slips/ — full console page (staff only)."""

    def get_required_permission(self):
        return 'grades.read'

    def get(self, request):
        from django.shortcuts import render

        staff = self._resolve_staff(request)
        if staff is None:
            return Response({'error': _("Akun ini tidak terhubung ke profil staf.")}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        class_groups = ClassGroup.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('school').order_by('school__name', 'name')

        slips_qs = PermissionSlip.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group', 'created_by__person').order_by('-created_at')[:CONSOLE_SLIP_PAGE_SIZE]

        slips = [_serialize_permission_slip(slip, get_slip_consent_tally(slip)) for slip in slips_qs]

        return render(request, 'pages/permission_slip_console_page.html', {
            'staff': staff,
            'class_groups': class_groups,
            'slips': slips,
            'create_url': '/web/academic/permission-slips/create/',
            'tally_url_prefix': '/web/academic/permission-slips',
            'form_error': None,
        })


class PermissionSlipConsoleCreateView(PermissionSlipWebAccessMixin, APIView):
    """POST /web/academic/permission-slips/create/ — HTMX form target.

    Creates the slip via the shared service and returns the refreshed
    _permission_slip_list fragment (or the form error partial on 400)."""

    def get_required_permission(self):
        return 'grades.write'

    def _error_response(self, request, message, status_code):
        if request.htmx:
            html = render_to_string('components/_slip_form_error.html', {'form_error': message}, request=request)
            return HttpResponse(html, status=status_code)
        return Response({'error': message}, status=status_code)

    def post(self, request):
        staff = self._resolve_staff(request)
        if staff is None:
            return self._error_response(request, _("Akun ini tidak terhubung ke profil staf."), 404)

        foundation_id = get_current_foundation_id()
        class_group = ClassGroup.objects.filter(
            id=request.data.get('class_group_id'), foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()
        if not class_group:
            return self._error_response(request, _("Kelas tidak ditemukan."), 400)

        title = (request.data.get('title') or '').strip()
        if not title:
            return self._error_response(request, _("Judul izin wajib diisi."), 400)

        import datetime as _dt
        event_date = None
        if request.data.get('event_date'):
            try:
                event_date = _dt.date.fromisoformat(request.data['event_date'])
            except ValueError:
                return self._error_response(request, _("Format tanggal acara tidak valid (YYYY-MM-DD)."), 400)

        due_at = None
        if request.data.get('due_at'):
            parsed = parse_datetime(request.data['due_at'])
            if parsed is None:
                return self._error_response(request, _("Format batas waktu tidak valid."), 400)
            due_at = parsed
            if timezone.is_naive(due_at):
                due_at = timezone.make_aware(due_at)

        try:
            create_permission_slip(
                staff, class_group, title,
                description=request.data.get('description') or '',
                event_date=event_date,
                location=request.data.get('location') or '',
                due_at=due_at,
                actor=request.user,
            )
        except ValueError as e:
            return self._error_response(request, str(e), 400)

        return self._slip_list_response(request, foundation_id)

    def _slip_list_response(self, request, foundation_id):
        slips_qs = PermissionSlip.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group', 'created_by__person').order_by('-created_at')[:CONSOLE_SLIP_PAGE_SIZE]
        slips = [_serialize_permission_slip(slip, get_slip_consent_tally(slip)) for slip in slips_qs]
        html = render_to_string('components/_permission_slip_list.html', {
            'slips': slips,
            'tally_url_prefix': '/web/academic/permission-slips',
        }, request=request)
        return HttpResponse(html, status=201 if request.method == 'POST' else 200)


class PermissionSlipConsoleTallyView(PermissionSlipWebAccessMixin, APIView):
    """GET /web/academic/permission-slips/:id/ — HTMX tally fragment (15s polling).
    GET /web/academic/permission-slips/:id/roster/ — per-student roster fragment."""

    def get_required_permission(self):
        return 'grades.read'

    def _get_slip_or_404(self, request, slip_id):
        foundation_id = get_current_foundation_id()
        return PermissionSlip.objects.filter(
            id=slip_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group').first()

    def get(self, request, slip_id):
        slip = self._get_slip_or_404(request, slip_id)
        if not slip:
            return Response({'error': _("Izin tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        tally = get_slip_consent_tally(slip)
        html = render_to_string('components/_permission_slip_tally.html', {'tally': tally}, request=request)
        return HttpResponse(html)

class PermissionSlipConsoleRosterView(PermissionSlipWebAccessMixin, APIView):
    """GET /web/academic/permission-slips/:id/roster/ — per-student roster fragment."""

    def get_required_permission(self):
        return 'grades.read'

    def get(self, request, slip_id):
        foundation_id = get_current_foundation_id()
        slip = PermissionSlip.objects.filter(
            id=slip_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('class_group').first()
        if not slip:
            return Response({'error': _("Izin tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        tally = get_slip_consent_tally(slip)
        enrollments = ClassEnrollment.objects.filter(
            class_group_id=slip.class_group_id, is_active=True,
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person')

        acks = list(PermissionSlipAcknowledgement.objects.filter(
            permission_slip=slip, foundation_id=foundation_id, deleted_at__isnull=True,
        ).order_by('responded_at', 'id'))
        latest_by_pair = {}
        for ack in acks:
            latest_by_pair[(ack.student_id, ack.guardian_id)] = ack

        roster = []
        for enrollment in enrollments:
            student = enrollment.student
            student_acks = [a for (sid, _gid), a in latest_by_pair.items() if sid == student.id]
            effective = max(student_acks, key=lambda a: (a.responded_at, a.id)) if student_acks else None
            roster.append({
                'student_id': student.id,
                'student_name': student.person.full_name if student.person else '',
                'nis': student.nis,
                'response': effective.response if effective else 'PENDING',
                'responded_at': effective.responded_at.strftime('%d %b %Y %H:%M') if effective else None,
            })

        html = render_to_string('components/_permission_slip_roster.html', {
            'roster': roster, 'tally': tally,
        }, request=request)
        return HttpResponse(html)
