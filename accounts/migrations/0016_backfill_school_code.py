from django.db import migrations

from accounts.school_codes import dedupe_code, derive_code


def backfill_school_codes(apps, schema_editor):
    """Give every School the code its email domain implies (ntu.edu.tw → NTU).

    Ordered by id so that on a collision inside a region the older school
    keeps the plain code and the newer one gets NTU-2, NTU-3 — and so the
    result is the same on every database this runs against. `taken` is per
    region: the same code in two regions is not a collision.
    """
    School = apps.get_model('accounts', 'School')
    taken = {}
    for school in School.objects.order_by('id'):
        region_taken = taken.setdefault(school.region_id, set())
        school.code = dedupe_code(derive_code(school.email_domain), region_taken)
        region_taken.add(school.code)
        school.save(update_fields=['code'])


class Migration(migrations.Migration):
    """Step 2 of 3 for School.code. Reversing is a no-op: going back past
    0015 drops the column anyway, and between 0015 and 0016 a filled-in code
    is harmless."""

    dependencies = [
        ('accounts', '0015_school_code_nullable'),
    ]

    operations = [
        migrations.RunPython(backfill_school_codes, migrations.RunPython.noop),
    ]
