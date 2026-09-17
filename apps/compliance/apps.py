from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.compliance'
    verbose_name = 'Compliance & Statutory Reporting'

    def ready(self):
        # Register statutory export renderers with the core ExportJob
        # pipeline (same hook pattern as apps.foundation).
        from apps.compliance import exports  # noqa: F401
