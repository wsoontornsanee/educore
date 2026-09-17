"""Partner & Vendor Integration API (spec/18-partner-vendor-api.md).

Public partner-facing REST surface: HMAC key auth, payroll/finance/roster/
attendance endpoints, webhook events.
"""
from django.apps import AppConfig


class PartnersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.partners'
    verbose_name = 'Partner & Vendor Integration API'

    def ready(self):
        # Register the TaskQueue handler for webhook delivery.
        from apps.partners import tasks  # noqa: F401
