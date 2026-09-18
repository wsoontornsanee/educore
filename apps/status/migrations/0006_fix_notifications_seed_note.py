"""Fix the 'notifications' ServiceComponent's permanently stale seed note.

0002_seed_components.py seeded this component with mockup copy describing a
degraded state ('queue currently delayed'), but the component's default
manual_status is None (operational) — a fresh deploy would forever show a
green/operational component claiming its queue is delayed. Replace it with
neutral, non-alarming copy; a real delay should instead be surfaced via a
manual_status override or a published StatusIncident, not the permanent note."""
from django.db import migrations

OLD_NOTE_ID = 'Push, SMS, dan surel — antrean sedang tertunda'
OLD_NOTE_EN = 'Push, SMS and email — queue currently delayed'
NEW_NOTE_ID = 'Push, SMS, dan surel'
NEW_NOTE_EN = 'Push, SMS, and email'


def fix_notifications_note(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    ServiceComponent.objects.filter(key='notifications').update(
        note_id=NEW_NOTE_ID, note_en=NEW_NOTE_EN,
    )


def restore_notifications_note(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    ServiceComponent.objects.filter(key='notifications').update(
        note_id=OLD_NOTE_ID, note_en=OLD_NOTE_EN,
    )


class Migration(migrations.Migration):
    dependencies = [('status', '0005_statussubscriber')]
    operations = [migrations.RunPython(fix_notifications_note, restore_notifications_note)]
