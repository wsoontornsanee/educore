from rest_framework.routers import DefaultRouter

from apps.attendance.views import CredentialViewSet

router = DefaultRouter()
router.register('credentials', CredentialViewSet, basename='credential')

urlpatterns = router.urls
