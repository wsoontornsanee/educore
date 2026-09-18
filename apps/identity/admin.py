from django.contrib import admin
from .models import PlatformRoleAssignment


@admin.register(PlatformRoleAssignment)
class PlatformRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__phone_e164', 'user__full_name')
