"""URL-conf walk: permission declaration and tenant scoping on every API route (ARC-004).

Two invariants, both enforced across every route registered in ``ROOT_URLCONF``
so a new endpoint cannot ship without them:

1. Fail-closed permissions (memory/00_CORE.md §4.9): every DRF view declares its
   permissions. DRF's default is ``AllowAny``, so a view that declares nothing is
   world-open. Views that are open on purpose sit in an allowlist with a reason.
2. Tenancy on generic views (spec/01 ARC-004): for a user of foundation B, every
   ``get_queryset()`` returns rows filtered to foundation B (or is empty). This is a
   structural check on the query, so it needs no per-model fixtures; a queryset that
   bypasses ``TenantManager`` (``all_tenants``, a raw manager) fails it.
3. Tenancy bypasses in any view: a plain ``APIView`` has no generic queryset to inspect, so
   ``ViewTenantBypassTests`` reads the view source and requires every deliberate bypass of
   ``TenantManager`` to name the foundation in the same expression. Row-level 404s on the
   generic routes are exercised in ``test_cross_tenant_rows``.
"""
import ast
import inspect
import textwrap

from django.apps import apps
from django.core.exceptions import EmptyResultSet
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.core.models import TenantModel
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import tenant_context

# Views that legitimately have no per-user permission check, keyed by dotted path.
# Every entry must say why; the staleness test fails once an entry is no longer open.
OPEN_VIEW_ALLOWLIST = {
    'apps.identity.views.EduCoreTokenObtainPairView': 'login: issues the JWT pair',
    'apps.identity.views.EduCoreTokenRefreshView': 'login: refreshes the JWT pair',
    'apps.identity.views.RequestOtpView': 'login: OTP request, throttled',
    'apps.identity.views.VerifyOtpView': 'login: OTP verification',
    'apps.identity.views.SocialLoginView': 'login: SSO exchange, provider-verified token',
    'apps.core.routers.EduCoreAPIRootView': 'API index page, lists route names only',
    'apps.notifications.views.whatsapp_webhook_status': 'provider webhook, signature-verified',
    'apps.finance.views.PaymentWebhookView': 'gateway webhook, signature-verified',
    'apps.wallet.views.WalletTopupWebhookView': 'gateway webhook, signature-verified',
    'apps.calendar_sync.views.CalendarOAuthCallbackView': 'OAuth redirect, signed-state gated',
    'apps.status.views.StatusSubscribeView': 'public status-page subscribe form, throttled',
    'apps.partners.api_views.PartnerFoundationListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerSchoolListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerStaffListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerPayrollRunListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerPayrollRunLinesView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerPayrollAcknowledgeView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerInvoiceListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerAttendanceDailyView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerEventListView': 'partner HMAC key auth (spec/18)',
    'apps.partners.api_views.PartnerWebhookRegisterView': 'partner HMAC key auth (spec/18)',
}


def _dotted(cls):
    return f'{cls.__module__}.{cls.__name__}'


def _own_classes(cls):
    """MRO members that are project code, not DRF/Django internals."""
    return [k for k in cls.__mro__
            if k is not object and not k.__module__.startswith(('rest_framework', 'django'))]


def api_routes():
    """[(route, view_class, actions)] for every DRF view reachable from ROOT_URLCONF."""
    found = []

    def walk(patterns, prefix):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
                continue
            assert isinstance(p, URLPattern)
            cb = p.callback
            cls = getattr(cb, 'cls', None) or getattr(cb, 'view_class', None)
            if cls is not None and issubclass(cls, APIView):
                found.append((prefix + str(p.pattern), cls, getattr(cb, 'actions', None)))

    walk(get_resolver().url_patterns, '')
    return found


def view_classes():
    return {cls for _, cls, _ in api_routes()}


def declares_permissions(cls):
    """True when project code sets ``permission_classes`` or overrides ``get_permissions``."""
    return any('permission_classes' in k.__dict__ or 'get_permissions' in k.__dict__
               for k in _own_classes(cls))


def is_open(cls):
    """True when nothing restricts the view: undeclared, ``AllowAny`` or an empty list."""
    if not declares_permissions(cls):
        return True
    if any('get_permissions' in k.__dict__ for k in _own_classes(cls)):
        return False
    return not cls.permission_classes or any(
        p.__name__ == 'AllowAny' for p in cls.permission_classes)


class UrlConfPermissionTests(SimpleTestCase):
    def test_routes_discovered(self):
        self.assertGreater(len(view_classes()), 200)

    def test_every_view_declares_permissions_or_is_allowlisted(self):
        undeclared = sorted(
            _dotted(c) for c in view_classes()
            if not declares_permissions(c) and _dotted(c) not in OPEN_VIEW_ALLOWLIST)
        self.assertEqual(
            undeclared, [],
            'API views with no permission_classes/get_permissions fall back to AllowAny. '
            'Declare permissions, or add to OPEN_VIEW_ALLOWLIST with a reason.')

    def test_open_views_are_allowlisted(self):
        open_views = sorted(
            _dotted(c) for c in view_classes()
            if is_open(c) and _dotted(c) not in OPEN_VIEW_ALLOWLIST)
        self.assertEqual(
            open_views, [],
            'These views are open (AllowAny or empty permission_classes). '
            'Restrict them, or add to OPEN_VIEW_ALLOWLIST with a reason.')

    def test_allowlist_is_not_stale(self):
        open_now = {_dotted(c) for c in view_classes() if is_open(c)}
        stale = sorted(set(OPEN_VIEW_ALLOWLIST) - open_now)
        self.assertEqual(stale, [], 'Remove these from OPEN_VIEW_ALLOWLIST: they are no longer open.')

    def test_required_permission_is_declared_wherever_it_is_checked(self):
        """HasRequiredPermission denies at runtime (IAM-010) when a view names no
        permission. Surface that at test time, per 00_CORE §4.9."""
        missing = []
        for cls in view_classes():
            uses = HasRequiredPermission in cls.permission_classes or any(
                'get_permissions' in k.__dict__ for k in _own_classes(cls))
            if not uses:
                continue
            if not (getattr(cls, 'required_permission', None)
                    or hasattr(cls, 'get_required_permission')
                    or getattr(cls, 'action_permissions', None)):
                missing.append(_dotted(cls))
        self.assertEqual(sorted(missing), [], 'No required_permission declared on these views.')


# Generic views whose queryset is not scoped by foundation_id on purpose.
UNSCOPED_QUERYSET_ALLOWLIST = {}


class ViewsetTenantScopingTests(TestCase):
    """A foundation-B admin's querysets never reach outside foundation B."""

    @classmethod
    def setUpTestData(cls):
        cls.foundation = Foundation.objects.create(
            legal_name='Yayasan Uji', brand_name='Uji', status=Foundation.STATUS_ACTIVE)
        cls.school = School.all_tenants.create(
            foundation_id=cls.foundation.id, name='SD Uji', npsn='90000001', level=School.LEVEL_SD)
        cls.user = User.objects.create(
            foundation_id=cls.foundation.id, phone_e164='+6280000000001',
            email='uji@example.test', full_name='Admin Uji')
        RoleAssignment.all_tenants.create(
            foundation_id=cls.foundation.id, user=cls.user,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=cls.foundation.id)

    def _view(self, cls, action):
        django_request = RequestFactory().get('/')
        django_request.user = self.user
        django_request.foundation_id = self.foundation.id
        view = cls()
        view.request = Request(django_request)
        view.request._user = self.user
        view.args, view.kwargs, view.format_kwarg = (), {}, None
        view.action = action
        return view

    def test_generic_view_querysets_are_scoped_to_the_callers_foundation(self):
        fid = self.foundation.id
        checked = 0
        unscoped, errors = [], []
        pairs = set()
        for _, cls, actions in api_routes():
            if not issubclass(cls, GenericAPIView):
                continue
            for action in (set(actions.values()) if actions else {'list'}):
                pairs.add((cls, action))
        for cls, action in sorted(pairs, key=lambda p: (_dotted(p[0]), p[1])):
            if _dotted(cls) in UNSCOPED_QUERYSET_ALLOWLIST:
                continue
            if not (getattr(cls, 'queryset', None) is not None or 'get_queryset' in
                    [n for k in _own_classes(cls) for n in k.__dict__]):
                continue
            try:
                with tenant_context(fid):
                    qs = self._view(cls, action).get_queryset()
                    if qs.query.is_empty():
                        checked += 1
                        continue
                    sql, params = qs.query.sql_with_params()
            except EmptyResultSet:
                checked += 1
                continue
            except Exception as exc:  # noqa: BLE001 - report every view we could not evaluate
                errors.append(f'{_dotted(cls)}[{action}]: {type(exc).__name__}: {str(exc)[:80]}')
                continue
            checked += 1
            if not ('foundation_id' in sql and fid in params):
                unscoped.append(f'{_dotted(cls)}[{action}] -> {qs.model.__name__}')
        self.assertGreater(checked, 40)
        self.assertEqual(errors, [], 'get_queryset() could not be evaluated for these views.')
        self.assertEqual(
            unscoped, [],
            'These generic views return querysets not filtered by the caller\'s foundation_id.')


# Views that bypass TenantManager without naming a foundation in the expression, and why that is safe.
UNSCOPED_BYPASS_ALLOWLIST = {
    'apps.identity.views.VerifyOtpView': 'guardian login: the phone number is looked up before any foundation is known',
    'apps.foundation.views.FxRateViewSet': 'class-level queryset only; get_queryset() re-filters by foundation_id (covered above)',
}

# @api_view function views compile to a class with no source of its own to read.
SOURCELESS_VIEWS = {'apps.notifications.views.whatsapp_webhook_status': 'signature-verified provider webhook'}

# Attribute names that read rows without the thread-local foundation filter.
BYPASS_ATTRIBUTES = {'all_tenants', '_base_manager', '_default_manager', 'raw'}


def foundation_unmanaged_models():
    """Models carrying foundation_id but not TenantManager-scoped (audit, queue, archives...)."""
    return {
        m.__name__ for m in apps.get_models()
        if not issubclass(m, TenantModel) and any(f.attname == 'foundation_id' for f in m._meta.concrete_fields)
    }


def tenancy_bypasses(cls, unmanaged):
    """[(kind, expression)] for each bypass of the tenant filter in ``cls``'s own source."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in BYPASS_ATTRIBUTES:
            kind = node.attr
        elif node.attr == 'objects' and isinstance(node.value, ast.Name) and node.value.id in unmanaged:
            kind = f'{node.value.id}.objects'
        else:
            continue
        top = node  # climb to the whole call chain: X.all_tenants.filter(...).order_by(...)
        while True:
            parent = parents.get(top)
            if (isinstance(parent, ast.Attribute) and parent.value is top) or \
               (isinstance(parent, ast.Call) and parent.func is top) or \
               (isinstance(parent, ast.Subscript) and parent.value is top):
                top = parent
            else:
                break
        found.append((kind, ast.unparse(top)))
    return found


class ViewTenantBypassTests(SimpleTestCase):
    def test_every_bypass_of_the_tenant_filter_names_the_foundation(self):
        unmanaged = foundation_unmanaged_models()
        classes = {k for cls in view_classes() for k in _own_classes(cls) if inspect.isclass(k)
                   and _dotted(k) not in SOURCELESS_VIEWS}
        offenders = sorted(
            f'{_dotted(k)}: {expr[:90]}'
            for k in classes if _dotted(k) not in UNSCOPED_BYPASS_ALLOWLIST
            for _, expr in tenancy_bypasses(k, unmanaged)
            if 'foundation' not in expr)
        self.assertEqual(
            offenders, [],
            'These views read rows without the tenant filter and never name a foundation. '
            'Filter by the caller\'s foundation_id, or add to UNSCOPED_BYPASS_ALLOWLIST with a reason.')

    def test_bypass_allowlist_is_not_stale(self):
        unmanaged = foundation_unmanaged_models()
        by_name = {_dotted(k): k for cls in view_classes() for k in _own_classes(cls) if inspect.isclass(k)}
        stale = sorted(
            name for name in UNSCOPED_BYPASS_ALLOWLIST
            if name not in by_name
            or not any('foundation' not in expr for _, expr in tenancy_bypasses(by_name[name], unmanaged)))
        self.assertEqual(stale, [], 'Remove these from UNSCOPED_BYPASS_ALLOWLIST: they no longer bypass unscoped.')
