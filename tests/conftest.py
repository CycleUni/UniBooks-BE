import pytest
from django.core.management import call_command
from django.core.cache import cache

@pytest.fixture(autouse=True)
def setup_regions(db):
    from core.models import Region, Currency, Language
    twd, _ = Currency.objects.get_or_create(code='TWD', defaults={'symbol':'NT$', 'decimal_places':0})
    zh, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name':'Taiwanese', 'native_name':'繁體中文'})
    Region.objects.update_or_create(code='TW', defaults={'name':'Taiwan', 'currency':twd, 'default_language':zh, 'is_active':True, 'edu_email_suffix':['.edu.tw']})
    
    hkd, _ = Currency.objects.get_or_create(code='HKD', defaults={'symbol':'HK$', 'decimal_places':1})
    zh_hk, _ = Language.objects.get_or_create(code='zh-HK', defaults={'name':'Hong Kong', 'native_name':'繁體中文（香港）'})
    Region.objects.update_or_create(code='HK', defaults={'name':'Hong Kong', 'currency':hkd, 'default_language':zh_hk, 'is_active':True, 'edu_email_suffix':['.edu.hk']})
    cache.clear()
