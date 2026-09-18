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
    'apps.partners.apps.PartnersConfig',
    'apps.marketing.apps.MarketingConfig',
    'apps.calendar_sync.apps.CalendarSyncConfig',
    'apps.status.apps.StatusConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Wraps almost the entire stack so its duration_ms covers the full
    # request, and assigns request_id early enough for later middleware/error
    # handlers to reference it via request.request_id.
    'educore.middleware.logging.RequestLoggingMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'educore.middleware.i18n.ForceDefaultLanguageMiddleware',
    'django.middleware.locale.LocaleMiddleware',
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
                'django.template.context_processors.i18n',
                'educore.context_processors.static_version',
                'apps.identity.context_processors.console_nav',
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

# Internationalization (spec/01 §2, spec/appendix §2.10: id-ID first).
# Indonesian is the source language everywhere; English is a translation,
# switchable only where a LocaleMiddleware-based UI exposes it (currently
# the public marketing site) via django.views.i18n.set_language.
LANGUAGE_CODE = 'id'
LANGUAGES = [
    ('id', 'Bahasa Indonesia'),
    ('en', 'English'),
]
TIME_ZONE = 'Asia/Jakarta'
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / 'locale']

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'frontend' / 'collected_static'
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'static'] if (BASE_DIR / 'frontend' / 'static').exists() else []

# Cache-busting query string for hand-linked static assets (base.html's <link>
# tags aren't run through {% static %}/ManifestStaticFilesStorage). Without
# this, a deploy that changes site.css/app.css keeps serving the previous
# version from the browser and CDN edge cache until it naturally expires.
# Derived from the deployed commit so it changes exactly once per deploy;
# falls back to process start time in an environment with no .git (e.g. a
# tarball deploy) so it still busts cache across restarts.
def _static_version():
    import subprocess
    try:
        return subprocess.run(
            ['git', 'rev-parse', '--short=12', 'HEAD'],
            cwd=BASE_DIR, capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        import time
        return str(int(time.time()))


STATIC_VERSION = _static_version()

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

# Web console session-auth login redirect target (spec: web-console-nav-and-landing).
# LoginRequiredMixin-based views (e.g. apps.core.views.ComingSoonView) redirect
# anonymous users here instead of Django's default '/accounts/login/'.
LOGIN_URL = '/web/login/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework configuration (spec/01 §8.1, spec/16 §6)
REST_FRAMEWORK = {
    'COERCE_DECIMAL_TO_STRING': True,  # CUR-026: serialize MoneyField/Decimal as string
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardCursorPagination',
    # Global handler (apps/partners/errors.py): partner views render RFC 9457
    # problem+json (spec/18 §5); any Throttled exception renders the platform's
    # standard 429 contract (apps.core.throttling); every other app/exception
    # is delegated to DRF's own default handler unchanged.
    'EXCEPTION_HANDLER': 'apps.partners.errors._drf_problem_handler',
    'PAGE_SIZE': 50,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.identity.authentication.EduCoreJWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    # Per-view scoped rate limits (apps.core.throttling) for the endpoints named
    # in the Production Readiness rate-limiting task — NOT a blanket
    # DEFAULT_THROTTLE_CLASSES (see apps/core/throttling.py's module docstring
    # for why a platform-wide anonymous throttle was deliberately not added).
    'DEFAULT_THROTTLE_RATES': {
        'auth_login': '10/min',
        'otp_request_ip': '10/hour',
        'payment_webhook': '100/min',
        'status_subscribe': '10/hour',
    },
}

# Xendit payment gateway (spec/06 §4 FIN-011/FIN-012) — launch VA provider per the
# [Open Decision] Virtual Account Provider Integration Strategy research (2026-09-15):
# Xendit's Fixed Virtual Account product is the only one of Midtrans/Xendit that
# supports a genuinely stable, reusable per-student VA.
XENDIT_API_KEY = os.environ.get('XENDIT_API_KEY', '')
XENDIT_CALLBACK_TOKEN = os.environ.get('XENDIT_CALLBACK_TOKEN', 'sandbox-token')
XENDIT_BASE_URL = os.environ.get('XENDIT_BASE_URL', 'https://api.xendit.co')

# Partner & Vendor Integration API (spec/18). Fernet key for partner secret
# encryption at rest — production MUST set an explicit 32-byte urlsafe-b64
# key; the SECRET_KEY-derived fallback exists only for dev/test parity.
EDUCORE_PARTNER_FERNET_KEY = os.environ.get('EDUCORE_PARTNER_FERNET_KEY', '')
# Days a payroll run stays acknowledgeable after approval (PVA-032 window).
EDUCORE_PARTNER_PAYROLL_ACK_DAYS = int(os.environ.get('EDUCORE_PARTNER_PAYROLL_ACK_DAYS', '14'))

# Biometric template encryption at rest (spec/14 CMP-003, HW-010) — own Fernet
# key, same pattern as EDUCORE_PARTNER_FERNET_KEY. Production MUST set an
# explicit key; the SECRET_KEY-derived fallback is dev/test-only.
EDUCORE_BIOMETRIC_FERNET_KEY = os.environ.get('EDUCORE_BIOMETRIC_FERNET_KEY', '')

# Clinic visit note encryption at rest (spec/10 LIF-007) — own Fernet key, same
# pattern as EDUCORE_BIOMETRIC_FERNET_KEY. Production MUST set an explicit key;
# the SECRET_KEY-derived fallback is dev/test-only.
EDUCORE_CLINIC_FERNET_KEY = os.environ.get('EDUCORE_CLINIC_FERNET_KEY', '')

# Counselling (BK) session note encryption at rest (spec/10 §5, LIF-015) — own
# Fernet key, same pattern as EDUCORE_BIOMETRIC_FERNET_KEY. Production MUST
# set an explicit key; the SECRET_KEY-derived fallback is dev/test-only.
EDUCORE_COUNSELLING_FERNET_KEY = os.environ.get('EDUCORE_COUNSELLING_FERNET_KEY', '')

# Status page subscriber incident email (Notion: "Status page: subscriber
# email delivery") — Django's built-in SMTP backend, not a provider SDK.
# Defaults to the console backend so local/dev/test never attempts a real
# SMTP connection unless EMAIL_BACKEND is explicitly overridden.
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() == 'true'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'status@educore.id')

# Public base URL for building absolute links (e.g. the status-email
# unsubscribe link) from contexts with no `request` object, such as the
# drain_tasks cron. apps.marketing uses request.build_absolute_uri() where a
# request is available; this is the equivalent for cron/task contexts.
EDUCORE_PUBLIC_BASE_URL = os.environ.get('EDUCORE_PUBLIC_BASE_URL', 'http://localhost:8000')


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

# Calendar sync — Google Workspace / Microsoft 365 (spec/14 §6, second purpose).
# Deliberately separate OAuth clients from the SSO ones: calendar needs
# authorization-code flow with offline refresh tokens and different scopes.
# An empty CLIENT_ID disables that provider's calendar sync (503 on connect).
GOOGLE_CALENDAR_CLIENT_ID = os.environ.get('GOOGLE_CALENDAR_CLIENT_ID', '')
GOOGLE_CALENDAR_CLIENT_SECRET = os.environ.get('GOOGLE_CALENDAR_CLIENT_SECRET', '')
MICROSOFT_CALENDAR_CLIENT_ID = os.environ.get('MICROSOFT_CALENDAR_CLIENT_ID', '')
MICROSOFT_CALENDAR_CLIENT_SECRET = os.environ.get('MICROSOFT_CALENDAR_CLIENT_SECRET', '')
MICROSOFT_CALENDAR_TENANT_ID = os.environ.get('MICROSOFT_CALENDAR_TENANT_ID', 'common')
# One redirect URI is whitelisted in both provider consoles; the provider
# rides inside the HMAC-signed state.
CALENDAR_SYNC_REDIRECT_URI = os.environ.get(
    'CALENDAR_SYNC_REDIRECT_URI', 'http://localhost:8000/web/auth/calendar/callback/')
# OAuth tokens for calendar connections are encrypted at rest with their own
# Fernet key (same pattern as EDUCORE_PARTNER_FERNET_KEY; derived from
# SECRET_KEY when unset — production must set an explicit key).
EDUCORE_CALENDAR_FERNET_KEY = os.environ.get('EDUCORE_CALENDAR_FERNET_KEY', '')

# Structured JSON logging + universal PII scrubbing (AGENTS Red Line #5:
# NIK/NISN/phone/passwords/tokens must never reach raw logs). Every handler
# runs PIIScrubbingFilter before JSONFormatter renders the line — see
# apps/core/logging.py for the scrubbing engine shared with Sentry below.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'pii_scrub': {'()': 'apps.core.logging.PIIScrubbingFilter'},
        'require_debug_false': {'()': 'django.utils.log.RequireDebugFalse'},
    },
    'formatters': {
        'json': {'()': 'apps.core.logging.JSONFormatter'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['pii_scrub'],
            'formatter': 'json',
        },
        # Replaces Django's own LOGGING dict wholesale, so its default
        # mail_admins-on-unhandled-500 wiring has to be re-declared here or
        # it silently disappears the moment ADMINS is ever configured.
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false', 'pii_scrub'],
            'class': 'django.utils.log.AdminEmailHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'mail_admins'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

# Sentry error tracking — optional. sentry_sdk is NOT a hard dependency
# (absent from requirements.txt), so this stays a no-op unless both SENTRY_DSN
# is set AND sentry_sdk is installed. before_send reuses the exact same
# scrub_value() engine as application logs so Sentry never receives raw PII.
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
if SENTRY_DSN:
    try:
        import sentry_sdk
        from apps.core.logging import sentry_before_send
        sentry_sdk.init(dsn=SENTRY_DSN, before_send=sentry_before_send)
    except ImportError:
        pass
