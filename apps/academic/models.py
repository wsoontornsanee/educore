from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantModel
from apps.identity.models import School, Staff, Student, User


class AcademicYear(TenantModel):
    """A school's academic year (e.g. 2026/2027), scoping terms and class groups."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='academic_years')
    name = models.CharField(max_length=32, help_text=_("e.g. 2026/2027"))
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'academic_years'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'name'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_academic_year_name_per_school',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.name}"


class Term(TenantModel):
    """A semester/term within an academic year (spec/04)."""
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='terms')
    name = models.CharField(max_length=64, help_text=_("e.g. Semester 1 (Ganjil)"))
    term_no = models.PositiveSmallIntegerField(help_text=_("1 or 2"))
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'terms'
        indexes = [
            models.Index(fields=['foundation_id', 'academic_year_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'academic_year', 'term_no'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_term_no_per_academic_year',
            ),
        ]

    def __str__(self):
        return f"{self.academic_year.name} - {self.name}"


class Subject(TenantModel):
    """Curriculum subject catalogue item (spec/04 §2)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='subjects')
    code = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=128)
    level = models.CharField(max_length=16, blank=True, default='', help_text=_("School level this subject applies to, e.g. SMA"))
    is_religious = models.BooleanField(default=False)
    credit_hours = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'subjects'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'code'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_subject_code_per_school',
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ClassGroup(TenantModel):
    """A rombel (class group) within an academic year (spec/04 §2)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='class_groups')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name='class_groups')
    grade_level = models.PositiveSmallIntegerField(help_text=_("Numeric grade level, e.g. 10"))
    name = models.CharField(max_length=64, help_text=_("e.g. X IPA 1"))
    homeroom_teacher = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, null=True, blank=True, related_name='homeroom_class_groups'
    )
    capacity = models.PositiveSmallIntegerField(default=36)

    class Meta:
        db_table = 'class_groups'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'academic_year_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'academic_year', 'name'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_class_group_name_per_academic_year',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year.name})"


class ClassEnrollment(TenantModel):
    """Roster membership linking a student to a class group for a given academic year."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='class_enrollments')
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name='enrollments')
    enrolled_at = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'class_enrollments'
        indexes = [
            models.Index(fields=['foundation_id', 'class_group_id', 'is_active']),
            models.Index(fields=['foundation_id', 'student_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'class_group'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_active_enrollment_per_student_class_group',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} -> {self.class_group.name}"


class ClassSubject(TenantModel):
    """A subject taught to a specific class group in a specific term, by a teacher (spec/04 §2)."""
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name='class_subjects')
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='class_subjects')
    teacher = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name='teaching_assignments')
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name='class_subjects')

    class Meta:
        db_table = 'class_subjects'
        indexes = [
            models.Index(fields=['foundation_id', 'class_group_id', 'term_id']),
            models.Index(fields=['foundation_id', 'teacher_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'class_group', 'subject', 'term'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_class_subject_per_term',
            ),
        ]

    def __str__(self):
        return f"{self.subject.code} - {self.class_group.name} ({self.term.name})"


class LearningObjective(TenantModel):
    """A Tujuan Pembelajaran (TP) linked to a subject and grade level (spec/04 §2)."""
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='learning_objectives')
    grade_level = models.PositiveSmallIntegerField()
    code = models.CharField(max_length=32)
    description = models.TextField()

    class Meta:
        db_table = 'learning_objectives'
        indexes = [
            models.Index(fields=['foundation_id', 'subject_id', 'grade_level']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'subject', 'code'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_objective_code_per_subject',
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.subject.code}"


class AssessmentType(models.TextChoices):
    """ACD-001: canonical assessment types."""
    FORMATIVE = 'FORMATIVE', _('Formatif')
    SUMMATIVE = 'SUMMATIVE', _('Sumatif')
    PROJECT = 'PROJECT', _('Proyek')
    PRACTICAL = 'PRACTICAL', _('Praktik')
    EXAM = 'EXAM', _('Ujian')


# Types that carry report-card weight by default (ACD-001).
WEIGHTED_ASSESSMENT_TYPES = {AssessmentType.SUMMATIVE, AssessmentType.EXAM, AssessmentType.PROJECT}

# Default descriptor band thresholds (ACD-003), school-configurable in a future slice.
DEFAULT_DESCRIPTOR_BANDS = (
    (90, _('Sangat Baik')),
    (80, _('Baik')),
    (70, _('Cukup')),
    (0, _('Perlu Bimbingan')),
)


class Assessment(TenantModel):
    """A gradable assessment within a class subject (spec/04 §2, §3)."""
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name='assessments')
    type = models.CharField(max_length=16, choices=AssessmentType.choices)
    title = models.CharField(max_length=128)
    objectives = models.ManyToManyField(LearningObjective, blank=True, related_name='assessments')
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('100.00'))
    weight = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        help_text=_("Percentage weight toward the final term grade, for weighted types only")
    )
    due_at = models.DateTimeField(null=True, blank=True)
    published = models.BooleanField(default=False)

    class Meta:
        db_table = 'assessments'
        indexes = [
            models.Index(fields=['foundation_id', 'class_subject_id', 'type']),
            models.Index(fields=['foundation_id', 'class_subject_id', 'published']),
        ]

    def __str__(self):
        return f"{self.title} ({self.class_subject})"

    @property
    def is_weighted(self):
        return self.type in WEIGHTED_ASSESSMENT_TYPES


class AssessmentScore(TenantModel):
    """A student's score on an assessment (spec/04 §2, §3)."""
    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name='scores')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='assessment_scores')
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    descriptor = models.CharField(max_length=32, blank=True, default='')
    feedback = models.TextField(blank=True, default='')
    graded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'assessment_scores'
        indexes = [
            models.Index(fields=['foundation_id', 'assessment_id']),
            models.Index(fields=['foundation_id', 'student_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'assessment', 'student'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_score_per_assessment_student',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.assessment.title}: {self.score}"


class DayOfWeek(models.IntegerChoices):
    """ISO weekday numbering (1=Monday .. 7=Sunday)."""
    MONDAY = 1, _('Senin')
    TUESDAY = 2, _('Selasa')
    WEDNESDAY = 3, _('Rabu')
    THURSDAY = 4, _('Kamis')
    FRIDAY = 5, _('Jumat')
    SATURDAY = 6, _('Sabtu')
    SUNDAY = 7, _('Minggu')


class TimetableSlot(TenantModel):
    """A recurring weekly timetable slot for a class subject (spec/04 §5)."""
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name='timetable_slots')
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices)
    period_no = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'timetable_slots'
        indexes = [
            models.Index(fields=['foundation_id', 'class_subject_id', 'day_of_week']),
            models.Index(fields=['foundation_id', 'day_of_week', 'period_no']),
        ]

    @property
    def class_group(self):
        return self.class_subject.class_group

    def __str__(self):
        return f"{self.class_subject} - {self.get_day_of_week_display()} P{self.period_no}"


class TimetableSubstitution(TenantModel):
    """A single-date teacher substitution for a timetable slot (ACD-019)."""
    slot = models.ForeignKey(TimetableSlot, on_delete=models.PROTECT, related_name='substitutions')
    date = models.DateField()
    original_teacher = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name='+')
    substitute_teacher = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name='substitute_assignments')
    reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'timetable_substitutions'
        indexes = [
            models.Index(fields=['foundation_id', 'slot_id', 'date']),
            models.Index(fields=['foundation_id', 'substitute_teacher_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'slot', 'date'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_substitution_per_slot_date',
            ),
        ]

    def __str__(self):
        return f"{self.slot} on {self.date}: {self.original_teacher} -> {self.substitute_teacher}"


class HomeworkSubmissionStatus(models.TextChoices):
    """ACD-028: homework submission lifecycle (NOT_STARTED is derived, never stored)."""
    SUBMITTED = 'SUBMITTED', _('Terkumpul')
    LATE = 'LATE', _('Terlambat')
    GRADED = 'GRADED', _('Dinilai')
    RETURNED = 'RETURNED', _('Dikembalikan')


# ACD-027: accepted submission attachment types and limits.
ALLOWED_SUBMISSION_CONTENT_TYPES = {
    'application/pdf', 'image/jpeg', 'image/png',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}
MAX_SUBMISSION_FILE_SIZE = 20 * 1024 * 1024
MAX_SUBMISSION_FILES = 5


class Homework(TenantModel):
    """A homework assignment for a class subject (spec/04 §7)."""
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name='homework_assignments')
    title = models.CharField(max_length=128)
    instructions = models.TextField(blank=True, default='')
    assigned_at = models.DateTimeField()
    due_at = models.DateTimeField()
    last_reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'homework'
        indexes = [
            models.Index(fields=['foundation_id', 'class_subject_id', 'due_at']),
        ]

    def __str__(self):
        return f"{self.title} ({self.class_subject})"


class HomeworkSubmission(TenantModel):
    """A student's submission for a homework assignment (spec/04 §7)."""
    homework = models.ForeignKey(Homework, on_delete=models.PROTECT, related_name='submissions')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='homework_submissions')
    submitted_at = models.DateTimeField()
    files = models.JSONField(default=list, blank=True, help_text=_("List of {key, filename, size, content_type}"))
    text = models.TextField(blank=True, default='')
    status = models.CharField(max_length=16, choices=HomeworkSubmissionStatus.choices)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True, default='')
    graded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'homework_submissions'
        indexes = [
            models.Index(fields=['foundation_id', 'homework_id', 'status']),
            models.Index(fields=['foundation_id', 'student_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'homework', 'student'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_submission_per_homework_student',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.homework.title} ({self.status})"


class ExamMode(models.TextChoices):
    ONLINE = 'ONLINE', _('Daring')
    PAPER = 'PAPER', _('Kertas')


class Exam(TenantModel):
    """An exam window for a class subject (spec/04 §6)."""
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name='exams')
    title = models.CharField(max_length=128)
    mode = models.CharField(max_length=16, choices=ExamMode.choices, default=ExamMode.ONLINE)
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    duration_min = models.PositiveSmallIntegerField()
    shuffle = models.BooleanField(default=False)
    settings = models.JSONField(default=dict, blank=True, help_text=_("one_question_at_a_time, block_back_navigation, full_screen_lock, etc."))
    published = models.BooleanField(default=False)

    class Meta:
        db_table = 'exams'
        indexes = [
            models.Index(fields=['foundation_id', 'class_subject_id']),
        ]

    def __str__(self):
        return f"{self.title} ({self.class_subject})"


class ExamQuestionType(models.TextChoices):
    MCQ = 'MCQ', _('Pilihan Ganda')
    MULTI = 'MULTI', _('Pilihan Ganda Kompleks')
    TRUE_FALSE = 'TRUE_FALSE', _('Benar/Salah')
    SHORT = 'SHORT', _('Jawaban Singkat')
    ESSAY = 'ESSAY', _('Esai')
    MATCHING = 'MATCHING', _('Menjodohkan')


# Types that auto-grade; ESSAY always routes to manual grading (ACD-023).
AUTO_GRADABLE_QUESTION_TYPES = {
    ExamQuestionType.MCQ, ExamQuestionType.MULTI, ExamQuestionType.TRUE_FALSE,
    ExamQuestionType.SHORT, ExamQuestionType.MATCHING,
}


class ExamQuestion(TenantModel):
    """A question within an exam (spec/04 §6)."""
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='questions')
    seq = models.PositiveSmallIntegerField()
    type = models.CharField(max_length=16, choices=ExamQuestionType.choices)
    body = models.TextField()
    media = models.JSONField(default=list, blank=True)
    options = models.JSONField(default=list, blank=True, help_text=_("Choice list for MCQ/MULTI/MATCHING, e.g. [{key, text}]"))
    points = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.00'))
    answer_key = models.JSONField(default=dict, blank=True, help_text=_("Grading key; shape depends on type"))

    class Meta:
        db_table = 'exam_questions'
        indexes = [
            models.Index(fields=['foundation_id', 'exam_id', 'seq']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'exam', 'seq'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_question_seq_per_exam',
            ),
        ]

    def __str__(self):
        return f"{self.exam.title} Q{self.seq}"


class ExamAttemptStatus(models.TextChoices):
    IN_PROGRESS = 'IN_PROGRESS', _('Sedang Berjalan')
    SUBMITTED = 'SUBMITTED', _('Terkumpul')
    AUTO_SUBMITTED = 'AUTO_SUBMITTED', _('Terkumpul Otomatis')


class ExamAttempt(TenantModel):
    """A student's attempt at an exam (spec/04 §6)."""
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='attempts')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='exam_attempts')
    started_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    auto_score = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
    manual_score = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0.00'))
    final_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, choices=ExamAttemptStatus.choices, default=ExamAttemptStatus.IN_PROGRESS)
    question_order = models.JSONField(default=list, blank=True, help_text=_("Per-attempt shuffled question id order (ACD-024)"))
    focus_loss_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'exam_attempts'
        indexes = [
            models.Index(fields=['foundation_id', 'exam_id', 'student_id']),
            models.Index(fields=['foundation_id', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'exam', 'student'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_attempt_per_exam_student',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.exam.title} ({self.status})"


class ExamAnswer(TenantModel):
    """A student's persisted answer to one question within an attempt (ACD-021)."""
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.PROTECT, related_name='answers')
    question = models.ForeignKey(ExamQuestion, on_delete=models.PROTECT, related_name='+')
    answer = models.JSONField(default=dict, blank=True)
    points_awarded = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    graded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'exam_answers'
        indexes = [
            models.Index(fields=['foundation_id', 'attempt_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'attempt', 'question'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_answer_per_attempt_question',
            ),
        ]

    def __str__(self):
        return f"Attempt#{self.attempt_id} - Q{self.question.seq}"


class ReportCardStatus(models.TextChoices):
    """ACD-012: rapor status machine."""
    DRAFT = 'DRAFT', _('Konsep')
    PENDING_REVIEW = 'PENDING_REVIEW', _('Menunggu Tinjauan')
    APPROVED = 'APPROVED', _('Disetujui')
    PUBLISHED = 'PUBLISHED', _('Diterbitkan')


class ReportCard(TenantModel):
    """A student's term report card / rapor (spec/04 §4)."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='report_cards')
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name='report_cards')
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name='report_cards')
    status = models.CharField(max_length=16, choices=ReportCardStatus.choices, default=ReportCardStatus.DRAFT)
    grades_snapshot = models.JSONField(default=list, blank=True, help_text=_("Per-subject final grade/descriptor/status, frozen at generation"))
    attendance_summary = models.JSONField(default=dict, blank=True, help_text=_("Counts by AttendanceStatus for the term"))
    narrative = models.TextField(blank=True, default='')
    version = models.PositiveSmallIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    pdf_key = models.CharField(max_length=255, blank=True, default='')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'report_cards'
        indexes = [
            models.Index(fields=['foundation_id', 'student_id', 'term_id', 'is_current']),
            models.Index(fields=['foundation_id', 'class_group_id', 'term_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'term'],
                condition=models.Q(deleted_at__isnull=True, is_current=True),
                name='unique_current_report_card_per_student_term',
            ),
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'term', 'version'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_report_card_version_per_student_term',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.term.name} v{self.version} ({self.status})"


class ReportCardPolicy(TenantModel):
    """Per-school rapor publication policy (ACD-014)."""
    school = models.OneToOneField(School, on_delete=models.PROTECT, related_name='report_card_policy')
    block_rapor_on_arrears = models.BooleanField(default=False)

    class Meta:
        db_table = 'report_card_policies'

    def __str__(self):
        return f"{self.school.name} (block_on_arrears={self.block_rapor_on_arrears})"


class LessonPlan(TenantModel):
    """A teacher's lesson plan (RPP/modul ajar) for a class subject in a given week (spec/09 TCH-010)."""
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.PROTECT, related_name='lesson_plans')
    week_start_date = models.DateField(help_text=_("Monday of the ISO week this plan covers"))
    title = models.CharField(max_length=128)
    content = models.TextField(blank=True, default='')
    attachments = models.JSONField(default=list, blank=True, help_text=_("List of {key, filename, size, content_type}"))
    slots = models.ManyToManyField(TimetableSlot, blank=True, related_name='lesson_plans')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'lesson_plans'
        indexes = [
            models.Index(fields=['foundation_id', 'class_subject_id', 'week_start_date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'class_subject', 'week_start_date'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_lesson_plan_per_class_subject_week',
            ),
        ]

    def __str__(self):
        return f"{self.class_subject} - week of {self.week_start_date}"


class BroadcastPolicy(TenantModel):
    """Per-school policy gating teacher-to-guardian broadcast announcements (ACD/TCH-011)."""
    school = models.OneToOneField(School, on_delete=models.PROTECT, related_name='broadcast_policy')
    teacher_can_broadcast = models.BooleanField(default=True)

    class Meta:
        db_table = 'broadcast_policies'

    def __str__(self):
        return f"{self.school.name} (teacher_can_broadcast={self.teacher_can_broadcast})"


class Broadcast(TenantModel):
    """A teacher's group announcement to a class group's guardians (spec/09 TCH-011)."""
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name='broadcasts')
    sender = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name='broadcasts_sent')
    title = models.CharField(max_length=128)
    body = models.TextField()
    sent_at = models.DateTimeField()
    recipient_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'broadcasts'
        indexes = [
            models.Index(fields=['foundation_id', 'class_group_id', 'sent_at']),
        ]

    def __str__(self):
        return f"{self.title} -> {self.class_group.name} ({self.sent_at})"
