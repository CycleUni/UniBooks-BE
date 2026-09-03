from django.urls import path
from accounts import views

urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='auth-register'),
    path('verify/', views.VerifyEmailView.as_view(), name='auth-verify'),
    path('verify-registration/', views.VerifyRegistrationView.as_view(), name='auth-verify-registration'),
    path('verify/request/', views.RequestEduVerificationView.as_view(), name='auth-verify-request'),
    path('verify/auto/', views.AutoVerifyEduEmailView.as_view(), name='auth-verify-auto'),
    path('verify/unbind/', views.UnbindEduEmailView.as_view(), name='auth-verify-unbind'),
    path('token/', views.LoginView.as_view(), name='auth-login'),
    path('google/', views.GoogleLoginView.as_view(), name='auth-google'),
    path('config/', views.AuthConfigView.as_view(), name='auth-config'),
    path('refresh/', views.RefreshTokenView.as_view(), name='auth-refresh'),
    path('logout/', views.LogoutView.as_view(), name='auth-logout'),
    path('password/', views.ChangePasswordView.as_view(), name='auth-password'),
    path('password/remove/', views.RemovePasswordView.as_view(), name='auth-password-remove'),
    path('password/reset/request/', views.RequestPasswordResetView.as_view(), name='auth-password-reset-request'),
    path('password/reset/confirm/', views.ConfirmPasswordResetView.as_view(), name='auth-password-reset-confirm'),
    path('email/change/confirm/', views.ConfirmEmailChangeView.as_view(), name='auth-email-change-confirm'),
    path('email/change/cancel/', views.CancelEmailChangeView.as_view(), name='auth-email-change-cancel'),
    path('me/', views.MyProfileView.as_view(), name='auth-me'),
    path('users/<int:pk>/', views.PublicUserProfileView.as_view(), name='auth-user-profile'),
]
