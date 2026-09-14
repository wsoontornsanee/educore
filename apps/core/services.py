"""Core service helpers for auditing, asynchronous task enqueueing, and domain events."""
import logging
from django.utils import timezone
from educore.middleware.tenancy import get_current_foundation_id
from educore.middleware.audit import get_current_actor, get_current_ip
from .models import AuditEvent, DomainEvent, TaskQueue

logger = logging.getLogger(__name__)

# Registry for task handlers invoked by drain_tasks
_TASK_HANDLERS = {}

def register_task_handler(task_type):
    """Decorator to register a callable as a handler for task_type."""
    def decorator(fn):
        _TASK_HANDLERS[task_type] = fn
        return fn
    return decorator

def get_task_handler(task_type):
    """Retrieve registered handler for task_type."""
    return _TASK_HANDLERS.get(task_type)

def audit(action, entity_type, entity_id, actor_id=None, role='', foundation_id=None, school_id=None, ip_address=None, diff=None):
    """Write an explicit, immutable audit event (ARC-008, spec/01 §8.5).
    
    All mutating actions in business apps MUST invoke this helper.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if actor_id is None:
        current_actor = get_current_actor()
        if current_actor:
            actor_id = str(getattr(current_actor, 'pk', current_actor))
            if hasattr(current_actor, 'role') and not role:
                role = str(current_actor.role)

    if ip_address is None:
        ip_address = get_current_ip()

    return AuditEvent.objects.create(
        actor_id=actor_id,
        role=role,
        foundation_id=foundation_id,
        school_id=school_id,
        ip_address=ip_address,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        diff=diff or {},
    )

def enqueue_task(task_type, payload=None, foundation_id=None, run_after=None, max_attempts=4):
    """Enqueue an asynchronous task in MySQL TaskQueue (ARC-010).
    
    Safe to call inside transactional atomic blocks.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if run_after is None:
        run_after = timezone.now()

    return TaskQueue.objects.create(
        foundation_id=foundation_id,
        task_type=task_type,
        payload=payload or {},
        run_after=run_after,
        max_attempts=max_attempts,
        status=TaskQueue.STATUS_PENDING,
    )

def record_domain_event(name, payload=None, foundation_id=None):
    """Record a transactional domain event (ARC-010, spec/01 §8.4).
    
    MUST be written in the same transaction as state changes.
    """
    if foundation_id is None:
        foundation_id = get_current_foundation_id()

    if foundation_id is None:
        raise ValueError("Cannot record DomainEvent without an explicit or thread-local foundation_id.")

    return DomainEvent.objects.create(
        foundation_id=foundation_id,
        name=name,
        payload=payload or {},
    )
