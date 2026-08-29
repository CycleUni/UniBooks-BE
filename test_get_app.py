import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "unibooks.settings")
django.setup()

from allauth.socialaccount.models import SocialApp
try:
    app = SocialApp.objects.get(provider='google')
    print("Found app:", app)
except Exception as e:
    print("Error:", e)
