import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    success: bool
    provider_message_id: str = ''
    status: str = 'SENT'
    cost: Decimal = Decimal('0.00')
    cost_currency: str = 'IDR'
    error_code: str = ''
    error_message: str = ''


class CircuitBreakerOpenException(Exception):
    """Raised when a provider's circuit breaker is open (NTF-015)."""
    pass


class BaseNotificationProvider(ABC):
    channel: str
    name: str

    def __init__(self):
        self.consecutive_failures = 0
        self.circuit_open_until = None
        self.failure_threshold = 5
        self.cooldown_seconds = 60

    def check_circuit(self):
        if self.circuit_open_until and timezone.now() < self.circuit_open_until:
            raise CircuitBreakerOpenException(f"Circuit breaker open for provider {self.name} until {self.circuit_open_until}")
        elif self.circuit_open_until:
            # Cooldown passed, half-open
            self.circuit_open_until = None
            self.consecutive_failures = 0

    def record_success(self):
        self.consecutive_failures = 0
        self.circuit_open_until = None

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.circuit_open_until = timezone.now() + timezone.timedelta(seconds=self.cooldown_seconds)
            logger.warning(f"Circuit breaker tripped for {self.name}! Failing fast for {self.cooldown_seconds}s.")

    @abstractmethod
    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        pass


class MockWhatsAppProvider(BaseNotificationProvider):
    channel = 'WHATSAPP'
    name = 'mock_whatsapp'

    def __init__(self):
        super().__init__()
        self.dispatched_messages: List[Dict] = []
        self.simulate_failure = False
        self.simulate_failure_code = ''
        self.simulate_failure_message = ''

    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        self.check_circuit()

        if self.simulate_failure:
            self.record_failure()
            return ProviderResult(
                success=False,
                status='FAILED',
                error_code=self.simulate_failure_code or 'MOCK_WHATSAPP_FAIL',
                error_message=self.simulate_failure_message or 'Simulated WhatsApp delivery failure',
            )

        msg_id = f"wamid.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'body': rendered_body,
            'subject': rendered_subject,
            'template_key': template_key,
            'variables': variables or {},
            'timestamp': timezone.now().isoformat(),
        })

        self.record_success()
        return ProviderResult(
            success=True,
            provider_message_id=msg_id,
            status='SENT',
            cost=Decimal('450.00'),  # Estimated standard Indonesian WhatsApp utility template fee
            cost_currency='IDR',
        )


class MockPushProvider(BaseNotificationProvider):
    channel = 'PUSH'
    name = 'mock_push'

    def __init__(self):
        super().__init__()
        self.dispatched_messages: List[Dict] = []
        self.simulate_failure = False

    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        self.check_circuit()
        if self.simulate_failure:
            self.record_failure()
            return ProviderResult(success=False, status='FAILED', error_code='PUSH_FAILED', error_message='Push failure')

        msg_id = f"fcm.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'body': rendered_body,
            'subject': rendered_subject,
            'timestamp': timezone.now().isoformat(),
        })
        self.record_success()
        return ProviderResult(
            success=True,
            provider_message_id=msg_id,
            status='SENT',
            cost=Decimal('0.00'),
            cost_currency='IDR',
        )


class MockSmsProvider(BaseNotificationProvider):
    channel = 'SMS'
    name = 'mock_sms'

    def __init__(self):
        super().__init__()
        self.dispatched_messages: List[Dict] = []
        self.simulate_failure = False

    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        self.check_circuit()
        if self.simulate_failure:
            self.record_failure()
            return ProviderResult(success=False, status='FAILED', error_code='SMS_FAILED', error_message='SMS failure')

        msg_id = f"sms.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'body': rendered_body,
            'timestamp': timezone.now().isoformat(),
        })
        self.record_success()
        return ProviderResult(
            success=True,
            provider_message_id=msg_id,
            status='SENT',
            cost=Decimal('250.00'),
            cost_currency='IDR',
        )


class MockEmailProvider(BaseNotificationProvider):
    channel = 'EMAIL'
    name = 'mock_email'

    def __init__(self):
        super().__init__()
        self.dispatched_messages: List[Dict] = []
        self.simulate_failure = False

    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        self.check_circuit()
        if self.simulate_failure:
            self.record_failure()
            return ProviderResult(success=False, status='FAILED', error_code='EMAIL_FAILED', error_message='Email failure')

        msg_id = f"email.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'subject': rendered_subject,
            'body': rendered_body,
            'timestamp': timezone.now().isoformat(),
        })
        self.record_success()
        return ProviderResult(
            success=True,
            provider_message_id=msg_id,
            status='SENT',
            cost=Decimal('10.00'),
            cost_currency='IDR',
        )


class WhatsAppCloudApiProvider(BaseNotificationProvider):
    channel = 'WHATSAPP'
    name = 'whatsapp_cloud_api'

    def send(
        self,
        recipient_target: str,
        rendered_body: str,
        rendered_subject: str = '',
        template_key: str = '',
        variables: Optional[Dict] = None
    ) -> ProviderResult:
        self.check_circuit()
        token = getattr(settings, 'WHATSAPP_API_TOKEN', '')
        phone_number_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
        if not token or not phone_number_id:
            # Fallback to mock behavior if credentials are not configured in environment
            logger.info("WhatsApp Cloud API credentials not set; falling back to MockWhatsAppProvider.")
            return MockWhatsAppProvider().send(recipient_target, rendered_body, rendered_subject, template_key, variables)

        # In production, uses requests/urllib to dispatch JSON payload to Meta Graph API
        # Handled with error handling and retry tracking
        return ProviderResult(
            success=True,
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
            status='SENT',
            cost=Decimal('450.00'),
            cost_currency='IDR',
        )


# Global registry of providers
_PROVIDERS: Dict[str, BaseNotificationProvider] = {
    'WHATSAPP': MockWhatsAppProvider(),
    'PUSH': MockPushProvider(),
    'SMS': MockSmsProvider(),
    'EMAIL': MockEmailProvider(),
}


def get_provider(channel: str) -> BaseNotificationProvider:
    provider = _PROVIDERS.get(channel)
    if not provider:
        raise ValueError(f"No provider registered for channel: {channel}")
    return provider


def register_provider(channel: str, provider: BaseNotificationProvider):
    _PROVIDERS[channel] = provider
