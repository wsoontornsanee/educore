"""Push the RBAC visibility matrix to a Google Sheet via the Sheets API.

Service-account auth. The sheet must be shared with the service account as
Editor. The last-synced content hash lives in the sheet's own Meta tab
('Meta'!B3), so an unchanged matrix costs one read and zero writes.
"""
import hashlib
import json

from django.conf import settings

META_TAB = 'Meta'
_HASH_CELL = f"'{META_TAB}'!B3"
_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'


class SheetNotConfigured(Exception):
    pass


def matrix_hash(matrix):
    return hashlib.sha256(json.dumps(matrix, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def build_service():
    if not settings.RBAC_SHEET_ID or not settings.RBAC_SHEET_SERVICE_ACCOUNT_JSON:
        raise SheetNotConfigured('RBAC_SHEET_ID / RBAC_SHEET_SERVICE_ACCOUNT_JSON not set')
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_service_account_info(
        json.loads(settings.RBAC_SHEET_SERVICE_ACCOUNT_JSON), scopes=[_SCOPE],
    )
    return build('sheets', 'v4', credentials=credentials, cache_discovery=False)


def _stored_hash(sheets, sheet_id):
    response = sheets.values().get(spreadsheetId=sheet_id, range=_HASH_CELL).execute()
    values = response.get('values') or [['']]
    return values[0][0] if values[0] else ''


def sync_matrix(service, sheet_id, matrix, synced_at):
    """Rewrite every tab if the matrix changed. Returns True if rewritten, False if unchanged.

    Meta is written in the same batch as the data; batchUpdate on values is not
    atomic across ranges, so Meta sits last and a partial failure leaves a stale
    hash that forces a retry on the next run.
    """
    digest = matrix_hash(matrix)
    sheets = service.spreadsheets()
    existing = {
        s['properties']['title']
        for s in sheets.get(spreadsheetId=sheet_id, fields='sheets.properties.title').execute().get('sheets', [])
    }
    wanted = [*matrix, META_TAB]
    missing = [tab for tab in wanted if tab not in existing]
    if missing:
        sheets.batchUpdate(spreadsheetId=sheet_id, body={
            'requests': [{'addSheet': {'properties': {'title': tab}}} for tab in missing],
        }).execute()
    elif _stored_hash(sheets, sheet_id) == digest:
        return False

    sheets.values().batchClear(spreadsheetId=sheet_id, body={'ranges': [f"'{tab}'" for tab in wanted]}).execute()
    data = [{'range': f"'{tab}'!A1", 'values': rows} for tab, rows in matrix.items()]
    data.append({'range': f"'{META_TAB}'!A1", 'values': [
        ['synced_at', synced_at],
        ['source', 'apps.identity.rbac_matrix'],
        ['content_hash', digest],
    ]})
    sheets.values().batchUpdate(spreadsheetId=sheet_id, body={'valueInputOption': 'RAW', 'data': data}).execute()
    return True
