"""Managers for multi-tenancy and soft-deletion lifecycle."""
from django.db import models
from django.utils import timezone
from educore.middleware.tenancy import get_current_foundation_id

class TenantQuerySet(models.QuerySet):
    """QuerySet supporting tenant scoping and soft-deletion."""

    def delete(self):
        """Soft delete all records in this queryset."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        """Permanently delete records in this queryset from the database."""
        return super().delete()

    def restore(self):
        """Restore soft-deleted records."""
        return self.update(deleted_at=None)

    def alive(self):
        """Return non-deleted records."""
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        """Return soft-deleted records only."""
        return self.filter(deleted_at__isnull=False)

class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Tenant-scoped manager enforcing 3-layer isolation (ARC-002).
    
    Filters by the active foundation in thread-local and excludes soft-deleted records.
    """

    def get_queryset(self):
        qs = super().get_queryset().filter(deleted_at__isnull=True)
        current_fid = get_current_foundation_id()
        if current_fid is not None:
            return qs.filter(foundation_id=current_fid)
        # If thread-local has no foundation context, return empty queryset to fail-closed
        return qs.none()

class AllTenantsManager(models.Manager.from_queryset(TenantQuerySet)):
    """Unscoped manager for platform admin, migrations, and cross-tenant ops (ARC-002).
    
    Still filters out soft-deleted records by default; use with_deleted() for all.
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def with_deleted(self):
        """Return all records including soft-deleted ones."""
        return super().get_queryset()
