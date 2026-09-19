"""ARC-003: every TenantModel has an indexed foundation_id and a foundation-led composite index."""
from django.apps import apps
from django.test import SimpleTestCase

from apps.core.models import TenantModel

# One-row-per-owner tables looked up through their own unique FK. A
# (foundation_id, ...) index would never serve a query here, so they are
# exempt. Any other model needs a foundation-led index in Meta.indexes or
# Meta.constraints (Meta must extend TenantModel.Meta or redeclare it).
FOUNDATION_INDEX_EXEMPT = {
    'identity.UserPin': 'one row per user, looked up by unique user FK',
    'finance.SchoolQrisConfig': 'one row per school, looked up by unique school FK',
    'finance.SchoolConvenienceFeePolicy': 'one row per school, looked up by unique school FK',
    'finance.SchoolArrearsPolicy': 'one row per school, looked up by unique school FK',
    'academic.ReportCardPolicy': 'one row per school, looked up by unique school FK',
    'academic.BroadcastPolicy': 'one row per school, looked up by unique school FK',
    'wallet.SpendRule': 'one row per student, looked up by unique student FK',
    'wallet.WalletAutoTopupConfig': 'one row per wallet, looked up by unique wallet FK',
    'campus.BehaviourPolicy': 'one row per school, looked up by unique school FK',
    'campus.ClinicPolicy': 'one row per school, looked up by unique school FK',
    'campus.HealthProfile': 'one row per student, looked up by unique student FK',
}


def tenant_models():
    # Skip throwaway models declared inside test modules.
    return [
        m for m in apps.get_models()
        if issubclass(m, TenantModel) and '.tests' not in m.__module__
    ]


def has_foundation_led_index(model):
    opts = model._meta
    leading = [i.fields[0] for i in opts.indexes if i.fields]
    leading += [c.fields[0] for c in opts.constraints if getattr(c, 'fields', None)]
    leading += [fields[0] for fields in opts.unique_together]
    return 'foundation_id' in leading


class TenantModelIndexTests(SimpleTestCase):
    def test_tenant_models_discovered(self):
        self.assertGreater(len(tenant_models()), 100)

    def test_foundation_id_is_indexed(self):
        missing = [
            m._meta.label for m in tenant_models()
            if not m._meta.get_field('foundation_id').db_index
        ]
        self.assertEqual(missing, [], 'foundation_id must have db_index=True (ARC-003)')

    def test_every_model_has_foundation_led_composite_index(self):
        missing = sorted(
            m._meta.label for m in tenant_models()
            if not has_foundation_led_index(m) and m._meta.label not in FOUNDATION_INDEX_EXEMPT
        )
        self.assertEqual(
            missing, [],
            'Add models.Index(fields=["foundation_id", ...]) on the primary query path '
            '(extend TenantModel.Meta if Meta is redeclared), or exempt the model with a reason '
            'in FOUNDATION_INDEX_EXEMPT (ARC-003).',
        )

    def test_exemptions_are_not_stale(self):
        by_label = {m._meta.label: m for m in tenant_models()}
        stale = sorted(
            label for label in FOUNDATION_INDEX_EXEMPT
            if label not in by_label or has_foundation_led_index(by_label[label])
        )
        self.assertEqual(stale, [], 'Remove exemptions for models that are gone or now indexed.')
