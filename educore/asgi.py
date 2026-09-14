"""ASGI config for educore project."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'educore.settings.local')
application = get_asgi_application()
