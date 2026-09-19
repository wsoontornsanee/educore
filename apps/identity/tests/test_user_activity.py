"""Activity signal for platform health metrics (spec docs/superpowers/specs/2026-09-19-platform-health-metrics-design.md §1)."""
import datetime
from types import SimpleNamespace
from unittest import mock

from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.identity.activity import record_activity
from apps.identity.models import Foundation, User, UserActivityDay


class RecordActivityTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Yayasan Aktif', brand_name='Aktif')
        self.user = User.all_tenants.create_user(
            phone_e164='+6281200000501', full_name='Ibu Aktif', foundation_id=self.foundation.id,
        )

    def _days(self):
        return UserActivityDay.all_tenants.filter(user_id=self.user.pk)

    def test_first_call_of_the_day_sets_the_gate_and_inserts_one_row(self):
        record_activity(self.user)
        today = timezone.localdate()
        self.assertEqual(self.user.last_activity_date, today)
        self.assertEqual(User.all_tenants.get(pk=self.user.pk).last_activity_date, today)
        self.assertEqual(list(self._days().values_list('date', flat=True)), [today])
        self.assertEqual(self._days().get().foundation_id, self.foundation.id)

    def test_second_call_the_same_day_does_no_query(self):
        record_activity(self.user)
        with self.assertNumQueries(0):
            record_activity(self.user)
        self.assertEqual(self._days().count(), 1)

    def test_stale_copy_of_the_user_does_not_insert_twice(self):
        # Two requests loaded the user before either recorded: only the one whose UPDATE matches inserts.
        first = User.all_tenants.get(pk=self.user.pk)
        second = User.all_tenants.get(pk=self.user.pk)
        record_activity(first)
        record_activity(second)
        self.assertEqual(self._days().count(), 1)

    def test_a_new_day_inserts_a_new_row(self):
        record_activity(self.user)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        with mock.patch('apps.identity.activity.timezone.localdate', return_value=tomorrow):
            record_activity(self.user)
        self.assertEqual(self._days().count(), 2)

    def test_user_without_a_foundation_is_skipped(self):
        record_activity(SimpleNamespace(foundation_id=None, last_activity_date=None, pk=1))
        self.assertEqual(UserActivityDay.all_tenants.count(), 0)

    def test_a_recording_failure_is_swallowed_and_leaves_the_gate_open(self):
        broken = mock.MagicMock()
        broken.all_tenants.create.side_effect = DatabaseError('boom')
        with mock.patch('apps.identity.activity.UserActivityDay', broken), \
                self.assertLogs('apps.identity.activity', level='WARNING'):
            record_activity(self.user)  # must not raise
        self.assertIsNone(User.all_tenants.get(pk=self.user.pk).last_activity_date)
        record_activity(User.all_tenants.get(pk=self.user.pk))  # retry succeeds
        self.assertEqual(self._days().count(), 1)


class AuthenticationHookTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Yayasan Hook', brand_name='Hook')
        self.user = User.all_tenants.create_user(
            phone_e164='+6281200000502', full_name='Bapak Hook', foundation_id=self.foundation.id,
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_authenticated_request_records_one_row_per_day(self):
        self.assertEqual(self.client.get('/api/v1/me').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/me').status_code, 200)
        self.assertEqual(UserActivityDay.all_tenants.filter(user_id=self.user.pk).count(), 1)
