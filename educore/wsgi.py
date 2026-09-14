"""WSGI config for educore project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'educore.settings.local')
application = get_wsgi_application()
