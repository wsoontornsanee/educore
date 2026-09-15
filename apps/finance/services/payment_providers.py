import hashlib
from abc import ABC, abstractmethod
from decimal import Decimal
from django.conf import settings
from django.utils import timezone


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


class XenditPaymentProvider(PaymentProvider):
    """Xendit API implementation."""

    def __init__(self, callback_token: str = None):
        self.callback_token = callback_token or getattr(settings, 'XENDIT_CALLBACK_TOKEN', 'sandbox-token')

    def create_va(self, student, school, bank: str, amount: Decimal, expires_at) -> dict:
        code = get_school_code(school)
        va_number = f"99{code[:4].upper()}{student.id:06d}"
        return {
            'provider': 'XENDIT',
            'va_bank': bank.upper(),
            'va_number': va_number,
            'amount': amount,
            'expires_at': expires_at,
        }

    def create_qris(self, student, school, amount: Decimal, expires_at) -> dict:
        code = get_school_code(school)
        return {
            'provider': 'XENDIT',
            'qris_payload': f"https://qr.xendit.co/qr/{code}-{student.id}-{int(amount)}",
            'amount': amount,
            'expires_at': expires_at,
        }

    def verify_webhook(self, payload: dict, headers: dict = None) -> bool:
        if not headers:
            return False
        token = headers.get('x-callback-token') or headers.get('X-CALLBACK-TOKEN')
        return token == self.callback_token

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
        return {
            'provider': 'MOCK',
            'qris_payload': payload,
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


def get_payment_provider(provider_name: str) -> PaymentProvider:
    name = (provider_name or 'MOCK').upper()
    if name == 'MIDTRANS':
        return MidtransPaymentProvider()
    elif name == 'XENDIT':
        return XenditPaymentProvider()
    return MockPaymentProvider()
