from django.db import migrations

# The English every region's default categories were seeded with: Taiwan's
# "College of ..." naming. Frozen here so later edits to
# core.default_categories cannot change which rows this migration recognises.
OLD_ENGLISH = {
    'management': ('College of Management', 'Economics, Accounting, Business Administration'),
    'engineering': ('College of Engineering', 'Mechanical, Civil, Chemical Engineering'),
    'science': ('College of Science', 'Mathematics, Physics, Chemistry'),
    'liberal-arts': ('College of Liberal Arts', 'Foreign Languages, Chinese, History'),
    'medicine': ('College of Medicine', 'Medicine, Nursing, Pharmacy'),
    'eecs': ('College of EECS', 'Electrical Engineering, Computer Science'),
    'law': ('College of Law', 'Department of Law'),
    'social-sciences': ('College of Social Sciences', 'Political Science, Sociology, Social Work'),
}


def forwards(apps, schema_editor):
    """Give Hong Kong's default categories Hong Kong's English.

    Title and description are compared separately: whichever still has the
    seeded Taiwanese English is replaced, and one an operator has edited is
    left as it is.
    """
    from core.default_categories import DEFAULT_CATEGORIES

    Category = apps.get_model('core', 'Category')
    hong_kong = {item['slug']: item['en-HK'] for item in DEFAULT_CATEGORIES}
    for category in Category.objects.filter(region_id='HK', slug__in=OLD_ENGLISH):
        old_title, old_description = OLD_ENGLISH[category.slug]
        new_title, new_description = hong_kong[category.slug]
        changed = []
        if category.title == old_title:
            category.title = new_title
            changed.append('title')
        if category.description == old_description:
            category.description = new_description
            changed.append('description')
        if changed:
            category.save(update_fields=changed)
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
        ('core', '0003_hong_kong_category_wording'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
