from django.contrib import admin
from .models import BehaviourCase, BehaviourPolicy, BehaviourReason, BehaviourRecord


@admin.register(BehaviourPolicy)
class BehaviourPolicyAdmin(admin.ModelAdmin):
    list_display = ('school', 'escalation_negative_threshold', 'rapor_includes_behaviour', 'default_counsellor')
    search_fields = ('school__name',)


@admin.register(BehaviourReason)
class BehaviourReasonAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'school', 'category', 'points', 'is_active')
    list_filter = ('category', 'is_active', 'school')
    search_fields = ('code', 'label')


@admin.register(BehaviourRecord)
class BehaviourRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'reason', 'points', 'term', 'occurred_at', 'recorded_by', 'is_superseded', 'acknowledged_by_guardian_at')
    list_filter = ('is_superseded', 'term', 'reason__category')
    search_fields = ('student__person__full_name', 'reason__label', 'reason__code')


@admin.register(BehaviourCase)
class BehaviourCaseAdmin(admin.ModelAdmin):
    list_display = ('student', 'term', 'status', 'assigned_counsellor', 'opened_at', 'closed_at')
    list_filter = ('status', 'term')
    search_fields = ('student__person__full_name', 'trigger')
