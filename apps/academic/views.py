from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
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
    ExamQuestion,
    ExamQuestionType,
    Homework,
    HomeworkSubmission,
    LearningObjective,
    LessonPlan,
    ReportCard,
    Subject,
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
        except (TimetableConflictError, PeriodGridMismatchError) as e:
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
    }

    @action(detail=True, methods=['post'], url_path='upload-file', parser_classes=[MultiPartParser])
    def upload_file(self, request, pk=None):
        homework = self.get_object()
        payload = HomeworkFileUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            file_meta = store_homework_submission_file(homework, payload.validated_data['file'])
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


class ExamQuestionViewSet(TenantScopedModelViewSet):
    model = ExamQuestion
    serializer_class = ExamQuestionSerializer
    filter_params = {'exam_id': 'exam_id'}
    action_permissions = {
        'list': 'grades.write', 'retrieve': 'grades.write',
        'create': 'grades.write', 'update': 'grades.write',
        'partial_update': 'grades.write', 'destroy': 'grades.write',
    }


class ExamAttemptViewSet(TenantScopedModelViewSet):
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

    @action(detail=True, methods=['patch'], url_path='answers')
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
        report_card = ReportCard.objects.filter(
            student=student, term_id=term_id, is_current=True, foundation_id=foundation_id,
        ).first()
        if not report_card:
            return Response({'error': _("Rapor tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        visibility = get_visible_report_card(report_card)
        if not visibility['visible']:
            return Response({'visible': False, 'reason': visibility['reason']}, status=status.HTTP_403_FORBIDDEN)

        return Response({'visible': True, 'report_card': ReportCardSerializer(report_card).data})


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
