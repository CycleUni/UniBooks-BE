"""Sentry is told which user a request is for, by id only."""
from unittest import mock

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User


@pytest.mark.django_db
def test_authenticated_request_sets_only_the_user_id():
    user = User.objects.create_user(email="someone@example.edu", password="x" * 12, first_name="A", last_name="B")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    with mock.patch("core.authentication.sentry_sdk.set_user") as set_user:
        client.get("/api/v1/auth/me/")
    set_user.assert_called_once_with({"id": str(user.pk)})


@pytest.mark.django_db
def test_anonymous_request_sets_no_user():
    with mock.patch("core.authentication.sentry_sdk.set_user") as set_user:
        APIClient().get("/api/v1/auth/me/")
    set_user.assert_not_called()
