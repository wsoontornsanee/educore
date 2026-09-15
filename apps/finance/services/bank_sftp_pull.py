"""Automated SFTP pull of daily bank statement files (spec/14 CMP-024, CMP-026).

This is a deliberate STUB, not a working SFTP client — same spirit as
MidtransPaymentProvider.fetch_settlement's documented gap for CMP-024. Two
things don't exist yet in this codebase and aren't solved here:
1. An SFTP client dependency (e.g. paramiko) — not added speculatively.
2. Per-tenant secrets storage for bank credentials — BankSftpConfig stores
   only connection metadata (host/port/username/directory/format), never a
   password or private key.

fetch_bank_statement_via_sftp() raises BankSftpPullError immediately rather
than returning an empty list, because an empty list would look exactly like
"pulled the file, found zero settlements today" — a misleading false
success for a scheduled reconciliation job. Failing loud, with a clear
message, is the correct default until a real client is implemented; see the
[Open Item] this stub is tracked under for the follow-up.

Once implemented, fetch_bank_statement_via_sftp should return the raw file
bytes/text, to be handed to
apps.finance.services.bank_statement_parser.parse_bank_statement and then
apps.finance.services.reconciliation.reconcile_bank_statement_file's
record-processing path — the parser and reconciliation machinery (TASK-062)
already exist and need no changes for a real client to plug into this stub.
"""
import os


class BankSftpPullError(Exception):
    """Raised when a scheduled SFTP pull attempt cannot complete."""
    pass


def fetch_bank_statement_via_sftp(config, settlement_date):
    """Connect to config.host and pull the statement file for settlement_date.

    NOT YET IMPLEMENTED. Always raises BankSftpPullError. Kept as a real
    function (not a TODO comment) so pull_bank_statements has a single,
    named call site to swap a real implementation into later.
    """
    credential_env_var = f'BANK_SFTP_KEY_{config.id}'
    if not os.environ.get(credential_env_var):
        raise BankSftpPullError(
            f"No SFTP client is implemented yet for bank_code={config.bank_code} "
            f"(config id={config.id}). This is a deliberate stub (CMP-024/CMP-026 "
            f"tracked as a follow-up Open Item) — use the manual upload endpoint "
            f"(POST /finance/reconciliation/bank-statements/upload/) in the meantime."
        )
    # Unreachable until a real client exists: the env var check above always
    # raises first, since no BANK_SFTP_KEY_* variable is ever set by this stub.
    raise BankSftpPullError("SFTP client not implemented.")
