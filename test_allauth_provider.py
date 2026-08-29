import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "unibooks.settings")
django.setup()

from allauth.socialaccount.adapter import get_adapter

try:
    provider = get_adapter().get_provider(request=None, provider="google")
    app = provider.app
    print("SUCCESS: Found app in memory with client_id:", app.client_id)
except Exception as e:
    print("Error:", type(e), e)
