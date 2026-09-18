"""Production settings for EduCore."""
import os
from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'app.educore.id').split(',')

EDUCORE_PUBLIC_BASE_URL = os.environ.get('EDUCORE_PUBLIC_BASE_URL', 'https://app.educore.id')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Enforce secret key from environment
if SECRET_KEY == 'django-insecure-educore-dev-key-change-in-production':
    raise ValueError("DJANGO_SECRET_KEY environment variable must be set in production.")

# spec/01 §7, ARC-013: production has exactly one dedicated cron host.
EDUCORE_CRON_HOST_ENFORCED = True

# GCS objects live under /PRD/ in production, vs. /STG/ for local + staging (base.py).
GCS_PATH_PREFIX = 'PRD'
