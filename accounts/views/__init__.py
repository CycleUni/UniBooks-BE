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
from .school_requests import SchoolRequestCreateView
from .profile import MyProfileView, NotificationSettingsView, PublicUserProfileView, SiteLanguageView
from .home import (
    HomeMetadataView,
    invalidate_home_static_cache,
    HOME_STATIC_CACHE_LANGUAGES,
)

__all__ = [
    "NotificationSettingsView",
    "SiteLanguageView",
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
    "SchoolRequestCreateView",
    "MyProfileView",
    "PublicUserProfileView",
    "HomeMetadataView",
    "invalidate_home_static_cache",
]
