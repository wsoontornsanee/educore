import re
from pathlib import Path
from unittest import mock, skipUnless

from django.test import SimpleTestCase
from django.utils.translation import override

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
        with override('id'):
            for group in NAV_GROUPS:
                for item in group['items']:
                    if item['url_name'] == 'console:coming_soon':
                        continue
                    self.assertIn(
                        f"{group['label']} > {item['label']}", labels,
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

    def test_staff_access_menu_open_to_staff_roles_but_not_parent(self):
        # The inbox declares requires_staff_access (nav.has_staff_access): a
        # staff role passes, a guardian-only account does not.
        row = _access_row(self.matrix, 'Kotak tugas')
        self.assertEqual(row['Teacher'], '✓')
        self.assertEqual(row['Clinic Officer'], '✓')
        self.assertEqual(row['Parent'], '')

    def test_parent_column_blank_on_every_web_row(self):
        # A guardian has no staff role, Staff row or admin flag, and the
        # parent role holds none of the menu permissions.
        for row in self.matrix['Access Matrix'][1:]:
            if row[0] == 'Web console':
                cells = dict(zip(self.matrix['Access Matrix'][0], row))
                self.assertEqual(cells['Parent'], '', row[1])

    def test_ungated_permission_none_item_still_ticks_parent(self):
        # Derivation, not assertion: an item with no gate at all is open to
        # every role, so the parent cell is a tick.
        fake = [{'label': 'Grup', 'items': [
            {'id': 'x', 'label': 'Terbuka', 'permission': None, 'url_name': 'console-inbox'},
        ]}]
        with mock.patch.object(rbac_matrix, 'NAV_GROUPS', fake):
            matrix = build_matrix()
        row = _access_row(matrix, 'Terbuka')
        self.assertEqual(row['Parent'], '✓')

    def test_every_nav_gate_key_is_known_to_the_generator(self):
        # Drift guard: a new requires_* gate in nav.py must be mirrored in
        # _web_cell, otherwise the sheet silently overstates access.
        declared = {
            key for group in NAV_GROUPS for item in group['items']
            for key in item if key.startswith('requires_')
        }
        self.assertLessEqual(declared, rbac_matrix.KNOWN_WEB_GATES)

    def test_surfaces_legend_explains_all_cell_markers(self):
        cells = {cell for row in self.matrix['Surfaces'] for cell in row}
        for marker in ('✓', '✓*', '✓†'):
            self.assertIn(marker, cells)

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
        return set(re.findall(r"'([A-Za-z_]+)'", match.group(1)))

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
