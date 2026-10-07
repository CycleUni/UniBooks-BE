import sentry_sdk
from rest_framework_simplejwt.authentication import JWTAuthentication as BaseJWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken, TokenError


class JWTAuthentication(BaseJWTAuthentication):
    """
    simplejwt's JWTAuthentication, plus it tells Sentry who the request is
    for. Only the user id is sent — never email or name — so an error can be
    told apart as "one user, ten times" or "ten users". With Sentry off
    (no SENTRY_DSN) set_user does nothing.
    """
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            sentry_sdk.set_user({"id": str(result[0].pk)})
        return result


class OptionalJWTAuthentication(JWTAuthentication):
    """
    Tries to authenticate with JWT. If the token is invalid or expired,
    it simply returns None (treating the user as anonymous) instead of
    raising an exception that would cause a 401 response on AllowAny views.
    """
    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except (AuthenticationFailed, InvalidToken, TokenError):
            return None
