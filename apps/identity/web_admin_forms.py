"""Forms for the Administrasi console's staff write actions (create, offboard).

Validation mirrors what the JSON StaffViewSet enforces (the same
apps.identity.services entry points do the writing); the forms only add what a
browser needs on top: friendly field errors, duplicate-login checks that would
otherwise surface as a database IntegrityError, and school choices already
narrowed to the schools the acting user may write to.
"""
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Staff, User
from .services import normalize_phone_e164


class _ConsoleForm(forms.Form):
    """Gives every widget the console's `form-input` class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')


class StaffCreateForm(_ConsoleForm):
    full_name = forms.CharField(label=_("Nama lengkap"), max_length=128)
    phone_e164 = forms.CharField(label=_("Nomor HP"), max_length=20, help_text=_("Format 08… atau +62…"))
    email = forms.EmailField(label=_("Email"), required=False)
    nik = forms.CharField(label=_("NIK"), max_length=16, required=False)
    gender = forms.ChoiceField(
        label=_("Jenis kelamin"), required=False, choices=[('', '—'), ('L', _("Laki-laki")), ('P', _("Perempuan"))],
    )
    dob = forms.DateField(label=_("Tanggal lahir"), required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    school = forms.ChoiceField(label=_("Sekolah"), required=False)
    nip = forms.CharField(label=_("NIP"), max_length=32, required=False)
    employment_type = forms.ChoiceField(label=_("Status kepegawaian"), choices=Staff.EMPLOYMENT_CHOICES)
    join_date = forms.DateField(label=_("Tanggal bergabung"), widget=forms.DateInput(attrs={'type': 'date'}))
    password = forms.CharField(
        label=_("Kata sandi awal"), required=False, widget=forms.PasswordInput(render_value=False),
        help_text=_("Opsional. Tanpa kata sandi, staf belum dapat masuk sampai kata sandi diatur."),
    )

    def __init__(self, *args, schools, allow_no_school, **kwargs):
        super().__init__(*args, **kwargs)
        self.schools = {str(school.id): school for school in schools}
        choices = [(school_id, school.name) for school_id, school in self.schools.items()]
        if allow_no_school:
            choices.insert(0, ('', _("Yayasan (tanpa sekolah)")))
        else:
            self.fields['school'].required = True
        self.fields['school'].choices = choices
        self.fields['join_date'].initial = timezone.localdate()

    def clean_school(self):
        value = self.cleaned_data['school']
        return self.schools[value] if value else None

    def clean_phone_e164(self):
        phone = normalize_phone_e164(self.cleaned_data['phone_e164'])  # raises a ValidationError with a message
        if User.all_tenants.filter(phone_e164=phone).exists():
            raise forms.ValidationError(_("Nomor HP ini sudah terdaftar."))
        return phone

    def clean_email(self):
        email = self.cleaned_data['email']
        if email and User.all_tenants.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Email ini sudah terdaftar."))
        return email or None

    def clean_nik(self):
        nik = self.cleaned_data['nik'].strip()
        if nik and not (nik.isdigit() and len(nik) == 16):
            raise forms.ValidationError(_("NIK harus 16 digit angka."))
        return nik

    def clean_password(self):
        password = self.cleaned_data['password']
        if password:
            validate_password(password)
        return password

    def staff_data(self):
        """cleaned_data as create_staff_member expects it (StaffCreateSerializer field names)."""
        data = {key: value for key, value in self.cleaned_data.items() if key != 'school'}
        data['gender'] = data.get('gender') or ''
        return data


class StaffOffboardForm(_ConsoleForm):
    resignation_date = forms.DateField(label=_("Tanggal berhenti"), widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(label=_("Alasan"), required=False, max_length=255)
    reassign_to = forms.ChoiceField(label=_("Alihkan tugas kelas ke"), required=False)

    def __init__(self, *args, reassign_candidates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reassign_to'].choices = [('', _("— Tidak dialihkan —"))] + [
            (str(candidate.id), candidate.person.full_name) for candidate in reassign_candidates
        ]
        self.fields['resignation_date'].initial = timezone.localdate()

    def clean_reassign_to(self):
        value = self.cleaned_data['reassign_to']
        return int(value) if value else None
