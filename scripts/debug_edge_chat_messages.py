# Manual/ad-hoc debug script for exercising a locally-running CFEdgeChat
# worker (localhost:8787) with seeded backdated messages. It is NOT an
# automated pytest test: it has no assertions, requires a hardcoded
# conversation id to already exist in the DB, and performs real network
# calls, so it must not live at the repo root under a `test_*.py` name
# (pytest would try to collect and execute it as a test module and fail on
# both the network call and unguarded DB access outside a `django_db`
# fixture). Run manually with `python manage.py shell` env active, e.g.:
#   DJANGO_SETTINGS_MODULE=unibooks.settings python scripts/debug_edge_chat_messages.py
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from django.conf import settings
from rest_framework_simplejwt.backends import TokenBackend
from messaging.models import Conversation
import datetime
import urllib.request
import json
import time

conv_id = '74c1c3031c344e1ebb87e51e516d028c'
conv = Conversation.objects.get(id=conv_id)
user_id = str(conv.buyer_id)
seller_id = str(conv.listing.seller_id)

app_id = getattr(settings, 'EDGE_CHAT_APP_ID', 'unibooks')
token_backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)
token = token_backend.encode({
    "user_id": user_id,
    "room_id": str(conv.id),
    # Required now: CFEdgeChat rejects room-scoped requests whose app_id
    # claim doesn't match the appId URL segment below.
    "app_id": app_id,
    "participant_ids": [user_id, seller_id],
    "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
})

# timestamps in ms
now = int(time.time() * 1000)
day = 24 * 60 * 60 * 1000

test_msgs = [
    {"content": "【測試】這是一週前（大約 5 天前）的訊息", "timestamp": now - 5 * day},
    {"content": "【測試】這是上個月的訊息", "timestamp": now - 35 * day},
    {"content": "【測試】這是去年的訊息", "timestamp": now - 400 * day},
]

for msg in test_msgs:
    req = urllib.request.Request(
        f"http://localhost:8787/api/{app_id}/{conv.id}/messages",
        data=json.dumps(msg).encode('utf-8'),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    res = urllib.request.urlopen(req)
    print(res.read().decode())
