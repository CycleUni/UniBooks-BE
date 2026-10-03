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
from core.default_cities import seed_default_cities
from core.models import City, Region

# Get regions
tw = Region.objects.get(code='TW')
hk = Region.objects.get(code='HK')
for region in (tw, hk):
    seed_default_cities(region)

# `code` is unique per region only: a Taiwanese school could also be HKU.
schools_data = [
    # Taiwan
    {"email_domain": "ntu.edu.tw", "city": "TPE", "code": "NTU", "name": "National Taiwan University", "translations": {"zh-TW": {"name": "國立台灣大學"}}, "region": tw},
    {"email_domain": "nccu.edu.tw", "city": "TPE", "code": "NCCU", "name": "National Chengchi University", "translations": {"zh-TW": {"name": "國立政治大學"}}, "region": tw},
    {"email_domain": "ntnu.edu.tw", "city": "TPE", "code": "NTNU", "name": "National Taiwan Normal University", "translations": {"zh-TW": {"name": "國立台灣師範大學"}}, "region": tw},
    {"email_domain": "ncku.edu.tw", "city": "TNN", "code": "NCKU", "name": "National Cheng Kung University", "translations": {"zh-TW": {"name": "國立成功大學"}}, "region": tw},
    {"email_domain": "nthu.edu.tw", "city": "HSZ", "code": "NTHU", "name": "National Tsing Hua University", "translations": {"zh-TW": {"name": "國立清華大學"}}, "region": tw},
    # Hong Kong
    {"email_domain": "hku.hk", "city": "HKI", "code": "HKU", "name": "The University of Hong Kong", "translations": {"zh-HK": {"name": "香港大學"}}, "region": hk},
    {"email_domain": "cuhk.edu.hk", "city": "NT", "code": "CUHK", "name": "The Chinese University of Hong Kong", "translations": {"zh-HK": {"name": "香港中文大學"}}, "region": hk},
    {"email_domain": "ust.hk", "city": "NT", "code": "HKUST", "name": "The Hong Kong University of Science and Technology", "translations": {"zh-HK": {"name": "香港科技大學"}}, "region": hk},
    {"email_domain": "polyu.edu.hk", "city": "KLN", "code": "POLYU", "name": "The Hong Kong Polytechnic University", "translations": {"zh-HK": {"name": "香港理工大學"}}, "region": hk},
    {"email_domain": "cityu.edu.hk", "city": "KLN", "code": "CITYU", "name": "City University of Hong Kong", "translations": {"zh-HK": {"name": "香港城市大學"}}, "region": hk},
]

for sd in schools_data:
    School.objects.update_or_create(
        email_domain=sd['email_domain'],
        defaults={
            "code": sd['code'],
            "name": sd['name'],
            "translations": sd['translations'],
            "region": sd['region'],
            "city": City.objects.get(region=sd['region'], code=sd['city']),
        },
    )

print("Schools seeded!")
