from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import StoredFile
from apps.core.serializers import InitiateUploadSerializer, StoredFileSerializer
from apps.core.services import PURPOSE_RULES, InvalidUploadError, confirm_upload, initiate_upload
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.audit import get_current_actor
from educore.middleware.tenancy import get_current_foundation_id


class InitiateUploadView(APIView):
    """POST /api/v1/files/uploads/ — phase 1 of the two-phase commit."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        purpose = self.request.data.get('purpose')
        rules = PURPOSE_RULES.get(purpose)
        return rules['required_permission'] if rules else None

    def post(self, request):
        payload = InitiateUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        actor = get_current_actor()
        uploaded_by = str(getattr(actor, 'pk', actor)) if actor else ''

        try:
            stored_file, upload_url = initiate_upload(
                foundation_id=get_current_foundation_id(), uploaded_by=uploaded_by,
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

        confirmed = confirm_upload(stored_file.id)
        return Response(StoredFileSerializer(confirmed).data, status=200)
