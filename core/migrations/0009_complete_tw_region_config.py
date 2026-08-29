from django.db import migrations


def forward_func(apps, schema_editor):
    """Finish configuring the TW region that 0006_backfill_regions created.

    0006 set only what it needed to attach existing rows to a region: name,
    currency, default_language and is_active. Everything the region needs to
    be *usable* was left at its field default, and on a real deployment that
    surfaced immediately:

      - `languages` (M2M) was empty, so the language picker had nothing to
        offer and rendered "no matching option".
      - `translations` was {}, so localized_name() fell through to the
        canonical English 'Taiwan' in every locale.
      - `edu_email_suffix` was '', so the verification prompt asked for a
        campus address with a blank suffix, and the fallback check in
        accounts.views.auth._is_valid_edu_email had nothing to match on.
      - `search_engines` was [], so search/views.py fell back to its
        ['googlebooks'] default and openlibrary/isbnnet became unreachable.

    Only blank values are filled. An operator who has already configured any
    of this through the admin keeps their values.

    Deliberately does not create the HK region. Creating a region makes it
    selectable to real users, and which regions are live is a product
    decision, not a data-integrity one. seed_regions.py remains the way to
    stand up a new region.
    """
    Region = apps.get_model('core', 'Region')
    Language = apps.get_model('core', 'Language')

    try:
        tw = Region.objects.get(code='TW')
    except Region.DoesNotExist:
        return

    zh_tw, _ = Language.objects.get_or_create(
        code='zh-TW',
        defaults={'name': 'Traditional Chinese (Taiwan)', 'native_name': '繁體中文（台灣）'},
    )
    # 0006 only ever created zh-TW, so English does not exist yet on a
    # database that was migrated rather than seeded.
    en, _ = Language.objects.get_or_create(
        code='en',
        defaults={'name': 'English', 'native_name': 'English'},
    )

    changed = []
    if not tw.translations:
        tw.translations = {
            'zh-TW': {'name': '台灣'},
            'zh-HK': {'name': '台灣'},
            'en': {'name': 'Taiwan'},
        }
        changed.append('translations')
    if not tw.edu_email_suffix:
        tw.edu_email_suffix = '.edu.tw'
        changed.append('edu_email_suffix')
    if not tw.search_engines:
        tw.search_engines = ['googlebooks', 'openlibrary', 'isbnnet']
        changed.append('search_engines')
    if not tw.timezone:
        tw.timezone = 'Asia/Taipei'
        changed.append('timezone')
    if changed:
        tw.save(update_fields=changed)

    if not tw.languages.exists():
        tw.languages.set([zh_tw, en])

    # The post_save receiver that normally clears this is registered against
    # the real Region class, not the historical one this migration operates
    # on, so it does not fire here. Without this the cached region list keeps
    # serving the half-configured version until its TTL expires.
    try:
        from django.core.cache import cache
        cache.delete('active_regions')
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0008_alter_category_slug'),
    ]

    operations = [
        migrations.RunPython(forward_func, migrations.RunPython.noop),
    ]
