# RBAC Google Sheet Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cron-driven command that keeps a read-only Google Sheet showing every menu x client surface x role access mapping, generated from live RBAC code.

**Architecture:** `apps/identity/rbac_matrix.py` is a pure, DB-free generator that imports the live `ROLE_PERMISSIONS`, `NAV_GROUPS`, `PLATFORM_ROLE_PERMISSIONS` and `PartnerApiKey.ALLOWED_SCOPES`, plus one small `MOBILE_MENUS` registry (mobile TSX declares no permissions) guarded by a test against the TSX tab unions. `apps/identity/rbac_sheet.py` pushes the matrix to Google Sheets (service account), skipping the write when a content hash stored in the sheet's Meta tab is unchanged. `sync_rbac_sheet` is a `CronHostCommand` wrapping both.

**Tech Stack:** Django 5.1, `google-api-python-client`, `google-auth`, Django `TestCase`/`SimpleTestCase` (run with `pytest`).

## Global Constraints

- Sheet ID and service account are env config (`RBAC_SHEET_ID`, `RBAC_SHEET_SERVICE_ACCOUNT_JSON`); never hardcode the sheet ID. When unset, the command exits 0 with a "not configured" message.
- Only config is written to the sheet: no per-user role assignments, no PII.
- Single monolith, cron background execution, no Redis/Celery (CLAUDE.md).
- Cron lines in `deploy/crontab` need a unique, increasing `sleep N` stagger.
- The generator must import live RBAC data, never copy it.
- Web console is staff-only: the `parent` column is always blank for web menus (see `apps/identity/nav.py` docstring: "a guardian must never reach a school-side console").
- "Device" in the spec means client surface (web console / mobile parent / mobile staff / POS kiosk / partner API), not hardware classes.

---

### Task 1: Matrix generator + mobile registry

**Files:**
- Create: `apps/identity/rbac_matrix.py`
- Test: `apps/identity/tests/test_rbac_matrix.py`

**Interfaces:**
- Consumes: `apps.identity.rbac.ROLE_PERMISSIONS`, `PLATFORM_ROLE_PERMISSIONS`, `ROLE_PARENT`, `ROLE_FOUNDATION_ADMIN`; `apps.identity.nav.NAV_GROUPS`, `COMING_SOON_URL_NAME`; `apps.identity.guardian_access.STAFF_ROLES` (a `set` of role strings); `apps.identity.models.RoleAssignment.ROLE_CHOICES`; `apps.partners.models.PartnerApiKey.ALLOWED_SCOPES`.
- Produces: `build_matrix() -> dict[str, list[list[str]]]` mapping tab name to rows (first row is the header), tab order `"Access Matrix"`, `"Roles x Permissions"`, `"Menus"`, `"Surfaces"`. Also `MOBILE_MENUS`, `TAB_PARENT_IDS`-style constants are internal; only `build_matrix` and `MOBILE_MENUS` are used elsewhere (tests).

- [ ] **Step 1: Write the failing tests**

Create `apps/identity/tests/test_rbac_matrix.py`:

```python
import re
from pathlib import Path
from unittest import mock, skipUnless

from django.test import SimpleTestCase

from apps.identity import rbac_matrix
from apps.identity.nav import NAV_GROUPS
from apps.identity.rbac import ROLE_PERMISSIONS
from apps.identity.rbac_matrix import MOBILE_MENUS, build_matrix

MOBILE_DIR = Path(__file__).resolve().parents[3] / 'mobile'


def _access_row(matrix, menu_id_label):
    header = matrix['Access Matrix'][0]
    for row in matrix['Access Matrix'][1:]:
        if row[1].endswith(menu_id_label):
            return dict(zip(header, row))
    raise AssertionError(f'no Access Matrix row ending with {menu_id_label!r}')


class BuildMatrixTests(SimpleTestCase):
    def setUp(self):
        self.matrix = build_matrix()

    def test_tab_order(self):
        self.assertEqual(
            list(self.matrix),
            ['Access Matrix', 'Roles x Permissions', 'Menus', 'Surfaces'],
        )

    def test_every_live_web_menu_appears_in_access_matrix(self):
        labels = {row[1] for row in self.matrix['Access Matrix'][1:]}
        for group in NAV_GROUPS:
            for item in group['items']:
                if item['url_name'] == 'console:coming_soon':
                    continue
                self.assertTrue(
                    any(label.endswith(str(item['label'])) for label in labels),
                    f"{item['id']} missing from Access Matrix",
                )

    def test_foundation_admin_only_menu_marks_dagger_for_foundation_admin_only(self):
        row = _access_row(self.matrix, 'Mitra & kunci API')
        self.assertEqual(row['Foundation Admin'], '✓†')
        self.assertEqual(row['School Admin'], '')

    def test_staff_profile_menu_marks_asterisk(self):
        row = _access_row(self.matrix, 'Tagihan & pembayaran')
        self.assertEqual(row['Finance Officer'], '✓*')
        self.assertEqual(row['Teacher'], '')

    def test_no_permission_menu_open_to_staff_but_not_parent(self):
        row = _access_row(self.matrix, 'Kotak tugas')
        self.assertEqual(row['Teacher'], '✓')
        self.assertEqual(row['Parent'], '')

    def test_parent_column_blank_for_all_web_rows(self):
        for row in self.matrix['Access Matrix'][1:]:
            if row[0] == 'Web console':
                cells = dict(zip(self.matrix['Access Matrix'][0], row))
                self.assertEqual(cells['Parent'], '', row[1])

    def test_mobile_parent_tabs_only_for_parent(self):
        row = _access_row(self.matrix, 'Wallet')
        self.assertEqual(row['Parent'], '✓')
        self.assertEqual(row['Teacher'], '')

    def test_every_permission_key_listed_with_platform_column(self):
        header = self.matrix['Roles x Permissions'][0]
        self.assertIn('Platform Operator', header)
        keys = {row[0] for row in self.matrix['Roles x Permissions'][1:]}
        for perms in ROLE_PERMISSIONS.values():
            self.assertLessEqual(perms, keys)
        self.assertIn('status.write', keys)

    def test_coming_soon_item_in_menus_but_not_access_matrix(self):
        fake = [{'label': 'Grup', 'items': [
            {'id': 'x', 'label': 'Segera', 'permission': None, 'url_name': 'console:coming_soon'},
        ]}]
        with mock.patch.object(rbac_matrix, 'NAV_GROUPS', fake):
            matrix = build_matrix()
        self.assertNotIn('Segera', ' '.join(r[1] for r in matrix['Access Matrix']))
        menu_rows = [r for r in matrix['Menus'][1:] if r[3] == 'Segera']
        self.assertEqual(menu_rows[0][-1], 'coming soon')

    def test_surfaces_lists_partner_scopes(self):
        partner = [r for r in self.matrix['Surfaces'][1:] if r[0] == 'Partner API'][0]
        self.assertIn('roster.read', partner[1])


@skipUnless(MOBILE_DIR.is_dir(), 'mobile/ not present')
class MobileRegistryGuardTests(SimpleTestCase):
    def _tab_ids(self, relpath, type_name):
        source = (MOBILE_DIR / relpath).read_text(encoding='utf-8')
        match = re.search(rf'type {type_name} = ([^;]+);', source)
        self.assertIsNotNone(match, f'{type_name} not found in {relpath}')
        return set(re.findall(r"'([A-Z_]+)'", match.group(1)))

    def _registry_ids(self, surface):
        return {m['id'] for m in MOBILE_MENUS if m['surface'] == surface}

    def test_parent_tabs_match_registry(self):
        self.assertEqual(
            self._tab_ids('src/screens/parent/ParentShell.tsx', 'ParentTab'),
            self._registry_ids('Mobile: parent app'),
        )

    def test_staff_tabs_match_registry(self):
        self.assertEqual(
            self._tab_ids('src/screens/teacher/TeacherShell.tsx', 'TeacherTab'),
            self._registry_ids('Mobile: staff app'),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest apps/identity/tests/test_rbac_matrix.py -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'rbac_matrix'`.

- [ ] **Step 3: Write the implementation**

Create `apps/identity/rbac_matrix.py`:

```python
"""Read-only RBAC visibility matrix, built from live code (never a copy).

Feeds the RBAC management Google Sheet (see rbac_sheet.py /
`manage.py sync_rbac_sheet`). Pure and DB-free: every cell is derived from
ROLE_PERMISSIONS / NAV_GROUPS / PLATFORM_ROLE_PERMISSIONS / PartnerApiKey scopes.

Mobile menus declare no permissions in TSX (the app tree is picked by role,
see mobile/src/services/roleRouting.ts), so MOBILE_MENUS below is the one
explicit registry. mobile tab ids are guarded by
apps/identity/tests/test_rbac_matrix.py against the TSX type unions.
"""
from django.utils.translation import override

from apps.partners.models import PartnerApiKey

from .guardian_access import STAFF_ROLES
from .models import RoleAssignment
from .nav import COMING_SOON_URL_NAME, NAV_GROUPS
from .rbac import (
    PLATFORM_ROLE_PERMISSIONS,
    ROLE_CANTEEN_OPERATOR,
    ROLE_FOUNDATION_ADMIN,
    ROLE_PARENT,
    ROLE_PERMISSIONS,
)

SURFACE_WEB = 'Web console'
SURFACE_MOBILE_PARENT = 'Mobile: parent app'
SURFACE_MOBILE_STAFF = 'Mobile: staff app'
SURFACE_MOBILE_POS = 'Mobile: POS kiosk'
SURFACE_PARTNER_API = 'Partner API'

ROLE_NAMES = dict(RoleAssignment.ROLE_CHOICES)
TENANT_ROLES = list(ROLE_NAMES)

# App.tsx: parent -> ParentShell; canteen_operator/posMode -> POSKioskScreen;
# every other signed-in user -> TeacherShell.
_STAFF_APP_ROLES = tuple(sorted(STAFF_ROLES - {ROLE_CANTEEN_OPERATOR}))

MOBILE_MENUS = [
    {'surface': SURFACE_MOBILE_PARENT, 'id': tab, 'label': label, 'roles': (ROLE_PARENT,)}
    for tab, label in [
        ('HOME', 'Home'), ('ATTENDANCE', 'Attendance'), ('ACADEMIC', 'Academic'),
        ('MESSAGES', 'Messages'), ('WALLET', 'Wallet'), ('NUTRITION', 'Nutrition'),
        ('INVOICES', 'Invoices'), ('PROFILE', 'Profile'),
    ]
] + [
    {'surface': SURFACE_MOBILE_STAFF, 'id': tab, 'label': label, 'roles': _STAFF_APP_ROLES}
    for tab, label in [
        ('AGENDA', 'Agenda'), ('ATTENDANCE', 'Roll call'),
        ('BROADCAST', 'Broadcast'), ('PROFILE', 'Profile'),
    ]
] + [
    {'surface': SURFACE_MOBILE_POS, 'id': 'POS', 'label': 'POS kiosk', 'roles': (ROLE_CANTEEN_OPERATOR,)},
]


def _web_cell(role, item):
    # Web console is staff-only (nav.py docstring): a guardian never reaches it.
    if role == ROLE_PARENT:
        return ''
    admin_only = item.get('requires_foundation_admin')
    if admin_only and role != ROLE_FOUNDATION_ADMIN:
        return ''
    permission = item['permission']
    if permission is not None and permission not in ROLE_PERMISSIONS[role]:
        return ''
    if admin_only:
        return '✓†'
    if item.get('requires_staff_profile'):
        return '✓*'
    return '✓'


def _web_items():
    """(group label, item) for every NAV_GROUPS item, labels resolved in Indonesian."""
    with override('id'):
        return [
            (str(group['label']), {**item, 'label': str(item['label'])})
            for group in NAV_GROUPS
            for item in group['items']
        ]


def _access_matrix(web_items):
    rows = [['Surface', 'Menu', 'Permission', *(ROLE_NAMES[r] for r in TENANT_ROLES)]]
    for group, item in web_items:
        if item['url_name'] == COMING_SOON_URL_NAME:
            continue
        rows.append([
            SURFACE_WEB, f"{group} > {item['label']}", item['permission'] or '(any staff)',
            *(_web_cell(role, item) for role in TENANT_ROLES),
        ])
    for menu in MOBILE_MENUS:
        rows.append([
            menu['surface'], menu['label'], '(role-gated at login)',
            *('✓' if role in menu['roles'] else '' for role in TENANT_ROLES),
        ])
    return rows


def _roles_x_permissions():
    platform_roles = list(PLATFORM_ROLE_PERMISSIONS)
    header = ['Permission', *(ROLE_NAMES[r] for r in TENANT_ROLES),
              *(r.replace('_', ' ').title() for r in platform_roles)]
    keys = set().union(*ROLE_PERMISSIONS.values(), *PLATFORM_ROLE_PERMISSIONS.values())
    rows = [header]
    for key in sorted(keys):
        rows.append([
            key,
            *('✓' if key in ROLE_PERMISSIONS[r] else '' for r in TENANT_ROLES),
            *('✓' if key in PLATFORM_ROLE_PERMISSIONS[r] else '' for r in platform_roles),
        ])
    return rows


def _menus(web_items):
    rows = [['Surface', 'Group', 'Menu id', 'Label', 'URL name', 'Permission',
             'Staff profile required', 'Foundation admin only', 'Status']]
    for group, item in web_items:
        rows.append([
            SURFACE_WEB, group, item['id'], item['label'], item['url_name'],
            item['permission'] or '',
            'yes' if item.get('requires_staff_profile') else '',
            'yes' if item.get('requires_foundation_admin') else '',
            'coming soon' if item['url_name'] == COMING_SOON_URL_NAME else 'live',
        ])
    for menu in MOBILE_MENUS:
        rows.append([menu['surface'], '', menu['id'], menu['label'], '', '', '', '', 'live'])
    return rows


def _surfaces():
    rows = [['Surface', 'Audience', 'Source']]
    rows.append([SURFACE_WEB, 'Staff roles holding the menu permission (parent excluded)', 'apps/identity/nav.py'])
    for surface in (SURFACE_MOBILE_PARENT, SURFACE_MOBILE_STAFF, SURFACE_MOBILE_POS):
        roles = next(m['roles'] for m in MOBILE_MENUS if m['surface'] == surface)
        rows.append([surface, ', '.join(ROLE_NAMES[r] for r in roles), 'mobile/App.tsx, apps/identity/rbac_matrix.py'])
    rows.append([SURFACE_PARTNER_API, 'API-key scopes: ' + ', '.join(PartnerApiKey.ALLOWED_SCOPES),
                 'apps/partners/models.py'])
    return rows


def build_matrix():
    web_items = _web_items()
    return {
        'Access Matrix': _access_matrix(web_items),
        'Roles x Permissions': _roles_x_permissions(),
        'Menus': _menus(web_items),
        'Surfaces': _surfaces(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest apps/identity/tests/test_rbac_matrix.py -v`
Expected: PASS (all). If `test_parent_tabs_match_registry` fails, the TSX union changed — update `MOBILE_MENUS`.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/rbac_matrix.py apps/identity/tests/test_rbac_matrix.py
git commit -m "feat(identity): RBAC visibility matrix generator

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Sheets writer, command, config, cron

**Files:**
- Create: `apps/identity/rbac_sheet.py`
- Create: `apps/identity/management/commands/sync_rbac_sheet.py`
- Modify: `educore/settings/base.py` (after the calendar-sync settings block, near line 340)
- Modify: `requirements.txt` (after `google-cloud-storage`)
- Modify: `deploy/crontab` (append line)
- Modify: `docs/superpowers/specs/2026-09-19-rbac-sheet-sync-design.md` (Meta tab has no git SHA; deploy-time run is manual)
- Test: `apps/identity/tests/test_rbac_sheet_sync.py`

**Interfaces:**
- Consumes: `apps.identity.rbac_matrix.build_matrix() -> dict[str, list[list[str]]]`; `apps.core.locks.advisory_lock(name, timeout=0)` context manager yielding `acquired: bool`; `apps.core.management.base.CronHostCommand`.
- Produces: `rbac_sheet.SheetNotConfigured` (Exception); `rbac_sheet.matrix_hash(matrix) -> str`; `rbac_sheet.build_service()` (raises `SheetNotConfigured`); `rbac_sheet.sync_matrix(service, sheet_id, matrix, synced_at) -> bool` (True = rewritten, False = unchanged).

- [ ] **Step 1: Write the failing tests**

Create `apps/identity/tests/test_rbac_sheet_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest apps/identity/tests/test_rbac_sheet_sync.py -v`
Expected: FAIL/ERROR (`cannot import name 'rbac_sheet'` / unknown command).

- [ ] **Step 3: Implement writer, command, settings, deps, cron**

Create `apps/identity/rbac_sheet.py`:

```python
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
```

Create `apps/identity/management/commands/sync_rbac_sheet.py`:

```python
"""Cron: push the live RBAC visibility matrix to the RBAC management Google Sheet (hourly, deploy/crontab)."""
from django.conf import settings
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.identity.rbac_matrix import build_matrix
from apps.identity.rbac_sheet import SheetNotConfigured, build_service, sync_matrix


class Command(CronHostCommand):
    help = "Sync roles/menus/surfaces access matrix to the RBAC Google Sheet (no-op when unchanged or unconfigured)."

    def handle(self, *args, **options):
        with advisory_lock('sync_rbac_sheet', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING("Advisory lock 'sync_rbac_sheet' already held. Exiting."))
                return
            try:
                service = build_service()
            except SheetNotConfigured:
                self.stdout.write("sync_rbac_sheet: not configured (RBAC_SHEET_ID / RBAC_SHEET_SERVICE_ACCOUNT_JSON), skipping")
                return
            changed = sync_matrix(service, settings.RBAC_SHEET_ID, build_matrix(), timezone.now().isoformat())
            self.stdout.write(self.style.SUCCESS(
                "sync_rbac_sheet: sheet rewritten" if changed else "sync_rbac_sheet: unchanged, no write"
            ))
```

In `educore/settings/base.py`, after the `MICROSOFT_CALENDAR_TENANT_ID` line block, add:

```python
# RBAC visibility sheet — `manage.py sync_rbac_sheet` (docs/superpowers/specs/2026-09-19-rbac-sheet-sync-design.md).
# Google service-account JSON (one line) + target spreadsheet ID; share the sheet with the
# service account email as Editor. Either empty disables the sync (command exits 0).
RBAC_SHEET_ID = os.environ.get('RBAC_SHEET_ID', '')
RBAC_SHEET_SERVICE_ACCOUNT_JSON = os.environ.get('RBAC_SHEET_SERVICE_ACCOUNT_JSON', '')
```

In `requirements.txt`, after the `google-cloud-storage` line add:

```
# RBAC visibility sheet sync (apps/identity/rbac_sheet.py)
google-api-python-client>=2.140,<3.0
google-auth>=2.34,<3.0
```

Append to `deploy/crontab`:

```
15 * * * *     sleep 84; python /app/manage.py sync_rbac_sheet                   # RBAC visibility Google Sheet (no-op if unchanged)
```

In `docs/superpowers/specs/2026-09-19-rbac-sheet-sync-design.md`: change the Meta tab bullet to "synced-at, content hash" and replace "and run after each deploy" with "RBAC only changes on deploy, so the hourly run picks changes up; run `manage.py sync_rbac_sheet` by hand for an immediate refresh".

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest apps/identity/tests/test_rbac_matrix.py apps/identity/tests/test_rbac_sheet_sync.py -v`
Expected: PASS (all).

- [ ] **Step 5: Smoke test the command unconfigured**

Run: `python manage.py sync_rbac_sheet`
Expected: prints `sync_rbac_sheet: not configured ... skipping`, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add apps/identity/rbac_sheet.py apps/identity/management/commands/sync_rbac_sheet.py apps/identity/tests/test_rbac_sheet_sync.py educore/settings/base.py requirements.txt deploy/crontab docs/superpowers/specs/2026-09-19-rbac-sheet-sync-design.md
git commit -m "feat(identity): sync RBAC matrix to Google Sheet via cron

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Owner setup (after merge, not code)

1. Google Cloud: create a service account, enable the Google Sheets API, create a JSON key.
2. Share the sheet (`1cI0FBBzF3k1V28aNnNRSNY4CUwElqXeC88gMkDoT--Y`) with the service account email as Editor.
3. On prod set `RBAC_SHEET_ID` and `RBAC_SHEET_SERVICE_ACCOUNT_JSON` (key JSON on one line), reinstall requirements, then run `python manage.py sync_rbac_sheet` once to verify.
