import pytest
from accounts.views.auth import _is_valid_edu_email

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
