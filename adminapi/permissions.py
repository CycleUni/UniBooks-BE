from rest_framework import permissions

# Where a region lives on the objects the admin API hands to
# has_object_permission. Every admin view already filters its queryset by
# managed_regions, so in practice get_object() 404s before this runs — this is
# the second lock, for the view that forgets.
_REGION_PATHS = (
    ('region',),                                  # Listing, Order, Category, Ad, School
    ('listing', 'region'),                        # Report
    ('conversation', 'listing', 'region'),        # ChatReport
)


def _object_region(obj):
    """The Region an object belongs to, or None if it doesn't belong to one.

    None is a real answer, not a failure: a User is not scoped to a region
    (they are reachable from several), and neither is an upload.
    """
    from core.models import Region

    if isinstance(obj, Region):
        return obj
    for path in _REGION_PATHS:
        current = obj
        for attr in path:
            current = getattr(current, attr, None)
            if current is None:
                break
        if isinstance(current, Region):
            return current
    return None


class IsRegionManager(permissions.BasePermission):
    """A staff user may only act on objects in the regions they manage.

    has_permission stays open on purpose: whether a *list* is in scope is a
    question about its rows, and each view answers it by filtering the
    queryset on managed_regions. This class used to return True from
    has_object_permission as well, which made the name a promise it never
    kept — every guarantee rested on those querysets alone.
    """

    def has_permission(self, request, view):
        # IsAdminUser, which every view pairs this with, is what checks is_staff.
        return True

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        region = _object_region(obj)
        if region is None:
            return True
        return request.user.managed_regions.filter(pk=region.pk).exists()
