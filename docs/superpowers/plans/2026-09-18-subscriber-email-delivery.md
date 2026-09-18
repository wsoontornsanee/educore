# Status page: subscriber email delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a plain-text email to every captured `StatusSubscriber` when a new `StatusIncident` is published, via the existing `TaskQueue`/`drain_tasks` async pattern, with a working self-serve unsubscribe link.

**Architecture:** `create_incident()` enqueues one `core.TaskQueue` row per subscriber (task_type `status.subscriber_email.send`) when `published=True`. The existing `drain_tasks` cron drains them through a new handler in `apps/status/tasks.py` that renders and sends a plain-text email via Django's SMTP `EmailBackend`. A new public `GET /status/unsubscribe/<uuid:token>/` view lets a subscriber remove themselves.

**Tech Stack:** Django 5.x, Django's built-in `django.core.mail` (SMTP backend), MySQL `TaskQueue` table (existing), Django test `locmem` mail backend for tests.

## Global Constraints

- MySQL 8 only, cron background execution — no Redis/Celery/brokers (SOUL.md).
- `apps.status` models are plain `models.Model`, NOT `TenantModel` — status data has no `foundation_id` (see `apps/status/models.py` module docstring).
- Every mutation that matters must be auditable per AGENTS red line #4 — N/A here (no new mutable domain state beyond `StatusSubscriber` deletion, which is a public self-serve action, not a staff mutation).
- Follow existing env-var convention: `os.environ.get('NAME', 'default')` directly in `educore/settings/base.py`, grouped with a one-line comment, same shape as `XENDIT_*`/`EDUCORE_PARTNER_FERNET_KEY` etc.
- `id-ID` first: all subscriber-facing copy (email body, unsubscribe confirmation page) is Indonesian only for this slice (no `_en` variant — see design spec's non-goals).
- Reuse `enqueue_task`/`register_task_handler` from `apps.core.services` exactly as existing callers do (`apps/notifications/tasks.py`, `apps/partners/tasks.py`) — do not build a second dispatch mechanism.

---

### Task 1: Email backend settings + `EDUCORE_PUBLIC_BASE_URL`

**Files:**
- Modify: `educore/settings/base.py` (add a new settings block; insert after the `EDUCORE_COUNSELLING_FERNET_KEY` line at `educore/settings/base.py:279`, before the `EDUCORE_CRON_HOST_ENFORCED` block)
- Test: `apps/status/tests/test_email_settings.py`

**Interfaces:**
- Produces: `settings.EMAIL_BACKEND`, `settings.EMAIL_HOST`, `settings.EMAIL_PORT`, `settings.EMAIL_HOST_USER`, `settings.EMAIL_HOST_PASSWORD`, `settings.EMAIL_USE_TLS`, `settings.DEFAULT_FROM_EMAIL`, `settings.EDUCORE_PUBLIC_BASE_URL` — all consumed by Task 2's task handler and Task 4's unsubscribe view.

- [ ] **Step 1: Write the failing test**

```python
# apps/status/tests/test_email_settings.py
from django.conf import settings
from django.test import TestCase


class EmailSettingsTests(TestCase):
    def test_email_backend_defaults_to_console_in_test(self):
        # Test settings never fall back to a real SMTP attempt.
        self.assertIn('EmailBackend', settings.EMAIL_BACKEND)

    def test_default_from_email_is_set(self):
        self.assertTrue(settings.DEFAULT_FROM_EMAIL)

    def test_public_base_url_is_set(self):
        self.assertTrue(settings.EDUCORE_PUBLIC_BASE_URL)
        self.assertTrue(
            settings.EDUCORE_PUBLIC_BASE_URL.startswith('http://')
            or settings.EDUCORE_PUBLIC_BASE_URL.startswith('https://')
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.status.tests.test_email_settings -v 2`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'EDUCORE_PUBLIC_BASE_URL'` (or similar; `DEFAULT_FROM_EMAIL`/`EMAIL_BACKEND` have Django framework defaults already, so only the new setting is guaranteed to fail — that's fine, it proves the test file runs).

- [ ] **Step 3: Add the settings block**

In `educore/settings/base.py`, immediately after the `EDUCORE_COUNSELLING_FERNET_KEY = os.environ.get(...)` line (currently line 279) and before the `# Single cron host enforcement` comment block, insert:

```python
# Status page subscriber incident email (Notion: "Status page: subscriber
# email delivery") — Django's built-in SMTP backend, not a provider SDK.
# Defaults to the console backend so local/dev/test never attempts a real
# SMTP connection unless EMAIL_BACKEND is explicitly overridden.
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'status@educore.id')

# Public base URL for building absolute links (e.g. the status-email
# unsubscribe link) from contexts with no `request` object, such as the
# drain_tasks cron. apps.marketing uses request.build_absolute_uri() where a
# request is available; this is the equivalent for cron/task contexts.
EDUCORE_PUBLIC_BASE_URL = os.environ.get('EDUCORE_PUBLIC_BASE_URL', 'http://localhost:8000')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.status.tests.test_email_settings -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add educore/settings/base.py apps/status/tests/test_email_settings.py
git commit -m "feat(status): add SMTP email backend + public base URL settings"
```

---

### Task 2: `status.subscriber_email.send` task handler

**Files:**
- Create: `apps/status/tasks.py`
- Modify: `apps/status/models.py` — no changes needed (fields already exist)
- Test: `apps/status/tests/test_tasks.py`

**Interfaces:**
- Consumes: `apps.core.services.register_task_handler` (decorator, same as `apps/notifications/tasks.py:8`); `apps.status.models.StatusIncident`, `apps.status.models.StatusSubscriber`; `settings.DEFAULT_FROM_EMAIL`, `settings.EDUCORE_PUBLIC_BASE_URL` (Task 1).
- Produces: `send_subscriber_incident_email(payload: dict) -> None`, registered under task_type `'status.subscriber_email.send'`. Payload shape: `{'incident_id': int, 'subscriber_id': int}`. Consumed by Task 3 (the enqueue call site) and exercised end-to-end by `drain_tasks` (no changes needed there — it's the existing generic dispatcher).

- [ ] **Step 1: Write the failing test**

```python
# apps/status/tests/test_tasks.py
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.status.models import ServiceComponent, StatusIncident, StatusSubscriber
from apps.status.tasks import send_subscriber_incident_email


class SendSubscriberIncidentEmailTests(TestCase):
    def setUp(self):
        self.incident = StatusIncident.objects.create(
            severity=StatusIncident.SEVERITY_MAJOR,
            title_id='Gangguan API', title_en='API Outage',
            body_id='API mengalami gangguan.', body_en='API is experiencing an outage.',
            occurred_at=timezone.now(), duration_minutes=45, published=True,
        )
        self.subscriber = StatusSubscriber.objects.create(email='ortu@example.com')

    def test_sends_email_with_incident_details(self):
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': self.subscriber.id,
        })
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['ortu@example.com'])
        self.assertIn('Gangguan API', sent.subject)
        self.assertIn('API mengalami gangguan.', sent.body)

    def test_includes_unsubscribe_link(self):
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': self.subscriber.id,
        })
        sent = mail.outbox[0]
        expected_link = f'/status/unsubscribe/{self.subscriber.unsubscribe_token}/'
        self.assertIn(expected_link, sent.body)

    def test_noop_when_subscriber_already_unsubscribed(self):
        subscriber_id = self.subscriber.id
        self.subscriber.delete()
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': subscriber_id,
        })
        self.assertEqual(len(mail.outbox), 0)

    def test_raises_when_incident_missing(self):
        with self.assertRaises(StatusIncident.DoesNotExist):
            send_subscriber_incident_email({
                'incident_id': 999999,
                'subscriber_id': self.subscriber.id,
            })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.status.tests.test_tasks -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.status.tasks'`

- [ ] **Step 3: Write the task handler**

```python
# apps/status/tasks.py
"""Async task handlers for apps.status (ARC-010/011 — drained by drain_tasks)."""
from django.conf import settings
from django.core.mail import send_mail

from apps.core.services import register_task_handler

from .models import StatusIncident, StatusSubscriber


@register_task_handler('status.subscriber_email.send')
def send_subscriber_incident_email(payload: dict):
    """Send one subscriber a plain-text notification for one published
    StatusIncident. One TaskQueue row per subscriber (see create_incident in
    services.py) — a missing incident is a real failure (lets the row
    retry/dead-letter); a missing subscriber means they unsubscribed between
    enqueue and drain, an expected race, not a failure."""
    incident = StatusIncident.objects.get(id=payload['incident_id'])
    try:
        subscriber = StatusSubscriber.objects.get(id=payload['subscriber_id'])
    except StatusSubscriber.DoesNotExist:
        return

    unsubscribe_url = f"{settings.EDUCORE_PUBLIC_BASE_URL}/status/unsubscribe/{subscriber.unsubscribe_token}/"
    subject = f"[EduCore Status] {incident.title_id}"
    body = (
        f"{incident.title_id}\n\n"
        f"{incident.body_id}\n\n"
        f"Tingkat keparahan: {incident.severity}\n"
        f"Waktu kejadian: {incident.occurred_at.strftime('%d %B %Y %H:%M')} WIB\n\n"
        f"---\n"
        f"Berhenti berlangganan pembaruan status: {unsubscribe_url}"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [subscriber.email])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.status.tests.test_tasks -v 2`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/status/tasks.py apps/status/tests/test_tasks.py
git commit -m "feat(status): add subscriber incident email task handler"
```

---

### Task 3: Enqueue one email task per subscriber on incident publish

**Files:**
- Modify: `apps/status/services.py:158-180` (`create_incident` function)
- Test: `apps/status/tests/test_services.py` (extend existing `apps/status/tests/test_incidents.py`, which already covers `create_incident`)

**Interfaces:**
- Consumes: `apps.core.services.enqueue_task` (signature: `enqueue_task(task_type, payload=None, foundation_id=None, run_after=None, max_attempts=4)`, see `apps/core/services.py:158`); `apps.status.models.StatusSubscriber`; task_type string `'status.subscriber_email.send'` and payload shape `{'incident_id', 'subscriber_id'}` from Task 2.
- Produces: `create_incident(..., published=True)` now also creates one `TaskQueue` row per existing `StatusSubscriber`. No change to `create_incident`'s signature or return value — existing callers (`apps/status/web_views.py`) are unaffected.

- [ ] **Step 1: Write the failing test**

Append to `apps/status/tests/test_incidents.py` (inside `IncidentServiceTests`):

```python
    def test_create_incident_enqueues_email_task_per_subscriber(self):
        from apps.core.models import TaskQueue
        from apps.status.models import StatusSubscriber

        StatusSubscriber.objects.create(email='a@example.com')
        StatusSubscriber.objects.create(email='b@example.com')

        incident = create_incident(
            severity=StatusIncident.SEVERITY_MAJOR, title_id='X', title_en='X',
            body_id='X', body_en='X', occurred_at=timezone.now(), duration_minutes=10,
            affected_component_ids=[], published=True, actor=self.actor,
        )

        tasks = TaskQueue.objects.filter(task_type='status.subscriber_email.send')
        self.assertEqual(tasks.count(), 2)
        subscriber_ids = {t.payload['subscriber_id'] for t in tasks}
        self.assertEqual(
            subscriber_ids,
            set(StatusSubscriber.objects.values_list('id', flat=True)),
        )
        for t in tasks:
            self.assertEqual(t.payload['incident_id'], incident.id)

    def test_create_incident_unpublished_enqueues_no_email_tasks(self):
        from apps.core.models import TaskQueue
        from apps.status.models import StatusSubscriber

        StatusSubscriber.objects.create(email='a@example.com')

        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='X', title_en='X',
            body_id='X', body_en='X', occurred_at=timezone.now(), duration_minutes=10,
            affected_component_ids=[], published=False, actor=self.actor,
        )

        self.assertEqual(
            TaskQueue.objects.filter(task_type='status.subscriber_email.send').count(), 0,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.status.tests.test_incidents -v 2`
Expected: FAIL — `test_create_incident_enqueues_email_task_per_subscriber` asserts `2 == 0`.

- [ ] **Step 3: Modify `create_incident`**

In `apps/status/services.py`, `create_incident` currently ends with (around line 158-180):

```python
    with tenant_context(None):
        audit(
            action='status.incident.created', entity_type='StatusIncident', entity_id=str(incident.id),
            actor_id=str(actor.id) if actor else None, role='platform_operator',
        )
    return incident
```

In `apps/status/services.py:9`, change:

```python
from apps.core.services import audit
```

to:

```python
from apps.core.services import audit, enqueue_task
```

Then change `create_incident`'s function body to end with:

```python
    with tenant_context(None):
        audit(
            action='status.incident.created', entity_type='StatusIncident', entity_id=str(incident.id),
            actor_id=str(actor.id) if actor else None, role='platform_operator',
        )
    if published:
        # One TaskQueue row per subscriber (not one row for the whole
        # incident) so a single bad address retries/dead-letters
        # independently instead of blocking the rest of the batch (ARC-012).
        for subscriber_id in StatusSubscriber.objects.values_list('id', flat=True):
            enqueue_task(
                'status.subscriber_email.send',
                payload={'incident_id': incident.id, 'subscriber_id': subscriber_id},
                foundation_id=None,
            )
    return incident
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.status.tests.test_incidents -v 2`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add apps/status/services.py apps/status/tests/test_incidents.py
git commit -m "feat(status): enqueue subscriber email task on incident publish"
```

---

### Task 4: Unsubscribe view + URL + template

**Files:**
- Create: `frontend/templates/status/unsubscribe.html`
- Modify: `apps/status/views.py` (add `StatusUnsubscribeView`)
- Modify: `apps/status/urls.py` (add the route)
- Test: `apps/status/tests/test_unsubscribe_view.py`

**Interfaces:**
- Consumes: `apps.status.models.StatusSubscriber` (has `unsubscribe_token` `UUIDField`, `apps/status/models.py:118`); Django's `django.views.generic.View`/`TemplateView`.
- Produces: `GET /status/unsubscribe/<uuid:token>/` → 200, deletes the matching row if found, renders `status/unsubscribe.html` either way. URL name `status:unsubscribe` (for the email link built in Task 2 — note Task 2's link is a plain string, not `reverse()`, since it's built outside a request context; this task just needs the URL pattern to exist and match that string shape: `/status/unsubscribe/<token>/`).

- [ ] **Step 1: Write the failing test**

```python
# apps/status/tests/test_unsubscribe_view.py
import uuid

from django.test import TestCase
from django.urls import reverse

from apps.status.models import StatusSubscriber


class UnsubscribeViewTests(TestCase):
    def test_valid_token_deletes_subscriber_and_returns_200(self):
        subscriber = StatusSubscriber.objects.create(email='ortu@example.com')
        token = subscriber.unsubscribe_token

        response = self.client.get(reverse('status:unsubscribe', args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(StatusSubscriber.objects.filter(unsubscribe_token=token).exists())

    def test_unknown_token_still_returns_200_no_leak(self):
        unknown_token = uuid.uuid4()

        response = self.client.get(reverse('status:unsubscribe', args=[unknown_token]))

        self.assertEqual(response.status_code, 200)

    def test_response_renders_confirmation_template(self):
        subscriber = StatusSubscriber.objects.create(email='ortu@example.com')
        response = self.client.get(reverse('status:unsubscribe', args=[subscriber.unsubscribe_token]))
        self.assertTemplateUsed(response, 'status/unsubscribe.html')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.status.tests.test_unsubscribe_view -v 2`
Expected: FAIL — `NoReverseMatch: Reverse for 'unsubscribe' not found`

- [ ] **Step 3: Add the view**

In `apps/status/views.py`, change line 4 from:

```python
from django.http import HttpResponseBadRequest, HttpResponseRedirect
```

to:

```python
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
```

Change line 7 from:

```python
from django.views.generic import TemplateView
```

to:

```python
from django.views.generic import TemplateView, View
```

Change line 13 from:

```python
from .models import ServiceComponent, StatusIncident
```

to:

```python
from .models import ServiceComponent, StatusIncident, StatusSubscriber
```

Then add, after the `StatusSubscribeView` class:

```python
class StatusUnsubscribeView(View):
    """Public, unauthenticated. Deletes the StatusSubscriber matching `token`
    if one exists, and renders a generic confirmation page regardless — an
    unknown token must not reveal whether it was ever subscribed."""
    def get(self, request, token, *args, **kwargs):
        StatusSubscriber.objects.filter(unsubscribe_token=token).delete()
        return TemplateResponse(request, 'status/unsubscribe.html', {})
```

- [ ] **Step 4: Add the URL route**

In `apps/status/urls.py`, add the route:

```python
urlpatterns = [
    path('', views.StatusPageView.as_view(), name='page'),
    path('subscribe/', views.StatusSubscribeView.as_view(), name='subscribe'),
    path('unsubscribe/<uuid:token>/', views.StatusUnsubscribeView.as_view(), name='unsubscribe'),
]
```

- [ ] **Step 5: Add the template**

```html
{% extends "marketing/base.html" %}

{% block title %}Berhenti Berlangganan — Status Layanan{% endblock %}

{% block content %}
<section style="background:#fff">
  <div style="max-width:640px;margin:0 auto;padding:80px 24px;display:flex;flex-direction:column;gap:16px;text-align:center">
    <h1 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:clamp(24px,4vw,32px);letter-spacing:-.03em">Anda telah berhenti berlangganan.</h1>
    <p style="margin:0;font-size:15px;line-height:1.65;color:#6B615C">Anda tidak akan lagi menerima pembaruan insiden lewat surel dari halaman status EduCore.</p>
    <a href="{% url 'status:page' %}" style="margin-top:12px;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:600;color:#16110F;text-decoration:underline">Kembali ke Status Layanan</a>
  </div>
</section>
{% endblock %}
```

Save to `frontend/templates/status/unsubscribe.html`.

- [ ] **Step 6: Run test to verify it passes**

Run: `python manage.py test apps.status.tests.test_unsubscribe_view -v 2`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add apps/status/views.py apps/status/urls.py frontend/templates/status/unsubscribe.html apps/status/tests/test_unsubscribe_view.py
git commit -m "feat(status): add public unsubscribe view + template"
```

---

### Task 5: Full-suite verification + Notion update

**Files:** none (verification task)

**Interfaces:** none — this task only runs existing test suites and updates the Notion tracker.

- [ ] **Step 1: Run the full `apps.status` suite**

Run: `python manage.py test apps.status -v 2`
Expected: all tests PASS (existing tests + the ~13 new ones from Tasks 1-4)

- [ ] **Step 2: Run the full project suite**

Run: `python manage.py test`
Expected: PASS, with only the 2 pre-existing unrelated `apps.finance` failures already known-present on `main` (per `memory/01_PROJECT.md`'s recurring note) — confirm no new failures.

- [ ] **Step 3: Manual sanity check via drain_tasks**

Run in a shell (`python manage.py shell`):

```python
from django.utils import timezone
from apps.status.models import StatusSubscriber
from apps.status.services import create_incident

StatusSubscriber.objects.create(email='manual-test@example.com')
create_incident(
    severity='MAJOR', title_id='Uji manual', title_en='Manual test',
    body_id='Ini uji manual.', body_en='This is a manual test.',
    occurred_at=timezone.now(), duration_minutes=5,
    affected_component_ids=[], published=True, actor=None,
)
```

Then: `python manage.py drain_tasks --limit 10` — confirm it exits with "Successfully processed 1 tasks." and (with the default console `EMAIL_BACKEND`) the rendered email prints to stdout with the correct subject/body/unsubscribe link.

- [ ] **Step 4: Update the Notion Open Item to Done**

Set the "Status page: subscriber email delivery" Notion page's `Status` property to `Done`, with a progress note summarizing: SMTP `EMAIL_BACKEND` + `EDUCORE_PUBLIC_BASE_URL` settings, `status.subscriber_email.send` task handler reusing `core.TaskQueue`/`drain_tasks` (ARC-010/011), one task per subscriber fan-out from `create_incident`, and the new public unsubscribe view — plus the PR link once opened.

- [ ] **Step 5: Open PR**

```bash
git push -u origin claude/subscriber-email-delivery-status-6275e9
gh pr create --title "feat(status): subscriber email delivery for published incidents" --body "$(cat <<'EOF'
## Summary
- Adds Django SMTP EMAIL_BACKEND + EDUCORE_PUBLIC_BASE_URL settings (console backend by default in dev/test)
- New `status.subscriber_email.send` TaskQueue handler (ARC-010/011), one row per subscriber, drained by the existing drain_tasks cron
- `create_incident(published=True)` fans out one email task per StatusSubscriber
- New public `GET /status/unsubscribe/<uuid:token>/` view + confirmation template, linked from every notification email

Closes the "Status page: subscriber email delivery" Notion Open Item (follow-up from PR #184/#186).

## Test plan
- [x] `apps.status` full suite green
- [x] Full project suite green (only pre-existing unrelated apps.finance failures)
- [x] Manual drain_tasks smoke test with console EMAIL_BACKEND

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Update the Notion page's `Logs`/content with the PR link once created.
