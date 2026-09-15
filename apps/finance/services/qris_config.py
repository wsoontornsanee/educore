from pathlib import Path
from uuid import uuid4

from django.conf import settings

from apps.core.services import audit
from apps.finance.models import SchoolQrisConfig

# Own constants, not shared with apps.academic's submission limits — each app in
# this codebase owns its own validation constants rather than cross-importing them.
ALLOWED_PROOF_CONTENT_TYPES = {'application/pdf', 'image/jpeg', 'image/png'}
MAX_PROOF_FILE_SIZE = 20 * 1024 * 1024


class InvalidProofFileError(ValueError):
    pass


def set_school_qris_config(school, qris_image=None, qris_payload='', is_active=True) -> SchoolQrisConfig:
    """FIN-010: configure a school's static QRIS — the kind printed/exported
    directly from the school's own bank, not a per-transaction gateway QRIS.

    Passing qris_image=None keeps whatever image was already configured (a caller
    updating only qris_payload/is_active shouldn't have to re-upload the image).
    """
    existing = SchoolQrisConfig.objects.filter(school=school).first()
    qris_image_key = existing.qris_image_key if existing else ''

    if qris_image is not None:
        if qris_image.content_type not in ALLOWED_PROOF_CONTENT_TYPES:
            raise InvalidProofFileError(f"UNSUPPORTED_FILE_TYPE: '{qris_image.content_type}' is not accepted.")
        if qris_image.size > MAX_PROOF_FILE_SIZE:
            raise InvalidProofFileError(f"FILE_TOO_LARGE: '{qris_image.name}' exceeds 20MB.")

        output_dir = Path(settings.MEDIA_ROOT) / 'qris_config' / str(school.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        qris_image_key = f"qris_config/{school.id}/{uuid4().hex}_{qris_image.name}"
        with open(Path(settings.MEDIA_ROOT) / qris_image_key, 'wb') as out:
            for chunk in qris_image.chunks():
                out.write(chunk)

    config, _created = SchoolQrisConfig.objects.update_or_create(
        foundation_id=school.foundation_id,
        school=school,
        defaults={
            'qris_image_key': qris_image_key,
            'qris_payload': qris_payload,
            'is_active': is_active,
        },
    )
    audit(
        action='finance.qris_config.set',
        entity_type='SchoolQrisConfig',
        entity_id=config.id,
        foundation_id=school.foundation_id,
        school_id=school.id,
        diff={'is_active': is_active, 'has_image': bool(config.qris_image_key), 'has_payload': bool(qris_payload)},
    )
    return config


def get_school_qris_config(school) -> SchoolQrisConfig:
    return SchoolQrisConfig.objects.filter(school=school).first()


def store_payment_proof_file(school, uploaded_file) -> dict:
    """FIN-018: validate and persist one uploaded payment proof (manual transfer or
    static QRIS receipt), returning the {key, filename, size, content_type} dict
    submit_manual_transfer's `proof_file` field expects.

    Same raw-pathlib/MEDIA_ROOT convention as store_homework_submission_file — no
    Django storage abstraction is used anywhere in this codebase.
    """
    if uploaded_file.content_type not in ALLOWED_PROOF_CONTENT_TYPES:
        raise InvalidProofFileError(f"UNSUPPORTED_FILE_TYPE: '{uploaded_file.content_type}' is not accepted.")
    if uploaded_file.size > MAX_PROOF_FILE_SIZE:
        raise InvalidProofFileError(f"FILE_TOO_LARGE: '{uploaded_file.name}' exceeds 20MB.")

    output_dir = Path(settings.MEDIA_ROOT) / 'payment_proofs' / str(school.id)
    output_dir.mkdir(parents=True, exist_ok=True)

    key = f"payment_proofs/{school.id}/{uuid4().hex}_{uploaded_file.name}"
    with open(Path(settings.MEDIA_ROOT) / key, 'wb') as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)

    return {
        'key': key,
        'filename': uploaded_file.name,
        'size': uploaded_file.size,
        'content_type': uploaded_file.content_type,
    }
