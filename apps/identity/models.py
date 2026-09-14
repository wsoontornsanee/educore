"""Identity and Tenancy models (spec/02, spec/03).

Defines Foundation (the root multi-tenant organization) and School (operating unit).
"""
from django.db import models
from apps.core.models import TenantModel

class Foundation(models.Model):
    """The governing foundation (Yayasan) managing one or more schools (spec/02 §2, spec/03)."""
    PLAN_STARTER = 'STARTER'
    PLAN_STANDARD = 'STANDARD'
    PLAN_ENTERPRISE = 'ENTERPRISE'
    PLAN_CHOICES = [
        (PLAN_STARTER, 'Starter'),
        (PLAN_STANDARD, 'Standard'),
        (PLAN_ENTERPRISE, 'Enterprise'),
    ]

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_INACTIVE = 'INACTIVE'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_INACTIVE, 'Inactive'),
    ]

    id = models.BigAutoField(primary_key=True)
    legal_name = models.CharField(max_length=255, help_text="Legal registered name, e.g. Yayasan Pendidikan Islam Al-Hikmah")
    brand_name = models.CharField(max_length=128, help_text="Public-facing brand name, e.g. Al-Hikmah Nusantara")
    npwp = models.CharField(max_length=32, blank=True, default='', help_text="Indonesian tax identification number (Nomor Pokok Wajib Pajak)")
    address = models.TextField(blank=True, default='', help_text="Official registered address")
    timezone = models.CharField(max_length=32, default='Asia/Jakarta', help_text="Default timezone: Asia/Jakarta, Asia/Makassar, or Asia/Jayapura")
    reporting_currency = models.CharField(max_length=3, default='IDR', help_text="Currency for consolidated reporting (CUR-007)")
    plan_tier = models.CharField(max_length=32, choices=PLAN_CHOICES, default=PLAN_STANDARD)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'foundations'
        verbose_name = 'Yayasan'
        verbose_name_plural = 'Daftar Yayasan'

    def __str__(self):
        return f"{self.brand_name} ({self.legal_name})"

class School(TenantModel):
    """An educational operating unit (school/madrasah) belonging to a Foundation (spec/02 §2)."""
    LEVEL_SD = 'SD'       # Sekolah Dasar
    LEVEL_SMP = 'SMP'     # Sekolah Menengah Pertama
    LEVEL_SMA = 'SMA'     # Sekolah Menengah Atas
    LEVEL_SMK = 'SMK'     # Sekolah Menengah Kejuruan
    LEVEL_MI = 'MI'       # Madrasah Ibtidaiyah
    LEVEL_MTS = 'MTs'     # Madrasah Tsanawiyah
    LEVEL_MA = 'MA'       # Madrasah Aliyah

    LEVEL_CHOICES = [
        (LEVEL_SD, 'SD (Sekolah Dasar)'),
        (LEVEL_SMP, 'SMP (Sekolah Menengah Pertama)'),
        (LEVEL_SMA, 'SMA (Sekolah Menengah Atas)'),
        (LEVEL_SMK, 'SMK (Sekolah Menengah Kejuruan)'),
        (LEVEL_MI, 'MI (Madrasah Ibtidaiyah)'),
        (LEVEL_MTS, 'MTs (Madrasah Tsanawiyah)'),
        (LEVEL_MA, 'MA (Madrasah Aliyah)'),
    ]

    name = models.CharField(max_length=128, help_text="e.g. SMP Al-Hikmah Nusantara")
    npsn = models.CharField(max_length=16, unique=True, db_index=True, help_text="Nomor Pokok Sekolah Nasional (8 digits)")
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES)
    curriculum = models.CharField(max_length=64, default='KURIKULUM_MERDEKA', help_text="e.g. KURIKULUM_MERDEKA, K13, CAMBRIDGE, IB")
    timezone = models.CharField(max_length=32, default='Asia/Jakarta')
    base_currency = models.CharField(max_length=3, default='IDR', help_text="School base currency (CUR-007)")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'schools'
        verbose_name = 'Sekolah'
        verbose_name_plural = 'Daftar Sekolah'
        indexes = [
            models.Index(fields=['foundation_id', 'level']),
            models.Index(fields=['foundation_id', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} (NPSN: {self.npsn})"
