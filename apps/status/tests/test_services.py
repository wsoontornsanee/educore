import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from apps.status.models import ServiceComponent, ComponentHeartbeat, DailyComponentStatus
from apps.status.services import (
    record_heartbeats, rollup_daily_status, get_component_status,
    get_component_bars, get_uptime_percentage, get_average_latency_ms,
    get_open_component_count,
)
from apps.status import probes as status_probes


class RecordHeartbeatsTests(TestCase):
    def setUp(self):
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2')

    def test_creates_one_heartbeat_per_component(self):
        # Migration 0002 seeds additional ServiceComponents alongside c1/c2, so
        # record_heartbeats() creates one heartbeat per *all* existing components,
        # not just the two created in setUp.
        created = record_heartbeats()
        self.assertEqual(created, ServiceComponent.objects.count())
        self.assertEqual(ComponentHeartbeat.objects.filter(component=self.c1).count(), 1)
        self.assertEqual(ComponentHeartbeat.objects.filter(component=self.c2).count(), 1)

    def test_manual_down_override_marks_heartbeat_down(self):
        self.c1.manual_status = ServiceComponent.STATUS_DOWN
        self.c1.save()
        record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.c1)
        self.assertFalse(hb.is_up)


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


class RollupDailyStatusTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.today = timezone.localdate()

    def test_all_up_heartbeats_roll_up_operational(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)

    def test_any_down_heartbeat_rolls_up_down(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=False, latency_ms=None)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DOWN)

    def test_manual_status_overrides_heartbeats(self):
        self.component.manual_status = ServiceComponent.STATUS_DEGRADED
        self.component.save()
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DEGRADED)

    def test_rollup_is_idempotent(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        rollup_daily_status(self.today)
        self.assertEqual(DailyComponentStatus.objects.filter(component=self.component, date=self.today).count(), 1)

    def test_no_heartbeats_defaults_operational(self):
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)


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


class ComponentStatusQueryTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.today = timezone.localdate()

    def test_manual_status_takes_precedence(self):
        self.component.manual_status = ServiceComponent.STATUS_DOWN
        self.component.save()
        DailyComponentStatus.objects.create(component=self.component, date=self.today, status=ServiceComponent.STATUS_OPERATIONAL)
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_DOWN)

    def test_falls_back_to_daily_status(self):
        DailyComponentStatus.objects.create(component=self.component, date=self.today, status=ServiceComponent.STATUS_DEGRADED)
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_DEGRADED)

    def test_defaults_operational_with_no_data(self):
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_OPERATIONAL)

    def test_open_component_count(self):
        c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2', manual_status=ServiceComponent.STATUS_DEGRADED)
        self.assertEqual(get_open_component_count(self.today), 1)


class ComponentBarsTests(TestCase):
    def test_returns_30_bars_defaulting_unknown_color_for_missing_days(self):
        # No DailyComponentStatus rows exist for this component at all — a
        # fresh deploy shouldn't render a fake green/operational history;
        # it should use the distinct "unknown" color instead (Fix 5).
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        bars = get_component_bars(component, days=30, as_of=timezone.localdate())
        self.assertEqual(len(bars), 30)
        from apps.status.services import BAR_COLOR_UNKNOWN
        self.assertEqual(bars[-1]['color'], BAR_COLOR_UNKNOWN)
        self.assertNotEqual(bars[-1]['color'], '#0E7A4F')

    def test_reflects_recorded_daily_status(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_DOWN)
        bars = get_component_bars(component, days=30, as_of=today)
        self.assertEqual(bars[-1]['color'], '#B3261E')

    def test_recorded_operational_day_still_renders_green(self):
        # A day WITH a DailyComponentStatus=OPERATIONAL row is real,
        # confirmed uptime — still green, unlike a day with no row at all.
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_OPERATIONAL)
        bars = get_component_bars(component, days=30, as_of=today)
        self.assertEqual(bars[-1]['color'], '#0E7A4F')

    def test_invalid_status_value_already_in_db_does_not_crash(self):
        # Defense-in-depth: a bogus status value that ended up in the DB by
        # some other route (e.g. Django admin's list_editable on
        # ServiceComponent.manual_status, which bypasses choices validation
        # on the list page) must not 500 the public status page via a bare
        # BAR_COLORS[status] dict lookup.
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status='NOT_A_REAL_STATUS')
        bars = get_component_bars(component, days=30, as_of=today)
        self.assertEqual(bars[-1]['color'], '#0E7A4F')


class MetricsTests(TestCase):
    def test_uptime_percentage_none_with_no_data(self):
        self.assertIsNone(get_uptime_percentage(days=90, as_of=timezone.localdate()))

    def test_uptime_percentage_computed(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_OPERATIONAL)
        DailyComponentStatus.objects.create(component=component, date=today - datetime.timedelta(days=1), status=ServiceComponent.STATUS_DOWN)
        pct = get_uptime_percentage(days=90, as_of=today)
        self.assertEqual(pct, 50.0)

    def test_average_latency_none_with_no_data(self):
        self.assertIsNone(get_average_latency_ms(days=90, as_of=timezone.now()))

    def test_average_latency_computed(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        ComponentHeartbeat.objects.create(component=component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        ComponentHeartbeat.objects.create(component=component, checked_at=timezone.now(), is_up=True, latency_ms=200)
        self.assertEqual(get_average_latency_ms(days=90, as_of=timezone.now()), 150)
