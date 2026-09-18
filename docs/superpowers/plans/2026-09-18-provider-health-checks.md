# Status Page: Real Per-Provider Health Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `payments`, `notifications`, and `canteen_pos` `ServiceComponent`s on the public `/status/` page reflect a real per-provider signal (Xendit reachability, WhatsApp Cloud API reachability, `Device` POS-terminal heartbeats) instead of the shared DB-only probe, while every other component keeps today's DB-only behavior unchanged.

**Architecture:** New `apps/status/probes.py` holds one probe function per `ServiceComponent.key`, each returning `None` (no real signal — caller falls back to the existing DB probe) or `(status, latency_ms)`. `apps/status/services.py::record_heartbeats()` dispatches to a probe by key inside try/except, then falls back to the shared DB signal on `None` or on any exception. A new nullable `ComponentHeartbeat.status` field carries the 3-way `OPERATIONAL`/`DEGRADED`/`DOWN` signal; `rollup_daily_status()` takes the worst status per day, backward-compatible with existing NULL-status rows.

**Tech Stack:** Django 5.x, MySQL 8 (SQLite fallback for tests), `requests` (already a dependency via `apps.finance.services.payment_providers`), `unittest.mock.patch`.

## Global Constraints

- Every mutating action writes an audit event (AGENTS.md red line #4) — **not applicable here**: `record_heartbeats`/`rollup_daily_status` are automated cron writes to non-tenant operational data, matching the existing precedent (today's `record_heartbeats` already writes `ComponentHeartbeat` rows with no `audit()` call).
- `Device` is a `TenantModel` — cross-tenant reads MUST use `.all_tenants`, never rely on thread-local `foundation_id` (no tenancy context exists inside a cron process).
- No new cron job, advisory lock, or `JobRun` — reuses `check_service_health`'s existing ones.
- Full test suite must stay green except the 2 pre-existing unrelated `apps.finance` failures (confirmed already on `main`).

---

### Task 1: `ComponentHeartbeat.status` field + migration

**Files:**
- Modify: `apps/status/models.py` (add `ComponentHeartbeat.status`)
- Modify: `apps/status/admin.py:12-15` (`ComponentHeartbeatAdmin.list_display`/`list_filter`)
- Create: `apps/status/migrations/0007_componentheartbeat_status.py`
- Test: `apps/status/tests/test_models.py`

**Interfaces:**
- Produces: `ComponentHeartbeat.status` — nullable `CharField(max_length=16, choices=ServiceComponent.STATUS_CHOICES)`. Later tasks read/write this field directly.

- [ ] **Step 1: Write the failing test**

Add to `apps/status/tests/test_models.py` (create the file if it doesn't already cover `ComponentHeartbeat`; check first — if it exists, append this test class):

```python
from apps.status.models import ComponentHeartbeat, ServiceComponent
from django.test import TestCase
from django.utils import timezone


class ComponentHeartbeatStatusFieldTests(TestCase):
    def test_status_field_accepts_null_and_status_choices(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        hb_null = ComponentHeartbeat.objects.create(
            component=component, checked_at=timezone.now(), is_up=True, latency_ms=10,
        )
        self.assertIsNone(hb_null.status)
        hb_degraded = ComponentHeartbeat.objects.create(
            component=component, checked_at=timezone.now(), is_up=True, latency_ms=10,
            status=ServiceComponent.STATUS_DEGRADED,
        )
        self.assertEqual(hb_degraded.status, ServiceComponent.STATUS_DEGRADED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.status.tests.test_models.ComponentHeartbeatStatusFieldTests -v 2`
Expected: FAIL — `TypeError: 'status' is an invalid keyword argument for this function` (field doesn't exist yet).

- [ ] **Step 3: Add the field**

In `apps/status/models.py`, inside `class ComponentHeartbeat(models.Model):`, after the existing `latency_ms` field:

```python
    status = models.CharField(
        max_length=16, choices=ServiceComponent.STATUS_CHOICES, null=True, blank=True,
        help_text="Full OPERATIONAL/DEGRADED/DOWN probe signal. Null on pre-migration rows — "
                   "read as DOWN if is_up=False else OPERATIONAL for backward compatibility.",
    )
```

- [ ] **Step 4: Generate and inspect the migration**

Run: `python manage.py makemigrations status --name componentheartbeat_status`

Verify the generated file (`apps/status/migrations/0007_componentheartbeat_status.py`) is a plain `AddField` migration adding `status` to `ComponentHeartbeat`, no other changes.

- [ ] **Step 5: Update admin to expose the new field**

In `apps/status/admin.py`, change:

```python
@admin.register(ComponentHeartbeat)
class ComponentHeartbeatAdmin(admin.ModelAdmin):
    list_display = ('component', 'checked_at', 'is_up', 'latency_ms')
    list_filter = ('component', 'is_up')
```

to:

```python
@admin.register(ComponentHeartbeat)
class ComponentHeartbeatAdmin(admin.ModelAdmin):
    list_display = ('component', 'checked_at', 'is_up', 'status', 'latency_ms')
    list_filter = ('component', 'is_up', 'status')
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python manage.py test apps.status.tests.test_models -v 2`
Expected: PASS

- [ ] **Step 7: Run the full apps.status suite to check for regressions**

Run: `python manage.py test apps.status -v 2`
Expected: PASS (all existing tests still green — this is a purely additive nullable field)

- [ ] **Step 8: Commit**

```bash
git add apps/status/models.py apps/status/admin.py apps/status/migrations/0007_componentheartbeat_status.py apps/status/tests/test_models.py
git commit -m "feat(status): add ComponentHeartbeat.status field for the DEGRADED tier"
```

---

### Task 2: Per-provider probe functions (`apps/status/probes.py`)

**Files:**
- Create: `apps/status/probes.py`
- Test: `apps/status/tests/test_probes.py`

**Interfaces:**
- Consumes: `ServiceComponent.STATUS_OPERATIONAL`/`STATUS_DEGRADED`/`STATUS_DOWN` (from `apps/status/models.py`, already defined); `settings.XENDIT_API_KEY`/`XENDIT_BASE_URL` (already declared in `educore/settings/base.py`); `settings.WHATSAPP_API_TOKEN`/`WHATSAPP_PHONE_NUMBER_ID` (read via `getattr(settings, ..., '')`, matching `apps/notifications/providers.py:253-254` — not declared in `base.py`, that's fine, `getattr` supplies the empty-string default); `apps.hardware.models.Device`/`DeviceClass`/`DeviceStatus`.
- Produces: `probe_payments() -> tuple[str, int] | None`, `probe_whatsapp() -> tuple[str, int] | None`, `probe_canteen_pos() -> tuple[str, None] | None`, and `PROBES: dict[str, Callable[[], tuple[str, int | None] | None]]` mapping `ServiceComponent.key` → probe function. Task 3 imports `PROBES` directly.

- [ ] **Step 1: Write the failing tests**

Create `apps/status/tests/test_probes.py`:

```python
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from apps.hardware.models import Device, DeviceClass, DeviceStatus
from apps.identity.models import Foundation, School
from apps.status.models import ServiceComponent
from apps.status.probes import probe_payments, probe_whatsapp, probe_canteen_pos, PROBES
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ProbePaymentsTests(TestCase):
    @override_settings(XENDIT_API_KEY='')
    def test_returns_none_when_unconfigured(self):
        self.assertIsNone(probe_payments())

    @override_settings(XENDIT_API_KEY='test-key', XENDIT_BASE_URL='https://api.xendit.co')
    @patch('apps.status.probes.requests.get')
    def test_returns_operational_on_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        status, latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsInstance(latency_ms, int)
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://api.xendit.co/balance')
        self.assertEqual(mock_get.call_args[1]['auth'], ('test-key', ''))
        self.assertEqual(mock_get.call_args[1]['timeout'], 5)

    @override_settings(XENDIT_API_KEY='test-key')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_non_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=False, status_code=500)
        status, _latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)

    @override_settings(XENDIT_API_KEY='test-key')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_request_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("boom")
        status, _latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)


class ProbeWhatsappTests(TestCase):
    @override_settings(WHATSAPP_API_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='')
    def test_returns_none_when_unconfigured(self):
        self.assertIsNone(probe_whatsapp())

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='12345')
    def test_returns_none_when_only_token_set(self):
        self.assertIsNone(probe_whatsapp())

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='12345')
    @patch('apps.status.probes.requests.get')
    def test_returns_operational_on_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        status, latency_ms = probe_whatsapp()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsInstance(latency_ms, int)
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://graph.facebook.com/v19.0/12345')
        self.assertEqual(mock_get.call_args[1]['params'], {'fields': 'id'})
        self.assertEqual(mock_get.call_args[1]['headers'], {'Authorization': 'Bearer tok'})
        self.assertEqual(mock_get.call_args[1]['timeout'], 5)

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='12345')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_non_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=False, status_code=401)
        status, _latency_ms = probe_whatsapp()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)


class ProbeCanteenPosTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji",
            npwp="01.111.222.3-444.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Uji", npsn="30500099",
            level=School.LEVEL_SMP, timezone='Asia/Jakarta',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _make_pos(self, status, device_code):
        return Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code=device_code,
            name="Kasir Kantin", device_class=DeviceClass.POS_TERMINAL, status=status,
        )

    def test_returns_none_when_no_pos_devices(self):
        self.assertIsNone(probe_canteen_pos())

    def test_all_online_is_operational(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.ONLINE, 'POS-2')
        status, latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsNone(latency_ms)

    def test_minority_offline_is_degraded(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.ONLINE, 'POS-2')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-3')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DEGRADED)

    def test_majority_offline_is_down(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-2')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-3')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)

    def test_retired_devices_excluded_from_denominator(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.RETIRED, 'POS-2')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)

    def test_non_pos_device_class_ignored(self):
        Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code='GATE-1',
            name="Gerbang", device_class=DeviceClass.GATE_READER, status=DeviceStatus.OFFLINE,
        )
        self.assertIsNone(probe_canteen_pos())

    def test_aggregates_across_foundations(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain",
            npwp="01.999.888.7-666.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(other_foundation.id)
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMP Lain", npsn="30500098",
            level=School.LEVEL_SMP, timezone='Asia/Jakarta',
        )
        Device.objects.create(
            foundation_id=other_foundation.id, school=other_school, device_code='POS-OTHER',
            name="Kasir Lain", device_class=DeviceClass.POS_TERMINAL, status=DeviceStatus.OFFLINE,
        )
        # Called with no ambient tenant context, matching the real cron process.
        clear_current_foundation_id()
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DEGRADED)


class ProbesRegistryTests(TestCase):
    def test_registry_maps_expected_component_keys(self):
        self.assertEqual(
            set(PROBES.keys()), {'payments', 'notifications', 'canteen_pos'},
        )
        self.assertIs(PROBES['payments'], probe_payments)
        self.assertIs(PROBES['notifications'], probe_whatsapp)
        self.assertIs(PROBES['canteen_pos'], probe_canteen_pos)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test apps.status.tests.test_probes -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.status.probes'`

- [ ] **Step 3: Write the implementation**

Create `apps/status/probes.py`:

```python
"""Real per-provider health probes for apps.status.check_service_health.

Each probe returns None (no real signal for this environment/deployment —
caller falls back to the shared DB-connectivity heartbeat, exactly like
every ServiceComponent not listed in PROBES) or (status, latency_ms).
"""
import time

import requests
from django.conf import settings

from .models import ServiceComponent


def probe_payments():
    """GET /balance: cheapest authenticated read-only Xendit endpoint —
    confirms API key validity + connectivity + latency without creating or
    mutating anything. Skipped (returns None) when XENDIT_API_KEY isn't
    configured — no single global "active payment provider" setting exists
    (apps.finance.services.payment_providers.get_payment_provider is chosen
    per-call), so an unset key means this deployment isn't actually relying
    on Xendit and the DB-only fallback applies instead.
    """
    api_key = getattr(settings, 'XENDIT_API_KEY', '')
    if not api_key:
        return None
    base_url = getattr(settings, 'XENDIT_BASE_URL', 'https://api.xendit.co')
    start = time.monotonic()
    try:
        response = requests.get(f"{base_url}/balance", auth=(api_key, ''), timeout=5)
        ok = response.ok
    except requests.RequestException:
        ok = False
    latency_ms = int((time.monotonic() - start) * 1000)
    status = ServiceComponent.STATUS_OPERATIONAL if ok else ServiceComponent.STATUS_DOWN
    return status, latency_ms


def probe_whatsapp():
    """GET our own registered WhatsApp Business phone number resource from
    Meta's Graph API — confirms token validity + connectivity. Skipped
    (returns None) unless both WHATSAPP_API_TOKEN and WHATSAPP_PHONE_NUMBER_ID
    are configured, same "unconfigured means not actually in use" contract
    as probe_payments.
    """
    token = getattr(settings, 'WHATSAPP_API_TOKEN', '')
    phone_number_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    if not (token and phone_number_id):
        return None
    start = time.monotonic()
    try:
        response = requests.get(
            f"https://graph.facebook.com/v19.0/{phone_number_id}",
            params={'fields': 'id'},
            headers={'Authorization': f'Bearer {token}'},
            timeout=5,
        )
        ok = response.ok
    except requests.RequestException:
        ok = False
    latency_ms = int((time.monotonic() - start) * 1000)
    status = ServiceComponent.STATUS_OPERATIONAL if ok else ServiceComponent.STATUS_DOWN
    return status, latency_ms


def probe_canteen_pos():
    """Derive canteen_pos status from real apps.hardware.Device POS-terminal
    heartbeats, aggregated across every foundation (apps.status is
    platform-wide — see apps/status/models.py module docstring). Device is a
    TenantModel; this cron process has no ambient thread-local
    foundation_id, so it must use .all_tenants with no implicit tenant
    filtering (same cross-tenant precedent as the clinic/library modules).

    Skipped (returns None) when there are zero POS-terminal devices
    registered anywhere yet, falling back to the DB-only signal rather than
    reporting a fabricated status for a component with no real data.
    """
    from apps.hardware.models import Device, DeviceClass, DeviceStatus

    devices = Device.all_tenants.filter(device_class=DeviceClass.POS_TERMINAL).exclude(
        status=DeviceStatus.RETIRED,
    )
    total = devices.count()
    if total == 0:
        return None
    offline = devices.filter(status=DeviceStatus.OFFLINE).count()
    ratio = offline / total
    if ratio > 0.5:
        status = ServiceComponent.STATUS_DOWN
    elif offline > 0:
        status = ServiceComponent.STATUS_DEGRADED
    else:
        status = ServiceComponent.STATUS_OPERATIONAL
    return status, None


PROBES = {
    'payments': probe_payments,
    'notifications': probe_whatsapp,
    'canteen_pos': probe_canteen_pos,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test apps.status.tests.test_probes -v 2`
Expected: PASS (all cases in Step 1)

- [ ] **Step 5: Commit**

```bash
git add apps/status/probes.py apps/status/tests/test_probes.py
git commit -m "feat(status): add per-provider health probes (Xendit, WhatsApp, POS)"
```

---

### Task 3: Wire probes into `record_heartbeats()`

**Files:**
- Modify: `apps/status/services.py:50-66` (`record_heartbeats`)
- Test: `apps/status/tests/test_services.py` (extend `RecordHeartbeatsTests`)

**Interfaces:**
- Consumes: `PROBES` dict + `probe_payments`/`probe_whatsapp`/`probe_canteen_pos` from `apps/status/probes.py` (Task 2).
- Produces: `record_heartbeats()` return type/signature unchanged (`int` count of heartbeats created); every `ComponentHeartbeat` it creates now also sets `.status`. Task 4 (`rollup_daily_status`) reads this field.

- [ ] **Step 1: Write the failing tests**

Add to `apps/status/tests/test_services.py`, inside `RecordHeartbeatsTests` (or as a new test class in the same file, following the existing `setUp` pattern that creates `c1`/`c2`):

```python
from unittest.mock import patch

from apps.status import probes as status_probes


class RecordHeartbeatsProbeDispatchTests(TestCase):
    def setUp(self):
        self.payments = ServiceComponent.objects.get(key='payments')
        self.notifications = ServiceComponent.objects.get(key='notifications')
        self.canteen_pos = ServiceComponent.objects.get(key='canteen_pos')
        self.web_portal = ServiceComponent.objects.get(key='web_portal')

    def test_probed_component_uses_probe_result(self):
        with patch.object(status_probes, 'probe_payments', return_value=(ServiceComponent.STATUS_DEGRADED, 42)):
            with patch.dict(status_probes.PROBES, {'payments': status_probes.probe_payments}):
                record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.payments)
        self.assertEqual(hb.status, ServiceComponent.STATUS_DEGRADED)
        self.assertEqual(hb.latency_ms, 42)
        self.assertTrue(hb.is_up)

    def test_probe_returning_none_falls_back_to_db_signal(self):
        with patch.object(status_probes, 'probe_payments', return_value=None):
            with patch.dict(status_probes.PROBES, {'payments': status_probes.probe_payments}):
                with patch('apps.status.services._probe_database', return_value=(True, 5)):
                    record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.payments)
        self.assertEqual(hb.status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertEqual(hb.latency_ms, 5)

    def test_probe_raising_falls_back_to_db_signal_without_crashing(self):
        with patch.object(status_probes, 'probe_whatsapp', side_effect=RuntimeError("boom")):
            with patch.dict(status_probes.PROBES, {'notifications': status_probes.probe_whatsapp}):
                with patch('apps.status.services._probe_database', return_value=(True, 7)):
                    created = record_heartbeats()
        self.assertEqual(created, ServiceComponent.objects.count())
        hb = ComponentHeartbeat.objects.get(component=self.notifications)
        self.assertEqual(hb.status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertEqual(hb.latency_ms, 7)

    def test_unprobed_component_uses_db_signal_as_before(self):
        with patch('apps.status.services._probe_database', return_value=(False, None)):
            record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.web_portal)
        self.assertEqual(hb.status, ServiceComponent.STATUS_DOWN)
        self.assertFalse(hb.is_up)

    def test_manual_down_override_still_wins_over_probe_result(self):
        self.payments.manual_status = ServiceComponent.STATUS_DOWN
        self.payments.save()
        with patch.object(status_probes, 'probe_payments', return_value=(ServiceComponent.STATUS_OPERATIONAL, 10)):
            with patch.dict(status_probes.PROBES, {'payments': status_probes.probe_payments}):
                record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.payments)
        self.assertEqual(hb.status, ServiceComponent.STATUS_DOWN)
        self.assertFalse(hb.is_up)
```

Note: `patch.dict(status_probes.PROBES, {...})` combined with `patch.object(status_probes, 'probe_payments', ...)` is required because `record_heartbeats` (Step 3 below) does `from .probes import PROBES` — patching only the module-level function wouldn't affect the dict's already-bound reference, so the dict entry is explicitly re-pointed at the patched function for the duration of each test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test apps.status.tests.test_services.RecordHeartbeatsProbeDispatchTests -v 2`
Expected: FAIL — probe results are ignored, `hb.status` stays `None` for every component (current `record_heartbeats` doesn't dispatch to `PROBES` or set `.status` at all yet).

- [ ] **Step 3: Rewrite `record_heartbeats()`**

In `apps/status/services.py`, add the import at the top (alongside the existing imports):

```python
import logging

from .probes import PROBES
```

(`logging` goes with the existing `import datetime` / `import time` block; add `logger = logging.getLogger(__name__)` right after the module-level `BAR_COLORS` dict.)

Replace the existing `record_heartbeats()` function body:

```python
def record_heartbeats():
    """Probe the platform once and write one ComponentHeartbeat per ServiceComponent.

    A component with a registered probe (apps.status.probes.PROBES) uses that
    probe's real signal; every other component (and a probed one whose probe
    returns None, meaning "no real signal for this deployment") falls back to
    the shared DB-connectivity probe, exactly as before this change. A probe
    that raises is caught here too (on top of each probe catching its own
    requests.RequestException internally) so a bug in one provider
    integration can never prevent this cycle's heartbeat from being recorded
    for any component, probed or not.

    A manual_status=DOWN override forces that component's heartbeat down
    regardless of the probe/DB result; other overrides don't affect the
    heartbeat itself (they're applied at rollup/read time instead).
    """
    db_is_up, db_latency_ms = _probe_database()
    db_status = ServiceComponent.STATUS_OPERATIONAL if db_is_up else ServiceComponent.STATUS_DOWN
    checked_at = timezone.now()
    created = 0
    for component in ServiceComponent.objects.all():
        probe = PROBES.get(component.key)
        result = None
        if probe is not None:
            try:
                result = probe()
            except Exception:
                logger.warning("status probe for %s failed", component.key, exc_info=True)
        status, latency_ms = result if result is not None else (db_status, db_latency_ms)
        if component.manual_status == ServiceComponent.STATUS_DOWN:
            status = ServiceComponent.STATUS_DOWN
        ComponentHeartbeat.objects.create(
            component=component, checked_at=checked_at,
            is_up=(status != ServiceComponent.STATUS_DOWN), status=status, latency_ms=latency_ms,
        )
        created += 1
    return created
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python manage.py test apps.status.tests.test_services.RecordHeartbeatsProbeDispatchTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the full apps.status suite to check for regressions**

Run: `python manage.py test apps.status -v 2`
Expected: PASS — the pre-existing `RecordHeartbeatsTests.test_manual_down_override_marks_heartbeat_down` test uses components keyed `c1`/`c2` (not in `PROBES`), so it exercises the unchanged DB-fallback path and must still pass unmodified.

- [ ] **Step 6: Commit**

```bash
git add apps/status/services.py apps/status/tests/test_services.py
git commit -m "feat(status): dispatch record_heartbeats to per-provider probes"
```

---

### Task 4: Worst-status rollup + backward compatibility

**Files:**
- Modify: `apps/status/services.py:69-92` (`rollup_daily_status`)
- Test: `apps/status/tests/test_services.py` (extend `RollupDailyStatusTests`)

**Interfaces:**
- Consumes: `ComponentHeartbeat.status` (Task 1), heartbeats written by `record_heartbeats()` (Task 3).
- Produces: `rollup_daily_status(date=None)` signature/return type unchanged (`int` count of components rolled up); `DailyComponentStatus.status` now reflects the worst of `OPERATIONAL`/`DEGRADED`/`DOWN` seen that day instead of only `OPERATIONAL`/`DOWN`.

- [ ] **Step 1: Write the failing tests**

Add to `apps/status/tests/test_services.py`, inside or alongside `RollupDailyStatusTests`:

```python
class RollupDailyStatusDegradedTierTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c3', name_id='C3', name_en='C3')
        self.today = timezone.localdate()

    def test_degraded_heartbeat_rolls_up_degraded(self):
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=10,
            status=ServiceComponent.STATUS_DEGRADED,
        )
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DEGRADED)

    def test_down_beats_degraded_same_day(self):
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=10,
            status=ServiceComponent.STATUS_DEGRADED,
        )
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=False, latency_ms=None,
            status=ServiceComponent.STATUS_DOWN,
        )
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DOWN)

    def test_degraded_beats_operational_same_day(self):
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=10,
            status=ServiceComponent.STATUS_OPERATIONAL,
        )
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=10,
            status=ServiceComponent.STATUS_DEGRADED,
        )
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DEGRADED)

    def test_null_status_heartbeats_roll_up_same_as_before_migration(self):
        # Pre-migration-style rows: status=NULL, only is_up set.
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=10, status=None,
        )
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)

    def test_null_status_down_heartbeat_still_rolls_up_down(self):
        ComponentHeartbeat.objects.create(
            component=self.component, checked_at=timezone.now(), is_up=False, latency_ms=None, status=None,
        )
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DOWN)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test apps.status.tests.test_services.RollupDailyStatusDegradedTierTests -v 2`
Expected: FAIL — `test_degraded_heartbeat_rolls_up_degraded` and `test_down_beats_degraded_same_day` fail because current `rollup_daily_status` only ever writes `OPERATIONAL` or `DOWN` (it has no `DEGRADED` branch at all).

- [ ] **Step 3: Rewrite `rollup_daily_status()`**

In `apps/status/services.py`, replace the loop body inside `rollup_daily_status`:

```python
def rollup_daily_status(date=None):
    """Upsert today's (or `date`'s) DailyComponentStatus per component from its heartbeats.

    Idempotent — safe to call repeatedly within the same day. manual_status
    always wins; otherwise the worst status seen that day (DOWN > DEGRADED >
    OPERATIONAL). A heartbeat with status=NULL (written before the status
    field existed) is treated as DOWN if is_up=False else OPERATIONAL —
    identical to this function's pre-migration behavior, so historical rows
    keep rolling up exactly as they always did.
    """
    date = date or timezone.localdate()
    day_start = timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))
    day_end = timezone.make_aware(datetime.datetime.combine(date, datetime.time.max))
    updated = 0
    for component in ServiceComponent.objects.all():
        if component.manual_status:
            status = component.manual_status
        else:
            heartbeats = ComponentHeartbeat.objects.filter(
                component=component, checked_at__gte=day_start, checked_at__lte=day_end,
            )
            if heartbeats.filter(status=ServiceComponent.STATUS_DOWN).exists() or \
                    heartbeats.filter(status__isnull=True, is_up=False).exists():
                status = ServiceComponent.STATUS_DOWN
            elif heartbeats.filter(status=ServiceComponent.STATUS_DEGRADED).exists():
                status = ServiceComponent.STATUS_DEGRADED
            else:
                status = ServiceComponent.STATUS_OPERATIONAL
        DailyComponentStatus.objects.update_or_create(
            component=component, date=date, defaults={'status': status},
        )
        updated += 1
    return updated
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python manage.py test apps.status.tests.test_services.RollupDailyStatusDegradedTierTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the full apps.status suite to check for regressions**

Run: `python manage.py test apps.status -v 2`
Expected: PASS — all pre-existing `RollupDailyStatusTests` cases (`test_all_up_heartbeats_roll_up_operational`, `test_any_down_heartbeat_rolls_up_down`, `test_manual_status_overrides_heartbeats`, `test_rollup_is_idempotent`) construct heartbeats with `is_up` only (no `status` kwarg, so `status=NULL` by default) and must still produce identical results.

- [ ] **Step 6: Commit**

```bash
git add apps/status/services.py apps/status/tests/test_services.py
git commit -m "feat(status): worst-status daily rollup with NULL-status backward compatibility"
```

---

### Task 5: Full-branch verification and cron/integration smoke test

**Files:**
- Test: `apps/status/tests/test_check_service_health.py` (extend)

**Interfaces:**
- Consumes: everything from Tasks 1-4 — no new production code in this task, verification only.

- [ ] **Step 1: Write an end-to-end management-command test**

Check `apps/status/tests/test_check_service_health.py` first for its existing test class name/pattern (it already tests the `check_service_health` command end-to-end against `record_heartbeats`/`rollup_daily_status`), then add:

```python
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.status.models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent


class CheckServiceHealthProviderProbesIntegrationTests(TestCase):
    @override_settings(XENDIT_API_KEY='test-key', WHATSAPP_API_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='')
    @patch('apps.status.probes.requests.get')
    def test_command_records_real_payments_signal_end_to_end(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        call_command('check_service_health')
        payments = ServiceComponent.objects.get(key='payments')
        hb = ComponentHeartbeat.objects.filter(component=payments).latest('checked_at')
        self.assertEqual(hb.status, ServiceComponent.STATUS_OPERATIONAL)
        daily = DailyComponentStatus.objects.get(component=payments)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)
        # notifications had no credentials configured -> DB-only fallback, unaffected by the mock.
        notifications = ServiceComponent.objects.get(key='notifications')
        self.assertTrue(
            ComponentHeartbeat.objects.filter(component=notifications).exists()
        )
```

- [ ] **Step 2: Run the new test**

Run: `python manage.py test apps.status.tests.test_check_service_health.CheckServiceHealthProviderProbesIntegrationTests -v 2`
Expected: PASS (Tasks 1-4 already make this pass — this step is verification, not new behavior)

- [ ] **Step 3: Run the entire apps.status suite**

Run: `python manage.py test apps.status -v 2`
Expected: PASS, all tests.

- [ ] **Step 4: Run the full project test suite**

Run: `python manage.py test`
Expected: PASS except the 2 pre-existing unrelated `apps.finance` failures (confirmed already on `main` — verify by checking `git log` / re-running on a clean `main` checkout if the failure count differs).

- [ ] **Step 5: Commit**

```bash
git add apps/status/tests/test_check_service_health.py
git commit -m "test(status): end-to-end coverage for check_service_health provider probes"
```

---

## Post-plan: PR

Not a plan step (per AGENTS.md's 5-stage SOP, PR creation follows plan completion, not a task within it) — once all 5 tasks are committed and the full suite is green:

1. Fetch latest `origin/main`, rebase if it has moved, resolve any conflicts.
2. Open a PR from `claude/provider-health-checks-status-ad5b4a` summarizing: new `ComponentHeartbeat.status` field/migration, `apps/status/probes.py` (Xendit `/balance`, WhatsApp Graph API, cross-tenant POS `Device` ratio), `record_heartbeats`/`rollup_daily_status` changes, and the DEGRADED tier now reachable automatically (not just via `manual_status`).
3. Wait for explicit user instruction before merging or deploying (AGENTS.md rule #12).
4. Update the Notion Open Item to `Done` with the PR link once merged (per [[feedback_notion_status_sync]]).
