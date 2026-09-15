from rest_framework import serializers

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
            'feedback', 'graded_by', 'graded_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'descriptor', 'graded_by', 'graded_at', 'created_at', 'updated_at']


class ScoreEntrySerializer(serializers.Serializer):
    """One row of the bulk score-entry payload for PUT /assessments/:id/scores."""
    student_id = serializers.IntegerField()
    score = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True, required=False)
    feedback = serializers.CharField(required=False, allow_blank=True, default='')
    reason = serializers.CharField(required=False, allow_blank=True, default=None, allow_null=True)


class BulkScoreEntrySerializer(serializers.Serializer):
    scores = ScoreEntrySerializer(many=True)


class TimetableSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetableSlot
        fields = ['id', 'foundation_id', 'class_subject', 'day_of_week', 'period_no', 'start_time', 'end_time', 'room', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class TimetableSubstitutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetableSubstitution
        fields = ['id', 'foundation_id', 'slot', 'date', 'original_teacher', 'substitute_teacher', 'reason', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'original_teacher', 'created_at', 'updated_at']
