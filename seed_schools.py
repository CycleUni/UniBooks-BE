import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_schools.py with DEBUG=False "
        "(this script must never run against production)."
    )
    sys.exit(1)

from accounts.models import School
from core.models import Region

# Get regions
tw = Region.objects.get(code='TW')
hk = Region.objects.get(code='HK')

schools_data = [
    # Taiwan
    {"email_domain": "ntu.edu.tw", "name": "National Taiwan University", "translations": {"zh-TW": {"name": "國立台灣大學"}}, "region": tw},
    {"email_domain": "nccu.edu.tw", "name": "National Chengchi University", "translations": {"zh-TW": {"name": "國立政治大學"}}, "region": tw},
    {"email_domain": "ntnu.edu.tw", "name": "National Taiwan Normal University", "translations": {"zh-TW": {"name": "國立台灣師範大學"}}, "region": tw},
    {"email_domain": "ncku.edu.tw", "name": "National Cheng Kung University", "translations": {"zh-TW": {"name": "國立成功大學"}}, "region": tw},
    {"email_domain": "nthu.edu.tw", "name": "National Tsing Hua University", "translations": {"zh-TW": {"name": "國立清華大學"}}, "region": tw},
    # Hong Kong
    {"email_domain": "hku.hk", "name": "The University of Hong Kong", "translations": {"zh-HK": {"name": "香港大學"}}, "region": hk},
    {"email_domain": "cuhk.edu.hk", "name": "The Chinese University of Hong Kong", "translations": {"zh-HK": {"name": "香港中文大學"}}, "region": hk},
    {"email_domain": "ust.hk", "name": "The Hong Kong University of Science and Technology", "translations": {"zh-HK": {"name": "香港科技大學"}}, "region": hk},
    {"email_domain": "polyu.edu.hk", "name": "The Hong Kong Polytechnic University", "translations": {"zh-HK": {"name": "香港理工大學"}}, "region": hk},
    {"email_domain": "cityu.edu.hk", "name": "City University of Hong Kong", "translations": {"zh-HK": {"name": "香港城市大學"}}, "region": hk},
]

for sd in schools_data:
    School.objects.update_or_create(
        email_domain=sd['email_domain'],
        defaults={
            "name": sd['name'],
            "translations": sd['translations'],
            "region": sd['region'],
        },
    )

print("Schools seeded!")
