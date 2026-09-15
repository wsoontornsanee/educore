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
