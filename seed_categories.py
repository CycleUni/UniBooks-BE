import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_categories.py with DEBUG=False "
        "(this script must never run against production)."
    )
    sys.exit(1)

from core.models import Category, Region

# Get regions
tw = Region.objects.get(code='TW')
hk = Region.objects.get(code='HK')

categories_data = [
    {
        "slug": "management",
        "title": "商管學院",
        "en_title": "College of Management",
        "desc": "經濟、會計、企管",
        "en_desc": "Economics, Accounting, Business Administration",
        "sort_order": 1,
    },
    {
        "slug": "engineering",
        "title": "工學院",
        "en_title": "College of Engineering",
        "desc": "機械、土木、化工",
        "en_desc": "Mechanical, Civil, Chemical Engineering",
        "sort_order": 2,
    },
    {
        "slug": "science",
        "title": "理學院",
        "en_title": "College of Science",
        "desc": "數學、物理、化學",
        "en_desc": "Mathematics, Physics, Chemistry",
        "sort_order": 3,
    },
    {
        "slug": "liberal-arts",
        "title": "文學院",
        "en_title": "College of Liberal Arts",
        "desc": "外文、中文、歷史",
        "en_desc": "Foreign Languages, Chinese, History",
        "sort_order": 4,
    },
    {
        "slug": "medicine",
        "title": "醫學院",
        "en_title": "College of Medicine",
        "desc": "醫學、護理、藥學",
        "en_desc": "Medicine, Nursing, Pharmacy",
        "sort_order": 5,
    },
    {
        "slug": "eecs",
        "title": "電資學院",
        "en_title": "College of EECS",
        "desc": "電機、資工",
        "en_desc": "Electrical Engineering, Computer Science",
        "sort_order": 6,
    },
    {
        "slug": "law",
        "title": "法學院",
        "en_title": "College of Law",
        "desc": "法律學系",
        "en_desc": "Department of Law",
        "sort_order": 7,
    },
    {
        "slug": "social-sciences",
        "title": "社科院",
        "en_title": "College of Social Sciences",
        "desc": "政治、社會、社工",
        "en_desc": "Political Science, Sociology, Social Work",
        "sort_order": 8,
    }
]

for region, lang_code in [(tw, 'zh-TW'), (hk, 'zh-HK')]:
    for cd in categories_data:
        Category.objects.update_or_create(
            slug=cd['slug'],
            region=region,
            defaults={
                "title": cd['en_title'],
                "description": cd['en_desc'],
                "sort_order": cd['sort_order'],
                "translations": {
                    lang_code: {
                        "title": cd['title'],
                        "description": cd['desc']
                    }
                },
                "is_active": True
            }
        )

print("Categories seeded!")
