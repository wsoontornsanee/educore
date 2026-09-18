from django.contrib import admin
from .models import ServiceComponent, ComponentHeartbeat, DailyComponentStatus


@admin.register(ServiceComponent)
class ServiceComponentAdmin(admin.ModelAdmin):
    list_display = ('key', 'name_id', 'display_order', 'manual_status')
    list_editable = ('manual_status',)
    ordering = ('display_order',)


@admin.register(ComponentHeartbeat)
class ComponentHeartbeatAdmin(admin.ModelAdmin):
    list_display = ('component', 'checked_at', 'is_up', 'latency_ms')
    list_filter = ('component', 'is_up')


@admin.register(DailyComponentStatus)
class DailyComponentStatusAdmin(admin.ModelAdmin):
    list_display = ('component', 'date', 'status')
    list_filter = ('component', 'status')
