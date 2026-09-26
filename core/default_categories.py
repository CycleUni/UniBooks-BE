"""The college categories a region starts with.

A region created without any categories showed an empty category rail on
its home page until someone filled it in by hand. Taiwan got its set from
0002_seed_categories; every other region only ever got one if a developer
happened to run seed_categories.py against that database.

`title`/`description` follow the Category model's convention: canonical
English, with the Chinese wording in `translations`. Both zh-TW and zh-HK
carry it so the right text shows whichever Chinese locale a region uses.
"""

DEFAULT_CATEGORIES = [
    {'slug': 'management', 'title': 'College of Management', 'description': 'Economics, Accounting, Business Administration',
     'zh_title': '商管學院', 'zh_description': '經濟、會計、企管'},
    {'slug': 'engineering', 'title': 'College of Engineering', 'description': 'Mechanical, Civil, Chemical Engineering',
     'zh_title': '工學院', 'zh_description': '機械、土木、化工'},
    {'slug': 'science', 'title': 'College of Science', 'description': 'Mathematics, Physics, Chemistry',
     'zh_title': '理學院', 'zh_description': '數學、物理、化學'},
    {'slug': 'liberal-arts', 'title': 'College of Liberal Arts', 'description': 'Foreign Languages, Chinese, History',
     'zh_title': '文學院', 'zh_description': '外文、中文、歷史'},
    {'slug': 'medicine', 'title': 'College of Medicine', 'description': 'Medicine, Nursing, Pharmacy',
     'zh_title': '醫學院', 'zh_description': '醫學、護理、藥學'},
    {'slug': 'eecs', 'title': 'College of EECS', 'description': 'Electrical Engineering, Computer Science',
     'zh_title': '電資學院', 'zh_description': '電機、資工'},
    {'slug': 'law', 'title': 'College of Law', 'description': 'Department of Law',
     'zh_title': '法學院', 'zh_description': '法律學系'},
    {'slug': 'social-sciences', 'title': 'College of Social Sciences', 'description': 'Political Science, Sociology, Social Work',
     'zh_title': '社科院', 'zh_description': '政治、社會、社工'},
]


def category_defaults(item, sort_order):
    """Field values for one DEFAULT_CATEGORIES entry, as Category kwargs."""
    zh = {'title': item['zh_title'], 'description': item['zh_description']}
    return {
        'title': item['title'],
        'description': item['description'],
        'translations': {'zh-TW': zh, 'zh-HK': dict(zh)},
        'sort_order': sort_order,
        'is_active': True,
    }


def seed_default_categories(region, category_model=None):
    """Give `region` the default categories if it has none at all.

    A region that already has categories is left alone: an operator who
    renamed, removed or replaced them did so on purpose. Returns the number
    of categories created.

    `category_model` lets a data migration pass its historical model.
    """
    if category_model is None:
        from core.models import Category as category_model
    if category_model.objects.filter(region=region).exists():
        return 0
    category_model.objects.bulk_create([
        category_model(region=region, slug=item['slug'], **category_defaults(item, order))
        for order, item in enumerate(DEFAULT_CATEGORIES, start=1)
    ])
    return len(DEFAULT_CATEGORIES)
