import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "unibooks.settings")
django.setup()

from django.test import RequestFactory
from accounts.models import User
from adminapi.views import AdminSchoolListView
from rest_framework.test import force_authenticate

rf = RequestFactory()
request = rf.get('/api/v1/admin/schools/')
user = User.objects.filter(is_superuser=True).first()
if not user:
    user = User.objects.create_superuser('admin@test.com', 'Admin', 'User', 'password')

view = AdminSchoolListView.as_view()
force_authenticate(request, user=user)
try:
    response = view(request)
    print("STATUS CODE:", response.status_code)
    print("DATA:", response.data)
except Exception as e:
    import traceback
    traceback.print_exc()
