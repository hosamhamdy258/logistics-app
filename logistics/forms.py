from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _


class CustomAdminLoginForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)

        if hasattr(user, "profile") and user.profile.is_blocked:
            raise forms.ValidationError(_("This account is blocked."), code="inactive")
