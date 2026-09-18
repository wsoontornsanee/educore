"""Form-based validation for apps.status's internal management POST endpoints —
replaces raw request.POST[...] / int(...) access that could 500 on malformed
staff input (see StatusIncidentCreateView in web_views.py)."""
from django import forms

from .models import ServiceComponent, StatusIncident


class IncidentCreateForm(forms.Form):
    severity = forms.ChoiceField(choices=StatusIncident.SEVERITY_CHOICES)
    title_id = forms.CharField()
    title_en = forms.CharField()
    body_id = forms.CharField()
    body_en = forms.CharField()
    duration_minutes = forms.IntegerField(min_value=0)
    affected_components = forms.ModelMultipleChoiceField(
        queryset=ServiceComponent.objects.all(), required=False,
    )
    published = forms.BooleanField(required=False)
