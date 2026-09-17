"""
Third-Party SSO — Google Workspace / Microsoft 365 Integration (spec/14 §6, TASK-036).

Provides token verification (JWKS-based) and account login/link services for
Staff SSO. The frontend obtains an ID token via the provider's SDK and sends
it to the backend; this module verifies the token, resolves the user, and
returns an EduCore JWT.

Token verification uses PyJWT (already a dependency via djangorestframework-simplejwt)
and the provider's public JWKS endpoint — no additional libraries required.
"""
import json
import logging

import jwt
import requests
from django.conf import settings
from django.utils import timezone

from apps.identity.models import SocialLogin, User

logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────

class SocialAuthError(Exception):
    """Base error for SSO failures."""


class TokenVerificationError(SocialAuthError):
    """ID token is invalid, expired, or has wrong audience."""


class AccountNotLinkedError(SocialAuthError):
    """No EduCore account is linked to this provider identity."""


class AccountAlreadyLinkedError(SocialAuthError):
    """This provider account is already linked to a different user."""


# ── Provider JWKS helpers ──────────────────────────────────────────

GOOGLE_JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
GOOGLE_ISSUERS = ['accounts.google.com', 'https://accounts.google.com']

_jwks_cache: dict[str, dict] = {}
_jwks_cache_timestamps: dict[str, float] = {}
JWKS_CACHE_TTL = 3600  # 1 hour


def _fetch_jwks(url: str) -> dict:
    """Fetch and cache JWKS from a provider URL."""
    now = timezone.now().timestamp()
    cached_at = _jwks_cache_timestamps.get(url, 0)
    if url in _jwks_cache and (now - cached_at) < JWKS_CACHE_TTL:
        return _jwks_cache[url]

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _jwks_cache[url] = data
    _jwks_cache_timestamps[url] = now
    return data


def _build_rsa_key_map(jwks: dict) -> dict[str, object]:
    """Build {kid: RSAAlgorithm public key} from JWKS."""
    key_map = {}
    for key_data in jwks.get('keys', []):
        kid = key_data.get('kid')
        if kid:
            key_map[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
    return key_map


# ── Token verification ──────────────────────────────────────────────

def verify_google_token(id_token: str) -> dict:
    """Verify a Google ID token and return its claims.

    Raises TokenVerificationError on invalid signature, expired token,
    wrong audience, or unverified issuer.
    """
    client_id = settings.SOCIAL_AUTH_GOOGLE_CLIENT_ID
    if not client_id:
        raise TokenVerificationError("Google SSO is not configured (GOOGLE_OAUTH_CLIENT_ID is empty).")

    try:
        jwks = _fetch_jwks(GOOGLE_JWKS_URL)
        key_map = _build_rsa_key_map(jwks)

        header = jwt.get_unverified_header(id_token)
        kid = header.get('kid')
        public_key = key_map.get(kid)
        if not public_key:
            raise TokenVerificationError(f"Google JWKS does not contain key id: {kid}")

        claims = jwt.decode(
            id_token, public_key,
            algorithms=['RS256'],
            audience=client_id,
            issuer=GOOGLE_ISSUERS,
            options={'require': ['sub', 'email']},
        )
        return claims
    except jwt.ExpiredSignatureError:
        raise TokenVerificationError("Google ID token has expired.")
    except jwt.InvalidAudienceError:
        raise TokenVerificationError("Google ID token audience does not match configured client ID.")
    except jwt.InvalidIssuerError:
        raise TokenVerificationError("Google ID token issuer is not recognized.")
    except jwt.PyJWTError as e:
        raise TokenVerificationError(f"Google ID token verification failed: {e}")
    except requests.RequestException as e:
        raise TokenVerificationError(f"Failed to fetch Google JWKS: {e}")


def verify_microsoft_token(id_token: str, tenant_id: str | None = None) -> dict:
    """Verify a Microsoft 365 ID token and return its claims.

    `tenant_id` pins verification to a specific Microsoft Entra tenant
    (per-foundation config); when omitted, the global
    SOCIAL_AUTH_MICROSOFT_TENANT_ID setting (default 'common') is used.
    Raises TokenVerificationError on failure.
    """
    client_id = settings.SOCIAL_AUTH_MICROSOFT_CLIENT_ID
    tenant_id = (tenant_id or '').strip() or settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID
    if not client_id:
        raise TokenVerificationError("Microsoft SSO is not configured (MICROSOFT_OAUTH_CLIENT_ID is empty).")

    jwks_url = f'https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys'
    issuer = f'https://login.microsoftonline.com/{tenant_id}/v2.0'

    try:
        jwks = _fetch_jwks(jwks_url)
        key_map = _build_rsa_key_map(jwks)

        header = jwt.get_unverified_header(id_token)
        kid = header.get('kid')
        public_key = key_map.get(kid)
        if not public_key:
            raise TokenVerificationError(f"Microsoft JWKS does not contain key id: {kid}")

        claims = jwt.decode(
            id_token, public_key,
            algorithms=['RS256'],
            audience=client_id,
            issuer=issuer,
            options={'require': ['oid', 'email']},
        )
        # Defense-in-depth for tenant pinning: the issuer check already pins the
        # tenant, and the `tid` directory claim (present in Microsoft ID tokens)
        # must agree with it when provided.
        token_tid = claims.get('tid')
        if token_tid and token_tid != tenant_id:
            raise TokenVerificationError(
                "Microsoft ID token was issued for a different tenant than the configured one."
            )
        return claims
    except jwt.ExpiredSignatureError:
        raise TokenVerificationError("Microsoft ID token has expired.")
    except jwt.InvalidAudienceError:
        raise TokenVerificationError("Microsoft ID token audience does not match configured client ID.")
    except jwt.InvalidIssuerError:
        raise TokenVerificationError("Microsoft ID token issuer is not recognized.")
    except jwt.PyJWTError as e:
        raise TokenVerificationError(f"Microsoft ID token verification failed: {e}")
    except requests.RequestException as e:
        raise TokenVerificationError(f"Failed to fetch Microsoft JWKS: {e}")


VERIFY_FUNCTIONS = {
    SocialLogin.PROVIDER_GOOGLE: verify_google_token,
    SocialLogin.PROVIDER_MICROSOFT: verify_microsoft_token,
}


def resolve_microsoft_tenant_id(foundation_id: int | None) -> str:
    """Resolve the Microsoft tenant ID for a foundation.

    A foundation with an active MicrosoftTenantConfig row is pinned to its own
    Entra tenant; otherwise the global setting fallback applies.
    """
    if foundation_id:
        from apps.identity.models import MicrosoftTenantConfig
        config = MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).first()
        if config:
            return config.tenant_id
    return settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID


def verify_id_token(provider: str, id_token: str, tenant_id: str | None = None) -> dict:
    """Verify an ID token for the given provider and return its claims.

    `tenant_id` only applies to Microsoft tokens (per-foundation Entra pinning).

    Extracts the provider-specific unique user ID (sub for Google, oid for
    Microsoft) and the email claim.
    """
    verify_fn = VERIFY_FUNCTIONS.get(provider)
    if not verify_fn:
        raise SocialAuthError(f"Unsupported SSO provider: {provider}")
    if provider == SocialLogin.PROVIDER_MICROSOFT:
        return verify_fn(id_token, tenant_id=tenant_id)
    return verify_fn(id_token)


def extract_identity(provider: str, claims: dict) -> tuple[str, str]:
    """Extract (provider_user_id, email) from verified token claims."""
    if provider == SocialLogin.PROVIDER_GOOGLE:
        return claims['sub'], claims.get('email', '')
    elif provider == SocialLogin.PROVIDER_MICROSOFT:
        return claims['oid'], claims.get('email', '')
    raise SocialAuthError(f"Unsupported provider: {provider}")


# ── Account helpers ─────────────────────────────────────────────────

STAFF_ROLES = {'foundation_admin', 'school_admin', 'finance_officer', 'teacher', 'counsellor'}


def _user_has_staff_role(user: User, foundation_id: int) -> bool:
    """Check if a user has any staff-level role in the given foundation."""
    from apps.identity.models import RoleAssignment
    return RoleAssignment.all_tenants.filter(
        user=user,
        foundation_id=foundation_id,
        role__in=STAFF_ROLES,
        deleted_at__isnull=True,
    ).exists()


# ── Public API ──────────────────────────────────────────────────────

def social_login(provider: str, id_token: str) -> tuple[User, dict]:
    """Verify an ID token and return (user, claims) for an SSO login.

    Resolution order:
    1. Look up SocialLogin by (provider, provider_user_id).
    2. If not found, look up User by email with a staff role and auto-link.
    3. If neither, raise AccountNotLinkedError.

    Returns (user, claims) on success.
    """
    from educore.middleware.tenancy import get_current_foundation_id

    foundation_id = get_current_foundation_id()
    if not foundation_id:
        raise SocialAuthError("Foundation context is required for SSO login.")

    tenant_id = resolve_microsoft_tenant_id(foundation_id) \
        if provider == SocialLogin.PROVIDER_MICROSOFT else None
    claims = verify_id_token(provider, id_token, tenant_id=tenant_id)
    provider_user_id, email = extract_identity(provider, claims)

    # Step 1: Look up existing SocialLogin
    social = SocialLogin.all_tenants.filter(
        foundation_id=foundation_id,
        provider=provider,
        provider_user_id=provider_user_id,
        deleted_at__isnull=True,
    ).select_related('user').first()

    if social:
        return social.user, claims

    # Step 2: Auto-link by email if user has a staff role
    if email:
        user = User.all_tenants.filter(
            foundation_id=foundation_id,
            email=email,
            is_active=True,
        ).first()
        if user and _user_has_staff_role(user, foundation_id):
            SocialLogin.all_tenants.create(
                foundation_id=foundation_id,
                user=user,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
            )
            logger.info("Auto-linked SSO account for user %s (%s %s)", user.id, provider, provider_user_id)
            return user, claims

    # Step 3: Not found
    raise AccountNotLinkedError(
        "Akun EduCore tidak ditemukan untuk login SSO ini. "
        "Hubungi admin sekolah untuk menghubungkan akun Anda."
    )


def social_link(user: User, provider: str, id_token: str) -> SocialLogin:
    """Link the authenticated user's account to an SSO provider.

    Raises AccountAlreadyLinkedError if another user already linked this
    provider identity.
    """
    from educore.middleware.tenancy import get_current_foundation_id

    foundation_id = get_current_foundation_id()
    if not foundation_id:
        raise SocialAuthError("Foundation context is required.")

    tenant_id = resolve_microsoft_tenant_id(foundation_id) \
        if provider == SocialLogin.PROVIDER_MICROSOFT else None
    claims = verify_id_token(provider, id_token, tenant_id=tenant_id)
    provider_user_id, email = extract_identity(provider, claims)

    # Check no other user has linked this provider identity
    existing = SocialLogin.all_tenants.filter(
        foundation_id=foundation_id,
        provider=provider,
        provider_user_id=provider_user_id,
        deleted_at__isnull=True,
    ).exclude(user=user).first()

    if existing:
        raise AccountAlreadyLinkedError(
            "Akun {provider} ini sudah terhubung dengan pengguna lain. "
            "Hubungi admin sekolah jika ini adalah kesalahan."
        )

    # Soft-delete any existing link for this user + provider (re-link)
    SocialLogin.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        provider=provider,
        deleted_at__isnull=True,
    ).update(deleted_at=timezone.now())

    social = SocialLogin.all_tenants.create(
        foundation_id=foundation_id,
        user=user,
        provider=provider,
        provider_user_id=provider_user_id,
        email=email,
    )
    logger.info("Linked SSO account for user %s (%s %s)", user.id, provider, provider_user_id)
    return social


def social_unlink(user: User, provider: str) -> None:
    """Unlink the authenticated user's account from an SSO provider (soft-delete)."""
    from educore.middleware.tenancy import get_current_foundation_id

    foundation_id = get_current_foundation_id()
    if not foundation_id:
        raise SocialAuthError("Foundation context is required.")

    count = SocialLogin.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        provider=provider,
        deleted_at__isnull=True,
    ).update(deleted_at=timezone.now())

    if count:
        logger.info("Unlinked SSO account for user %s (%s)", user.id, provider)
