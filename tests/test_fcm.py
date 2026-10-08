"""core/fcm.py: the FCM HTTP v1 sender, with Google's two endpoints mocked."""

import json
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from accounts.models import PushDevice
from core import fcm


@pytest.fixture(scope="module")
def service_account_json():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps({"client_email": "sa@proj.iam.gserviceaccount.com", "private_key": pem})


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(
        email="push@example.com", first_name="P", last_name="U", password="pw-12345-xyz",
    )


@pytest.fixture(autouse=True)
def fresh_token_cache():
    fcm._cached_token.update(value=None, expires_at=0.0)


def _response(status=200, body=None):
    resp = mock.Mock(status_code=status, ok=status < 400)
    resp.json.return_value = body if body is not None else {}
    resp.raise_for_status.side_effect = None if status < 400 else RuntimeError("http error")
    return resp


def _fake_post(send_responses):
    """requests.post stand-in: the OAuth endpoint, then one reply per send."""
    sends = iter(send_responses)

    def post(url, **kwargs):
        if "oauth2" in url:
            return _response(200, {"access_token": "access-1", "expires_in": 3600})
        return next(sends)
    return mock.patch("core.fcm._session.post", side_effect=post)


def test_not_configured_sends_nothing():
    with override_settings(FCM_PROJECT_ID="", FCM_SERVICE_ACCOUNT_JSON=""), \
            mock.patch("core.fcm._session.post") as post:
        assert fcm.is_configured() is False
        assert fcm.send_data_message(["t"], {"title": "x"}) == (0, [])
    post.assert_not_called()


def test_a_broken_key_is_treated_as_not_configured():
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON="{not json"):
        assert fcm.is_configured() is False


def test_the_key_may_be_base64_encoded(service_account_json):
    import base64
    encoded = base64.b64encode(service_account_json.encode()).decode()
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=encoded):
        assert fcm.is_configured() is True


def test_sends_a_data_only_message_with_a_bearer_token(service_account_json):
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=service_account_json), \
            _fake_post([_response(200)]) as post:
        sent, invalid = fcm.send_data_message(["tok"], {"title": "Hi", "body": "b", "link": "https://x/y"})

    assert (sent, invalid) == (1, [])
    send_call = post.call_args_list[-1]
    assert send_call.args[0] == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert send_call.kwargs["headers"] == {"Authorization": "Bearer access-1"}
    message = send_call.kwargs["json"]["message"]
    assert message["token"] == "tok"
    assert message["data"] == {"title": "Hi", "body": "b", "link": "https://x/y"}
    assert "notification" not in message  # the service worker builds the notification


def test_the_access_token_is_reused_between_sends(service_account_json):
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=service_account_json), \
            _fake_post([_response(200), _response(200)]) as post:
        fcm.send_data_message(["a"], {"title": "1"})
        fcm.send_data_message(["b"], {"title": "2"})

    oauth_calls = [c for c in post.call_args_list if "oauth2" in c.args[0]]
    assert len(oauth_calls) == 1


def test_an_unregistered_token_is_reported_back(service_account_json):
    gone = _response(404, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}})
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=service_account_json), \
            _fake_post([gone, _response(200)]):
        sent, invalid = fcm.send_data_message(["dead", "alive"], {"title": "x"})

    assert (sent, invalid) == (1, ["dead"])


def test_other_rejections_do_not_mark_the_token_dead(service_account_json):
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=service_account_json), \
            _fake_post([_response(500)]):
        assert fcm.send_data_message(["tok"], {"title": "x"}) == (0, [])


def test_failing_to_authenticate_does_not_raise(service_account_json):
    with override_settings(FCM_PROJECT_ID="proj", FCM_SERVICE_ACCOUNT_JSON=service_account_json), \
            mock.patch("core.fcm._session.post", return_value=_response(401)):
        assert fcm.send_data_message(["tok"], {"title": "x"}) == (0, [])


@pytest.mark.django_db
def test_push_to_user_deletes_the_devices_fcm_says_are_gone(user, service_account_json):
    PushDevice.objects.create(user=user, token="dead")
    PushDevice.objects.create(user=user, token="alive")
    with mock.patch("core.fcm.send_data_message", return_value=(1, ["dead"])):
        assert fcm.push_to_user(user, title="t", body="b", link="https://x") == 1

    assert list(PushDevice.objects.values_list("token", flat=True)) == ["alive"]


@pytest.mark.django_db
def test_push_to_user_without_devices_does_not_call_fcm(user):
    with mock.patch("core.fcm.send_data_message") as send:
        assert fcm.push_to_user(user, title="t", body="b", link="https://x") == 0
    send.assert_not_called()


@pytest.mark.django_db
def test_push_to_user_does_nothing_when_the_user_turned_push_off(user):
    PushDevice.objects.create(user=user, token="tok")
    user.notify_push = False
    user.save(update_fields=["notify_push"])
    with mock.patch("core.fcm.send_data_message") as send:
        assert fcm.push_to_user(user, title="t", body="b", link="https://x") == 0
    send.assert_not_called()

