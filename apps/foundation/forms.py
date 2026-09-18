"""Forms for the Administrasi console's settings page (apps.foundation.web_views)."""
from django import forms
from django.utils.translation import gettext_lazy as _

from apps.identity.models import Foundation, School

TIMEZONE_CHOICES = ['Asia/Jakarta', 'Asia/Makassar', 'Asia/Jayapura']
TIMEZONE_LABELS = {
    'Asia/Jakarta': 'WIB — Asia/Jakarta',
    'Asia/Makassar': 'WITA — Asia/Makassar',
    'Asia/Jayapura': 'WIT — Asia/Jayapura',
}


class _SettingsForm(forms.ModelForm):
    """Shared timezone/currency handling. The timezone choices are the three
    Indonesian zones, plus the row's current value if it already holds
    something else, so saving an unrelated field never fails on legacy data."""
    currency_field = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.timezone if self.instance and self.instance.pk else None
        values = TIMEZONE_CHOICES + ([current] if current and current not in TIMEZONE_CHOICES else [])
        self.fields['timezone'] = forms.ChoiceField(
            label=_("Zona waktu"),
            choices=[(value, TIMEZONE_LABELS.get(value, value)) for value in values],
        )
        self.fields[self.currency_field] = forms.RegexField(
            regex=r'^[A-Z]{3}$', max_length=3, label=self.fields[self.currency_field].label,
            error_messages={'invalid': _("Gunakan kode mata uang ISO-4217 tiga huruf besar, mis. IDR.")},
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')

    def changed_diff(self):
        """{field: {before, after}} for the fields that actually changed,
        JSON-safe (Decimal and date rendered as strings)."""
        diff = {}
        for name in self.changed_data:
            diff[name] = {
                'before': _jsonable(self.initial.get(name)),
                'after': _jsonable(self.cleaned_data.get(name)),
            }
        return diff


def _jsonable(value):
    return value if value is None or isinstance(value, (str, int, bool)) else str(value)


class FoundationProfileForm(_SettingsForm):
    currency_field = 'reporting_currency'

    class Meta:
        model = Foundation
        fields = ['legal_name', 'brand_name', 'npwp', 'address', 'timezone', 'reporting_currency', 'approval_threshold']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}
        labels = {
            'legal_name': _("Nama hukum yayasan"),
            'brand_name': _("Nama merek"),
            'npwp': _("NPWP"),
            'address': _("Alamat terdaftar"),
            'reporting_currency': _("Mata uang pelaporan"),
            'approval_threshold': _("Ambang batas persetujuan (Rp)"),
        }

    def clean_approval_threshold(self):
        value = self.cleaned_data['approval_threshold']
        if value < 0:
            raise forms.ValidationError(_("Ambang batas persetujuan tidak boleh negatif."))
        return value


class SchoolSettingsForm(_SettingsForm):
    """School profile. `npsn` is deliberately absent: it is the statutory
    school identifier, globally unique, and correcting it is a data-fix, not
    a settings edit."""
    currency_field = 'base_currency'

    class Meta:
        model = School
        fields = [
            'name', 'level', 'curriculum', 'timezone', 'base_currency',
            'ownership_status', 'accreditation', 'nss', 'nsm', 'establishment_date',
            'street_address', 'kelurahan', 'kecamatan', 'kabupaten_kota', 'provinsi', 'postal_code',
        ]
        widgets = {
            'establishment_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'street_address': forms.Textarea(attrs={'rows': 2}),
        }
        labels = {
            'name': _("Nama sekolah"),
            'level': _("Jenjang"),
            'curriculum': _("Kurikulum"),
            'base_currency': _("Mata uang dasar"),
            'ownership_status': _("Status kepemilikan"),
            'accreditation': _("Akreditasi"),
            'nss': _("NSS"),
            'nsm': _("NSM"),
            'establishment_date': _("Tanggal pendirian"),
            'street_address': _("Alamat jalan"),
            'kelurahan': _("Kelurahan / Desa"),
            'kecamatan': _("Kecamatan"),
            'kabupaten_kota': _("Kabupaten / Kota"),
            'provinsi': _("Provinsi"),
            'postal_code': _("Kode pos"),
        }
