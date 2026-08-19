from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken, TokenError

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
