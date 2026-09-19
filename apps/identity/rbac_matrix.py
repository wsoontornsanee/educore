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
    # Derived from the real gates, not asserted. A guardian never has a Staff
    # row (console_access.py) or the foundation-admin flag, so those gated items
    # are blank for Parent; every other item falls through to the ordinary
    # permission check (permission=None items, e.g. the task inbox, are open to
    # any authenticated user, guardians included).
    if role == ROLE_PARENT and (
        item.get('requires_staff_profile') or item.get('requires_foundation_admin')
    ):
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
    rows.append([SURFACE_WEB, 'Roles holding the menu permission (see legend below)', 'apps/identity/nav.py'])
    for surface in (SURFACE_MOBILE_PARENT, SURFACE_MOBILE_STAFF, SURFACE_MOBILE_POS):
        roles = next(m['roles'] for m in MOBILE_MENUS if m['surface'] == surface)
        rows.append([surface, ', '.join(ROLE_NAMES[r] for r in roles), 'mobile/App.tsx, apps/identity/rbac_matrix.py'])
    rows.append([SURFACE_PARTNER_API, 'API-key scopes: ' + ', '.join(PartnerApiKey.ALLOWED_SCOPES),
                 'apps/partners/models.py'])
    rows.append(['Legend', '', ''])
    rows.append(['✓', 'Role holds the menu permission', ''])
    rows.append(['✓*', 'Also requires a linked Staff profile', 'apps/identity/console_access.py'])
    rows.append(['✓†', 'Foundation admin only', 'apps/identity/nav.py'])
    return rows


def build_matrix():
    web_items = _web_items()
    return {
        'Access Matrix': _access_matrix(web_items),
        'Roles x Permissions': _roles_x_permissions(),
        'Menus': _menus(web_items),
        'Surfaces': _surfaces(),
    }
