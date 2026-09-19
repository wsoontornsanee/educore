"""Give an existing user the platform_operator role, or take it away (no PII in the output beyond a name)."""
from django.core.management.base import BaseCommand, CommandError

from apps.core.services import audit
from apps.identity.models import PlatformRoleAssignment, User
from educore.middleware.tenancy import tenant_context


class Command(BaseCommand):
    help = (
        "Grant (or with --revoke, remove) the platform_operator role. The operator can open /web/status/manage/ "
        "and receives stale-job alert emails (ARC-008), so the user needs an email address."
    )

    def add_arguments(self, parser):
        who = parser.add_mutually_exclusive_group(required=True)
        who.add_argument('--phone', help="User's phone in E.164 format, e.g. +6281234567890.")
        who.add_argument('--email', help="User's email address.")
        parser.add_argument('--revoke', action='store_true', help="Remove the role instead of granting it.")

    def handle(self, *args, **options):
        lookup = {'phone_e164': options['phone']} if options['phone'] else {'email__iexact': options['email']}
        user = User.all_tenants.filter(**lookup).first()
        if user is None:
            raise CommandError("No such user. The account must already exist; this command does not create one.")

        role = PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR
        if options['revoke']:
            deleted, _ = PlatformRoleAssignment.objects.filter(user=user, role=role).delete()
            action = 'platform_role.revoked'
            message = f"Revoked {role} from {user.full_name}." if deleted else f"{user.full_name} was not a {role}."
        else:
            _, created = PlatformRoleAssignment.objects.get_or_create(user=user, role=role)
            action = 'platform_role.granted'
            message = f"Granted {role} to {user.full_name}." if created else f"{user.full_name} is already a {role}."
        # Platform-wide, not tenant data: clear the ambient foundation so the audit row does not inherit one.
        with tenant_context(None):
            audit(action=action, entity_type='PlatformRoleAssignment', entity_id=str(user.id), role=role,
                  diff={'role': role})
        self.stdout.write(self.style.SUCCESS(message))
        if not options['revoke'] and not (user.is_active and user.email):
            self.stdout.write(self.style.WARNING(
                "This user has no email address or is inactive, so stale-job alerts will not reach them."
            ))
