"""Seed demo foundation and schools fixture (spec/01 §7, spec/02, spec/17 §7.1)."""
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.identity.models import Foundation, School, Person, User

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

            # Seed demo foundation admin user idempotently
            admin_phone = "+6281234567890"
            admin_email = "admin@alhikmah.sch.id"
            if not User.all_tenants.filter(phone_e164=admin_phone).exists():
                admin_user = User.objects.create_superuser(
                    phone_e164=admin_phone,
                    email=admin_email,
                    password="DemoAdminPassword123!",
                    foundation_id=foundation.id,
                    full_name="KH. Ahmad Dahlan (Ketua Yayasan)",
                )
                Person.all_tenants.create(
                    foundation_id=foundation.id,
                    full_name="KH. Ahmad Dahlan",
                    nik="3171012345670001",
                    gender=Person.GENDER_MALE,
                    address="Jl. Pendidikan No. 45, Jakarta Selatan",
                )
                self.stdout.write(self.style.SUCCESS(f"  - Created Demo Admin: {admin_user.full_name} ({admin_user.phone_e164})"))
            else:
                admin_user = User.all_tenants.get(phone_e164=admin_phone)
                self.stdout.write(self.style.SUCCESS(f"  - Demo Admin already exists ({admin_phone})"))

            # Idempotently assign FOUNDATION_ADMIN role
            from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION
            assign_role(
                user=admin_user,
                role=ROLE_FOUNDATION_ADMIN,
                scope_type=SCOPE_FOUNDATION,
                scope_id=foundation.id,
                foundation_id=foundation.id,
                created_by="seed_demo_foundation"
            )
            self.stdout.write(self.style.SUCCESS(f"  - Assigned Role: {ROLE_FOUNDATION_ADMIN} (Scope: FOUNDATION {foundation.id})"))

            # Seed default feature entitlements for all 8 modules (IAM-023, IAM-025)
            from apps.identity.entitlements import ALL_MODULES, set_module_entitlement
            for module in ALL_MODULES:
                set_module_entitlement(
                    foundation_id=foundation.id,
                    module_key=module,
                    enabled=True,
                )
            self.stdout.write(self.style.SUCCESS(f"  - Seeded Entitlements: All {len(ALL_MODULES)} modules enabled"))

        self.stdout.write(self.style.SUCCESS("Demo seeding completed successfully."))
