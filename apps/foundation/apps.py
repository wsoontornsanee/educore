from django.apps import AppConfig

class FoundationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.foundation'
    verbose_name = 'Portal Yayasan'

    def ready(self):
        # Registers the foundation_dashboard export renderer/notifier (FND-014, RPT-002/003)
        try:
            import apps.foundation.services  # noqa
        except ImportError:
            pass
