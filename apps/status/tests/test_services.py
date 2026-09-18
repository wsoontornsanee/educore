import datetime
from django.test import TestCase
from django.utils import timezone
from apps.status.models import ServiceComponent, ComponentHeartbeat, DailyComponentStatus
from apps.status.services import (
    record_heartbeats, rollup_daily_status, get_component_status,
    get_component_bars, get_uptime_percentage, get_average_latency_ms,
    get_open_component_count,
)


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
    def test_returns_30_bars_defaulting_operational_color(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        bars = get_component_bars(component, days=30, as_of=timezone.localdate())
        self.assertEqual(len(bars), 30)
        self.assertEqual(bars[-1]['color'], '#0E7A4F')

    def test_reflects_recorded_daily_status(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_DOWN)
        bars = get_component_bars(component, days=30, as_of=today)
        self.assertEqual(bars[-1]['color'], '#B3261E')


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
