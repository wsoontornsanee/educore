"""Staging settings for EduCore."""
import os
from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'staging.educore.id').split(',')

EDUCORE_PUBLIC_BASE_URL = os.environ.get('EDUCORE_PUBLIC_BASE_URL', 'https://staging.educore.id')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
