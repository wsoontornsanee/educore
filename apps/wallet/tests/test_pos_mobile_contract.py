"""The POS kiosk's wire contract with the mobile app (mobile/src/services/posAdapter.ts).

The mobile unit tests mock the API, so when the two sides drifted (roster `student_id`/`name`/`wallet_balance`
vs the kiosk's `id`/`full_name`/`balance`; catalog with no `id`; sync `*_delta` keys) nothing failed until the
kiosk crashed on a real device. `mobile/__tests__/fixtures/pos_{session,sync}.json` are captured from this
server's real output and the mobile adapter tests read them; this test fails if the server's field names change
away from them. When it fails, update the fixtures (regenerate with the snippet in `_dump`) AND the adapter.
"""
import datetime
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from apps.wallet.models import SpendRule
from apps.wallet.services import get_or_create_wallet, pos_session, pos_sync, topup_wallet
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_core import build_wallet_fixture

FIXTURES = Path(settings.BASE_DIR) / 'mobile' / '__tests__' / 'fixtures'


def keys_of(value):
    """The field names an API payload uses at every level; values are ignored (they change, names are the contract)."""
    if isinstance(value, dict):
        return {k: keys_of(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        merged = {}
        for item in value:
            shape = keys_of(item)
            if isinstance(shape, dict):
                merged.update(shape)
        return [merged] if merged else []
    return None


class POSMobileContractTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(wallet, Decimal('100000'), 'CASH', 'seed-topup')
        SpendRule.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'], daily_limit=Decimal('25000.00'),
            blocked_categories=['SNACK'], blocked_products=['GUM-01'],
            allowed_window_start=datetime.time(9, 30), allowed_window_end=datetime.time(13, 30),
        )

    def _dump(self):
        """Regenerate the fixtures: run this from a shell test and paste the output into the JSON files."""
        return json.dumps(pos_session(self.terminal), indent=2, default=str), json.dumps(pos_sync(self.terminal), indent=2, default=str)

    def _fixture(self, name):
        return json.loads((FIXTURES / name).read_text())

    def test_session_payload_field_names_match_the_mobile_fixture(self):
        actual = json.loads(json.dumps(pos_session(self.terminal), default=str))
        self.assertEqual(keys_of(actual), keys_of(self._fixture('pos_session.json')))

    def test_sync_payload_field_names_match_the_mobile_fixture(self):
        actual = json.loads(json.dumps(pos_sync(self.terminal), default=str))
        self.assertEqual(keys_of(actual), keys_of(self._fixture('pos_sync.json')))

    def test_fixtures_exercise_every_nested_object(self):
        # A fixture with an empty roster/catalog/rules would make the comparison above pass vacuously.
        for name, lists in (('pos_session.json', ('roster', 'catalog', 'rules')),
                            ('pos_sync.json', ('roster_delta', 'catalog_delta', 'rules_delta'))):
            data = self._fixture(name)
            for key in lists:
                self.assertTrue(data[key], f'{name}: {key} is empty')
