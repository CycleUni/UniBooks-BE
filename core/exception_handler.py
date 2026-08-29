"""Keep DRF's built-in errors inside this API's error contract.

Every hand-written failure path in this project answers with
``{"error": {"code": "<i18n key>"}}`` and the frontend translates that code
through its dictionary. DRF's own permission and authentication classes
answer with ``{"detail": "..."}`` instead — an English sentence the frontend
cannot localize and does not know how to read.

That gap opened for real when the per-view ``if not request.user.
is_authenticated`` checks were replaced by ``IsAuthenticated`` /
``IsAuthenticatedOrReadOnly``: the status code stayed 401, so the API tests
(which assert on status alone) stayed green, while the response body silently
stopped carrying ``auth.errNotLoggedIn``. The frontend's i18n orphan-key test
is what caught it — it scans this repo for the codes its dictionary declares.

Only the authentication case is remapped here. Permission denials raised by
this project's own classes already build the contract shape themselves (see
``core.permissions.IsVerifiedInRegionError``), and those are passed through
untouched.
"""

from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    # Already in the contract shape — raised by one of this project's own
    # permission classes, which pass a dict detail. Leave it alone.
    if isinstance(response.data, dict) and 'error' in response.data:
        return response

    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        response.data = {"error": {"code": "auth.errNotLoggedIn"}}

    return response
