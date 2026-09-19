from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.identity import rbac_sheet

MATRIX = {'Access Matrix': [['h'], ['r']], 'Menus': [['h']]}


def _fake_service(existing_tabs, stored_hash=None):
    service = mock.MagicMock()
    sheets = service.spreadsheets.return_value
    sheets.get.return_value.execute.return_value = {
        'sheets': [{'properties': {'title': t}} for t in existing_tabs],
    }
    sheets.values.return_value.get.return_value.execute.return_value = (
        {'values': [[stored_hash]]} if stored_hash else {}
    )
    return service, sheets


class SyncMatrixTests(SimpleTestCase):
    def test_unchanged_hash_skips_write(self):
        digest = rbac_sheet.matrix_hash(MATRIX)
        service, sheets = _fake_service(['Access Matrix', 'Menus', 'Meta'], stored_hash=digest)
        changed = rbac_sheet.sync_matrix(service, 'sid', MATRIX, '2026-09-19T00:00:00Z')
        self.assertFalse(changed)
        sheets.values.return_value.batchUpdate.assert_not_called()
        sheets.values.return_value.batchClear.assert_not_called()

    def test_changed_hash_clears_then_rewrites_with_meta_last(self):
        service, sheets = _fake_service(['Access Matrix', 'Menus', 'Meta'], stored_hash='old')
        changed = rbac_sheet.sync_matrix(service, 'sid', MATRIX, '2026-09-19T00:00:00Z')
        self.assertTrue(changed)
        values = sheets.values.return_value
        values.batchClear.assert_called_once()
        body = values.batchUpdate.call_args.kwargs['body']
        self.assertEqual(body['valueInputOption'], 'RAW')
        self.assertEqual([d['range'] for d in body['data']],
                         ["'Access Matrix'!A1", "'Menus'!A1", "'Meta'!A1"])
        meta = body['data'][-1]['values']
        self.assertEqual(meta[2], ['content_hash', rbac_sheet.matrix_hash(MATRIX)])

    def test_missing_tabs_are_created_and_forces_write(self):
        service, sheets = _fake_service(['Sheet1'])
        changed = rbac_sheet.sync_matrix(service, 'sid', MATRIX, 'now')
        self.assertTrue(changed)
        requests = sheets.batchUpdate.call_args.kwargs['body']['requests']
        self.assertEqual(
            [r['addSheet']['properties']['title'] for r in requests],
            ['Access Matrix', 'Menus', 'Meta'],
        )

    def test_build_service_raises_when_unconfigured(self):
        with override_settings(RBAC_SHEET_ID='', RBAC_SHEET_SERVICE_ACCOUNT_JSON=''):
            with self.assertRaises(rbac_sheet.SheetNotConfigured):
                rbac_sheet.build_service()


CMD = 'apps.identity.management.commands.sync_rbac_sheet'


class SyncRbacSheetCommandTests(TestCase):
    @override_settings(RBAC_SHEET_ID='', RBAC_SHEET_SERVICE_ACCOUNT_JSON='')
    def test_unconfigured_exits_cleanly(self):
        out = StringIO()
        call_command('sync_rbac_sheet', stdout=out)
        self.assertIn('not configured', out.getvalue())

    @override_settings(RBAC_SHEET_ID='sid', RBAC_SHEET_SERVICE_ACCOUNT_JSON='{}')
    def test_configured_syncs_generated_matrix(self):
        out = StringIO()
        with mock.patch(f'{CMD}.build_service') as build, \
                mock.patch(f'{CMD}.sync_matrix', return_value=True) as sync:
            call_command('sync_rbac_sheet', stdout=out)
        sync.assert_called_once()
        args = sync.call_args.args
        self.assertEqual(args[0], build.return_value)
        self.assertEqual(args[1], 'sid')
        self.assertIn('Access Matrix', args[2])
        self.assertIn('rewritten', out.getvalue())

    @override_settings(RBAC_SHEET_ID='sid', RBAC_SHEET_SERVICE_ACCOUNT_JSON='{}')
    def test_unchanged_reports_no_change(self):
        out = StringIO()
        with mock.patch(f'{CMD}.build_service'), mock.patch(f'{CMD}.sync_matrix', return_value=False):
            call_command('sync_rbac_sheet', stdout=out)
        self.assertIn('unchanged', out.getvalue())
