# Status page: real per-provider health checks (Payments/WhatsApp/POS)

Notion Open Item: "Status page: real per-provider health checks (Payments/WhatsApp/POS)" (deferred from PR #184, Service Status Page).

## Goal

`check_service_health` (`*/5` cron) currently probes only shared DB connectivity and applies that single signal to every `ServiceComponent`. The `payments`, `notifications`, and `canteen_pos` components should instead be derived from a real per-provider signal (Xendit API reachability, WhatsApp Cloud API reachability, `Device` POS-terminal heartbeats) where one is available, falling back to the existing DB-only signal otherwise.

## Non-goals

- No new cron job, advisory lock, or `JobRun` — this extends the existing `check_service_health` command and its single lock/JobRun.
- No change to the internal `manual_status` staff-override UI or its precedence (`manual_status` still always wins).
- No probing of Midtrans (no global "active provider" setting exists; see Design decisions).
- No change to `PaymentProvider`/`BaseNotificationProvider` abstract interfaces — probes call the providers' underlying HTTP APIs directly rather than adding a `health_check()` abstract method, since only one real implementation per channel is ever actually probed (see below).

## Architecture

New module `apps/status/probes.py`. One probe function per `ServiceComponent.key`:

```python
PROBES = {
    'payments': probe_payments,
    'notifications': probe_whatsapp,
    'canteen_pos': probe_canteen_pos,
}
```

Each probe returns:
- `None` — no real signal available (credentials unconfigured, or zero POS devices exist yet) — caller falls back to the shared DB-connectivity signal, exactly like every other (unlisted) component does today.
- `(status, latency_ms)` — a real `ServiceComponent.STATUS_CHOICES` value plus an optional latency in ms (`None` where latency isn't meaningful, e.g. the POS ratio check).

`apps/status/services.py::record_heartbeats()` is rewritten to:

```python
def record_heartbeats():
    db_is_up, db_latency_ms = _probe_database()
    db_status = ServiceComponent.STATUS_OPERATIONAL if db_is_up else ServiceComponent.STATUS_DOWN
    checked_at = timezone.now()
    created = 0
    for component in ServiceComponent.objects.all():
        probe = PROBES.get(component.key)
        result = None
        if probe:
            try:
                result = probe()
            except Exception:
                logger.warning("status probe %s failed", component.key, exc_info=True)
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

A probe raising is caught per-component inside the loop — one broken/hanging provider integration can never prevent the DB heartbeat (or any other component's heartbeat) from being recorded that cycle, and never fails the whole `JobRun`.

### `probe_payments()`

```python
def probe_payments():
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
```

`GET /balance` is the cheapest authenticated, read-only Xendit endpoint — confirms API key validity + connectivity + latency without creating or mutating anything.

### `probe_whatsapp()`

```python
def probe_whatsapp():
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
```

### `probe_canteen_pos()`

```python
def probe_canteen_pos():
    from apps.hardware.models import Device, DeviceClass, DeviceStatus
    devices = Device.all_tenants.filter(device_class=DeviceClass.POS_TERMINAL).exclude(status=DeviceStatus.RETIRED)
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
```

`Device` is a `TenantModel` — the cron process has no ambient thread-local `foundation_id`, so this must use `.all_tenants` with no implicit tenant filtering, matching the existing clinic/library cross-tenant precedents. `apps.status` is platform-wide (see `apps/status/models.py` module docstring) so this intentionally aggregates every foundation's POS terminals into one platform-wide signal, same as every other component on this page.

## Data model

New migration on `apps.status`:

```python
ComponentHeartbeat.status = models.CharField(
    max_length=16, choices=ServiceComponent.STATUS_CHOICES, null=True, blank=True,
)
```

`is_up` is kept (not removed) — it stays the simple up/down summary consumed by `get_average_latency_ms` and any other boolean-only read site; `status` carries the richer 3-way signal. New heartbeats always set both, consistently (`is_up = status != DOWN`). Existing pre-migration rows keep `status=NULL`.

## Rollup

`rollup_daily_status()` takes the *worst* status seen that day per component, `DOWN` > `DEGRADED` > `OPERATIONAL`, treating a `NULL` `status` as `DOWN` if `is_up=False` else `OPERATIONAL` (exactly today's pre-migration behavior, so historical rows roll up identically to before this change):

```python
def rollup_daily_status(date=None):
    ...
    for component in ServiceComponent.objects.all():
        if component.manual_status:
            status = component.manual_status
        else:
            heartbeats = ComponentHeartbeat.objects.filter(component=component, checked_at__gte=day_start, checked_at__lte=day_end)
            if heartbeats.filter(status=ServiceComponent.STATUS_DOWN).exists() or \
               heartbeats.filter(status__isnull=True, is_up=False).exists():
                status = ServiceComponent.STATUS_DOWN
            elif heartbeats.filter(status=ServiceComponent.STATUS_DEGRADED).exists():
                status = ServiceComponent.STATUS_DEGRADED
            else:
                status = ServiceComponent.STATUS_OPERATIONAL
        DailyComponentStatus.objects.update_or_create(component=component, date=date, defaults={'status': status})
```

`manual_status` precedence is unchanged — it still short-circuits before any heartbeat is even queried.

## Error handling

- Every probe catches its own `requests.RequestException` internally and returns a `DOWN` status rather than raising (matches `_probe_database()`'s existing pattern).
- `record_heartbeats()` additionally wraps each probe call in try/except as a second layer, so a probe-internal bug (not just a network failure) can't take down the whole cron run — falls back to the DB signal and logs a warning.
- `check_service_health`'s existing `JobRun`/advisory-lock wrapping is untouched; this only changes what happens inside `record_heartbeats()`.

## Testing

- `apps/status/tests/test_probes.py`: mock `requests.get` (success/timeout/4xx/5xx) for `probe_payments`/`probe_whatsapp`, both configured and unconfigured (`None` return); `Device` fixtures across 2+ foundations for `probe_canteen_pos`'s ratio thresholds (0 devices, some offline, majority offline, all RETIRED excluded from the denominator).
- `apps/status/tests/test_services.py`: extend `record_heartbeats`/`rollup_daily_status` coverage for the new `status` field, the DEGRADED tier, a raising probe falling back to the DB signal, and pre-migration NULL-status rollup backward-compatibility.
- Migration test: existing `ComponentHeartbeat` rows with `status=NULL` still roll up identically to current behavior.
