"""TaskQueue handler registration for partner webhook delivery.

`partners.webhook.deliver` is enqueued by emit_partner_event and drained by
the every-minute `drain_tasks` cron command (deploy/crontab). Importing this
module from AppConfig.ready() registers the handler.
"""
import logging

from apps.core.services import register_task_handler
from apps.partners import services

logger = logging.getLogger(__name__)


@register_task_handler('partners.webhook.deliver')
def handle_webhook_deliver(payload: dict) -> dict:
    event_id = payload.get('event_id')
    if event_id is None:
        logger.warning("partners.webhook.deliver payload missing event_id: %r", payload)
        return {'status': 'invalid_payload'}
    return services.deliver_partner_webhook(event_id)
