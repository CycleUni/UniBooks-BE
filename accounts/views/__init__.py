import uuid  # noqa: F401 — re-exported so `mock.patch("accounts.views.uuid.uuid4")` keeps working

from .auth import (
    RegisterView,
    RequestEduVerificationView,
    AutoVerifyEduEmailView,
    VerifyEmailView,
    VerifyRegistrationView,
    LoginView,
    RefreshTokenView,
    GoogleLoginView,
    LogoutView,
    AuthConfigView,
    ChangePasswordView,
    RemovePasswordView,
    RequestPasswordResetView,
    ConfirmPasswordResetView,
    ConfirmEmailChangeView,
    CancelEmailChangeView,
    UnbindEduEmailView,
    _send_verification_email,
)
from .profile import MyProfileView, PublicUserProfileView
from .home import (
    HomeMetadataView,
    invalidate_home_static_cache,
    HOME_STATIC_CACHE_LANGUAGES,
)

__all__ = [
    "RegisterView",
    "RequestEduVerificationView",
    "AutoVerifyEduEmailView",
    "VerifyEmailView",
    "VerifyRegistrationView",
    "LoginView",
    "RefreshTokenView",
    "GoogleLoginView",
    "LogoutView",
    "AuthConfigView",
    "ChangePasswordView",
    "RemovePasswordView",
    "RequestPasswordResetView",
    "ConfirmPasswordResetView",
    "ConfirmEmailChangeView",
    "CancelEmailChangeView",
    "UnbindEduEmailView",
    "MyProfileView",
    "PublicUserProfileView",
    "HomeMetadataView",
    "invalidate_home_static_cache",
]
