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

# Load environment variables from .env if present
_env_file = BASE_DIR / '.env'
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        with open(_env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                os.environ.setdefault(k, v)

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
    'apps.campus.apps.CampusConfig',
    'apps.compliance.apps.ComplianceConfig',
    'apps.marketing.apps.MarketingConfig',
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
TEST_DB_NAME = os.environ.get('EDUCORE_TEST_DB_NAME', 'test_educore')

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
        'TEST': {
            'NAME': TEST_DB_NAME,
            'CHARSET': 'utf8mb4',
            'COLLATION': 'utf8mb4_0900_ai_ci',
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
LOCALE_PATHS = [BASE_DIR / 'locale']

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'frontend' / 'collected_static'
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'static'] if (BASE_DIR / 'frontend' / 'static').exists() else []

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# GCS file storage (ARC-026/ARC-030: every uploaded object is a core.StoredFile row,
# uploaded client-direct-to-GCS via signed URL, never written to server disk).
# GCS_CREDENTIALS_PATH points at a service-account JSON kept OUTSIDE the repo (.env,
# gitignored) — never commit that file. GCS_PATH_PREFIX namespaces objects per
# environment within the one shared bucket; overridden to 'PRD' in production.py so
# local + staging both default to 'STG' here without repeating the env-detection logic.
GCS_BUCKET_NAME = os.environ.get('GCS_BUCKET_NAME', 'educore-e46aa.firebasestorage.app')
GCS_CREDENTIALS_PATH = os.environ.get('GCS_CREDENTIALS_PATH', '')
GCS_PATH_PREFIX = 'STG'

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
        'apps.identity.authentication.EduCoreJWTAuthentication',
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

# Single cron host enforcement (spec/01 §7, ARC-013): production runs exactly one
# dedicated cron host, identified by EDUCORE_CRON_HOST=1. Off by default — local and
# staging are both single-VM per spec/01 §7's own environment table, where the
# constraint is trivially satisfied. See apps.core.management.base.CronHostCommand.
EDUCORE_CRON_HOST_ENFORCED = False

# Third-Party SSO — Google Workspace / Microsoft 365 (spec/14 §6, TASK-036).
# Set these via .env or environment variables in production.
# For Microsoft, set SOCIAL_AUTH_MICROSOFT_TENANT_ID to your tenant ID or 'common'.
SOCIAL_AUTH_GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
SOCIAL_AUTH_MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_OAUTH_CLIENT_ID', '')
SOCIAL_AUTH_MICROSOFT_TENANT_ID = os.environ.get('MICROSOFT_OAUTH_TENANT_ID', 'common')
