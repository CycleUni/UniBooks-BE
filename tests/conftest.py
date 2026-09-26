import pytest
from django.core.management import call_command
from django.core.cache import cache

@pytest.fixture(autouse=True)
def setup_regions(db):
    from core.default_categories import seed_default_categories
    from core.models import Region, Currency, Language
    twd, _ = Currency.objects.get_or_create(code='TWD', defaults={'symbol':'NT$', 'decimal_places':0})
    zh, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name':'Taiwanese', 'native_name':'繁體中文'})
    en, _ = Language.objects.get_or_create(code='en', defaults={'name':'English', 'native_name':'English'})
    # The rest of TW's configuration and its categories used to come from the
    # data migrations (0006/0009 and 0002). Those were consolidated away, and
    # migrations now build only the schema, so the test database gets them here.
    tw, _ = Region.objects.update_or_create(code='TW', defaults={
        'name':'Taiwan', 'currency':twd, 'default_language':zh, 'is_active':True, 'edu_email_suffix':['.edu.tw'],
        'translations':{'zh-TW': {'name': '台灣'}, 'zh-HK': {'name': '台灣'}, 'en': {'name': 'Taiwan'}},
        'search_engines':['googlebooks', 'openlibrary', 'isbnnet'], 'timezone':'Asia/Taipei',
    })
    tw.languages.set([zh, en])
    seed_default_categories(tw)
    
    hkd, _ = Currency.objects.get_or_create(code='HKD', defaults={'symbol':'HK$', 'decimal_places':1})
    zh_hk, _ = Language.objects.get_or_create(code='zh-HK', defaults={'name':'Hong Kong', 'native_name':'繁體中文（香港）'})
    Region.objects.update_or_create(code='HK', defaults={'name':'Hong Kong', 'currency':hkd, 'default_language':zh_hk, 'is_active':True, 'edu_email_suffix':['.edu.hk']})
    cache.clear()
