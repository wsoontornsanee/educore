"""Shared DRF router that rebrands the browsable API root as "Educore API"
instead of DRF's default "Api Root" (spec/18 partner-facing docs must not
read as generic framework output)."""
from rest_framework.routers import APIRootView, DefaultRouter


class EduCoreAPIRootView(APIRootView):
    name = 'Educore API'


class EduCoreRouter(DefaultRouter):
    APIRootView = EduCoreAPIRootView
