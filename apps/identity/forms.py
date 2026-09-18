"""Forms for the Administrasi console's staff pages (apps.identity.web_admin_views)."""
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Staff, User
from .services import normalize_phone_e164


class StaffCreateForm(forms.Form):
    """Minimal staff onboarding: enough to create the person, login identity
    and Staff row. Statutory/DAPODIK fields stay editable on the JSON API;
    no password is collected — the new staff member signs in by phone OTP or
    SSO. `school_choices` is the caller's authority ceiling: only schools the
    actor may write to are offered, and the blank (foundation-wide) choice
    only when `allow_foundation_wide`."""
    full_name = forms.CharField(label=_("Nama lengkap"), max_length=128)
    phone_e164 = forms.CharField(label=_("Nomor HP"), max_length=20, help_text=_("Format 08… atau +62…"))
    email = forms.EmailField(label=_("Email"), required=False)
    nip = forms.CharField(label=_("NIP"), max_length=32, required=False)
    employment_type = forms.ChoiceField(label=_("Status kepegawaian"), choices=Staff.EMPLOYMENT_CHOICES)
    join_date = forms.DateField(label=_("Tanggal bergabung"), widget=forms.DateInput(attrs={'type': 'date'}))
    school = forms.ChoiceField(label=_("Sekolah"))

    def __init__(self, *args, school_choices, allow_foundation_wide, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(str(school.id), school.name) for school in school_choices]
        if allow_foundation_wide:
            choices.insert(0, ('', _("Yayasan (semua sekolah)")))
        self.fields['school'].choices = choices
        self.fields['school'].required = not allow_foundation_wide
        self.fields['join_date'].initial = timezone.localdate()
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')

    def clean_phone_e164(self):
        try:
            phone = normalize_phone_e164(self.cleaned_data['phone_e164'])
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages[0])
        if User.all_tenants.filter(phone_e164=phone).exists():
            raise forms.ValidationError(_("Nomor HP ini sudah terdaftar."))
        return phone

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.all_tenants.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Email ini sudah terdaftar."))
        return email or None

    def staff_data(self):
        """The cleaned values as the dict apps.identity.services.create_staff takes."""
        data = dict(self.cleaned_data)
        data.pop('school')
        return data

    def school_id(self):
        return int(self.cleaned_data['school']) if self.cleaned_data['school'] else None
