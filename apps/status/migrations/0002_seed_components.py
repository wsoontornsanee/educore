from django.db import migrations

COMPONENTS = [
    dict(key='web_portal', display_order=1,
         name_id='Portal web', name_en='Web portal',
         note_id='Dasbor yayasan, admin sekolah, dan suite guru',
         note_en='Foundation dashboard, school admin, and teacher suite'),
    dict(key='partner_api', display_order=2,
         name_id='Partner API', name_en='Partner API',
         note_id='REST v1 dan pengiriman webhook',
         note_en='REST v1 and webhook delivery'),
    dict(key='mobile_apps', display_order=3,
         name_id='Aplikasi orang tua & guru', name_en='Parent & teacher apps',
         note_id='Sinkronisasi seluler dan antrean offline',
         note_en='Mobile sync and the offline queue'),
    dict(key='notifications', display_order=4,
         name_id='Notifikasi', name_en='Notifications',
         note_id='Push, SMS, dan surel — antrean sedang tertunda',
         note_en='Push, SMS and email — queue currently delayed'),
    dict(key='payments', display_order=5,
         name_id='Pembayaran', name_en='Payments',
         note_id='Virtual account, QRIS, dan rekonsiliasi',
         note_en='Virtual accounts, QRIS, and reconciliation'),
    dict(key='canteen_pos', display_order=6,
         name_id='POS kantin', name_en='Canteen POS',
         note_id='Terminal kantin dan sinkronisasi dompet',
         note_en='Canteen terminals and wallet sync'),
]


def seed_components(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    for data in COMPONENTS:
        ServiceComponent.objects.get_or_create(key=data['key'], defaults=data)


def remove_components(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    ServiceComponent.objects.filter(key__in=[c['key'] for c in COMPONENTS]).delete()


class Migration(migrations.Migration):
    dependencies = [('status', '0001_initial')]
    operations = [migrations.RunPython(seed_components, remove_components)]
