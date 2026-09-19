"""RPT-010/RPT-011: module-tier subscription charges, prorated from entitlement history, and take-rate."""
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.identity.entitlements import ALL_MODULES, entitled_days_in_month, set_module_entitlement
from apps.identity.models import EntitlementChange, Foundation, ModulePrice, RoleAssignment
from apps.reporting.models import RptActiveStudent, RptDailyFinance, RptSubscriptionCharge, RptWalletActivity
from apps.reporting.services import get_metering_statement, refresh_subscription_charges

JAKARTA = ZoneInfo('Asia/Jakarta')
FEB = datetime.date(2026, 2, 1)  # 28 days, long closed
TODAY = datetime.date(2026, 9, 19)


def at(day, hour=12):
    return datetime.datetime(2026, 2, day, hour, tzinfo=JAKARTA)


class _Base(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']

    def change(self, module, enabled, when, school=None):
        return EntitlementChange.all_tenants.create(
            foundation_id=self.foundation.id, school_id=school.id if school else None, module_key=module,
            enabled=enabled, effective_from=when,
        )

    def price(self, module, unit_price, effective_from=datetime.date(2026, 1, 1), tier=None, currency='IDR'):
        return ModulePrice.objects.create(
            plan_tier=tier or self.foundation.plan_tier, module_key=module, currency=currency,
            unit_price=Decimal(unit_price), effective_from=effective_from,
        )

    def count(self, month=FEB, active_count=100):
        return RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, month=month, active_count=active_count,
            computed_at=timezone.now(),
        )

    def charges(self, month=FEB):
        return {
            c.module_key: c for c in RptSubscriptionCharge.all_tenants.filter(
                foundation_id=self.foundation.id, school=self.school, month=month,
            )
        }


class EntitledDaysTests(_Base):
    def days(self, month=FEB):
        return entitled_days_in_month(self.foundation.id, self.school.id, month)

    def test_no_history_reads_as_entitled_every_day(self):
        self.assertEqual(self.days(), {m: 28 for m in ALL_MODULES})

    def test_switched_off_mid_month_bills_that_day_then_stops(self):
        self.change('wallet', False, at(15, hour=10))
        self.assertEqual(self.days()['wallet'], 14)  # Feb 1-14; Feb 15 ends switched off
        self.assertEqual(self.days()['finance'], 28)

    def test_switched_on_mid_month_bills_that_day(self):
        self.change('wallet', False, at(1, hour=0) - datetime.timedelta(days=30))
        self.change('wallet', True, at(15, hour=10))
        self.assertEqual(self.days()['wallet'], 14)  # Feb 15..28

    def test_a_value_set_in_an_earlier_month_carries_forward(self):
        self.change('wallet', False, datetime.datetime(2026, 1, 10, tzinfo=JAKARTA))
        self.assertEqual(self.days()['wallet'], 0)

    def test_a_change_after_the_month_is_ignored(self):
        self.change('wallet', False, datetime.datetime(2026, 3, 1, 8, tzinfo=JAKARTA))
        self.assertEqual(self.days()['wallet'], 28)

    def test_day_boundary_uses_the_foundation_timezone(self):
        # 2026-02-14 18:00 UTC is 2026-02-15 01:00 in Jakarta: the 15th, not the 14th.
        self.change('wallet', False, datetime.datetime(2026, 2, 14, 18, tzinfo=datetime.timezone.utc))
        self.assertEqual(self.days()['wallet'], 14)

    def test_school_value_beats_the_foundation_default_and_removal_falls_back(self):
        self.change('wallet', False, at(1, hour=0) - datetime.timedelta(days=5))
        self.change('wallet', True, at(10), school=self.school)
        self.change('wallet', None, at(20), school=self.school)  # school row removed: back to the default (off)
        self.assertEqual(self.days()['wallet'], 10)  # Feb 10..19

    def test_another_schools_override_is_not_counted(self):
        other = type(self.school).all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Lain", npsn="70155598", level='SMP', base_currency='IDR',
        )
        self.change('wallet', False, at(1, hour=0) - datetime.timedelta(days=5), school=other)
        self.assertEqual(self.days()['wallet'], 28)


class RefreshSubscriptionChargesTests(_Base):
    def test_full_month_charge_is_count_times_price(self):
        self.count(active_count=100)
        self.price('finance', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        line = self.charges()['finance']
        self.assertEqual(line.amount, Decimal('1500000.00'))
        self.assertEqual((line.active_days, line.days_in_month, line.active_count), (28, 28, 100))
        self.assertEqual((line.plan_tier, line.currency, line.unit_price), ('STANDARD', 'IDR', Decimal('15000.00')))

    def test_charge_is_prorated_by_days_entitled(self):
        self.count(active_count=100)
        self.price('wallet', '15000.00')
        self.change('wallet', False, at(15, hour=10))
        refresh_subscription_charges(scope='full', since=FEB)
        line = self.charges()['wallet']
        self.assertEqual((line.active_days, line.amount), (14, Decimal('750000.00')))

    def test_a_module_switched_off_all_month_is_a_zero_line_not_a_missing_one(self):
        self.count()
        self.price('wallet', '15000.00')
        self.change('wallet', False, datetime.datetime(2026, 1, 5, tzinfo=JAKARTA))
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(self.charges()['wallet'].amount, Decimal('0.00'))

    def test_rounded_half_up_once(self):
        self.count(active_count=1)
        self.price('wallet', '0.01')
        self.change('wallet', False, at(15, hour=10))
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(self.charges()['wallet'].amount, Decimal('0.01'))  # 0.005 -> 0.01, not banker's 0.00

    def test_module_without_a_price_is_not_charged(self):
        self.count()
        self.price('wallet', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(set(self.charges()), {'wallet'})

    def test_price_is_the_foundations_tier_and_the_latest_start(self):
        self.count()
        self.price('wallet', '10000.00')
        self.price('wallet', '12000.00', effective_from=datetime.date(2026, 2, 1))
        self.price('wallet', '99000.00', effective_from=datetime.date(2026, 3, 1))  # later month
        self.price('wallet', '77000.00', tier='ENTERPRISE')  # another tier
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(self.charges()['wallet'].unit_price, Decimal('12000.00'))

    def test_a_school_with_no_count_has_no_charge(self):
        self.price('wallet', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(self.charges(), {})

    def test_month_is_frozen_after_the_first_run_past_its_end(self):
        self.count()
        self.price('wallet', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        self.price('finance', '20000.00')
        self.change('wallet', False, at(3))
        ModulePrice.objects.filter(module_key='wallet').update(unit_price=Decimal('1.00'))
        refresh_subscription_charges(scope='full', since=FEB)
        charges = self.charges()
        self.assertEqual(set(charges), {'wallet'})
        self.assertEqual(charges['wallet'].amount, Decimal('1500000.00'))

    def test_a_month_computed_before_it_ended_is_rewritten_once_more_then_frozen(self):
        self.count()
        self.price('wallet', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        # Pretend that row was written on the month's last day, before it ended.
        RptSubscriptionCharge.all_tenants.filter(foundation_id=self.foundation.id).update(
            computed_at=datetime.datetime(2026, 2, 28, 12, tzinfo=datetime.timezone.utc), amount=Decimal('1.00'),
        )
        refresh_subscription_charges(scope='full', since=FEB)
        self.assertEqual(self.charges()['wallet'].amount, Decimal('1500000.00'))

    def test_open_month_is_recomputed_on_every_run(self):
        month = timezone.now().date().replace(day=1)
        self.count(month=month, active_count=10)
        self.price('wallet', '1000.00', effective_from=datetime.date(2026, 1, 1))
        refresh_subscription_charges(scope='dashboard')
        first = self.charges(month)['wallet']
        self.assertEqual(first.active_count, 10)
        RptActiveStudent.all_tenants.filter(foundation_id=self.foundation.id).update(active_count=20)
        refresh_subscription_charges(scope='dashboard')
        self.assertEqual(self.charges(month)['wallet'].active_count, 20)


class MeteringStatementRevenueTests(_Base):
    def setUp(self):
        super().setUp()
        self.count()
        self.price('wallet', '15000.00')
        refresh_subscription_charges(scope='full', since=FEB)
        RptWalletActivity.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=datetime.date(2026, 2, 3), currency='IDR',
            commission=Decimal('1250.00'), computed_at=timezone.now(),
        )
        RptWalletActivity.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=datetime.date(2026, 2, 20), currency='IDR',
            commission=Decimal('750.50'), computed_at=timezone.now(),
        )
        RptWalletActivity.all_tenants.create(  # another month: not counted
            foundation_id=self.foundation.id, school=self.school, date=datetime.date(2026, 3, 1), currency='IDR',
            commission=Decimal('9999.00'), computed_at=timezone.now(),
        )
        RptDailyFinance.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=datetime.date(2026, 2, 10), currency='IDR',
            fees=Decimal('4000.00'), computed_at=timezone.now(),
        )

    def entry(self, **kwargs):
        statement = get_metering_statement(self.foundation.id, FEB, today=TODAY, **kwargs)
        return statement, next(e for e in statement['schools'] if e['school_id'] == self.school.id)

    def test_school_carries_subscription_take_rate_and_fees(self):
        statement, entry = self.entry()
        self.assertEqual(entry['subscription']['totals'], [{'amount': '1500000.00', 'currency': 'IDR'}])
        self.assertEqual([l['module_key'] for l in entry['subscription']['lines']], ['wallet'])
        self.assertEqual(entry['campus_take_rate'], [{'amount': '2000.50', 'currency': 'IDR'}])
        self.assertEqual(entry['payment_fees'], [{'amount': '4000.00', 'currency': 'IDR'}])
        self.assertEqual(statement['revenue_totals'], [{
            'currency': 'IDR', 'subscription': '1500000.00', 'campus_take_rate': '2000.50', 'payment_fees': '4000.00',
        }])

    def test_school_without_a_count_has_no_subscription_block(self):
        statement = get_metering_statement(self.foundation.id, datetime.date(2026, 1, 1), today=TODAY)
        self.assertIsNone(statement['schools'][0]['subscription'])
        self.assertEqual(statement['schools'][0]['campus_take_rate'], [])

    def test_school_ceiling_limits_the_totals(self):
        statement = get_metering_statement(self.foundation.id, FEB, school_ids=[], today=TODAY)
        self.assertEqual(statement['schools'], [])
        self.assertEqual(statement['revenue_totals'], [])

    def test_api_serialises_money_as_strings(self):
        user = self.fx['teacher_user']
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=user, role='foundation_admin',
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get('/api/v1/metering/statements/?month=2026-02')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['schools'][0]['subscription']['totals'], [{'amount': '1500000.00', 'currency': 'IDR'}])
        self.assertEqual(body['revenue_totals'][0]['campus_take_rate'], '2000.50')
        self.assertEqual(body['schools'][0]['subscription']['lines'][0]['unit_price'], '15000.00')
