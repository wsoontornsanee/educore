from rest_framework import serializers

from .models import (
    BehaviourCase,
    BehaviourCategory,
    BehaviourPolicy,
    BehaviourReason,
    BehaviourRecord,
    CaseStatus,
    CounsellingConfidentiality,
    CounsellingSession,
    CounsellingSessionType,
)


class BehaviourReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = BehaviourReason
        fields = [
            'id',
            'school',
            'code',
            'label',
            'points',
            'category',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BehaviourPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = BehaviourPolicy
        fields = [
            'id',
            'school',
            'escalation_negative_threshold',
            'default_counsellor',
            'rapor_includes_behaviour',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'school', 'created_at', 'updated_at']


class BehaviourRecordSerializer(serializers.ModelSerializer):
    reason_code = serializers.CharField(source='reason.code', read_only=True)
    reason_label = serializers.CharField(source='reason.label', read_only=True)
    reason_category = serializers.CharField(source='reason.category', read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    student_name = serializers.SerializerMethodField()

    class Meta:
        model = BehaviourRecord
        fields = [
            'id',
            'school',
            'student',
            'student_name',
            'term',
            'reason',
            'reason_code',
            'reason_label',
            'reason_category',
            'points',
            'note',
            'occurred_at',
            'recorded_by',
            'recorded_by_name',
            'acknowledged_by_guardian_at',
            'acknowledged_by',
            'superseded_by',
            'is_superseded',
            'correction_reason',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'school',
            'student',
            'student_name',
            'term',
            'reason',
            'reason_code',
            'reason_label',
            'reason_category',
            'points',
            'note',
            'occurred_at',
            'recorded_by',
            'recorded_by_name',
            'acknowledged_by_guardian_at',
            'acknowledged_by',
            'superseded_by',
            'is_superseded',
            'correction_reason',
            'created_at',
            'updated_at',
        ]

    def get_recorded_by_name(self, obj) -> str:
        if obj.recorded_by:
            return getattr(obj.recorded_by, 'full_name', '') or obj.recorded_by.phone_e164 or str(obj.recorded_by)
        return ''

    def get_student_name(self, obj) -> str:
        if obj.student and getattr(obj.student, 'person', None):
            return obj.student.person.full_name
        return f"Siswa {obj.student_id}"


class RecordBehaviourInputSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=True)
    reason_id = serializers.IntegerField(required=True)
    term_id = serializers.IntegerField(required=False, allow_null=True)
    points = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)


class SupersedeRecordInputSerializer(serializers.Serializer):
    reason_id = serializers.IntegerField(required=True)
    correction_reason = serializers.CharField(required=True, min_length=3)
    points = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)


class AcknowledgeRecordInputSerializer(serializers.Serializer):
    guardian_id = serializers.IntegerField(required=False, allow_null=True)


class BehaviourCaseSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    counsellor_name = serializers.SerializerMethodField()

    class Meta:
        model = BehaviourCase
        fields = [
            'id',
            'school',
            'student',
            'student_name',
            'term',
            'opened_at',
            'trigger',
            'status',
            'assigned_counsellor',
            'counsellor_name',
            'resolution',
            'closed_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'school', 'opened_at', 'created_at', 'updated_at']

    def get_student_name(self, obj) -> str:
        if obj.student and getattr(obj.student, 'person', None):
            return obj.student.person.full_name
        return f"Siswa {obj.student_id}"

    def get_counsellor_name(self, obj) -> str:
        if obj.assigned_counsellor and getattr(obj.assigned_counsellor, 'person', None):
            return obj.assigned_counsellor.person.full_name
        return ''


class CounsellingSessionSerializer(serializers.ModelSerializer):
    """Read serializer. `notes` is populated by the view (decrypted only for
    authorized readers) — never sourced from the model's encrypted field directly."""
    student_name = serializers.SerializerMethodField()
    counsellor_name = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()

    class Meta:
        model = CounsellingSession
        fields = [
            'id',
            'school',
            'case',
            'student',
            'student_name',
            'counsellor',
            'counsellor_name',
            'occurred_at',
            'type',
            'notes',
            'follow_up_at',
            'confidentiality',
            'is_urgent',
            'urgent_notified_at',
            'follow_up_reminder_sent_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_student_name(self, obj) -> str:
        if obj.student and getattr(obj.student, 'person', None):
            return obj.student.person.full_name
        return f"Siswa {obj.student_id}"

    def get_counsellor_name(self, obj) -> str:
        if obj.counsellor and getattr(obj.counsellor, 'person', None):
            return obj.counsellor.person.full_name
        return ''

    def get_notes(self, obj) -> str:
        return getattr(obj, '_decrypted_notes', None) or ''


class RecordCounsellingSessionInputSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=True)
    counsellor_id = serializers.IntegerField(required=True)
    case_id = serializers.IntegerField(required=False, allow_null=True)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)
    type = serializers.ChoiceField(choices=CounsellingSessionType.choices, required=False, default=CounsellingSessionType.INITIAL)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    follow_up_at = serializers.DateTimeField(required=False, allow_null=True)
    confidentiality = serializers.ChoiceField(choices=CounsellingConfidentiality.choices, required=False, default=CounsellingConfidentiality.NORMAL)
    is_urgent = serializers.BooleanField(required=False, default=False)


class StudentBehaviourSummarySerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    term_id = serializers.IntegerField(allow_null=True)
    term_positive_points = serializers.IntegerField()
    term_negative_points = serializers.IntegerField()
    term_net_points = serializers.IntegerField()
    lifetime_positive_points = serializers.IntegerField()
    lifetime_negative_points = serializers.IntegerField()
    lifetime_net_points = serializers.IntegerField()
    positive_records_count = serializers.IntegerField()
    minor_records_count = serializers.IntegerField()
    major_records_count = serializers.IntegerField()
    unacknowledged_infractions_count = serializers.IntegerField()
    active_cases_count = serializers.IntegerField()
    escalation_threshold = serializers.IntegerField()
