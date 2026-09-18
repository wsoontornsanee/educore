# Status page: subscriber email delivery — design

**Notion:** [Status page: subscriber email delivery](https://app.notion.com/p/3df347a6659481edb3b3ec4c47cd688f)
**Depends on:** PR #184/#186 (`apps.status` app, `StatusSubscriber`, `StatusIncident`, `create_incident()`)

## Problem

`StatusSubscriber` rows are captured (email + unsubscribe token) on the public
`/status/` page, but nothing ever sends them anything. This repo has no
outbound transactional email channel anywhere yet — no `EMAIL_BACKEND` /
provider configured, and `apps.notifications` is WhatsApp/push/in-app for
authenticated users only, not public-visitor email.

## Scope

Send a plain-text incident notification email to every captured subscriber
when a new `StatusIncident` is published. Give subscribers a working
self-serve unsubscribe link (the `unsubscribe_token` field already exists
for this and is otherwise dead weight).

**Non-goals:** per-subscriber language preference (id-ID only for v1), HTML
email templates, notification on incident update/resolution (create-only),
provider-specific bounce/complaint handling, a UI for staff to preview/resend
an email.

## Design

### Email backend

Django's built-in SMTP `EmailBackend`, configured entirely via new env vars
in `educore/settings/base.py`, following the existing `XENDIT_*` /
`GOOGLE_OAUTH_*` convention (`os.environ.get(..., default)`):

- `EMAIL_BACKEND` (default `django.core.mail.backends.console.EmailBackend`
  so local/dev/test never silently attempts a real SMTP connect)
- `EMAIL_HOST`, `EMAIL_PORT` (default `587`), `EMAIL_HOST_USER`,
  `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (default `True`)
- `DEFAULT_FROM_EMAIL` (default `status@educore.id`)
- `EDUCORE_PUBLIC_BASE_URL` (default `http://localhost:8000`) — needed
  because the sending task runs in a cron/drain context with no `request`
  object to call `build_absolute_uri()` on, unlike `apps.marketing`'s
  `api_base_url` pattern.

Production/staging set the real SMTP vars; local/test stay on the console
backend unless explicitly overridden — matching how every other
provider integration in this repo defaults to inert in dev.

### Enqueue (fan-out, ARC-010/011)

`create_incident()` in `apps/status/services.py`, only when
`published=True`, after its existing `audit()` call: loop
`StatusSubscriber.objects.all()` and call
`enqueue_task('status.subscriber_email.send', payload={'incident_id': incident.id, 'subscriber_id': subscriber.id})`
once per subscriber. Each subscriber gets its own `TaskQueue` row, so one bad
address/bounce retries and eventually dead-letters independently instead of
blocking or retry-storming the rest of the batch (ARC-012 backoff is a
per-row unit already).

No new cron entry — the existing `drain_tasks` command (`*/1`) drains these
rows through the existing generic dispatcher, same as
`notifications.process_intent` / `partners.webhook.deliver`.

### Task handler

New `apps/status/tasks.py`:

```python
@register_task_handler('status.subscriber_email.send')
def send_subscriber_incident_email(payload: dict):
    incident = StatusIncident.objects.get(id=payload['incident_id'])
    subscriber = StatusSubscriber.objects.get(id=payload['subscriber_id'])
    ...
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [subscriber.email])
```

- Incident-not-found raises (lets the row dead-letter after retries — that's
  a real bug, not an expected race). Subscriber-not-found (they unsubscribed
  between enqueue and drain) is an expected race, not a failure: the handler
  catches `StatusSubscriber.DoesNotExist` and returns early, no error, no
  retry.
- Body is plain text (id-ID only — `title_id`/`body_id`), includes the
  incident severity/title/body/occurred_at, and an unsubscribe link:
  `{EDUCORE_PUBLIC_BASE_URL}/status/unsubscribe/{subscriber.unsubscribe_token}/`.
- `StatusIncident` has no per-subscriber dedupe/sent-tracking column — not
  needed, since each `TaskQueue` row is itself the one-send-per-subscriber
  unit and `drain_tasks` only claims `PENDING` rows once.

### Unsubscribe

New public view, `GET /status/unsubscribe/<uuid:token>/`:

- Deletes the matching `StatusSubscriber` row if found.
- Renders a generic confirmation template regardless of whether a matching
  row existed (don't leak whether a token/email is currently subscribed).
- No auth required (matches `StatusSubscribeView`'s existing
  `AllowAny`/public posture) — a bare UUID token is the auth mechanism, same
  security model as e.g. password-reset links elsewhere in the web.
- New template `frontend/templates/status/unsubscribe.html`, same
  `doc-layout` shell as `status.html`.

### Testing

- `apps/status/tests/test_tasks.py`: handler sends via `django.core.mail.outbox`
  (Django's `locmem` test backend), includes unsubscribe link, no-ops
  cleanly on an already-unsubscribed `subscriber_id`.
- `apps/status/tests/test_services.py` (extend): `create_incident(published=True)`
  enqueues one `TaskQueue` row per existing `StatusSubscriber`;
  `published=False` enqueues none.
- `apps/status/tests/test_views.py` or new `test_unsubscribe_view.py`:
  valid token deletes the row and 200s; unknown token still 200s (no leak);
  row is actually gone from the DB after.

## Open questions resolved during brainstorm

- Provider: SMTP via Django's built-in backend, not a provider SDK.
- Trigger: new incident creation only (`published=True`), not update/resolve.
- Queue: reuse `core.TaskQueue` (ARC-010), not a dedicated table/cron.
- Fan-out: one `TaskQueue` row per subscriber, not one row per incident.
- Unsubscribe: build the endpoint now, not deferred.
