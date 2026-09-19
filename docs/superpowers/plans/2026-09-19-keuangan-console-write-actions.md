# Keuangan Console Write Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four write actions to the read-only Keuangan web console pages: resolve a reconciliation discrepancy, approve/reject a discount, approve/reject a write-off request, and record a cash payment.

**Architecture:** Plain POST → existing finance service → `django.contrib.messages` flash → redirect back (no HTMX, no new domain logic). One shared gate mixin serves both the existing read pages and a new `FinanceActionView` template-method base; each action is a ~15-line subclass. Spec: `docs/superpowers/specs/2026-09-19-keuangan-console-write-actions-design.md`.

**Tech Stack:** Django 5.1 class-based views, Django messages framework, existing `apps.finance.services.*`, Django `TestCase` (run with `EDUCORE_USE_SQLITE=1 python3 manage.py test`).

## Global Constraints

- Single Django monolith, MySQL 8 in prod; no Redis/Celery/brokers. Tests run on SQLite via `EDUCORE_USE_SQLITE=1`.
- 3-layer tenancy: every lookup filters `foundation_id` (via `.all_tenants.filter(foundation_id=...)`); service calls run inside `tenant_context(foundation_id)`. Cross-tenant or out-of-scope ids are **404**, never 403.
- Money: `Decimal` parsed from strings, never float; at most 2 decimal places; amount must be finite and > 0.
- Every mutating action writes an audit event (`apps.core.services.audit`).
- No hard deletes. No PII (names, NIS) in flash messages or logs.
- `id-ID` first: Indonesian source strings via `gettext`/`{% translate %}`. EN entries are **hand-added** to `locale/en/LC_MESSAGES/django.po` and compiled with `msgfmt` — **never run `makemessages`** (it fuzzy-clobbers unrelated translations).
- Pre-existing baseline failures on `main` (ignore, do not fix): `apps.finance.tests.test_arrears_ladder.ArrearsLadderTests.test_send_time_validation_cancels_when_paid`, `apps.finance.tests.test_invoice_views.InvoiceViewsTests.test_api_cancel_and_write_off`, `apps.finance.tests.test_views.FinanceViewsTests.test_discount_approval_action`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Working branch: `claude/keuangan-write-actions` (already created; spec already committed).

## File Structure

- Modify `apps/finance/services/reconciliation.py` — add audit event to `resolve_discrepancy`.
- Modify `apps/finance/web_views.py` — extract `FinanceConsoleGateMixin`; add `FinanceActionView` and four concrete action views; add `can_*` flags to read-page contexts.
- Modify `apps/finance/web_urls.py` — register six action URLs.
- Create `frontend/templates/components/_console_messages.html` — renders flash messages.
- Modify `frontend/templates/components/_finance_console_styles.html` — `.fin-msg`, `.fin-actionform` styles.
- Modify `frontend/templates/pages/finance_{billing,reconciliation,receivables}.html` — include messages partial, add action forms.
- Create `apps/finance/tests/test_resolve_discrepancy_audit.py` — service-level audit test.
- Create `apps/finance/tests/test_web_console_actions.py` — all view tests (imports helper *functions* from `apps.finance.tests.test_web_console`: `make_foundation`, `make_student`, `make_invoice`, `make_user`).
- Modify `locale/en/LC_MESSAGES/django.po` + recompiled `django.mo`; modify `memory/01_PROJECT.md`.

---

### Task 1: Audit event in `resolve_discrepancy`

**Files:**
- Modify: `apps/finance/services/reconciliation.py` (`resolve_discrepancy`, ends near line 381; imports near line 15-30)
- Create: `apps/finance/tests/test_resolve_discrepancy_audit.py`

**Interfaces:**
- Consumes: `apps.core.services.audit(action, entity_type, entity_id, actor_id=None, role='', foundation_id=None, school_id=None, ip_address=None, diff=None)`.
- Produces: `resolve_discrepancy(...)` unchanged signature; now also writes an `AuditEvent` with `action='finance.reconciliation.discrepancy_resolved'`, `entity_type='PaymentDiscrepancy'`, `entity_id=str(id)`, `diff={'resolution': ..., 'notes': ...}`.

- [ ] **Step 1: Write the failing test**

Create `apps/finance/tests/test_resolve_discrepancy_audit.py`:

```python
import datetime
from decimal import Decimal

from django.test import TestCase

from apps.core.models import AuditEvent
from apps.finance.models import DiscrepancyResolution, GatewaySettlementBatch, PaymentDiscrepancy
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.finance.tests.test_web_console import make_foundation, make_user
from apps.identity.models import RoleAssignment


class ResolveDiscrepancyAuditTests(TestCase):
    def test_resolving_writes_an_audit_event(self):
        foundation, (school, _) = make_foundation('A')
        user = make_user(
            foundation, '+6281300007001', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, foundation.id, staff_school=school,
        )
        batch = GatewaySettlementBatch.objects.create(
            foundation_id=foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=foundation.id, batch=batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )

        resolve_discrepancy(
            discrepancy_id=discrepancy.id, resolution=DiscrepancyResolution.WAIVED,
            resolved_by=user, foundation_id=foundation.id, notes='tidak material',
        )

        event = AuditEvent.objects.get(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(discrepancy.id),
        )
        self.assertEqual(event.entity_type, 'PaymentDiscrepancy')
        self.assertEqual(event.foundation_id, foundation.id)
        self.assertEqual(event.actor_id, str(user.id))
        self.assertEqual(event.diff, {'resolution': 'WAIVED', 'notes': 'tidak material'})
```

- [ ] **Step 2: Run to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_resolve_discrepancy_audit 2>&1 | tail -15`
Expected: FAIL with `AuditEvent.DoesNotExist`.

- [ ] **Step 3: Implement**

In `apps/finance/services/reconciliation.py` add near the other imports: `from apps.core.services import audit`.

Replace the tail of `resolve_discrepancy`:

```python
                payment.save(update_fields=['status', 'settled_at'])

    return discrepancy
```

with:

```python
                payment.save(update_fields=['status', 'settled_at'])

        audit(
            action='finance.reconciliation.discrepancy_resolved',
            entity_type='PaymentDiscrepancy',
            entity_id=discrepancy.id,
            actor_id=str(resolved_by.id) if resolved_by else None,
            foundation_id=foundation_id,
            school_id=discrepancy.payment.school_id if discrepancy.payment else None,
            diff={'resolution': str(resolution), 'notes': notes},
        )

    return discrepancy
```

(The new `audit(...)` call sits inside the existing `with transaction.atomic():` block, at the same indent as the `if resolution == ...MANUAL_SETTLED` statement above it.)

- [ ] **Step 4: Run tests to verify pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_resolve_discrepancy_audit apps.finance.tests.test_gateway_reconciliation 2>&1 | tail -6`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/finance/services/reconciliation.py apps/finance/tests/test_resolve_discrepancy_audit.py
git commit -m "fix(finance): audit manual reconciliation discrepancy resolution

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Gate mixin, action base, messages partial

Pure refactor plus new base class; no user-visible change except flash messages now render on the three finance pages. Existing read-page tests are the regression net.

**Files:**
- Modify: `apps/finance/web_views.py` (replace `FinanceConsoleView` header, lines ~68-105)
- Create: `frontend/templates/components/_console_messages.html`
- Modify: `frontend/templates/components/_finance_console_styles.html`
- Modify: `frontend/templates/pages/finance_billing.html`, `finance_reconciliation.html`, `finance_receivables.html` (include the messages partial)
- Test: `apps/finance/tests/test_web_console_actions.py` (created here with base + gate tests)

**Interfaces:**
- Produces (used by Tasks 3-5):
  - `FinanceConsoleGateMixin` — attrs after dispatch: `self.foundation_id`, `self.school_ids` (`None` = unrestricted, else `set[int]`); method `scoped(model, school_field='school_id')`.
  - `FinanceActionView(FinanceConsoleGateMixin, View)` — subclasses set `required_permission` and implement `get_object(self, **url_kwargs)` (default `None`), `perform(self, obj) -> str` (returns success message; raises `ValueError`/`ValidationError`/`PermissionDenied` for user-facing errors), `redirect_url(self, obj) -> str`. POST-only.
  - Test helpers in the new test file: `ActionTestBase` with `self.foundation, self.school1, self.school2, self.s1, self.s2, self.admin` (foundation_admin), `self.officer` (finance_officer, foundation scope), `self.scoped_officer` (finance_officer, school1 scope); function `flashes(response) -> list[str]`.

- [ ] **Step 1: Create the shared test scaffolding**

This task adds no new behavior tests (it is a refactor; the 27 tests in `test_web_console.py` are its regression net). Flash-message rendering is asserted in Task 3. Create `apps/finance/tests/test_web_console_actions.py`:

```python
"""Keuangan console write actions (POST views): gate, tenancy, scope, service wiring."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.finance.tests.test_web_console import make_foundation, make_invoice, make_student, make_user
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import clear_current_foundation_id


def flashes(response):
    """Flash message texts on a followed response."""
    return [str(message) for message in response.context['messages']]


class ActionTestBase(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation, (self.school1, self.school2) = make_foundation('A')
        self.s1 = make_student(self.foundation, self.school1, 'Budi Satu', '0001')
        self.s2 = make_student(self.foundation, self.school2, 'Sari Dua', '0002')
        self.admin = make_user(
            self.foundation, '+6281300008001', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Admin',
        )
        self.officer = make_user(
            self.foundation, '+6281300008002', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Officer',
        )
        self.scoped_officer = make_user(
            self.foundation, '+6281300008003', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Scoped',
        )
```

- [ ] **Step 2: Baseline — confirm read-page tests pass before refactoring**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console 2>&1 | tail -4`
Expected: `OK` (27 tests).

- [ ] **Step 3: Create the messages partial**

`frontend/templates/components/_console_messages.html`:

```html
{% if messages %}
<div class="fin-msgs">
  {% for message in messages %}
  <p class="fin-msg fin-msg-{{ message.level_tag }}" role="{% if message.level_tag == 'error' %}alert{% else %}status{% endif %}">{{ message }}</p>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 4: Add styles**

Append inside the `<style>` block of `frontend/templates/components/_finance_console_styles.html`, before `</style>`:

```css
  .fin-msg { margin: 0 0 8px; padding: 10px 14px; border: 1px solid #E5DDD9; background: #fff; font-size: 13.5px; }
  .fin-msg-success { border-left: 4px solid #2E7D32; }
  .fin-msg-error { border-left: 4px solid #C8102E; }
  .fin-actionform { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .fin-actionform input[type=text], .fin-actionform select { padding: 6px 8px; border: 1px solid #E5DDD9; background: #fff; font: inherit; min-width: 140px; }
```

- [ ] **Step 5: Include the partial on the three pages**

In each of `frontend/templates/pages/finance_billing.html`, `finance_reconciliation.html`, `finance_receivables.html`, add `{% include "components/_console_messages.html" %}` immediately after the `<h1 class="fin-title">…</h1>` line.

- [ ] **Step 6: Refactor `web_views.py` — gate mixin + action base**

Add imports at the top of `apps/finance/web_views.py`:

```python
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404  # noqa: F401  (used by get_object_or_404 callers)
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View
from educore.middleware.tenancy import get_current_foundation_id, tenant_context
```

(`PermissionDenied`, `TemplateView`, `get_current_foundation_id` are already imported — merge, do not duplicate.)

Replace the whole `FinanceConsoleView` class (from `class FinanceConsoleView(...)` through its `build_context` stub) with:

```python
class FinanceConsoleGateMixin(LoginRequiredMixin):
    """Login + permission (any scope) + Staff-profile gate shared by every
    finance console view, read or write. Failing the gate is a 403 (the nav
    already hides items from users who'd fail it, so this is only reachable
    by URL). After dispatch passes, `foundation_id` and `school_ids` (None =
    unrestricted, else the caller's school ids) are set."""
    required_permission = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
            if not (
                self.foundation_id
                and has_permission_in_any_scope(request.user, self.required_permission, self.foundation_id)
                and has_staff_profile(request.user, self.foundation_id)
            ):
                raise PermissionDenied
            self.school_ids = staff_school_scope(request.user, self.foundation_id)
        return super().dispatch(request, *args, **kwargs)

    def scoped(self, model, school_field='school_id'):
        """Undeleted rows of `model` for this foundation, limited to the
        caller's schools (school_field is the ORM path to the school id)."""
        qs = model.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True)
        if self.school_ids is not None:
            qs = qs.filter(**{f'{school_field}__in': self.school_ids})
        return qs


class FinanceConsoleView(FinanceConsoleGateMixin, TemplateView):
    """Read pages: subclasses build their context in `build_context`."""
    page_title = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = self.page_title
        ctx.update(self.build_context())
        return ctx

    def build_context(self):
        raise NotImplementedError


class FinanceActionView(FinanceConsoleGateMixin, View):
    """POST-only write action: look up the (scoped) object, call an existing
    service inside the tenant context, flash the outcome, redirect back.

    Subclasses set `required_permission` and implement `get_object` (default:
    no object), `perform` (returns the success message; raises ValueError /
    ValidationError / PermissionDenied for user-facing failures) and
    `redirect_url`. A service PermissionDenied is shown as a flash error, not
    a 403 page: the user was allowed to reach the view."""
    http_method_names = ['post']

    def get_object(self, **url_kwargs):
        return None

    def perform(self, obj):
        raise NotImplementedError

    def redirect_url(self, obj):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        obj = self.get_object(**kwargs)
        try:
            with transaction.atomic(), tenant_context(self.foundation_id):
                message = self.perform(obj)
        except PermissionDenied:
            messages.error(request, _('Anda tidak berwenang melakukan tindakan ini.'))
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, message)
        return redirect(self.redirect_url(obj))
```

Delete the now-unused inline gate code from the old class (it is replaced above). `_` here is the existing `gettext_lazy` alias; `messages.error(request, lazy_str)` is fine.

- [ ] **Step 7: Run regression tests**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console apps.finance.tests.test_web_console_actions 2>&1 | tail -8`
Expected: `OK` (27 existing tests, no behavior change; the new test module has no tests yet).

- [ ] **Step 8: Commit**

```bash
git add apps/finance/web_views.py frontend/templates apps/finance/tests/test_web_console_actions.py
git commit -m "refactor(finance): shared console gate mixin and POST action base

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Resolve reconciliation discrepancy

**Files:**
- Modify: `apps/finance/web_views.py` (add `DiscrepancyResolveView`; add `can_resolve` to `ReconciliationConsoleView.build_context`)
- Modify: `apps/finance/web_urls.py`
- Modify: `frontend/templates/pages/finance_reconciliation.html`
- Test: `apps/finance/tests/test_web_console_actions.py`

**Interfaces:**
- Consumes: `FinanceActionView`, `resolve_discrepancy(discrepancy_id, resolution, resolved_by, foundation_id, notes='')` (Task 1 adds audit), `DiscrepancyResolution`.
- Produces: URL name `finance-console-discrepancy-resolve` (`reconciliation/discrepancies/<int:pk>/resolve/`, POST fields `resolution`, `notes`); context flag `can_resolve` on the reconciliation page.

- [ ] **Step 1: Write failing tests** (append to `test_web_console_actions.py`)

```python
from apps.finance.models import (
    DiscrepancyResolution, GatewaySettlementBatch, Payment, PaymentDiscrepancy, PaymentStatus,
)
from apps.core.models import AuditEvent


class DiscrepancyResolveTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        self.discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=self.foundation.id, batch=self.batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )
        self.url = reverse('finance-console-discrepancy-resolve', args=[self.discrepancy.id])

    def _post(self, **data):
        data.setdefault('resolution', 'WAIVED')
        return self.client.post(self.url, data, follow=True)

    def test_resolves_and_redirects_back_to_the_batch(self):
        self.client.force_login(self.officer)
        response = self._post(notes='tidak material')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.WAIVED)
        self.assertEqual(self.discrepancy.resolution_notes, 'tidak material')
        self.assertEqual(response.redirect_chain[-1][0].split('#')[0],
                         f"{reverse('finance-console-reconciliation')}?batch={self.batch.id}")
        self.assertEqual(len(flashes(response)), 1)
        self.assertContains(response, 'fin-msg')  # messages partial rendered on the page
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(self.discrepancy.id)).exists())

    def test_invalid_resolution_rejected_before_service(self):
        self.client.force_login(self.officer)
        response = self._post(resolution='AUTO_SETTLED')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.PENDING)
        self.assertEqual(len(flashes(response)), 1)

    def test_already_resolved_shows_error_and_keeps_first_resolution(self):
        self.client.force_login(self.officer)
        self._post(resolution='WAIVED')
        response = self._post(resolution='ESCALATED')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.WAIVED)
        self.assertIn('already', flashes(response)[0])

    def test_get_is_405(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_anonymous_redirects_to_login(self):
        response = self.client.post(self.url, {'resolution': 'WAIVED'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/web/login/', response['Location'])

    def test_user_without_payment_write_gets_403(self):
        teacher = make_user(
            self.foundation, '+6281300008010', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.url, {'resolution': 'WAIVED'}).status_code, 403)

    def test_no_staff_profile_gets_403(self):
        no_staff = make_user(
            self.foundation, '+6281300008011', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, name='NoStaff',
        )
        self.client.force_login(no_staff)
        self.assertEqual(self.client.post(self.url, {'resolution': 'WAIVED'}).status_code, 403)

    def test_other_tenants_discrepancy_is_404(self):
        other, (other_school, _) = make_foundation('B')
        foreign_batch = GatewaySettlementBatch.objects.create(
            foundation_id=other.id, provider='MIDTRANS', settlement_date=datetime.date(2026, 9, 1),
        )
        foreign = PaymentDiscrepancy.objects.create(
            foundation_id=other.id, batch=foreign_batch, external_id='EXT-9',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('5.00'),
        )
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse('finance-console-discrepancy-resolve', args=[foreign.id]), {'resolution': 'WAIVED'},
        )
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.resolution, DiscrepancyResolution.PENDING)

    def test_resolve_form_shown_only_for_pending_rows_with_write_permission(self):
        self.client.force_login(self.officer)
        page = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertContains(page, self.url)
        self._post(resolution='WAIVED')
        page = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertNotContains(page, self.url)
```

- [ ] **Step 2: Run to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions.DiscrepancyResolveTests 2>&1 | tail -10`
Expected: errors `NoReverseMatch: 'finance-console-discrepancy-resolve'`.

- [ ] **Step 3: Implement the view**

In `apps/finance/web_views.py` add imports (`reverse` from `django.urls`, `resolve_discrepancy` and `DiscrepancyResolution`):

```python
from django.urls import reverse
from apps.finance.models import DiscrepancyResolution  # add to the existing models import list
from apps.finance.services.reconciliation import resolve_discrepancy
```

Add after `ReconciliationConsoleView`:

```python
class DiscrepancyResolveView(FinanceActionView):
    """POST: settle / waive / escalate a pending gateway discrepancy. Batches
    are foundation-level (not per-school), same as the read page and API."""
    required_permission = 'finance.payment.write'
    ALLOWED_RESOLUTIONS = (
        DiscrepancyResolution.MANUAL_SETTLED,
        DiscrepancyResolution.WAIVED,
        DiscrepancyResolution.ESCALATED,
    )

    def get_object(self, pk):
        return get_object_or_404(
            PaymentDiscrepancy.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True),
            pk=pk,
        )

    def perform(self, discrepancy):
        resolution = self.request.POST.get('resolution', '')
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValueError(_('Pilihan penyelesaian tidak valid.'))
        resolve_discrepancy(
            discrepancy_id=discrepancy.id,
            resolution=resolution,
            resolved_by=self.request.user,
            foundation_id=self.foundation_id,
            notes=self.request.POST.get('notes', '').strip(),
        )
        return _('Selisih berhasil diperbarui.')

    def redirect_url(self, discrepancy):
        return f"{reverse('finance-console-reconciliation')}?batch={discrepancy.batch_id}#discrepancies"
```

In `ReconciliationConsoleView.build_context`, change the return to include the flag:

```python
        return {
            'page': page, 'selected': selected, 'discrepancies': discrepancies,
            'can_resolve': has_permission_in_any_scope(self.request.user, 'finance.payment.write', self.foundation_id),
        }
```

- [ ] **Step 4: Register the URL**

In `apps/finance/web_urls.py` import `DiscrepancyResolveView` and add:

```python
    path('reconciliation/discrepancies/<int:pk>/resolve/', DiscrepancyResolveView.as_view(),
         name='finance-console-discrepancy-resolve'),
```

- [ ] **Step 5: Add the row form to the template**

In `frontend/templates/pages/finance_reconciliation.html`, in the discrepancy table `<thead>` add `{% if can_resolve %}<th></th>{% endif %}` after the "Tindak lanjut" header, and in each row after the last `<td>` add:

```html
            {% if can_resolve %}<td>
              {% if d.resolution == "PENDING" %}
              <form method="post" action="{% url 'finance-console-discrepancy-resolve' d.id %}" class="fin-actionform">
                {% csrf_token %}
                <select name="resolution" aria-label="{% translate 'Penyelesaian' %}">
                  <option value="MANUAL_SETTLED">{% translate "Selesaikan manual" %}</option>
                  <option value="WAIVED">{% translate "Abaikan" %}</option>
                  <option value="ESCALATED">{% translate "Teruskan ke tim keuangan" %}</option>
                </select>
                <input type="text" name="notes" maxlength="500" placeholder="{% translate 'Catatan' %}">
                <button type="submit" class="btn btn-primary text-body-sm">{% translate "Simpan" %}</button>
              </form>
              {% endif %}
            </td>{% endif %}
```

Also update the page's intro note: replace the sentence "Penyelesaian selisih dilakukan melalui API keuangan." with nothing (delete it) — the resolve form now exists. The `msgid` changes; Task 6 handles the `.po`.

- [ ] **Step 6: Run tests**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions apps.finance.tests.test_web_console 2>&1 | tail -8`
Expected: `OK`. (If `test_already_resolved…` fails on the `'already'` substring, print `flashes(response)` and match the service's actual English message: "…is already 'WAIVED' - cannot resolve again.")

- [ ] **Step 7: Commit**

```bash
git add apps/finance frontend/templates
git commit -m "feat(finance): resolve reconciliation discrepancies from the web console

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Approve / reject discounts and write-offs

**Files:**
- Modify: `apps/finance/web_views.py`, `apps/finance/web_urls.py`, `frontend/templates/pages/finance_receivables.html`
- Test: `apps/finance/tests/test_web_console_actions.py`

**Interfaces:**
- Consumes: `FinanceActionView`; `approve_discount(discount, user, reason='')`, `reject_discount(discount, user, reason='')`, `approve_invoice_write_off(request_obj, user, notes='')`, `reject_invoice_write_off(request_obj, user, reason='', notes='')` from `apps.finance.services.invoicing`; `is_foundation_admin(user, foundation_id)` from `apps.identity.rbac`.
- Produces: URL names `finance-console-discount-approve|reject` (`receivables/discounts/<int:pk>/approve|reject/`, POST `reason`) and `finance-console-writeoff-approve|reject` (`receivables/write-offs/<int:pk>/approve|reject/`, POST `notes`); context flag `can_decide` on the receivables page.

- [ ] **Step 1: Write failing tests** (append to `test_web_console_actions.py`)

```python
from apps.finance.models import (
    Discount, DiscountStatus, InvoiceStatus, InvoiceWriteOffRequest, InvoiceWriteOffStatus,
)


class DiscountDecisionTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.discount = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s1, type='PERCENT', value=Decimal('50.00'),
            reason='Beasiswa', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        self.approve_url = reverse('finance-console-discount-approve', args=[self.discount.id])
        self.reject_url = reverse('finance-console-discount-reject', args=[self.discount.id])

    def test_foundation_admin_approves(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.approve_url, {'reason': 'sesuai kebijakan'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.APPROVED)
        self.assertEqual(response.redirect_chain[-1][0], reverse('finance-console-receivables'))
        self.assertEqual(len(flashes(response)), 1)
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.discount.approved', entity_id=str(self.discount.id)).exists())

    def test_foundation_admin_rejects(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'reason': 'tidak memenuhi syarat'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.REJECTED)

    def test_reject_without_reason_shows_error_and_changes_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.reject_url, {'reason': '   '}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.PENDING_APPROVAL)
        self.assertEqual(len(flashes(response)), 1)

    def test_second_reject_shows_error(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'reason': 'x'}, follow=True)
        response = self.client.post(self.reject_url, {'reason': 'x'}, follow=True)
        self.assertEqual(len(flashes(response)), 1)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.REJECTED)

    def test_finance_officer_who_is_not_foundation_admin_gets_error_and_no_change(self):
        self.client.force_login(self.officer)
        response = self.client.post(self.approve_url, {'reason': 'ok'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.PENDING_APPROVAL)
        self.assertEqual(len(flashes(response)), 1)

    def test_buttons_only_rendered_for_foundation_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)
        self.client.force_login(self.officer)
        self.assertNotContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)

    def test_out_of_scope_school_discount_is_404(self):
        self.client.force_login(self.scoped_officer)  # school1 only
        other = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s2, type='PERCENT', value=Decimal('10.00'),
            reason='Sekolah dua', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        response = self.client.post(
            reverse('finance-console-discount-approve', args=[other.id]), {'reason': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_other_tenant_discount_is_404(self):
        other, (other_school, _) = make_foundation('B')
        student = make_student(other, other_school, 'Orang Lain', '9001')
        foreign = Discount.objects.create(
            foundation_id=other.id, student=student, type='PERCENT', value=Decimal('10.00'),
            reason='Asing', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('finance-console-discount-approve', args=[foreign.id]), {'reason': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_is_405_and_anonymous_redirects(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.approve_url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.approve_url, {'reason': 'x'}).status_code, 302)

    def test_user_without_invoice_write_gets_403(self):
        teacher = make_user(
            self.foundation, '+6281300008020', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.approve_url, {'reason': 'x'}).status_code, 403)


class WriteOffDecisionTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        self.write_off = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=self.invoice, school=self.school1,
            amount=Decimal('1500000.00'), reason='Siswa pindah', requested_by=self.officer,
        )
        self.approve_url = reverse('finance-console-writeoff-approve', args=[self.write_off.id])
        self.reject_url = reverse('finance-console-writeoff-reject', args=[self.write_off.id])

    def test_foundation_admin_approves_and_invoice_is_written_off(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.approve_url, {'notes': 'disetujui'}, follow=True)
        self.write_off.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.APPROVED)
        self.assertEqual(self.invoice.status, InvoiceStatus.WRITTEN_OFF)
        self.assertEqual(len(flashes(response)), 1)

    def test_foundation_admin_rejects_with_notes(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'notes': 'masih bisa ditagih'}, follow=True)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.REJECTED)
        self.assertEqual(self.write_off.rejection_reason, 'masih bisa ditagih')

    def test_second_decision_shows_error(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'notes': 'x'}, follow=True)
        response = self.client.post(self.approve_url, {'notes': 'y'}, follow=True)
        self.assertEqual(len(flashes(response)), 1)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.REJECTED)

    def test_non_foundation_admin_gets_error_and_no_change(self):
        self.client.force_login(self.officer)
        response = self.client.post(self.approve_url, {'notes': 'x'}, follow=True)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.PENDING)
        self.assertEqual(len(flashes(response)), 1)

    def test_out_of_scope_and_other_tenant_are_404(self):
        s2_invoice = make_invoice(self.foundation, self.s2, 'INV/A2/2026/000001')
        other_school_request = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=s2_invoice, school=self.school2,
            amount=Decimal('1500000.00'), reason='x', requested_by=self.officer,
        )
        self.client.force_login(self.scoped_officer)
        self.assertEqual(self.client.post(
            reverse('finance-console-writeoff-approve', args=[other_school_request.id]), {}).status_code, 404)

        other, (other_school, _) = make_foundation('B')
        student = make_student(other, other_school, 'Orang Lain', '9001')
        foreign_invoice = make_invoice(other, student, 'INV/B1/2026/000001')
        foreign_user = make_user(
            other, '+6281300008030', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, other.id, staff_school=other_school, name='B',
        )
        foreign = InvoiceWriteOffRequest.objects.create(
            foundation_id=other.id, invoice=foreign_invoice, school=other_school,
            amount=Decimal('1500000.00'), reason='x', requested_by=foreign_user,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(
            reverse('finance-console-writeoff-approve', args=[foreign.id]), {}).status_code, 404)

    def test_buttons_only_rendered_for_foundation_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)
        self.client.force_login(self.officer)
        self.assertNotContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)
```

- [ ] **Step 2: Run to verify failures**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions.DiscountDecisionTests apps.finance.tests.test_web_console_actions.WriteOffDecisionTests 2>&1 | tail -6`
Expected: `NoReverseMatch` errors.

- [ ] **Step 3: Implement the views**

In `apps/finance/web_views.py` add imports:

```python
from apps.finance.services.invoicing import (
    approve_discount,
    approve_invoice_write_off,
    reject_discount,
    reject_invoice_write_off,
)
from apps.identity.rbac import has_permission_in_any_scope, is_foundation_admin  # extend existing rbac import
```

Add after `ReceivablesConsoleView`:

```python
class _DecisionView(FinanceActionView):
    """Approve/reject pair. `decision` is fixed per URL via as_view(decision=...).
    Approval and rejection authority (foundation admin) is enforced by the
    services; the buttons are only *shown* to foundation admins."""
    required_permission = 'finance.invoice.write'
    decision = None  # 'approve' | 'reject'

    def redirect_url(self, obj):
        return reverse('finance-console-receivables')


class DiscountDecisionView(_DecisionView):
    def get_object(self, pk):
        return get_object_or_404(self.scoped(Discount, 'student__school_id'), pk=pk)

    def perform(self, discount):
        reason = self.request.POST.get('reason', '').strip()
        if self.decision == 'approve':
            approve_discount(discount, self.request.user, reason=reason)
            return _('Keringanan disetujui.')
        reject_discount(discount, self.request.user, reason=reason)
        return _('Keringanan ditolak.')


class WriteOffDecisionView(_DecisionView):
    def get_object(self, pk):
        return get_object_or_404(self.scoped(InvoiceWriteOffRequest), pk=pk)

    def perform(self, write_off):
        notes = self.request.POST.get('notes', '').strip()
        if self.decision == 'approve':
            approve_invoice_write_off(request_obj=write_off, user=self.request.user, notes=notes)
            return _('Penghapusbukuan disetujui.')
        reject_invoice_write_off(request_obj=write_off, user=self.request.user, notes=notes)
        return _('Penghapusbukuan ditolak.')
```

In `ReceivablesConsoleView.build_context`, add `'can_decide': is_foundation_admin(self.request.user, self.foundation_id),` to the returned dict.

- [ ] **Step 4: Register URLs**

In `apps/finance/web_urls.py` import both views and add:

```python
    path('receivables/discounts/<int:pk>/approve/', DiscountDecisionView.as_view(decision='approve'),
         name='finance-console-discount-approve'),
    path('receivables/discounts/<int:pk>/reject/', DiscountDecisionView.as_view(decision='reject'),
         name='finance-console-discount-reject'),
    path('receivables/write-offs/<int:pk>/approve/', WriteOffDecisionView.as_view(decision='approve'),
         name='finance-console-writeoff-approve'),
    path('receivables/write-offs/<int:pk>/reject/', WriteOffDecisionView.as_view(decision='reject'),
         name='finance-console-writeoff-reject'),
```

- [ ] **Step 5: Add forms to `finance_receivables.html`**

Discounts table: add `{% if can_decide %}<th></th>{% endif %}` to `<thead>` and, per row, after the "Berlaku" cell:

```html
            {% if can_decide %}<td>
              <form method="post" action="{% url 'finance-console-discount-approve' d.id %}" class="fin-actionform">
                {% csrf_token %}
                <input type="text" name="reason" required maxlength="255" placeholder="{% translate 'Alasan keputusan' %}">
                <button type="submit" class="btn btn-primary text-body-sm">{% translate "Setujui" %}</button>
                <button type="submit" formaction="{% url 'finance-console-discount-reject' d.id %}" class="btn btn-secondary text-body-sm">{% translate "Tolak" %}</button>
              </form>
            </td>{% endif %}
```

Write-offs table: same header addition and per row after the "Nominal" cell:

```html
            {% if can_decide %}<td>
              <form method="post" action="{% url 'finance-console-writeoff-approve' w.id %}" class="fin-actionform">
                {% csrf_token %}
                <input type="text" name="notes" maxlength="500" placeholder="{% translate 'Catatan' %}">
                <button type="submit" class="btn btn-primary text-body-sm">{% translate "Setujui" %}</button>
                <button type="submit" formaction="{% url 'finance-console-writeoff-reject' w.id %}" class="btn btn-secondary text-body-sm">{% translate "Tolak" %}</button>
              </form>
            </td>{% endif %}
```

- [ ] **Step 6: Run tests**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions apps.finance.tests.test_web_console 2>&1 | tail -8`
Expected: `OK`. If write-off approval errors on ledger prerequisites (`post_write_off_journal` needing accounts/fiscal period), mirror the fixture setup from `apps/finance/tests/test_write_offs.py::WriteOffWorkflowTests.setUp` in `WriteOffDecisionTests.setUp` — do not change service code.

- [ ] **Step 7: Commit**

```bash
git add apps/finance frontend/templates
git commit -m "feat(finance): approve/reject discounts and write-offs from the web console

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Record cash payment

**Files:**
- Modify: `apps/finance/web_views.py`, `apps/finance/web_urls.py`, `frontend/templates/pages/finance_billing.html`
- Test: `apps/finance/tests/test_web_console_actions.py`

**Interfaces:**
- Consumes: `FinanceActionView`; `record_cash_payment(school, student, amount: Decimal, invoice_ids=None, received_by=None, notes='') -> Payment` from `apps.finance.services.payments`; `Student` from `apps.identity.models`.
- Produces: URL name `finance-console-cash-payment` (`billing/cash/`, POST `nis`, `amount`, `notes`); context flag `can_record_cash` on the billing page.

- [ ] **Step 1: Write failing tests** (append)

```python
from apps.finance.models import Payment, PaymentMethod


class CashPaymentTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.url = reverse('finance-console-cash-payment')
        self.invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')

    def _post(self, **data):
        data.setdefault('nis', '0001')
        data.setdefault('amount', '500000')
        return self.client.post(self.url, data, follow=True)

    def test_records_cash_payment_and_allocates_to_open_invoice(self):
        self.client.force_login(self.officer)
        response = self._post(notes='setoran tunai')
        payment = Payment.objects.get(student=self.s1)
        self.assertEqual(payment.method, PaymentMethod.CASH)
        self.assertEqual(payment.amount, Decimal('500000.00'))
        self.assertEqual(payment.received_by, self.officer)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('500000.00'))
        self.assertEqual(response.redirect_chain[-1][0], reverse('finance-console-billing'))
        self.assertIn(payment.receipt_number, flashes(response)[0])

    def test_bad_amounts_create_nothing(self):
        self.client.force_login(self.officer)
        for bad in ('0', '-5', 'abc', '10.999', 'NaN', 'Infinity', ''):
            response = self._post(amount=bad)
            self.assertEqual(len(flashes(response)), 1, bad)
        self.assertFalse(Payment.objects.exists())

    def test_unknown_nis_creates_nothing(self):
        self.client.force_login(self.officer)
        response = self._post(nis='9999')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.objects.exists())

    def test_school_scoped_officer_cannot_pay_for_another_school(self):
        self.client.force_login(self.scoped_officer)  # school1 only; s2 is school2
        response = self._post(nis='0002')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.objects.exists())

    def test_other_tenants_nis_is_not_found(self):
        other, (other_school, _) = make_foundation('B')
        make_student(other, other_school, 'Orang Lain', '7777')
        self.client.force_login(self.admin)
        response = self._post(nis='7777')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.objects.exists())

    def test_ambiguous_nis_across_schools_is_refused(self):
        make_student(self.foundation, self.school2, 'Kembar NIS', '0001')  # same NIS, other school
        self.client.force_login(self.officer)  # foundation-wide: sees both
        response = self._post(nis='0001')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.objects.exists())

    def test_get_is_405_anonymous_redirects_no_permission_403(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {}).status_code, 302)
        teacher = make_user(
            self.foundation, '+6281300008040', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.url, {'nis': '0001', 'amount': '1'}).status_code, 403)

    def test_cash_form_shown_on_billing_page_for_writers_only(self):
        self.client.force_login(self.officer)
        self.assertContains(self.client.get(reverse('finance-console-billing')), self.url)
```

- [ ] **Step 2: Run to verify failures**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions.CashPaymentTests 2>&1 | tail -6`
Expected: `NoReverseMatch: 'finance-console-cash-payment'`.

- [ ] **Step 3: Implement the view**

Add imports to `apps/finance/web_views.py`:

```python
from decimal import Decimal, InvalidOperation  # extend the existing Decimal import
from apps.finance.services.payments import record_cash_payment
from apps.identity.models import Student
```

Add after `BillingConsoleView`:

```python
class CashPaymentView(FinanceActionView):
    """POST: record a cash payment for a student found by NIS within the
    caller's schools. The service allocates to open invoices oldest-first,
    posts the ledger journal and audits. A NIS matching more than one student
    (possible across schools) is refused rather than guessed."""
    required_permission = 'finance.invoice.write'

    def redirect_url(self, obj):
        return reverse('finance-console-billing')

    def _parse_amount(self, raw):
        try:
            amount = Decimal(raw.strip())
        except (InvalidOperation, AttributeError):
            raise ValueError(_('Jumlah tidak valid.'))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('0.01')):
            raise ValueError(_('Jumlah harus lebih dari nol dengan maksimal dua desimal.'))
        return amount

    def _find_student(self, nis):
        students = Student.all_tenants.filter(
            foundation_id=self.foundation_id, deleted_at__isnull=True, nis=nis,
        ).select_related('school')
        if self.school_ids is not None:
            students = students.filter(school_id__in=self.school_ids)
        matches = list(students[:2])
        if not matches:
            raise ValueError(_('Siswa dengan NIS tersebut tidak ditemukan.'))
        if len(matches) > 1:
            raise ValueError(_('NIS cocok dengan lebih dari satu siswa. Gunakan API keuangan untuk memilih siswa.'))
        return matches[0]

    def perform(self, obj):
        amount = self._parse_amount(self.request.POST.get('amount', ''))
        student = self._find_student(self.request.POST.get('nis', '').strip())
        payment = record_cash_payment(
            school=student.school,
            student=student,
            amount=amount,
            received_by=self.request.user,
            notes=self.request.POST.get('notes', '').strip(),
        )
        return _('Pembayaran tunai tercatat. No. kwitansi: %(receipt)s') % {'receipt': payment.receipt_number}
```

`_('...') % {...}` on a lazy string returns a plain `str` (lazy `%` evaluates) — acceptable for `messages.success`.

In `BillingConsoleView.build_context` add to the returned dict:

```python
            'can_record_cash': has_permission_in_any_scope(self.request.user, 'finance.invoice.write', self.foundation_id),
```

- [ ] **Step 4: Register URL**

```python
    path('billing/cash/', CashPaymentView.as_view(), name='finance-console-cash-payment'),
```

- [ ] **Step 5: Add the cash form to `finance_billing.html`**

Insert a new `<section>` right after the tiles loop and before the filter `<form>`:

```html
  {% if can_record_cash %}
  <section>
    <h2 class="fin-h2">{% translate "Catat pembayaran tunai" %}</h2>
    <form method="post" action="{% url 'finance-console-cash-payment' %}" class="fin-filters" onsubmit="this.querySelector('button[type=submit]').disabled = true">
      {% csrf_token %}
      <label><span class="fin-label">{% translate "NIS siswa" %}</span>
        <input type="text" name="nis" required maxlength="32" autocomplete="off"></label>
      <label><span class="fin-label">{% translate "Jumlah (Rp)" %}</span>
        <input type="text" name="amount" required inputmode="decimal" autocomplete="off"></label>
      <label><span class="fin-label">{% translate "Catatan" %}</span>
        <input type="text" name="notes" maxlength="255"></label>
      <button type="submit" class="btn btn-primary text-body-sm">{% translate "Catat pembayaran" %}</button>
    </form>
    <p class="fin-note">{% translate "Dialokasikan otomatis ke tagihan terbuka yang paling lama." %}</p>
  </section>
  {% endif %}
```

- [ ] **Step 6: Run tests**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console_actions apps.finance.tests.test_web_console 2>&1 | tail -8`
Expected: `OK`. If `Payment.objects.get(...)`/`Payment.objects.exists()` return nothing because `objects` is tenant-scoped with no ambient foundation, use `Payment.all_tenants` in the tests.

- [ ] **Step 7: Commit**

```bash
git add apps/finance frontend/templates
git commit -m "feat(finance): record cash payments from the web console billing page

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: i18n, project memory, sync, PR

**Files:**
- Modify: `locale/en/LC_MESSAGES/django.po` (+ recompiled `django.mo`), `memory/01_PROJECT.md`

**Interfaces:** none.

- [ ] **Step 1: Collect new msgids**

Run this from the repo root; it prints every `{% translate %}`/`blocktranslate`/`_('…')` string in the touched files that has no `msgid` in the EN `.po` yet:

```bash
python3 - <<'EOF'
import re
po = open('locale/en/LC_MESSAGES/django.po').read()
existing = set(re.findall(r'^msgid "(.*)"$', po, re.M))
files = ['apps/finance/web_views.py',
         'frontend/templates/pages/finance_billing.html',
         'frontend/templates/pages/finance_reconciliation.html',
         'frontend/templates/pages/finance_receivables.html']
for f in files:
    t = open(f).read()
    found = re.findall(r"""\{% translate (?:"([^"]*)"|'([^']*)')""", t)
    found = [a or b for a, b in found]
    if f.endswith('.py'):
        found += [a or b for a, b in re.findall(r"""_\((?:'([^']*)'|"([^"]*)")\)""", t)]
    for s in found:
        if s and s not in existing:
            print(repr(s))
EOF
```

For the `%`-formatted receipt message the msgid is exactly `Pembayaran tunai tercatat. No. kwitansi: %(receipt)s` (python-format).

- [ ] **Step 2: Append EN entries and compile**

Append one `msgid`/`msgstr` block per printed string (add `#, python-format` above the receipt one) to `locale/en/LC_MESSAGES/django.po`. Use these translations (skip any msgid the script did not print — duplicates break `msgfmt`):

| msgid | msgstr |
|---|---|
| Pilihan penyelesaian tidak valid. | Invalid resolution choice. |
| Selisih berhasil diperbarui. | Discrepancy updated. |
| Keringanan disetujui. | Discount approved. |
| Keringanan ditolak. | Discount rejected. |
| Penghapusbukuan disetujui. | Write-off approved. |
| Penghapusbukuan ditolak. | Write-off rejected. |
| Anda tidak berwenang melakukan tindakan ini. | You are not authorized to perform this action. |
| Jumlah tidak valid. | Invalid amount. |
| Jumlah harus lebih dari nol dengan maksimal dua desimal. | Amount must be greater than zero with at most two decimals. |
| Siswa dengan NIS tersebut tidak ditemukan. | No student found with that NIS. |
| NIS cocok dengan lebih dari satu siswa. Gunakan API keuangan untuk memilih siswa. | NIS matches more than one student. Use the finance API to choose the student. |
| Pembayaran tunai tercatat. No. kwitansi: %(receipt)s | Cash payment recorded. Receipt no.: %(receipt)s |
| Penyelesaian | Resolution |
| Selesaikan manual | Settle manually |
| Abaikan | Waive |
| Teruskan ke tim keuangan | Escalate to finance team |
| Catatan | Notes |
| Simpan | Save |
| Alasan keputusan | Reason for decision |
| Setujui | Approve |
| Tolak | Reject |
| Catat pembayaran tunai | Record cash payment |
| NIS siswa | Student NIS |
| Jumlah (Rp) | Amount (Rp) |
| Catat pembayaran | Record payment |
| Dialokasikan otomatis ke tagihan terbuka yang paling lama. | Automatically allocated to the oldest open invoices. |

Then compile: `msgfmt --check -o locale/en/LC_MESSAGES/django.mo locale/en/LC_MESSAGES/django.po && echo compiled`
Expected: `compiled`. Rerun the Step 1 script: it must print nothing.

- [ ] **Step 3: Update `memory/01_PROJECT.md`**

Upsert (do not append a new log entry): edit the existing **Current Step** paragraph for the Keuangan module so its header line reads `Web Console: Keuangan write actions [PR open …]`, summarising: the four actions, the `FinanceConsoleGateMixin`/`FinanceActionView` structure, the new audit event in `resolve_discrepancy`, and the known limitation (`MANUAL_SETTLED` flips a payment to SETTLED without allocation/journal — logged as a Notion Todo). Demote the previous Current Step (the merged read-only Keuangan pages, PR #201) to **Preceding Step**.

- [ ] **Step 4: Log the follow-up Notion Todo**

Create a Notion task in "Astra Educore" (data source `755347a6-6594-8379-97bb-877f515199e1`), Status `Todo`, Branch `backlog/open-items`, titled `Reconciliation: MANUAL_SETTLED resolution settles a payment without invoice allocation or ledger journal`, describing the gap in `apps/finance/services/reconciliation.py::resolve_discrepancy` and pointing at the console spec's "Known limitations".

- [ ] **Step 5: Targeted verification**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance.tests.test_web_console apps.finance.tests.test_web_console_actions apps.finance.tests.test_resolve_discrepancy_audit apps.finance.tests.test_gateway_reconciliation apps.finance.tests.test_write_offs apps.finance.tests.test_discounts_and_sibling apps.finance.tests.test_payments apps.identity.tests.test_nav apps.identity.tests.test_console_landing 2>&1 | tail -6`
Expected: `OK`.

Then the wider suite for the two touched apps, expecting only the 3 baseline failures listed in Global Constraints:
`EDUCORE_USE_SQLITE=1 python3 manage.py test apps.finance apps.identity 2>&1 | grep -E "^(FAIL|ERROR|Ran|OK)"`

- [ ] **Step 6: Commit, sync with main, push, open PR**

```bash
git add -A
git commit -m "i18n(finance): EN strings for Keuangan console write actions; update project memory

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git fetch origin main && git rebase origin/main
```

If the rebase conflicts, follow AGENTS.md Pre-PR protocol: keep both sides (never drop upstream logic); in `locale/.../django.po` keep both appended blocks and dedupe msgids, then recompile with `msgfmt`; rerun Step 5's targeted tests. Then:

```bash
git push -u origin claude/keuangan-write-actions --force-with-lease
gh pr create --base main --title "feat(finance): Keuangan console write actions" --body "<summary, spec/plan links, test plan, baseline failures note; end with the Claude Code attribution line>"
```

Then update the Notion task `3df347a6-6594-8114-b428-cb2f915d7a7e` Logs with the PR link (keep status `In progress` until the merge is confirmed with `gh pr view`). **Do not merge or deploy without an explicit user instruction.**
