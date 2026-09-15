"""Django base settings for EduCore project.

Adheres strictly to spec/01-platform-architecture.md:
- One Django monolith
- MySQL 8 only
- Cron-driven background jobs
- 3-layer multi-tenancy
- id-ID default locale
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-educore-dev-key-change-in-production')

DEBUG = False

ALLOWED_HOSTS = []

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party dependencies (ARC-Stack pure-python)
    'rest_framework',
    'rest_framework_simplejwt',
    'django_htmx',

    # EduCore Apps
    'apps.core.apps.CoreConfig',
    'apps.identity.apps.IdentityConfig',
    'apps.foundation.apps.FoundationConfig',
    'apps.hardware.apps.HardwareConfig',
    'apps.attendance.apps.AttendanceConfig',
    'apps.notifications.apps.NotificationsConfig',
    'apps.finance.apps.FinanceConfig',
    'apps.academic.apps.AcademicConfig',
    'apps.wallet.apps.WalletConfig',
    'apps.reporting.apps.ReportingConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',

    # Core Custom Middleware (spec/01 §3, §4, §8.5)
    'educore.middleware.tenancy.TenancyMiddleware',
    'educore.middleware.audit.AuditMiddleware',
    'educore.middleware.timezone.TimezoneMiddleware',
]

ROOT_URLCONF = 'educore.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'educore.wsgi.application'

# Database: MySQL 8 is the required default engine (spec/01 §1.2)
DB_NAME = os.environ.get('EDUCORE_DB_NAME', 'educore')
DB_USER = os.environ.get('EDUCORE_DB_USER', 'root')
DB_PASSWORD = os.environ.get('EDUCORE_DB_PASSWORD', '')
DB_HOST = os.environ.get('EDUCORE_DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('EDUCORE_DB_PORT', '3306')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': DB_NAME,
        'USER': DB_USER,
        'PASSWORD': DB_PASSWORD,
        'HOST': DB_HOST,
        'PORT': DB_PORT,
        'OPTIONS': {
            'charset': 'utf8mb4',
            'collation': 'utf8mb4_0900_ai_ci',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
        },
    }
}

# Cache: Django DatabaseCache (spec/01 §2, §3 - No Redis)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization (spec/01 §2, spec/appendix §2.10: id-ID first)
LANGUAGE_CODE = 'id'
TIME_ZONE = 'Asia/Jakarta'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'frontend' / 'collected_static'
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'static'] if (BASE_DIR / 'frontend' / 'static').exists() else []

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Custom User Model & Authentication (spec/02 §2, §3)
AUTH_USER_MODEL = 'identity.User'

AUTHENTICATION_BACKENDS = [
    'apps.identity.backends.DualAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework configuration (spec/01 §8.1, spec/16 §6)
REST_FRAMEWORK = {
    'COERCE_DECIMAL_TO_STRING': True,  # CUR-026: serialize MoneyField/Decimal as string
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardCursorPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
}

# Xendit payment gateway (spec/06 §4 FIN-011/FIN-012) — launch VA provider per the
# [Open Decision] Virtual Account Provider Integration Strategy research (2026-09-15):
# Xendit's Fixed Virtual Account product is the only one of Midtrans/Xendit that
# supports a genuinely stable, reusable per-student VA.
XENDIT_API_KEY = os.environ.get('XENDIT_API_KEY', '')
XENDIT_CALLBACK_TOKEN = os.environ.get('XENDIT_CALLBACK_TOKEN', 'sandbox-token')
XENDIT_BASE_URL = os.environ.get('XENDIT_BASE_URL', 'https://api.xendit.co')
