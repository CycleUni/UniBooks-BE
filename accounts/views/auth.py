import logging
import uuid

from django.utils.translation import gettext_lazy as _

from rest_framework import status, views
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.utils import timezone

from accounts.serializers import RegisterSerializer, UserSerializer
from accounts.services import (
    issue_tokens,
    verify_and_revoke_refresh_token,
    revoke_all_tokens_for_user,
    get_grace_tokens,
    store_grace_tokens,
    email_already_used,
)
from accounts.models import School
from core.i18n import resolve_language


def resolve_school_from_email(email):
    if not email or '@' not in email:
        return None, None
    domain = email.split('@')[-1].lower()
    parts = domain.split('.')
    for i in range(len(parts) - 1):
        sub_domain = '.'.join(parts[i:])
        try:
            school = School.objects.get(email_domain__iexact=sub_domain)
            return school, school.region
        except School.DoesNotExist:
            continue
    return None, None

def _is_valid_edu_email(email):
    """Whether `email` looks like a campus address for any active region.

    A registered School is what actually counts, so that is checked first:
    `Region.edu_email_suffix` is a display hint ("enter your .edu.hk address"),
    not a rule real domains obey. Taiwan happens to be uniform — every campus
    is under .edu.tw — and gating on the suffix alone quietly locked out five
    of Hong Kong's thirteen universities, including HKU (hku.hk) and HKUST
    (ust.hk), which carry no .edu at all, plus hkapa.edu, hksyu.edu and the
    Education University's s.eduhk.hk.

    The suffix check is kept as a fallback so an address at a not-yet-imported
    campus still gets `acct.errSchoolNotSupported` from the caller rather than
    the blunter "that is not a campus email".
    """
    school, _region = resolve_school_from_email(email)
    if school:
        return True

    from core.region import _get_active_regions
    active_regions = _get_active_regions()
    return any(
        any(email.endswith(suffix) for suffix in r.edu_email_suffix)
        for r in active_regions.values() if r.edu_email_suffix
    )

logger = logging.getLogger(__name__)

User = get_user_model()


def _send_verification_email(subject, message, recipient_email, log_context):
    """Best-effort send: the verification token is already cached by the
    caller before this runs, so a transient Mailjet/SMTP failure shouldn't
    fail the whole request — log for ops visibility and degrade gracefully.

    Never logs `message` (it embeds the verification/activation token — a
    bearer credential good for 24h) or `recipient_email` (PII) — only the
    caller-supplied context label and, on failure, the exception.
    """
    try:
        from django.core.mail import EmailMessage
        msg = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        msg.send(fail_silently=False)
        if hasattr(msg, 'anymail_status'):
            logger.debug("Sent verification email (%s), anymail status=%s", log_context, getattr(msg.anymail_status, 'status', None))
    except Exception:
        logger.exception("Failed to send verification email (%s)", log_context)


class RegisterView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Account is created inactive (see RegisterSerializer.create) —
            # this link is what activates it. Login is blocked until then
            # (LoginView checks is_active).
            verify_token = str(uuid.uuid4())
            cache.set(f"register-verify:{verify_token}", {"user_id": user.id}, timeout=86400)
            verify_link = f"{settings.FRONTEND_URL}/verify?token={verify_token}&type=register"

            lang = resolve_language(request)
            if lang == 'zh-TW':
                subject = 'CycleUni 帳號啟用信'
                message = f'感謝您註冊 CycleUni！請點擊以下連結以啟用您的帳號：\n\n{verify_link}\n\n如果您沒有註冊此帳號，請忽略這封信件。'
            else:
                subject = 'CycleUni Account Activation'
                message = f'Thanks for signing up for CycleUni! Click the link below to activate your account:\n\n{verify_link}\n\nIf you did not sign up for this account, please ignore this email.'

            name = f"{user.last_name}{user.first_name}".strip() or "User"
            recipient = f'"{name}" <{user.email}>'
            _send_verification_email(subject, message, recipient, f"registration for user {user.id}")

            return Response({"code": "auth.registerSuccess"}, status=status.HTTP_201_CREATED)
        return Response({"error": {"code": "auth.errValidation", "fields": serializer.errors}}, status=status.HTTP_400_BAD_REQUEST)


class RequestEduVerificationView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'verify-request'

    def post(self, request):
        edu_email = request.data.get('edu_email')
        if not edu_email or not isinstance(edu_email, str):
            return Response({"error": {"code": "acct.errEduEmail"}}, status=status.HTTP_400_BAD_REQUEST)
            
        edu_email = edu_email.strip().lower()
        if not _is_valid_edu_email(edu_email):
            return Response({"error": {"code": "acct.errEduEmail"}}, status=status.HTTP_400_BAD_REQUEST)

        school, region = resolve_school_from_email(edu_email)
        if not school:
            return Response({"error": {"code": "acct.errSchoolNotSupported"}}, status=status.HTTP_400_BAD_REQUEST)

        # Check if email is already in use by someone else as edu_email or email
        if email_already_used(edu_email, request.user.id):
            return Response({"error": {"code": "acct.errEmailTaken"}}, status=status.HTTP_400_BAD_REQUEST)

        verify_token = str(uuid.uuid4())
        # Cache token with user_id and edu_email for 24 hours
        cache.set(f"verify:{verify_token}", {"user_id": request.user.id, "edu_email": edu_email}, timeout=86400)

        verify_link = f"{settings.FRONTEND_URL}/verify?token={verify_token}&type=edu"

        lang = resolve_language(request)
        if lang == 'zh-TW':
            subject = 'CycleUni 學生信箱驗證'
            message = f'請點擊以下連結以驗證您的學生信箱：\n\n{verify_link}\n\n如果您沒有請求此驗證，請忽略這封信件。'
        else:
            subject = 'CycleUni Student Email Verification'
            message = f'Click the link below to verify your student email:\n\n{verify_link}\n\nIf you did not request this verification, please ignore this email.'

        name = f"{request.user.last_name}{request.user.first_name}".strip() or "User"
        recipient = f'"{name}" <{edu_email}>'
        _send_verification_email(subject, message, recipient, f"edu verification for user {request.user.id}")

        return Response({"code": "acct.sentVerification"}, status=status.HTTP_200_OK)


class AutoVerifyEduEmailView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'verify-request'

    def post(self, request):
        user = request.user
        email = user.email.strip().lower()
        if not _is_valid_edu_email(email):
            return Response({"error": {"code": "acct.errEduEmail"}}, status=status.HTTP_400_BAD_REQUEST)

        school, region = resolve_school_from_email(email)
        if not school:
            return Response({"error": {"code": "acct.errSchoolNotSupported"}}, status=status.HTTP_400_BAD_REQUEST)

        if user.region_verifications.verified_in(region).exists():
            return Response({"code": "acct.verifySuccess"})

        # Check if email is already in use by someone else as edu_email
        if email_already_used(email, user.id):
            return Response({"error": {"code": "acct.errEmailTaken"}}, status=status.HTTP_400_BAD_REQUEST)

        from accounts.models import RegionVerification
        RegionVerification.objects.update_or_create(
            user=user,
            region=region,
            defaults={
                'school': school,
                'edu_email': email,
                'verified_at': timezone.now(),
                'is_active': True
            }
        )

        return Response({"code": "acct.verifySuccess"})


class VerifyEmailView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"verify:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        user_id = token_data.get('user_id')
        edu_email = token_data.get('edu_email')

        try:
            user = User.objects.get(id=user_id)
            school, region = resolve_school_from_email(edu_email)
            if school and region:
                from accounts.models import RegionVerification
                RegionVerification.objects.update_or_create(
                    user=user,
                    region=region,
                    defaults={
                        'school': school,
                        'edu_email': edu_email,
                        'verified_at': timezone.now(),
                        'is_active': True
                    }
                )
            cache.delete(f"verify:{token}")
            return Response({"code": "acct.verifySuccess"})
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)


class VerifyRegistrationView(views.APIView):
    """Activates a just-registered account (see RegisterSerializer.create,
    is_active=False) from the link mailed by RegisterView. Unlike
    VerifyEmailView (edu-email binding, done from an already-logged-in
    session), the caller here has no session yet — so on success this
    issues JWTs directly, landing the user logged in."""
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"register-verify:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=token_data.get('user_id'))
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
        cache.delete(f"register-verify:{token}")

        tokens = issue_tokens(user)
        return Response(tokens)


class LoginView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        invalid_credentials = Response({"error": {"code": "auth.errInvalidCredentials"}}, status=status.HTTP_401_UNAUTHORIZED)

        # Case-insensitive: registration only lowercases the domain part
        # (Django's normalize_email), so an account registered as
        # "User@example.com" would otherwise only match an exact-case retype
        # of that same address — nobody expects email lookups to be
        # case-sensitive. .first() rather than .get() so the pre-existing
        # (not newly introduced) possibility of two accounts differing only
        # by case can't turn into a 500.
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            # Run a dummy hash so response timing does not reveal whether the account exists
            User().set_password(password)
            return invalid_credentials

        if not user.check_password(password):
            return invalid_credentials
        if not user.is_active and not user.is_superuser:
            return Response({"error": {"code": "auth.errAccountDisabled"}}, status=status.HTTP_403_FORBIDDEN)

        tokens = issue_tokens(user)
        return Response(tokens)


class RefreshTokenView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh_token'

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            jti = token['jti']
            user_id = token['user_id']

            # Already rotated (concurrent refresh from multiple tabs, or a
            # delayed replay of an old-but-still-within-lifetime token):
            # return the same pair it was rotated into rather than erroring.
            grace_tokens = get_grace_tokens(jti, user_id)
            if grace_tokens:
                return Response(grace_tokens)

            # Whitelist check; revokes the old jti on success. A bare "not
            # found" (cache miss, eviction, dev-server restart) fails just
            # this one refresh — it no longer nukes every other session; see
            # verify_and_revoke_refresh_token's docstring for why that
            # escalation was the actual bug behind frequent unwanted logouts.
            if not verify_and_revoke_refresh_token(jti, user_id):
                return Response({"error": {"code": "auth.errTokenRevoked"}}, status=status.HTTP_401_UNAUTHORIZED)

            user = User.objects.get(id=user_id)
            if not user.is_active and not user.is_superuser:
                return Response({"error": {"code": "auth.errAccountDisabled"}}, status=status.HTTP_403_FORBIDDEN)

            tokens = issue_tokens(user)
            store_grace_tokens(jti, user_id, tokens)
            return Response(tokens)
        except (TokenError, User.DoesNotExist):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_401_UNAUTHORIZED)


class GoogleLoginView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        credential = request.data.get('credential')
        if not credential:
            return Response({"error": {"code": "auth.errMissingCredential"}}, status=status.HTTP_400_BAD_REQUEST)

        from allauth.socialaccount.adapter import get_adapter
        from allauth.socialaccount.providers.google.provider import GoogleProvider
        from allauth.socialaccount.providers.google.views import _verify_and_decode
        from allauth.socialaccount.models import SocialAccount

        try:
            provider = get_adapter().get_provider(request, GoogleProvider.id)
            app = provider.app
        except Exception:
            return Response({"error": {"code": "auth.errProviderNotConfigured"}}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            # allauth will securely verify the ID token signature, issuer, and audience (client_id)
            # We monkey-patch PyJWT momentarily to add 5 seconds of leeway for clock skew (iat)
            import jwt
            _original_jwt_decode = jwt.decode

            def _jwt_decode_with_leeway(*args, **kwargs):
                kwargs.setdefault('leeway', 5)
                return _original_jwt_decode(*args, **kwargs)

            jwt.decode = _jwt_decode_with_leeway
            try:
                idinfo = _verify_and_decode(app, credential, verify_signature=True)
            finally:
                jwt.decode = _original_jwt_decode

            email = idinfo.get('email')
            if not email:
                return Response({"error": {"code": "auth.errNoEmail"}}, status=status.HTTP_400_BAD_REQUEST)

            uid = idinfo.get('sub')
            first_name = idinfo.get('given_name', '')
            last_name = idinfo.get('family_name', '')

            avatar_url = idinfo.get('picture', '')

            # Find or create user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'avatar_url': avatar_url,
                }
            )
            if not created and avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
                user.save(update_fields=['avatar_url'])
            if created:
                user.set_unusable_password()
                user.save()

            # Block login for disabled accounts (except superusers).
            # No auto-reactivation: once an admin disables an account,
            # Google login alone does not bypass the block.
            if not user.is_active and not user.is_superuser:
                return Response(
                    {"error": {"code": "auth.errAccountDisabled"}},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Automatically bind and verify edu email if the Google email is a supported .edu.tw address
            if _is_valid_edu_email(email):
                school, region = resolve_school_from_email(email)
                if school and region:
                    from accounts.models import RegionVerification
                    RegionVerification.objects.update_or_create(
                        user=user,
                        region=region,
                        defaults={
                            'school': school,
                            'edu_email': email,
                            'verified_at': timezone.now(),
                            'is_active': True
                        }
                    )

            # Link with SocialAccount
            SocialAccount.objects.get_or_create(
                user=user,
                provider=GoogleProvider.id,
                uid=uid,
                defaults={
                    'extra_data': idinfo
                }
            )

            # Issued the same way as password login, so the refresh token
            # is recorded in the JWT whitelist (jwt:rt:*/jwt:user:*) — a
            # token minted directly via RefreshToken.for_user() would never
            # be in that whitelist, making it both unrefreshable (rotation
            # checks the whitelist) and invisible to revoke_all_tokens_for_user.
            tokens = issue_tokens(user)
            from core.region import get_region
            region = get_region(request)
            is_verified = user.region_verifications.verified_in(region).exists() if region else False

            return Response({
                "access": tokens['access'],
                "refresh": tokens['refresh'],
                "user_id": user.id,
                "user": {
                    "is_verified": is_verified
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Google token verification failed: {e}", exc_info=True)
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_401_UNAUTHORIZED)


class LogoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        all_devices = request.data.get('all_devices', False)

        user_id = request.user.id

        if all_devices:
            revoke_all_tokens_for_user(user_id)
        elif refresh_token:
            try:
                token = RefreshToken(refresh_token)
                jti = token['jti']
                # The whitelist stores/compares user_id as a string (matching
                # simplejwt's own str(user.id) claim, see issue_tokens) —
                # request.user.id is a real int, so it must be cast here or
                # this never matches and the token silently stays whitelisted
                # despite the user asking to log out.
                verify_and_revoke_refresh_token(jti, str(user_id))
            except TokenError:
                pass  # Ignore invalid tokens; we are logging out anyway

        return Response({"code": "auth.logoutSuccess"}, status=status.HTTP_200_OK)


class AuthConfigView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings
        return Response({
            "google_client_id": getattr(settings, "GOOGLE_CLIENT_ID", "")
        })


class ChangePasswordView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_change'

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")

        if not new_password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        if user.has_usable_password():
            if not old_password:
                return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
            if not user.check_password(old_password):
                return Response({"old_password": [_("Incorrect password.")]}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()
        return Response({"code": "acct.passwordUpdated"})


class RemovePasswordView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_change'

    def post(self, request):
        user = request.user
        password = request.data.get("password")

        if not password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        # (a) Account without Google link cannot remove password
        if not user.socialaccount_set.filter(provider='google').exists():
            return Response({"error": {"code": "auth.errNoGoogleLinked"}}, status=status.HTTP_400_BAD_REQUEST)

        # (b) Staff or superuser cannot remove password because they need it for Django admin
        if user.is_staff or user.is_superuser:
            return Response({"error": {"code": "auth.errStaffCannotRemovePassword"}}, status=status.HTTP_400_BAD_REQUEST)

        if not user.has_usable_password():
            return Response({"error": {"code": "auth.errNoUsablePassword"}}, status=status.HTTP_400_BAD_REQUEST)

        if not user.check_password(password):
            return Response({"error": {"code": "auth.errInvalidPassword"}}, status=status.HTTP_400_BAD_REQUEST)

        user.set_unusable_password()
        user.save(update_fields=['password'])
        
        # We deliberately do not call revoke_all_tokens_for_user() here.
        # Removing a password from a Google-linked account is a user-initiated
        # cleanup, not a compromised account scenario. Forcing a logout across
        # all devices would be disruptive and unnecessary.
        
        return Response({"code": "acct.passwordRemoved"})


class RequestPasswordResetView(views.APIView):
    """Logged-out "forgot password" entry point: emails a reset link if the
    address belongs to an account. Always returns the same success response
    regardless of whether the email exists, so this can't be used to probe
    which addresses are registered."""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password-reset-request'

    def post(self, request):
        email = request.data.get('email')
        if not email or not isinstance(email, str):
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
        email = email.strip()

        # Case-insensitive, like LoginView — registration only lowercases the
        # domain part, so an account registered as "User@example.com" would
        # otherwise silently fail to match a plain-lowercase retype here and
        # this view would (by design, to avoid leaking which emails are
        # registered) return the same generic success response with no email
        # ever sent. That mismatch, not a Mailjet/deliverability problem, was
        # the actual bug behind "forgot password" appearing to do nothing.
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response({"code": "acct.passwordResetSent"})

        # A Google-linked account has no usable password to reset — sending
        # a reset link would just confuse someone who should use "Continue
        # with Google" instead. Still returns the generic success response.
        if user.socialaccount_set.filter(provider='google').exists():
            return Response({"code": "acct.passwordResetSent"})

        # Disabled users (is_active=False) should not be able to reset
        # their password, except superusers. Generic response to avoid
        # leaking account status.
        if not user.is_active and not user.is_superuser:
            return Response({"code": "acct.passwordResetSent"})

        reset_token = str(uuid.uuid4())
        cache.set(f"password-reset:{reset_token}", {"user_id": user.id}, timeout=3600)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

        lang = resolve_language(request)
        if lang == 'zh-TW':
            subject = 'CycleUni 密碼重設'
            message = f'請點擊以下連結以重設您的密碼（1 小時內有效）：\n\n{reset_link}\n\n如果您沒有請求重設密碼，請忽略這封信件，您的密碼不會被更動。'
        else:
            subject = 'CycleUni Password Reset'
            message = f'Click the link below to reset your password (valid for 1 hour):\n\n{reset_link}\n\nIf you did not request this, you can ignore this email — your password will not be changed.'

        _send_verification_email(subject, message, user.email, f"password reset for user {user.id}")

        return Response({"code": "acct.passwordResetSent"})


class ConfirmPasswordResetView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)
        if not new_password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"password-reset:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=token_data.get('user_id'))
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)

        # Guard against disabled accounts that may still hold a valid token
        # (e.g., issued before the account was disabled).
        # Superusers always retain the ability to reset.
        if not user.is_active and not user.is_superuser:
            cache.delete(f"password-reset:{token}")
            return Response({"error": {"code": "auth.errAccountDisabled"}}, status=status.HTTP_403_FORBIDDEN)

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"error": {"code": "auth.errValidation", "fields": e.messages}}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save(update_fields=['password'])
        cache.delete(f"password-reset:{token}")

        # A password reset is exactly the moment any existing session might
        # be the compromised credential this reset is meant to fix — don't
        # leave old refresh tokens (any device, any tab) still valid.
        revoke_all_tokens_for_user(user.id)

        return Response({"code": "acct.passwordResetSuccess"})


class UnbindEduEmailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        from core.region import get_region
        region = get_region(request)
        if not region:
            return Response({"error": {"code": "sys.errUnknownRegion"}}, status=status.HTTP_400_BAD_REQUEST)
        
        updated = user.region_verifications.filter(region=region, is_active=True).update(is_active=False)
        if updated == 0:
            return Response({"error": {"code": "acct.errNotVerified"}}, status=status.HTTP_400_BAD_REQUEST)
            
        return Response({"code": "acct.unbindSuccess"})
