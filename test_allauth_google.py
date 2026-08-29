import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "unibooks.settings")
django.setup()

import inspect
from allauth.socialaccount.providers.google.views import _verify_and_decode

print(inspect.signature(_verify_and_decode))
