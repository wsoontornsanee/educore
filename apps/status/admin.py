from django.contrib import admin
from .models import ServiceComponent


@admin.register(ServiceComponent)
class ServiceComponentAdmin(admin.ModelAdmin):
    list_display = ('key', 'name_id', 'display_order', 'manual_status')
    list_editable = ('manual_status',)
    ordering = ('display_order',)
