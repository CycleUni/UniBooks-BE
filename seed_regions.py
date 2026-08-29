import os
import django

# Setup django environment if executed directly
if __name__ == '__main__' and not os.environ.get('DJANGO_SETTINGS_MODULE'):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
    django.setup()

from core.models import Currency, Language, Region

def run():
    print("Seeding currencies...")
    twd, _ = Currency.objects.update_or_create(
        code='TWD',
        defaults={
            'symbol': 'NT$',
            'decimal_places': 0,
            'symbol_position': 'prefix',
            'is_active': True,
        }
    )
    hkd, _ = Currency.objects.update_or_create(
        code='HKD',
        defaults={
            'symbol': 'HK$',
            'decimal_places': 2,
            'symbol_position': 'prefix',
            'is_active': True,
        }
    )

    print("Seeding languages...")
    en, _ = Language.objects.update_or_create(
        code='en',
        defaults={
            'name': 'English',
            'native_name': 'English',
            'is_active': True,
            'sort_order': 10,
        }
    )
    zh_tw, _ = Language.objects.update_or_create(
        code='zh-TW',
        defaults={
            'name': 'Traditional Chinese (Taiwan)',
            'native_name': '繁體中文（台灣）',
            'is_active': True,
            'sort_order': 20,
        }
    )
    zh_hk, _ = Language.objects.update_or_create(
        code='zh-HK',
        defaults={
            'name': 'Traditional Chinese (Hong Kong)',
            'native_name': '繁體中文（香港）',
            'is_active': True,
            'sort_order': 30,
        }
    )

    print("Seeding regions...")
    tw, _ = Region.objects.update_or_create(
        code='TW',
        defaults={
            'name': 'Taiwan',
            'translations': {"zh-TW": {"name": "台灣"}, "zh-HK": {"name": "台灣"}, "en": {"name": "Taiwan"}},
            'currency': twd,
            'default_language': zh_tw,
            'timezone': 'Asia/Taipei',
            'search_engines': ['googlebooks', 'openlibrary', 'isbnnet'],
            'edu_email_suffix': ['.edu.tw'],
            'is_active': True,
            'sort_order': 10,
        }
    )
    tw.languages.set([zh_tw, en])

    hk, _ = Region.objects.update_or_create(
        code='HK',
        defaults={
            'name': 'Hong Kong',
            'translations': {"zh-TW": {"name": "香港"}, "zh-HK": {"name": "香港"}, "en": {"name": "Hong Kong"}},
            'currency': hkd,
            'default_language': zh_hk,
            'timezone': 'Asia/Hong_Kong',
            'search_engines': ['googlebooks', 'openlibrary'],
            'edu_email_suffix': ['.edu.hk', '.edu', '.hk', 's.eduhk.hk'],
            'is_active': True,
            'sort_order': 20,
        }
    )
    hk.languages.set([zh_hk, en])
    
    print("Regions seeded successfully.")

if __name__ == '__main__':
    run()
