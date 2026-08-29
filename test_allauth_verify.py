import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'unibooks.settings'
django.setup()
from allauth.socialaccount.providers.google.views import _verify_and_decode
print("Imported _verify_and_decode:", _verify_and_decode)
