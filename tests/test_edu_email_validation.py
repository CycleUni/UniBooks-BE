import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from accounts.views.auth import _is_valid_edu_email

@pytest.mark.django_db(transaction=True)
def test_edu_email_suffix_migration():
    executor = MigrationExecutor(connection)
    app = 'core'
    
    # Go back to 0009
    executor.loader.build_graph()
    executor.migrate([(app, '0009_complete_tw_region_config')])
    
    # Insert test data using old schema
    old_apps = executor.loader.project_state([(app, '0009_complete_tw_region_config')]).apps
    Region = old_apps.get_model('core', 'Region')
    
    currency_id = old_apps.get_model('core', 'Currency').objects.first().code
    lang_id = old_apps.get_model('core', 'Language').objects.first().code
    
    # It was a CharField
    r1 = Region.objects.create(code='ZZ', name='Test1', edu_email_suffix='.edu.zz', currency_id=currency_id, default_language_id=lang_id)
    r2 = Region.objects.create(code='YY', name='Test2', edu_email_suffix='', currency_id=currency_id, default_language_id=lang_id)
    
    # Migrate forward
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(app, '0010_convert_edu_email_suffix')])
    
    # Check new schema
    new_apps = executor.loader.project_state([(app, '0010_convert_edu_email_suffix')]).apps
    NewRegion = new_apps.get_model('core', 'Region')
    
    nr1 = NewRegion.objects.get(code='ZZ')
    assert nr1.edu_email_suffix == ['.edu.zz']
    
    nr2 = NewRegion.objects.get(code='YY')
    assert nr2.edu_email_suffix == []

@pytest.mark.django_db
def test_is_valid_edu_email_multiple_suffixes(client):
    from core.models import Region
    from accounts.models import School
    
    # Clear active regions cache just in case
    from core import region
    if hasattr(region, '_active_regions_cache'):
        region._active_regions_cache = None
        
    hk = Region.objects.get(code='HK')
    hk.edu_email_suffix = ['.edu.hk', '.edu', '.hk', 's.eduhk.hk']
    hk.save()
    
    tw = Region.objects.get(code='TW')
    tw.edu_email_suffix = ['.edu.tw']
    tw.save()

    # Create a registered school for HKU
    School.objects.create(
        region=hk,
        name='HKU',
        email_domain='hku.hk'
    )
    
    # _is_valid_edu_email 對清單中每一個後綴都成立
    assert _is_valid_edu_email('test@foo.edu.hk') is True
    assert _is_valid_edu_email('test@foo.edu') is True
    assert _is_valid_edu_email('test@s.eduhk.hk') is True
    assert _is_valid_edu_email('test@hku.hk') is True
    
    # 港大的 xxx@hku.hk（已註冊學校）能通過
    assert _is_valid_edu_email('student@hku.hk') is True
    
    # 一個不屬於任何學校但符合後綴的地址，仍然拿不到驗證
    # This means the API rejects it with errSchoolNotSupported, but _is_valid_edu_email returns True
    # The requirement: "證明安全性沒有降低" means we should test the auth view itself or just verify the behavior.
    assert _is_valid_edu_email('fake@not-a-school.hk') is True
    
    # Test API endpoint
    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient
    User = get_user_model()
    user = User.objects.create_user(email='testuser@gmail.com', password='pw', first_name='Test', last_name='User')
    
    api_client = APIClient()
    api_client.force_authenticate(user=user)

    # Try to verify a fake email that matches the suffix
    response = api_client.post('/api/v1/auth/verify/request/', {'edu_email': 'fake@not-a-school.hk'}, format='json')
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'acct.errSchoolNotSupported'
    
    # Try a totally invalid email
    response2 = api_client.post('/api/v1/auth/verify/request/', {'edu_email': 'fake@gmail.com'}, format='json')
    assert response2.status_code == 400
    assert response2.json()['error']['code'] == 'acct.errEduEmail'

@pytest.mark.django_db(transaction=True)
def test_migration_converts_edu_email_suffix():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    
    executor = MigrationExecutor(connection)
    app = 'core'
    
    # Go back to 0009
    executor.loader.build_graph()
    executor.migrate([(app, '0009_complete_tw_region_config')])
    
    # Insert test data using old schema
    old_apps = executor.loader.project_state([(app, '0009_complete_tw_region_config')]).apps
    Region = old_apps.get_model('core', 'Region')
    
    currency_id = old_apps.get_model('core', 'Currency').objects.first().code
    lang_id = old_apps.get_model('core', 'Language').objects.first().code
    
    # It was a CharField
    r1 = Region.objects.create(code='ZZ', name='Test1', edu_email_suffix='.edu.zz', currency_id=currency_id, default_language_id=lang_id)
    r2 = Region.objects.create(code='YY', name='Test2', edu_email_suffix='', currency_id=currency_id, default_language_id=lang_id)
    
    # Migrate forward
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(app, '0010_convert_edu_email_suffix')])
    
    # Check new schema
    new_apps = executor.loader.project_state([(app, '0010_convert_edu_email_suffix')]).apps
    NewRegion = new_apps.get_model('core', 'Region')
    
    nr1 = NewRegion.objects.get(code='ZZ')
    assert nr1.edu_email_suffix == ['.edu.zz']
    
    nr2 = NewRegion.objects.get(code='YY')
    assert nr2.edu_email_suffix == []
