from django.contrib import admin
from .models import PlatformRoleAssignment


@admin.register(PlatformRoleAssignment)
class PlatformRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__phone_e164', 'user__full_name')

    def get_queryset(self, request):
        """Eagerly load user via select_related to bypass TenantManager's fail-closed scoping.

        PlatformRoleAssignment.user is a FK to User, which uses TenantManager as its
        default manager. TenantManager silently returns nothing when accessed outside
        an active tenant context (which is normal for platform-wide code). By using
        select_related, we perform a SQL JOIN in PlatformRoleAssignment's plain queryset,
        avoiding the manager's filter entirely.
        """
        qs = super().get_queryset(request)
        return qs.select_related('user')
