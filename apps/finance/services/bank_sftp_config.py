from apps.core.services import audit
from apps.finance.models import BankSftpConfig, BankStatementFileFormat


def set_bank_sftp_config(
    school,
    bank_code: str,
    host: str,
    port: int = 22,
    username: str = '',
    remote_directory: str = '/',
    file_format: str = BankStatementFileFormat.MT940,
    is_active: bool = True,
) -> BankSftpConfig:
    """CMP-024/CMP-026: configure connection metadata for automated SFTP pull.

    Never accepts or stores a password/private key — see
    apps.finance.services.bank_sftp_pull's module docstring for why.
    """
    bank_code = bank_code.upper()
    config, _created = BankSftpConfig.objects.update_or_create(
        foundation_id=school.foundation_id,
        school=school,
        bank_code=bank_code,
        defaults={
            'host': host,
            'port': port,
            'username': username,
            'remote_directory': remote_directory,
            'file_format': file_format,
            'is_active': is_active,
        },
    )
    audit(
        action='finance.bank_sftp_config.set',
        entity_type='BankSftpConfig',
        entity_id=config.id,
        foundation_id=school.foundation_id,
        school_id=school.id,
        diff={'bank_code': bank_code, 'host': host, 'port': port, 'is_active': is_active},
    )
    return config


def get_bank_sftp_configs(school):
    return BankSftpConfig.objects.filter(school=school)
