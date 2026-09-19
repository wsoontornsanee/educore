"""Row-level cross-tenant access on every model-backed API route (ARC-004).

``test_urlconf_guards`` checks each queryset *structurally*. This module does what ARC-004
literally asks: for every generic view/viewset route it plants a real row in foundation A,
signs in as an admin of foundation B and expects the row to be invisible (list) or a
403/404 (detail and standard write actions).

Fixtures are generated from the model definition (``make_instance``), so a new viewset is
covered without writing one. A route only counts once a foundation-A admin can reach the
same row (the control), otherwise the B-side result proves nothing; routes where that
control fails sit in ``UNVERIFIED`` with a reason, and the test fails when an entry there
becomes verifiable so the list can only shrink.
"""
import datetime
import decimal
import itertools
import uuid

from django.db import models, transaction
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from rest_framework.generics import GenericAPIView
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.identity.models import Foundation, RoleAssignment, User
from apps.core.tests.test_urlconf_guards import _dotted

_counter = itertools.count(1)

# (view class dotted path, action) -> why the foundation-A control cannot reach its own row.
UNVERIFIED = {
    'apps.academic.views.ExamQuestionViewSet[list]': 'a foundation admin lacks the exam-authoring permission (403)',
    'apps.academic.views.ExamQuestionViewSet[retrieve]': 'a foundation admin lacks the exam-authoring permission (403)',
    'apps.academic.views.HomeworkViewSet[grading_queue]': 'queue lists only submissions awaiting grading; the generated row is not one',
    'apps.academic.views.ReportCardViewSet[download]': 'needs a published report card with a rendered PDF',
    'apps.campus.views.ClinicVisitViewSet[list]': 'serializer decrypts complaint_encrypted; a generated blob cannot be decrypted (500)',
    'apps.campus.views.ClinicVisitViewSet[retrieve]': 'serializer decrypts complaint_encrypted; a generated blob cannot be decrypted (500)',
    'apps.finance.views.PaymentViewSet[receipt]': 'needs a settled payment with an issuable receipt (400)',
    'apps.foundation.views.FoundationSettingsView[list]': 'singleton settings document, not a list of rows',
    'apps.wallet.views.POSTransactionViewSet[receipt]': 'receipt is limited to merchant/parent roles (403 for a foundation admin)',
}


class FixtureError(Exception):
    pass


def _value(field, foundation, stack, made):
    n = next(_counter)
    if field.name == 'foundation_id' or field.attname == 'foundation_id':
        return foundation.id
    if field.choices:
        return field.choices[0][0]
    if field.is_relation:
        related = field.related_model
        if related is Foundation:
            return foundation
        if field.null:
            return None
        return make_instance(related, foundation, stack, made)
    if field.has_default() and not field.unique:
        return field.get_default()
    if field.null:
        return None  # optional columns stay empty; deleted_at in particular must not be set
    limit = getattr(field, 'max_length', None) or 64
    if isinstance(field, models.EmailField):
        return f'row{n}@example.test'[:limit]
    if isinstance(field, models.URLField):
        return f'https://example.test/{n}'[:limit]
    if isinstance(field, models.GenericIPAddressField):
        return '127.0.0.1'
    if isinstance(field, (models.CharField, models.TextField)):
        return f'{field.name}-{n}'[:limit]
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, models.DecimalField):
        return decimal.Decimal('1.00')
    if isinstance(field, models.FloatField):
        return 1.0
    if isinstance(field, models.IntegerField):
        return n if field.unique else 1
    if isinstance(field, models.DateTimeField):
        return timezone.now()
    if isinstance(field, models.DateField):
        return datetime.date.today()
    if isinstance(field, models.TimeField):
        return datetime.time(8, 0)
    if isinstance(field, models.DurationField):
        return datetime.timedelta(days=1)
    if isinstance(field, models.UUIDField):
        return uuid.uuid4()
    if isinstance(field, models.JSONField):
        return {}
    if isinstance(field, models.BinaryField):
        return b''
    if isinstance(field, models.FileField):
        return ''
    raise FixtureError(f'no value rule for {type(field).__name__} {field.model.__name__}.{field.name}')


def make_instance(model, foundation, stack=(), made=None):
    """A saved row of ``model`` in ``foundation``, building required parents on the way."""
    made = {} if made is None else made
    if model in made:
        return made[model]
    if model in stack:
        raise FixtureError(f'required FK cycle through {model.__name__}')
    values = {}
    for field in model._meta.concrete_fields:
        if field.primary_key or getattr(field, 'generated', False):
            continue
        if isinstance(field, (models.DateTimeField,)) and (field.auto_now or field.auto_now_add):
            continue
        values[field.name if not field.is_relation else field.name] = _value(field, foundation, stack + (model,), made)
    try:
        with transaction.atomic():
            obj = model(**values)
            obj.save()
    except Exception as exc:  # noqa: BLE001
        raise FixtureError(f'{model.__name__}: {type(exc).__name__}: {str(exc)[:100]}') from exc
    made[model] = obj
    return obj


def own_rows(model, foundation):
    """Rows of ``model`` that legitimately belong to ``foundation`` (e.g. its own admin User)."""
    if not any(f.attname == 'foundation_id' for f in model._meta.concrete_fields):
        return 0
    return model._base_manager.filter(foundation_id=foundation.id).count()


def api_urls():
    """[(url_name, view_class, actions, param_names)] for every named DRF route."""
    found = []

    def walk(patterns, namespaces):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, namespaces + ([p.namespace] if p.namespace else []))
                continue
            assert isinstance(p, URLPattern)
            cb = p.callback
            cls = getattr(cb, 'cls', None) or getattr(cb, 'view_class', None)
            if cls is None or not issubclass(cls, GenericAPIView) or not p.name:
                continue
            found.append((':'.join(namespaces + [p.name]), cls, getattr(cb, 'actions', None),
                          tuple(p.pattern.regex.groupindex)))

    walk(get_resolver().url_patterns, [])
    return found


def route_model(cls):
    queryset = getattr(cls, 'queryset', None)
    if queryset is not None:
        return queryset.model
    serializer = getattr(cls, 'serializer_class', None)
    meta = getattr(serializer, 'Meta', None)
    return getattr(meta, 'model', None)


class CrossTenantRowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def foundation(label, phone):
            f = Foundation.objects.create(legal_name=f'Yayasan {label}', brand_name=label, status=Foundation.STATUS_ACTIVE)
            u = User.objects.create(foundation_id=f.id, phone_e164=phone, email=f'{label}@example.test', full_name=f'Admin {label}')
            RoleAssignment.all_tenants.create(
                foundation_id=f.id, user=u, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=f.id)
            return f, u
        cls.fa, cls.admin_a = foundation('alfa', '+6280000000101')
        cls.fb, cls.admin_b = foundation('beta', '+6280000000102')

    def client_for(self, user):
        # A real JWT: the foundation context is set by the auth layer, exactly as in production.
        client = APIClient(raise_request_exception=False)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
        return client

    @staticmethod
    def rows(response):
        data = response.json() if response.content else []
        if isinstance(data, dict):
            data = data.get('results', data.get('data', []))
        return data if isinstance(data, list) else []

    def test_foundation_b_admin_cannot_reach_foundation_a_rows(self):
        client_a, client_b = self.client_for(self.admin_a), self.client_for(self.admin_b)
        made_by_model, leaks, no_fixture, unverified, verified = {}, [], [], set(), 0
        for name, cls, actions, params in api_urls():
            model = route_model(cls)
            if model is None or params not in ((), ('pk',)):
                continue
            methods = {m: a for m, a in (actions or {'get': 'list'}).items()}
            if model not in made_by_model:
                try:
                    made_by_model[model] = make_instance(model, self.fa)
                except FixtureError as exc:
                    made_by_model[model] = exc
            row = made_by_model[model]
            if isinstance(row, FixtureError):
                no_fixture.append(f'{model._meta.label}: {row}')
                continue
            url = reverse(name, kwargs={'pk': row.pk} if params else {})
            for method, action in methods.items():
                if method == 'get':
                    ok_a = client_a.get(url)
                    if ok_a.status_code != 200 or (not params and not self.rows(ok_a)):
                        unverified.add(f'{_dotted(cls)}[{action}]')
                        continue
                    resp = client_b.get(url)
                    verified += 1
                    if params and resp.status_code not in (403, 404):
                        leaks.append(f'{_dotted(cls)}[{action}] GET -> {resp.status_code}')
                    if not params and resp.status_code == 200 and len(self.rows(resp)) > own_rows(model, self.fb):
                        leaks.append(f'{_dotted(cls)}[{action}] GET list shows {len(self.rows(resp))} row(s)')
                elif params and action in ('update', 'partial_update', 'destroy'):
                    resp = getattr(client_b, method)(url, {}, format='json')
                    verified += 1
                    if resp.status_code not in (403, 404, 405):
                        leaks.append(f'{_dotted(cls)}[{action}] {method.upper()} -> {resp.status_code}')
                    fresh = model._base_manager.filter(pk=row.pk).first()
                    self.assertTrue(
                        fresh is not None and getattr(fresh, 'deleted_at', None) is None,
                        f'{_dotted(cls)}[{action}] {method.upper()} removed a foreign row')
        self.assertGreater(verified, 200, 'the harness stopped reaching most routes')
        self.assertEqual(no_fixture, [], 'make_instance could not build a row for these models.')
        self.assertEqual(leaks, [], 'A foundation-B admin reached foundation-A data (ARC-004).')
        new = sorted(set(unverified) - set(UNVERIFIED))
        self.assertEqual(new, [], 'The foundation-A control could not reach its own row on these routes, '
                                  'so nothing was proven. Fix the fixture or add to UNVERIFIED with a reason.')
        stale = sorted(set(UNVERIFIED) - set(unverified))
        self.assertEqual(stale, [], 'Remove these from UNVERIFIED: they are now verified.')
