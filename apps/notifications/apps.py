from django.apps import AppConfig

class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.notifications'
    verbose_name = 'Notifications & Messaging'

    def ready(self):
        # Register domain event listeners and task handlers
        try:
            import apps.notifications.tasks  # noqa
        except ImportError:
            pass
