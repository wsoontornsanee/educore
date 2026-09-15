from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.finance.views import (
    DiscountViewSet,
    FeePlanViewSet,
    FeeTypeViewSet,
    SiblingDiscountPolicyViewSet,
    StudentFeeAssignmentViewSet,
)

router = DefaultRouter()
router.register(r'fee-types', FeeTypeViewSet, basename='fee-types')
router.register(r'fee-plans', FeePlanViewSet, basename='fee-plans')
router.register(r'assignments', StudentFeeAssignmentViewSet, basename='fee-assignments')
router.register(r'discounts', DiscountViewSet, basename='discounts')
router.register(r'sibling-policies', SiblingDiscountPolicyViewSet, basename='sibling-policies')

urlpatterns = [
    path('', include(router.urls)),
]
