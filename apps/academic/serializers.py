from rest_framework import serializers

from apps.academic.models import (
    AcademicYear,
    Assessment,
    AssessmentScore,
    Broadcast,
    BroadcastPolicy,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamQuestion,
    Homework,
    HomeworkSubmission,
    LearningObjective,
    LessonPlan,
    PeriodGridSlot,
    ReportCard,
    ReportCardPolicy,
    Subject,
    Term,
    TimetableSlot,
    TimetableSubstitution,
)


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ['id', 'foundation_id', 'school', 'name', 'start_date', 'end_date', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class TermSerializer(serializers.ModelSerializer):
    class Meta:
        model = Term
        fields = ['id', 'foundation_id', 'academic_year', 'name', 'term_no', 'start_date', 'end_date', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ['id', 'foundation_id', 'school', 'code', 'name', 'level', 'is_religious', 'credit_hours', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class ClassGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassGroup
        fields = ['id', 'foundation_id', 'school', 'academic_year', 'grade_level', 'name', 'homeroom_teacher', 'capacity', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class ClassEnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassEnrollment
        fields = ['id', 'foundation_id', 'student', 'class_group', 'enrolled_at', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class ClassSubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassSubject
        fields = ['id', 'foundation_id', 'class_group', 'subject', 'teacher', 'term', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class LearningObjectiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObjective
        fields = ['id', 'foundation_id', 'subject', 'grade_level', 'code', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class AssessmentSerializer(serializers.ModelSerializer):
    max_score = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True)
    weight = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = Assessment
        fields = [
            'id', 'foundation_id', 'class_subject', 'type', 'title', 'objectives',
            'max_score', 'weight', 'due_at', 'published', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'published', 'created_at', 'updated_at']


class AssessmentScoreSerializer(serializers.ModelSerializer):
    score = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True, allow_null=True)

    class Meta:
        model = AssessmentScore
        fields = [
            'id', 'foundation_id', 'assessment', 'student', 'score', 'descriptor',
            'feedback', 'graded_by', 'graded_at', 'version', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'descriptor', 'graded_by', 'graded_at', 'version', 'created_at', 'updated_at']


class ScoreEntrySerializer(serializers.Serializer):
    """One row of the bulk score-entry payload for PUT /assessments/:id/scores."""
    student_id = serializers.IntegerField()
    score = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True, required=False)
    feedback = serializers.CharField(required=False, allow_blank=True, default='')
    reason = serializers.CharField(required=False, allow_blank=True, default=None, allow_null=True)
    expected_version = serializers.IntegerField(required=False, allow_null=True, default=None)


class BulkScoreEntrySerializer(serializers.Serializer):
    scores = ScoreEntrySerializer(many=True)


class ScoreCsvImportSerializer(serializers.Serializer):
    """ACD-007: multipart payload for POST /assessments/:id/import-scores-csv/."""
    file = serializers.FileField()
    dry_run = serializers.BooleanField(required=False, default=True)
    reason = serializers.CharField(required=False, allow_blank=True, default=None, allow_null=True)


class TimetableSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetableSlot
        fields = ['id', 'foundation_id', 'class_subject', 'day_of_week', 'period_no', 'start_time', 'end_time', 'room', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class TimetableSubstitutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetableSubstitution
        fields = [
            'id', 'foundation_id', 'slot', 'date', 'original_teacher',
            'substitute_teacher', 'reason', 'status', 'decline_reason',
            'responded_at', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'foundation_id', 'original_teacher', 'status',
            'decline_reason', 'responded_at', 'created_at', 'updated_at'
        ]


class HomeworkSerializer(serializers.ModelSerializer):
    class Meta:
        model = Homework
        fields = ['id', 'foundation_id', 'class_subject', 'title', 'instructions', 'assigned_at', 'due_at', 'last_reminded_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'last_reminded_at', 'created_at', 'updated_at']


class HomeworkSubmissionSerializer(serializers.ModelSerializer):
    score = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True, allow_null=True, required=False)

    class Meta:
        model = HomeworkSubmission
        fields = [
            'id', 'foundation_id', 'homework', 'student', 'submitted_at', 'files', 'text',
            'status', 'score', 'feedback', 'graded_by', 'graded_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'submitted_at', 'status', 'graded_by', 'graded_at', 'created_at', 'updated_at']


class HomeworkGradingQueueItemSerializer(serializers.ModelSerializer):
    """TCH-014: Enriched submission item for homework grading queue."""
    homework_title = serializers.CharField(source='homework.title', read_only=True)
    homework_due_at = serializers.DateTimeField(source='homework.due_at', read_only=True)
    class_subject_id = serializers.UUIDField(source='homework.class_subject_id', read_only=True)
    class_subject_name = serializers.SerializerMethodField()
    student_nis = serializers.CharField(source='student.nis', read_only=True)
    student_name = serializers.SerializerMethodField()
    score = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True, allow_null=True, required=False)
    graded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = HomeworkSubmission
        fields = [
            'id', 'foundation_id', 'homework', 'homework_title', 'homework_due_at',
            'class_subject_id', 'class_subject_name', 'student', 'student_nis', 'student_name',
            'submitted_at', 'files', 'text', 'status', 'score', 'feedback',
            'graded_by', 'graded_by_name', 'graded_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_class_subject_name(self, obj):
        cs = obj.homework.class_subject if obj.homework else None
        if not cs:
            return ''
        subj = cs.subject.name if getattr(cs, 'subject', None) else ''
        cg = cs.class_group.name if getattr(cs, 'class_group', None) else ''
        return f"{subj} - {cg}".strip(' -')

    def get_student_name(self, obj):
        if not obj.student:
            return ''
        person = getattr(obj.student, 'person', None)
        if person and getattr(person, 'full_name', None):
            return person.full_name
        return getattr(obj.student, 'full_name', '') or getattr(obj.student, 'nis', '')

    def get_graded_by_name(self, obj):
        if obj.graded_by:
            return getattr(obj.graded_by, 'full_name', None) or getattr(obj.graded_by, 'phone_e164', '')
        return None


class HomeworkSubmitSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, default='')
    files = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class HomeworkFileUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class HomeworkGradeSerializer(serializers.Serializer):
    score = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True, required=False)
    feedback = serializers.CharField(required=False, allow_blank=True, default='')


class HomeworkReturnSerializer(serializers.Serializer):
    """ACD-028: feedback is required — it's the revision instruction the student sees."""
    feedback = serializers.CharField(allow_blank=False)


class ExamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exam
        fields = [
            'id', 'foundation_id', 'class_subject', 'title', 'mode', 'window_start', 'window_end',
            'duration_min', 'shuffle', 'settings', 'published', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'published', 'created_at', 'updated_at']


class ExamQuestionSerializer(serializers.ModelSerializer):
    """Full serializer, including answer_key — for teacher/admin use only."""
    points = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = ExamQuestion
        fields = ['id', 'foundation_id', 'exam', 'seq', 'type', 'body', 'media', 'options', 'points', 'answer_key', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class ExamQuestionPublicSerializer(serializers.ModelSerializer):
    """Student-facing serializer — never exposes answer_key."""
    points = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = ExamQuestion
        fields = ['id', 'seq', 'type', 'body', 'media', 'options', 'points']


class ExamAnswerSerializer(serializers.ModelSerializer):
    points_awarded = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True, allow_null=True)

    class Meta:
        model = ExamAnswer
        fields = ['id', 'foundation_id', 'attempt', 'question', 'answer', 'points_awarded', 'graded_by', 'answered_at']
        read_only_fields = ['id', 'foundation_id', 'points_awarded', 'graded_by', 'answered_at']


class ExamAttemptSerializer(serializers.ModelSerializer):
    auto_score = serializers.DecimalField(max_digits=7, decimal_places=2, coerce_to_string=True)
    manual_score = serializers.DecimalField(max_digits=7, decimal_places=2, coerce_to_string=True)
    final_score = serializers.DecimalField(max_digits=7, decimal_places=2, coerce_to_string=True, allow_null=True)

    class Meta:
        model = ExamAttempt
        fields = [
            'id', 'foundation_id', 'exam', 'student', 'started_at', 'submitted_at',
            'auto_score', 'manual_score', 'final_score', 'status', 'question_order',
            'focus_loss_count', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class SaveAnswerSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    answer = serializers.DictField()


class GradeEssaySerializer(serializers.Serializer):
    points = serializers.DecimalField(max_digits=6, decimal_places=2)


class ReportCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportCard
        fields = [
            'id', 'foundation_id', 'student', 'term', 'class_group', 'status',
            'grades_snapshot', 'attendance_summary', 'narrative', 'extracurricular_notes',
            'promotion_decision', 'version', 'is_current',
            'pdf_key', 'approved_by', 'approved_at', 'published_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'foundation_id', 'status', 'grades_snapshot', 'attendance_summary', 'version',
            'is_current', 'pdf_key', 'approved_by', 'approved_at', 'published_at', 'created_at', 'updated_at',
        ]


class ReportCardContentUpdateSerializer(serializers.Serializer):
    """ACD-011: PATCH payload for editing content before publication."""
    narrative = serializers.CharField(required=False, allow_blank=True)
    extracurricular_notes = serializers.ListField(
        child=serializers.DictField(), required=False,
    )
    promotion_decision = serializers.CharField(required=False, allow_blank=True, max_length=64)
    subject_narratives = serializers.DictField(child=serializers.CharField(allow_blank=True), required=False)


class ReportCardGenerateSerializer(serializers.Serializer):
    class_group_id = serializers.IntegerField()
    term_id = serializers.IntegerField()


class ReportCardPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportCardPolicy
        fields = ['id', 'foundation_id', 'school', 'block_rapor_on_arrears', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'school', 'created_at', 'updated_at']


class LessonPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonPlan
        fields = [
            'id', 'foundation_id', 'class_subject', 'week_start_date', 'title', 'content',
            'attachments', 'slots', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_by', 'created_at', 'updated_at']


class LessonPlanDuplicateSerializer(serializers.Serializer):
    target_week_start_date = serializers.DateField()


class BroadcastSerializer(serializers.ModelSerializer):
    class Meta:
        model = Broadcast
        fields = ['id', 'foundation_id', 'class_group', 'sender', 'title', 'body', 'sent_at', 'recipient_count', 'created_at']
        read_only_fields = fields


class BroadcastCreateSerializer(serializers.Serializer):
    class_group_id = serializers.IntegerField()
    title = serializers.CharField(max_length=128)
    body = serializers.CharField()


class BroadcastPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = BroadcastPolicy
        fields = ['id', 'foundation_id', 'school', 'teacher_can_broadcast', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'school', 'created_at', 'updated_at']


class PeriodGridSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PeriodGridSlot
        fields = ['id', 'day_of_week', 'period_no', 'start_time', 'end_time', 'is_break', 'label']
        read_only_fields = ['id']


class PeriodGridSlotInputSerializer(serializers.Serializer):
    period_no = serializers.IntegerField(min_value=1)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    is_break = serializers.BooleanField(required=False, default=False)
    label = serializers.CharField(required=False, allow_blank=True, default='')


class PeriodGridSetSerializer(serializers.Serializer):
    periods = PeriodGridSlotInputSerializer(many=True)


class ExamProctorStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    nis = serializers.CharField()
    attempt_id = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()
    answered_count = serializers.IntegerField()
    total_questions = serializers.IntegerField()
    progress_pct = serializers.IntegerField()
    focus_loss_count = serializers.IntegerField()
    last_saved_at = serializers.DateTimeField(allow_null=True)
    remaining_seconds = serializers.IntegerField(allow_null=True)
    needs_attention = serializers.BooleanField()


class ExamProctorSummarySerializer(serializers.Serializer):
    total_students = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    submitted = serializers.IntegerField()
    not_started = serializers.IntegerField()
    flagged_focus_loss = serializers.IntegerField()


class ExamProctorHeaderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    duration_min = serializers.IntegerField()
    window_start = serializers.DateTimeField()
    window_end = serializers.DateTimeField()
    total_questions = serializers.IntegerField()
    is_active = serializers.BooleanField()


class ExamProctorResponseSerializer(serializers.Serializer):
    exam = ExamProctorHeaderSerializer()
    summary = ExamProctorSummarySerializer()
    students = ExamProctorStudentSerializer(many=True)

