from django.apps import AppConfig


class StatusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.status'
    label = 'status'

    def ready(self):
        # Register the TaskQueue handler for subscriber incident emails.
        from apps.status import tasks  # noqa: F401
