"""The college categories a region starts with.

A region created without any categories showed an empty category rail on
its home page until someone filled it in by hand. Taiwan got its set from
0002_seed_categories; every other region only ever got one if a developer
happened to run seed_categories.py against that database.

`title`/`description` follow the Category model's convention: canonical
English, with the Chinese wording in `translations`. zh-TW and zh-HK each
carry their own terms (Hong Kong says 工商管理學院 and 計算機科學 where
Taiwan says 商管學院 and 資工).
"""

DEFAULT_CATEGORIES = [
    {'slug': 'management', 'title': 'College of Management', 'description': 'Economics, Accounting, Business Administration',
     'zh-TW': ('商管學院', '經濟、會計、企管'),
     'zh-HK': ('工商管理學院', '會計、金融、經濟')},
    {'slug': 'engineering', 'title': 'College of Engineering', 'description': 'Mechanical, Civil, Chemical Engineering',
     'zh-TW': ('工學院', '機械、土木、化工'),
     'zh-HK': ('工程學院', '機械工程、土木工程、化學工程')},
    {'slug': 'science', 'title': 'College of Science', 'description': 'Mathematics, Physics, Chemistry',
     'zh-TW': ('理學院', '數學、物理、化學'),
     'zh-HK': ('理學院', '數學、物理、化學')},
    {'slug': 'liberal-arts', 'title': 'College of Liberal Arts', 'description': 'Foreign Languages, Chinese, History',
     'zh-TW': ('文學院', '外文、中文、歷史'),
     'zh-HK': ('文學院', '英文、中文、歷史')},
    {'slug': 'medicine', 'title': 'College of Medicine', 'description': 'Medicine, Nursing, Pharmacy',
     'zh-TW': ('醫學院', '醫學、護理、藥學'),
     'zh-HK': ('醫學院', '醫學、護理、藥劑')},
    {'slug': 'eecs', 'title': 'College of EECS', 'description': 'Electrical Engineering, Computer Science',
     'zh-TW': ('電資學院', '電機、資工'),
     'zh-HK': ('電機及計算機學院', '電子工程、計算機科學')},
    {'slug': 'law', 'title': 'College of Law', 'description': 'Department of Law',
     'zh-TW': ('法學院', '法律學系'),
     'zh-HK': ('法律學院', '法律')},
    {'slug': 'social-sciences', 'title': 'College of Social Sciences', 'description': 'Political Science, Sociology, Social Work',
     'zh-TW': ('社科院', '政治、社會、社工'),
     'zh-HK': ('社會科學院', '政治、社會學、社會工作')},
]


def category_defaults(item, sort_order):
    """Field values for one DEFAULT_CATEGORIES entry, as Category kwargs."""
    return {
        'title': item['title'],
        'description': item['description'],
        'translations': {
            lang: {'title': item[lang][0], 'description': item[lang][1]}
            for lang in ('zh-TW', 'zh-HK')
        },
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
