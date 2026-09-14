"""Local development settings for EduCore."""
import os
from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ['*']

# Optional fallback to SQLite strictly for lightweight developer testing without a running MySQL instance
if os.environ.get('EDUCORE_USE_SQLITE') == '1':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',  # noqa: F405
        }
    }
