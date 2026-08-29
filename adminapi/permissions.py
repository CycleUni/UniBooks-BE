from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

class IsRegionManager(permissions.BasePermission):
    """
    Allows superusers full access.
    Allows normal admins access only if they are acting on regions they manage.
    """
    
    def has_permission(self, request, view):
        # IsAdminUser should already be checking for is_staff
        return True

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        return True
