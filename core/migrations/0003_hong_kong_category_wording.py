from django.db import migrations

# What zh-HK used to say for each default category: the Taiwanese wording,
# copied verbatim by seed_categories.py and by 0002 before Hong Kong had
# terms of its own. Frozen here so later edits to core.default_categories
# cannot change which rows this migration recognises.
OLD_ZH_HK = {
    'management': {'title': '商管學院', 'description': '經濟、會計、企管'},
    'engineering': {'title': '工學院', 'description': '機械、土木、化工'},
    'science': {'title': '理學院', 'description': '數學、物理、化學'},
    'liberal-arts': {'title': '文學院', 'description': '外文、中文、歷史'},
    'medicine': {'title': '醫學院', 'description': '醫學、護理、藥學'},
    'eecs': {'title': '電資學院', 'description': '電機、資工'},
    'law': {'title': '法學院', 'description': '法律學系'},
    'social-sciences': {'title': '社科院', 'description': '政治、社會、社工'},
}


def forwards(apps, schema_editor):
    """Replace Taiwanese wording under zh-HK with Hong Kong wording.

    Only a zh-HK entry that still matches the old wording exactly is
    changed; one an operator has edited is theirs and stays as it is.
    """
    from core.default_categories import DEFAULT_CATEGORIES

    Category = apps.get_model('core', 'Category')
    hong_kong = {
        item['slug']: {'title': item['zh-HK'][0], 'description': item['zh-HK'][1]}
        for item in DEFAULT_CATEGORIES
    }
    for category in Category.objects.filter(slug__in=OLD_ZH_HK):
        translations = category.translations if isinstance(category.translations, dict) else {}
        if translations.get('zh-HK') != OLD_ZH_HK[category.slug]:
            continue
        category.translations = {**translations, 'zh-HK': hong_kong[category.slug]}
        category.save(update_fields=['translations'])
    _invalidate_home_cache()


def _invalidate_home_cache():
    # The home page caches categories for 24h and only admin edits clear it;
    # this migration's historical models fire no signals. Best effort: a
    # cache outage must not fail the migration.
    try:
        from accounts.views.home import invalidate_home_static_cache
        invalidate_home_static_cache()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_seed_default_categories'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
