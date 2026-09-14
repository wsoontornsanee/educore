"""Seed demo foundation and schools fixture (spec/01 §7, spec/02, spec/17 §7.1)."""
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.identity.models import Foundation, School

class Command(BaseCommand):
    help = "Seeds initial demo Yayasan and campus schools idempotently."

    def handle(self, *args, **options):
        with transaction.atomic():
            foundation, created = Foundation.objects.update_or_create(
                brand_name="Yayasan Al-Hikmah Nusantara",
                defaults={
                    "legal_name": "Yayasan Pendidikan Islam Al-Hikmah Nusantara",
                    "npwp": "01.234.567.8-012.000",
                    "address": "Jl. Pendidikan No. 45, Jakarta Selatan, DKI Jakarta",
                    "timezone": "Asia/Jakarta",
                    "reporting_currency": "IDR",
                    "plan_tier": Foundation.PLAN_ENTERPRISE,
                    "status": Foundation.STATUS_ACTIVE,
                }
            )

            status_text = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{status_text} Foundation: {foundation.brand_name} (ID: {foundation.id})"))

            schools_data = [
                {
                    "name": "SD Al-Hikmah Nusantara",
                    "npsn": "20100001",
                    "level": School.LEVEL_SD,
                    "curriculum": "KURIKULUM_MERDEKA",
                    "timezone": "Asia/Jakarta",
                    "base_currency": "IDR",
                },
                {
                    "name": "SMP Al-Hikmah Nusantara",
                    "npsn": "20100002",
                    "level": School.LEVEL_SMP,
                    "curriculum": "KURIKULUM_MERDEKA",
                    "timezone": "Asia/Jakarta",
                    "base_currency": "IDR",
                },
                {
                    "name": "SMA Al-Hikmah Nusantara",
                    "npsn": "20100003",
                    "level": School.LEVEL_SMA,
                    "curriculum": "KURIKULUM_MERDEKA",
                    "timezone": "Asia/Jakarta",
                    "base_currency": "IDR",
                },
            ]

            for s_data in schools_data:
                # Use all_tenants manager to seed unscoped
                school, s_created = School.all_tenants.update_or_create(
                    npsn=s_data["npsn"],
                    defaults={
                        "foundation_id": foundation.id,
                        "name": s_data["name"],
                        "level": s_data["level"],
                        "curriculum": s_data["curriculum"],
                        "timezone": s_data["timezone"],
                        "base_currency": s_data["base_currency"],
                        "is_active": True,
                    }
                )
                s_status = "Created" if s_created else "Updated"
                self.stdout.write(self.style.SUCCESS(f"  - {s_status} School: {school.name} (NPSN: {school.npsn}, Level: {school.level})"))

        self.stdout.write(self.style.SUCCESS("Demo seeding completed successfully."))
