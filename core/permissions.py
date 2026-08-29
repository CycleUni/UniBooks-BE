from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

class IsVerifiedInRegionError(PermissionDenied):
    def __init__(self, region_code):
        detail = {
            "error": {
                "code": "acct.errUnverifiedInRegion",
                "region": region_code
            }
        }
        super().__init__(detail)

class IsVerifiedInRegion(permissions.BasePermission):
    """
    Checks if the user is verified in the target region.
    The region is determined by the object being interacted with.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
            
        if not request.user or not request.user.is_authenticated:
            return False

        target_region = None
        if hasattr(view, 'get_target_region'):
            target_region = view.get_target_region(request)
            
        if target_region and not request.user.is_verified_in(target_region):
            raise IsVerifiedInRegionError(target_region.code)
            
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
            
        if not request.user or not request.user.is_authenticated:
            return False
            
        target_region = None
        if hasattr(obj, 'region'):
            target_region = obj.region
        elif hasattr(obj, 'listing') and hasattr(obj.listing, 'region'):
            target_region = obj.listing.region
        elif hasattr(obj, 'order') and hasattr(obj.order, 'region'):
            target_region = obj.order.region
            
        if target_region and not request.user.is_verified_in(target_region):
            raise IsVerifiedInRegionError(target_region.code)
            
        return True
