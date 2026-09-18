from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.analytics_serializers import AnalyticsEventBatchSerializer, AnalyticsEventItemSerializer
from apps.core.idempotency import IdempotentViewMixin
from apps.core.models import AnalyticsEvent, StoredFile
from apps.core.serializers import InitiateUploadSerializer, StoredFileSerializer
from apps.core.services import PURPOSE_RULES, InvalidUploadError, confirm_upload, initiate_upload
# apps.identity is a platform/auth app (like apps.core itself), not a "business
# app" in the sense of the apps/core constraint that forbids depending on
# domain apps like apps.academic/apps.wallet — this import is intentional.
from apps.identity.models import RoleAssignment
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.audit import get_current_actor
from educore.middleware.tenancy import get_current_foundation_id


class InitiateUploadView(APIView):
    """POST /api/v1/files/uploads/ — phase 1 of the two-phase commit."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        purpose = self.request.data.get('purpose') if hasattr(self.request.data, 'get') else None
        rules = PURPOSE_RULES.get(purpose)
        return rules['required_permission'] if rules else None

    def post(self, request):
        payload = InitiateUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        actor = get_current_actor()
        uploaded_by = str(getattr(actor, 'pk', actor)) if actor else ''

        try:
            stored_file, upload_url = initiate_upload(
                foundation_id=get_current_foundation_id() or getattr(request.user, 'foundation_id', None),
                uploaded_by=uploaded_by,
                **payload.validated_data,
            )
        except InvalidUploadError as e:
            return Response({'error': str(e)}, status=400)

        data = StoredFileSerializer(stored_file).data
        data['upload_url'] = upload_url
        return Response(data, status=201)


class ConfirmUploadView(APIView):
    """POST /api/v1/files/uploads/<id>/confirm/ — phase 2 of the two-phase commit."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        try:
            stored_file = StoredFile.objects.get(id=self.kwargs['pk'])
        except StoredFile.DoesNotExist:
            return None
        rules = PURPOSE_RULES.get(stored_file.purpose)
        return rules['required_permission'] if rules else None

    def post(self, request, pk):
        foundation_id = get_current_foundation_id()
        try:
            stored_file = StoredFile.objects.get(id=pk, foundation_id=foundation_id, deleted_at__isnull=True)
        except StoredFile.DoesNotExist:
            raise PermissionDenied("Akses ditolak.")

        try:
            confirmed = confirm_upload(stored_file.id)
        except InvalidUploadError as e:
            return Response({'error': str(e)}, status=400)
        return Response(StoredFileSerializer(confirmed).data, status=200)


class AnalyticsEventIngestView(IdempotentViewMixin, APIView):
    """POST /api/v1/analytics/events/ — batch ingest of parent/staff app product
    analytics events (spec/08 §5, spec/15 RPT-015). foundation_id and role are
    always resolved server-side; never trusted from the request body.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'analytics.event.write'

    def post(self, request):
        batch = AnalyticsEventBatchSerializer(data=request.data)
        batch.is_valid(raise_exception=True)

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"error": "Konteks yayasan tidak ditemukan."}, status=400)

        role_assignment = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user=request.user, deleted_at__isnull=True,
        ).order_by('id').values_list('role', flat=True).first()
        role = role_assignment or ''

        events_to_create = []
        for raw_event in batch.validated_data['events']:
            item = AnalyticsEventItemSerializer(data=raw_event)
            if not item.is_valid():
                continue
            events_to_create.append(AnalyticsEvent(
                foundation_id=foundation_id,
                event_name=item.validated_data['event_name'],
                school_id=item.validated_data.get('school_id'),
                role=role,
                occurred_at=item.validated_data['occurred_at'],
            ))

        if events_to_create:
            AnalyticsEvent.objects.bulk_create(events_to_create)

        return Response({'accepted': len(events_to_create)}, status=201)


class ComingSoonView(LoginRequiredMixin, TemplateView):
    """GET /web/coming-soon/ — shared placeholder for nav items with no real page yet.

    LoginRequiredMixin redirects anonymous users to settings.LOGIN_URL
    (configured to /web/login/ for the web console).
    No RBAC permission check here: every nav item pointing at this view
    already gated the LINK by the item's own permission (apps.identity.nav);
    this view's only job is to not be reachable while logged out.
    """
    template_name = 'pages/coming_soon.html'
