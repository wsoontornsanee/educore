import datetime
import hashlib
from abc import ABC, abstractmethod
from decimal import Decimal
import dateutil.parser
import requests
from django.conf import settings
from django.utils import timezone


class PaymentGatewayError(Exception):
    """Raised on a payment gateway misconfiguration or a non-2xx gateway response —
    fails loud rather than silently returning garbage or a confusing downstream error.
    """
    pass


class PaymentProvider(ABC):
    """Abstract payment gateway provider (spec/06 §4 FIN-012)."""

    @abstractmethod
    def create_va(self, student, school, bank: str, amount: Decimal, expires_at) -> dict:
        """Allocate or request a virtual account number."""
        pass

    @abstractmethod
    def create_qris(self, student, school, amount: Decimal, expires_at) -> dict:
        """Generate a dynamic QRIS payload."""
        pass

    @abstractmethod
    def verify_webhook(self, payload: dict, headers: dict = None) -> bool:
        """Verify authenticity of webhook callback signature."""
        pass

    @abstractmethod
    def parse_webhook(self, payload: dict) -> dict:
        """
        Normalize webhook payload to:
        {
            'external_id': str,
            'status': 'SETTLED' | 'PENDING' | 'FAILED' | 'CANCELLED',
            'amount': Decimal,
            'fee': Decimal,
            'net': Decimal,
            'paid_at': datetime,
            'channel': str,
            'bank': str or None,
            'raw': dict,
        }
        """
        pass

    @abstractmethod
    def check_status(self, intent) -> dict | None:
        """
        Poll the gateway for whether a PENDING `PaymentIntent` has actually
        settled — the `sync_payment_status` safety net for a `FIN-013`
        settlement webhook that never arrived (`ARC-006`/`ARC-009`, spec/01 §5).

        Returns the same normalized shape as `parse_webhook()`, or `None` when
        the gateway has no matching settled transaction yet (still pending,
        or — for a provider/method combination with no queryable backend —
        genuinely unknown; the caller then falls back to expiring the intent
        once `expires_at` has passed).
        """
        pass

    @abstractmethod
    def fetch_settlement(self, date: datetime.date) -> list[dict]:
        """
        Fetch the gateway settlement report for a given settlement date.

        Returns a list of normalized settlement records:
        [
            {
                'external_id': str,          # gateway transaction / reference ID
                'amount': Decimal,           # gross settlement amount
                'fee': Decimal,              # gateway MDR / fee deducted
                'net': Decimal,              # amount actually remitted
                'settled_at': datetime,      # timestamp settlement was finalized
                'channel': str,              # payment channel e.g. BCA_VA, QRIS
                'bank': str or None,         # bank code if applicable
                'raw': dict,                 # unmodified gateway payload
            },
            …
        ]

        Raises PaymentGatewayError on network or authentication failure.
        Returns an empty list when the provider has no settlements for the date.
        """
        pass


def get_school_code(school) -> str:
    if school is None:
        return "SCH00"
    if hasattr(school, 'npsn') and school.npsn:
        return school.npsn
    if hasattr(school, 'code') and school.code:
        return school.code
    return f"SCH{school.id}"


class MidtransPaymentProvider(PaymentProvider):
    """Midtrans Snap / Core API implementation."""

    def __init__(self, server_key: str = None):
        self.server_key = server_key or getattr(settings, 'MIDTRANS_SERVER_KEY', 'sandbox-server-key')

    def create_va(self, student, school, bank: str, amount: Decimal, expires_at) -> dict:
        bank_code = bank.lower()
        # Simulated VA formatting per Midtrans convention
        code = get_school_code(school)
        va_number = f"88{code[:4].upper()}{student.id:06d}"
        return {
            'provider': 'MIDTRANS',
            'va_bank': bank.upper(),
            'va_number': va_number,
            'amount': amount,
            'expires_at': expires_at,
        }


    def create_qris(self, student, school, amount: Decimal, expires_at) -> dict:
        qr_payload = f"00020101021226600016ID.CO.EDUCORE.WWW0118EDUCORE{school.id:04d}{student.id:06d}52045812530336054{int(amount)}5802ID5913EDUCORE_PAY6007JAKARTA62070703A016304"
        return {
            'provider': 'MIDTRANS',
            'qris_payload': qr_payload,
            'external_id': None,
            'amount': amount,
            'expires_at': expires_at,
        }

    def verify_webhook(self, payload: dict, headers: dict = None) -> bool:
        signature_key = payload.get('signature_key')
        if not signature_key:
            return False
        order_id = payload.get('order_id', '')
        status_code = str(payload.get('status_code', ''))
        gross_amount = str(payload.get('gross_amount', ''))
        raw = f"{order_id}{status_code}{gross_amount}{self.server_key}"
        expected = hashlib.sha512(raw.encode('utf-8')).hexdigest()
        return signature_key.lower() == expected.lower()

    def parse_webhook(self, payload: dict) -> dict:
        txn_status = payload.get('transaction_status', '')
        fraud_status = payload.get('fraud_status', '')
        
        if txn_status in ['settlement', 'capture'] and fraud_status in ['accept', '']:
            status = 'SETTLED'
        elif txn_status == 'pending':
            status = 'PENDING'
        elif txn_status in ['deny', 'cancel', 'expire']:
            status = 'CANCELLED'
        else:
            status = 'FAILED'

        amount = Decimal(str(payload.get('gross_amount', '0.00')))
        fee = Decimal(str(payload.get('fee', '0.00')))
        net = amount - fee

        # Channel & Bank resolution
        payment_type = payload.get('payment_type', '')
        bank = None
        if payment_type == 'bank_transfer':
            va_numbers = payload.get('va_numbers', [])
            if va_numbers:
                bank = va_numbers[0].get('bank', '').upper()
            channel = f"{bank}_VA" if bank else 'BANK_TRANSFER'
        elif payment_type == 'qris':
            channel = 'QRIS'
        else:
            channel = payment_type.upper()

        paid_at = timezone.now()

        return {
            'external_id': str(payload.get('order_id')),
            'status': status,
            'amount': amount,
            'fee': fee,
            'net': net,
            'paid_at': paid_at,
            'channel': channel,
            'bank': bank,
            'raw': payload,
        }

    def check_status(self, intent) -> dict | None:
        """Not implemented for real: `create_va`/`create_qris` above never make a
        real Midtrans API call (they're local simulations — no order_id is ever
        actually minted at the gateway), so there is no genuine order_id to poll
        `GET /v2/{order_id}/status` with. Same documented limitation as
        `fetch_settlement` below; Midtrans intents rely on their webhook only.
        """
        return None

    def fetch_settlement(self, date: datetime.date) -> list[dict]:
        """Midtrans does not expose a bulk settlement pull API at this time.

        Settlement reconciliation for Midtrans must be handled via their
        merchant dashboard CSV export (CMP-024 open item).  Returns empty list
        so the reconciliation command can treat Midtrans as "no automated
        settlement" without raising an error.
        """
        return []


class XenditPaymentProvider(PaymentProvider):
    """Xendit API implementation.

    Launch VA provider per the researched [Open Decision] Virtual Account Provider
    Integration Strategy (2026-09-15): Xendit's Fixed Virtual Account product is the
    only one of Midtrans/Xendit that supports a genuinely stable, multi-use,
    reusable-per-customer VA — Midtrans VA numbers are order/transaction-scoped with
    a 24h-180d expiry, not persistent. FIN-011's "stable per-student VA" requirement
    is only actually achievable against Xendit as designed here.
    """

    def __init__(self, api_key: str = None, callback_token: str = None, base_url: str = None):
        self.api_key = api_key or getattr(settings, 'XENDIT_API_KEY', '')
        self.callback_token = callback_token or getattr(settings, 'XENDIT_CALLBACK_TOKEN', 'sandbox-token')
        self.base_url = base_url or getattr(settings, 'XENDIT_BASE_URL', 'https://api.xendit.co')

    def _auth(self):
        if not self.api_key:
            raise PaymentGatewayError("XENDIT_API_KEY is not configured — cannot call the Xendit API.")
        return (self.api_key, '')

    def _post(self, path: str, json_body: dict) -> dict:
        try:
            response = requests.post(f"{self.base_url}{path}", json=json_body, auth=self._auth(), timeout=15)
        except requests.RequestException as exc:
            raise PaymentGatewayError(f"Xendit request to {path} failed: {exc}") from exc
        if not response.ok:
            raise PaymentGatewayError(f"Xendit {path} returned {response.status_code}: {response.text}")
        return response.json()

    def create_va(self, student, school, bank: str, amount: Decimal, expires_at) -> dict:
        """Creates a Fixed (multi-use, open-amount) Virtual Account — reusable across
        every future payment by this student to this bank, per FIN-011.

        external_id is deterministic (not random) so a retried call after a network
        failure lands on the same Xendit VA rather than allocating a duplicate.
        """
        external_id = f"studentva-{school.foundation_id}-{student.id}-{bank.upper()}"
        body = self._post('/callback_virtual_accounts', {
            'external_id': external_id,
            'bank_code': bank.upper(),
            'name': student.person.full_name if student.person else external_id,
            'is_closed': False,
            'is_single_use': False,
            'expiration_date': expires_at.isoformat() if expires_at else None,
        })
        return {
            'provider': 'XENDIT',
            'va_bank': bank.upper(),
            'va_number': body['account_number'],
            'amount': amount,
            'expires_at': expires_at,
            'raw': body,
        }

    def create_qris(self, student, school, amount: Decimal, expires_at) -> dict:
        external_id = f"qris-{school.foundation_id}-{student.id}-{timezone.now().timestamp()}"
        body = self._post('/qr_codes', {
            'external_id': external_id,
            'type': 'DYNAMIC',
            'amount': float(amount),
        })
        return {
            'provider': 'XENDIT',
            'qris_payload': body['qr_string'],
            'external_id': external_id,
            'amount': amount,
            'expires_at': expires_at,
            'raw': body,
        }

    def verify_webhook(self, payload: dict, headers: dict = None) -> bool:
        if not headers:
            return False
        token = headers.get('x-callback-token') or headers.get('X-CALLBACK-TOKEN')
        return token == self.callback_token

    def check_status(self, intent) -> dict | None:
        """Poll `GET /transactions?reference_id=...` — the `sync_payment_status`
        safety net for a missed FIN-013 webhook.

        QRIS has a real 1:1 `external_id` per intent, captured in
        `intent.metadata['gateway_external_id']` at creation, so that lookup is
        exact. VA is a stable, reusable-per-student account (FIN-011), not a
        per-intent object — there is no single "this intent's transaction id"
        to look up directly, so this reconstructs the VA's own creation
        `external_id` (must match `create_va`'s formula exactly) and matches
        the newest SUCCESS transaction on it by exact amount, created no
        earlier than the intent itself. That is an amount-based heuristic, not
        a guaranteed 1:1 match — no worse than the webhook path's own VA
        fallback in `process_payment_webhook`, which doesn't disambiguate
        between intents on the same VA at all.
        """
        from apps.finance.models import PaymentMethod

        if intent.method == PaymentMethod.QRIS:
            reference_id = (intent.metadata or {}).get('gateway_external_id')
            if not reference_id:
                return None
        elif intent.method == PaymentMethod.VA:
            if not intent.va_bank:
                return None
            reference_id = f"studentva-{intent.foundation_id}-{intent.student_id}-{intent.va_bank.upper()}"
        else:
            return None

        try:
            response = requests.get(
                f"{self.base_url}/transactions",
                params={'reference_id': reference_id, 'statuses': ['SUCCESS']},
                auth=self._auth(),
                timeout=15,
            )
        except requests.RequestException as exc:
            raise PaymentGatewayError(f"Xendit transaction status check failed: {exc}") from exc
        if not response.ok:
            raise PaymentGatewayError(f"Xendit /transactions returned {response.status_code}: {response.text}")

        data = response.json()
        items = data if isinstance(data, list) else data.get('data', [])
        for item in items:
            if item.get('status') != 'SUCCESS':
                continue
            amount = Decimal(str(item.get('amount', '0.00')))
            if amount != intent.amount:
                continue
            created_str = item.get('created')
            created_at = None
            if created_str:
                try:
                    created_at = dateutil.parser.parse(created_str)
                    if created_at.tzinfo is None:
                        created_at = timezone.make_aware(created_at)
                except (ValueError, OverflowError, TypeError):
                    created_at = None
            if created_at and created_at < intent.created_at:
                continue

            fee = Decimal(str(item.get('fee', '0.00')))
            channel = str(item.get('channel_code') or reference_id).upper()
            return {
                'external_id': str(item.get('id') or item.get('reference_id') or reference_id),
                'status': 'SETTLED',
                'amount': amount,
                'fee': fee,
                'net': amount - fee,
                'paid_at': created_at or timezone.now(),
                'channel': channel,
                'bank': item.get('channel_code') if intent.method == PaymentMethod.VA else None,
                'raw': item,
            }
        return None

    def parse_webhook(self, payload: dict) -> dict:
        status_raw = payload.get('status', '').upper()
        if status_raw in ['PAID', 'SETTLED', 'COMPLETED']:
            status = 'SETTLED'
        elif status_raw == 'PENDING':
            status = 'PENDING'
        else:
            status = 'FAILED'

        amount = Decimal(str(payload.get('amount', payload.get('paid_amount', '0.00'))))
        fee = Decimal(str(payload.get('fee', '0.00')))
        net = amount - fee
        channel = payload.get('payment_method', payload.get('bank_code', 'XENDIT')).upper()

        return {
            'external_id': str(payload.get('external_id', payload.get('id'))),
            'status': status,
            'amount': amount,
            'fee': fee,
            'net': net,
            'paid_at': timezone.now(),
            'channel': channel,
            'bank': payload.get('bank_code'),
            'raw': payload,
        }

    def fetch_settlement(self, date: datetime.date) -> list[dict]:
        """Fetch Xendit settlement report for the given date via GET /v2/settlements.

        Xendit settles per-transaction; we filter by `settlement_date`.
        The API paginates via `after_id`; we iterate until exhausted.
        """
        date_str = date.strftime('%Y-%m-%d')
        params: dict = {
            'settlement_date': date_str,
            'limit': 100,
        }
        results: list[dict] = []
        while True:
            try:
                response = requests.get(
                    f"{self.base_url}/v2/settlements",
                    params=params,
                    auth=self._auth(),
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise PaymentGatewayError(f"Xendit settlement fetch failed: {exc}") from exc
            if not response.ok:
                raise PaymentGatewayError(
                    f"Xendit /v2/settlements returned {response.status_code}: {response.text}"
                )
            data = response.json()
            items = data if isinstance(data, list) else data.get('data', [])
            for item in items:
                amount = Decimal(str(item.get('settlement_amount', item.get('amount', '0.00'))))
                fee = Decimal(str(item.get('fee', item.get('fee_amount', '0.00'))))
                net = amount - fee
                channel = (item.get('payment_method') or item.get('channel') or 'XENDIT').upper()
                bank = item.get('bank_code') or item.get('bank')
                settled_str = item.get('settled_at') or item.get('settlement_date') or date_str
                try:
                    settled_at = dateutil.parser.parse(settled_str)
                    if settled_at.tzinfo is None:
                        settled_at = timezone.make_aware(settled_at)
                except (ValueError, OverflowError, TypeError):
                    settled_at = timezone.datetime.combine(date, timezone.datetime.min.time(),
                                                           tzinfo=timezone.get_current_timezone())
                results.append({
                    'external_id': str(item.get('reference_id') or item.get('external_id') or item.get('id')),
                    'amount': amount,
                    'fee': fee,
                    'net': net,
                    'settled_at': settled_at,
                    'channel': channel,
                    'bank': bank,
                    'raw': item,
                })
            # Pagination: stop if fewer items returned than limit, or no cursor
            has_more = data.get('has_more', False) if isinstance(data, dict) else False
            after_id = (items[-1].get('id') if items else None)
            if not has_more or not after_id:
                break
            params['after_id'] = after_id
        return results


class MockPaymentProvider(PaymentProvider):
    """Deterministic Mock provider for test suites and sandbox environments."""

    def __init__(self, secret: str = 'mock-secret'):
        self.secret = secret

    def create_va(self, student, school, bank: str, amount: Decimal, expires_at) -> dict:
        bank_upper = bank.upper()
        # Stable prefix per bank
        bank_prefixes = {
            'BCA': '100',
            'MANDIRI': '200',
            'BRI': '300',
            'BNI': '400',
            'PERMATA': '500',
        }
        prefix = bank_prefixes.get(bank_upper, '999')
        va_number = f"{prefix}{school.id:03d}{student.id:06d}"
        return {
            'provider': 'MOCK',
            'va_bank': bank_upper,
            'va_number': va_number,
            'amount': amount,
            'expires_at': expires_at,
        }

    def create_qris(self, student, school, amount: Decimal, expires_at) -> dict:
        code = get_school_code(school)
        payload = f"000201010211MOCK_QRIS_{code}_{student.id}_{int(amount)}"
        external_id = f"mock-qris-{code}-{student.id}-{int(amount)}"
        return {
            'provider': 'MOCK',
            'qris_payload': payload,
            'external_id': external_id,
            'amount': amount,
            'expires_at': expires_at,
        }


    def verify_webhook(self, payload: dict, headers: dict = None) -> bool:
        signature = payload.get('signature')
        if not signature:
            # Allow sandbox header override in tests
            if headers and headers.get('X-Mock-Sandbox') == 'true':
                return True
            return False
        order_id = payload.get('order_id', '')
        amount = str(payload.get('amount', ''))
        expected = hashlib.sha256(f"{order_id}{amount}{self.secret}".encode('utf-8')).hexdigest()
        return signature == expected

    def parse_webhook(self, payload: dict) -> dict:
        status = payload.get('status', 'SETTLED').upper()
        amount = Decimal(str(payload.get('amount', '0.00')))
        fee = Decimal(str(payload.get('fee', '0.00')))
        net = amount - fee
        return {
            'external_id': str(payload.get('order_id')),
            'status': status,
            'amount': amount,
            'fee': fee,
            'net': net,
            'paid_at': timezone.now(),
            'channel': payload.get('channel', 'MOCK_VA'),
            'bank': payload.get('bank'),
            'raw': payload,
        }

    def check_status(self, intent) -> dict | None:
        """The mock provider has no real backend transaction log to poll — tests
        that need to exercise `sync_payment_status`'s settled path monkeypatch
        this method directly (same style as `test_payment_providers.py`
        patching `requests.post`/`requests.get` for the real providers).
        """
        return None

    def fetch_settlement(self, date: datetime.date) -> list[dict]:
        """Return two deterministic settlement records for any date — one BCA VA,
        one QRIS — enabling fully offline unit tests without mocking HTTP.
        """
        import datetime as dt
        settled_at = timezone.datetime.combine(
            date, dt.time(16, 0, 0), tzinfo=timezone.get_current_timezone()
        )
        return [
            {
                'external_id': f'MOCK-{date.strftime("%Y%m%d")}-VA-001',
                'amount': Decimal('500000.00'),
                'fee': Decimal('3000.00'),
                'net': Decimal('497000.00'),
                'settled_at': settled_at,
                'channel': 'BCA_VA',
                'bank': 'BCA',
                'raw': {'mock': True, 'seq': 1},
            },
            {
                'external_id': f'MOCK-{date.strftime("%Y%m%d")}-QRIS-001',
                'amount': Decimal('250000.00'),
                'fee': Decimal('1750.00'),
                'net': Decimal('248250.00'),
                'settled_at': settled_at,
                'channel': 'QRIS',
                'bank': None,
                'raw': {'mock': True, 'seq': 2},
            },
        ]


def get_payment_provider(provider_name: str) -> PaymentProvider:
    name = (provider_name or 'MOCK').upper()
    if name == 'MIDTRANS':
        return MidtransPaymentProvider()
    elif name == 'XENDIT':
        return XenditPaymentProvider()
    return MockPaymentProvider()
