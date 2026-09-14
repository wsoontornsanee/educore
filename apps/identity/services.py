"""Authentication and OTP services (spec/02 §3)."""
import secrets
from datetime import timedelta
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import OTPChallenge, Person, User

def normalize_phone_e164(phone: str) -> str:
    """Normalize phone number to Indonesian E.164 (+62...)."""
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
    if cleaned.startswith('08'):
        return '+62' + cleaned[1:]
    if cleaned.startswith('62'):
        return '+' + cleaned
    if cleaned.startswith('+62'):
        return cleaned
    raise ValidationError(f"Format nomor telepon tidak valid: {phone}. Gunakan format Indonesia (+62... atau 08...).")

def request_phone_otp(phone: str) -> tuple[OTPChallenge, str]:
    """Generate and store a 6-digit OTP challenge (IAM-002, IAM-003).
    
    Enforces:
    - 6 digits
    - 5-minute TTL
    - Max 3 sends per phone per 15 minutes
    """
    phone_e164 = normalize_phone_e164(phone)
    now = timezone.now()
    window_start = now - timedelta(minutes=15)

    # Rate limiting: max 3 sends per 15 min
    recent_sends = OTPChallenge.objects.filter(
        phone_e164=phone_e164,
        created_at__gte=window_start
    ).count()

    if recent_sends >= 3:
        raise ValidationError("Batas pengiriman OTP terlampaui. Maksimal 3 kali pengiriman dalam 15 menit.")

    # Cryptographically secure 6-digit code
    raw_code = f"{secrets.randbelow(900000) + 100000}"
    code_hash = make_password(raw_code)
    expires_at = now + timedelta(minutes=5)

    challenge = OTPChallenge.objects.create(
        phone_e164=phone_e164,
        code_hash=code_hash,
        expires_at=expires_at,
        attempts=0,
        max_attempts=5,
    )

    return challenge, raw_code

def verify_phone_otp(challenge_id: int, code: str) -> tuple[bool, str]:
    """Verify submitted OTP code against salted hash (IAM-003)."""
    try:
        challenge = OTPChallenge.objects.get(id=challenge_id)
    except OTPChallenge.DoesNotExist:
        return False, "Sesi OTP tidak ditemukan."

    if challenge.is_expired:
        return False, "Kode OTP telah kedaluwarsa (berlaku 5 menit)."

    if challenge.verified_at is not None:
        return False, "Kode OTP ini sudah digunakan sebelumnya."

    if challenge.attempts >= challenge.max_attempts:
        return False, "Batas percobaan OTP terlampaui (maksimal 5 kali)."

    challenge.attempts += 1

    if check_password(code, challenge.code_hash):
        challenge.verified_at = timezone.now()
        challenge.save(update_fields=['attempts', 'verified_at'])
        return True, "Verifikasi OTP berhasil."
    else:
        challenge.save(update_fields=['attempts'])
        remaining = challenge.max_attempts - challenge.attempts
        return False, f"Kode OTP salah. Sisa percobaan: {remaining}."

def create_user_with_person(foundation_id: int, full_name: str, phone: str, email: str = None, password: str = None, nik: str = None, dob=None, gender: str = '', address: str = '') -> tuple[User, Person]:
    """Atomically create a User and their isolated Person PII vault record (spec/02 §2)."""
    phone_e164 = normalize_phone_e164(phone)

    with transaction.atomic():
        person = Person.objects.create(
            foundation_id=foundation_id,
            full_name=full_name,
            nik=nik,
            dob=dob,
            gender=gender,
            address=address,
        )

        user = User.objects.create_user(
            phone_e164=phone_e164,
            email=email,
            password=password,
            foundation_id=foundation_id,
            full_name=full_name,
            status=User.STATUS_ACTIVE,
        )

        return user, person
