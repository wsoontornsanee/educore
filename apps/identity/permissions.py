"""Django REST Framework permission classes for EduCore RBAC (spec/02 §4).

Enforces:
- IAM-010: Fail closed on undeclared permissions
- IAM-012: School-scoped permission isolation
"""
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from educore.middleware.tenancy import get_current_foundation_id
from .rbac import has_permission, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
from .models import RoleAssignment

class HasRequiredPermission(permissions.BasePermission):
    """DRF permission class that validates view.required_permission.
    
    Fail-closed guardrail (IAM-010):
    If the view does not define required_permission, access is denied by default.
    """

    def has_permission(self, request, view) -> bool:
        # Require authenticated user
        if not request.user or not request.user.is_authenticated:
            return False

        # Fail-closed guardrail (IAM-010): view MUST declare required_permission
        required_permission = None
        if hasattr(view, 'get_required_permission') and callable(view.get_required_permission):
            required_permission = view.get_required_permission()
        elif hasattr(view, 'action_permissions') and hasattr(view, 'action'):
            required_permission = view.action_permissions.get(view.action)

        if not required_permission:
            required_permission = getattr(view, 'required_permission', None)

        if not required_permission:
            raise PermissionDenied("Akses ditolak: Handler API tidak mendefinisikan required_permission (IAM-010).")

        # Resolve foundation context
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return False

        # Resolve optional school context from view kwargs or query params
        school_id = None
        if hasattr(view, 'kwargs') and view.kwargs:
            if 'school_id' in view.kwargs:
                try:
                    school_id = int(view.kwargs['school_id'])
                except (ValueError, TypeError):
                    pass
            elif 'pk' in view.kwargs:
                basename = getattr(view, 'basename', None)
                model = getattr(getattr(view, 'queryset', None), 'model', None)
                if basename == 'school' or (model and model.__name__ == 'School'):
                    try:
                        school_id = int(view.kwargs['pk'])
                    except (ValueError, TypeError):
                        pass

        if school_id is None and hasattr(request, 'query_params') and 'school_id' in request.query_params:
            try:
                school_id = int(request.query_params['school_id'])
            except (ValueError, TypeError):
                pass

        return has_permission(request.user, required_permission, foundation_id, school_id)

class IsFoundationAdmin(permissions.BasePermission):
    """Allows access only to superusers or users with foundation_admin role at foundation scope."""

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return False

        return RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id,
            user=request.user,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=foundation_id,
            deleted_at__isnull=True,
        ).exists()

class ModuleNotEntitled(PermissionDenied):
    """Exception raised when accessing a disabled module (IAM-024).
    
    Renders HTTP 403 with stable code MODULE_NOT_ENTITLED.
    """
    default_detail = "Modul tidak aktif untuk institusi ini."
    default_code = "MODULE_NOT_ENTITLED"

class RequiresModuleEntitlement(permissions.BasePermission):
    """DRF permission class gating endpoints by foundation feature entitlement (IAM-023, IAM-024)."""

    def has_permission(self, request, view) -> bool:
        required_module = getattr(view, 'required_module', None)
        if not required_module:
            return True

        from .entitlements import is_module_entitled

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return False

        school_id = None
        if hasattr(view, 'kwargs') and view.kwargs:
            if 'school_id' in view.kwargs:
                try:
                    school_id = int(view.kwargs['school_id'])
                except (ValueError, TypeError):
                    pass
            elif 'pk' in view.kwargs:
                basename = getattr(view, 'basename', None)
                model = getattr(getattr(view, 'queryset', None), 'model', None)
                if basename == 'school' or (model and model.__name__ == 'School'):
                    try:
                        school_id = int(view.kwargs['pk'])
                    except (ValueError, TypeError):
                        pass

        if school_id is None and hasattr(request, 'query_params') and 'school_id' in request.query_params:
            try:
                school_id = int(request.query_params['school_id'])
            except (ValueError, TypeError):
                pass

        if not is_module_entitled(foundation_id, required_module, school_id):
            raise ModuleNotEntitled(f"Modul '{required_module}' tidak aktif untuk institusi ini.")

        return True

