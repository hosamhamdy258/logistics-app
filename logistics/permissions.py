from rest_framework.permissions import BasePermission


class IsCompanyMember(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not hasattr(request.user, "profile") or not request.user.profile:
            return False
        if not hasattr(request.user.profile, "company") or not request.user.profile.company:
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False

        user_company = request.user.profile.company

        object_company_id = getattr(obj, "company_id", None)
        if object_company_id is not None:
            return object_company_id == user_company.id

        return False
