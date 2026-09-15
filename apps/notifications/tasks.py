import logging
from apps.core.services import register_task_handler
from apps.notifications.services import handle_gate_scanned_event, process_intent

logger = logging.getLogger(__name__)


@register_task_handler('notifications.process_intent')
def handle_process_notification_intent(payload: dict):
    intent_id = payload.get('intent_id')
    if intent_id:
        process_intent(intent_id)


@register_task_handler('attendance.gate.scanned')
def handle_gate_scanned_task(payload: dict):
    handle_gate_scanned_event(payload)
