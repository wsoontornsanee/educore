from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CampusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.campus'
    verbose_name = _('Kehidupan Kampus & Perilaku (Campus Life)')
