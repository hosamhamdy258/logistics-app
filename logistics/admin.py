import logging

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from .forms import CustomAdminLoginForm
from .models import Company, Export, Order, OrderStatus, Product, Profile, Roles
from .tasks import generate_export, process_order

logger = logging.getLogger(__name__)


class CustomAdminSite(admin.AdminSite):
    site_header = _("Logistics Admin")
    login_form = CustomAdminLoginForm


custom_admin_site = CustomAdminSite(name="admin")

admin.site = custom_admin_site


class BaseAdminModel(admin.ModelAdmin):

    def _get_user_company(self, request):
        if not hasattr(request, "_cached_company"):
            try:
                request._cached_company = request.user.profile.company.pk
            except ObjectDoesNotExist:
                request._cached_company = None
                print(f"{request.user} doesn't have a profile")
        return request._cached_company

    def _get_user_role(self, request):
        if not hasattr(request, "_cached_role"):
            try:
                request._cached_role = request.user.profile.role
            except ObjectDoesNotExist:
                request._cached_role = None
                print(f"{request.user} doesn't have a profile")
        return request._cached_role

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        company = self._get_user_company(request)

        if request.user.is_superuser:
            return qs

        if not company:
            return qs.none()

        if self.model == Company:
            return qs.filter(pk=company)

        if hasattr(self.model, "company"):
            return qs.filter(company=company)

        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if request.user.is_superuser:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        company = self._get_user_company(request)
        if db_field.name == "company":
            kwargs["queryset"] = Company.objects.filter(pk=company)
        elif hasattr(db_field.related_model, "company"):
            kwargs["queryset"] = db_field.related_model.objects.filter(company=company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request):
        role = self._get_user_role(request)
        if role == Roles.VIEWER:
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        role = self._get_user_role(request)
        if role == Roles.VIEWER:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        role = self._get_user_role(request)
        if role == Roles.VIEWER:
            return False
        return super().has_delete_permission(request, obj)


class ProfileInline(admin.TabularInline):
    model = Profile
    can_delete = False


class CustomUserAdmin(UserAdmin):
    inlines = [ProfileInline]
    list_filter = ("profile__company", "profile__role")


class ProductAdmin(BaseAdminModel):
    list_display = ["sku", "name", "stock_quantity", "is_active"]
    list_filter = ["is_active", ("company", admin.RelatedOnlyFieldListFilter)]


class OrderAdmin(BaseAdminModel):
    list_display = ["reference_code", "product", "quantity", "status"]
    list_filter = ["status", ("created_by__company", admin.RelatedOnlyFieldListFilter), "has_been_processed"]
    actions = ["approve_orders", "retry_failed_orders", "export_orders", "deactivate_profiles"]
    list_display = ["reference_code", "product", "quantity", "status", "created_by", "has_been_processed"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        role = self._get_user_role(request)

        if request.user.is_superuser:
            return qs

        if not role:
            return qs.none()

        if role == Roles.OPERATOR:
            return qs.filter(created_by=request.user.profile)

        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "product":
            formfield.queryset = formfield.queryset.filter(is_active=True)
        return formfield

    @admin.action(description=_("Approve orders"))
    def approve_orders(self, request, queryset):
        approved_orders = queryset.filter(status=OrderStatus.PENDING)
        if approved_orders.exists():
            for order in approved_orders:
                process_order.delay(order.id)
            self.message_user(request, _(f"Queued Process of {approved_orders.count()} orders. They will update shortly in the background."))
        else:
            self.message_user(request, _(f"Non of selected orders are pending orders."), level="WARNING")

    @admin.action(description=_("Retry failed orders"))
    def retry_failed_orders(self, request, queryset):
        failed_orders = queryset.filter(status=OrderStatus.FAILED, has_been_processed=True)
        if failed_orders.exists():
            for order in failed_orders:
                process_order.delay(order.id)
            self.message_user(request, _(f"Queued Retry Process of {failed_orders.count()} orders. They will update shortly in the background."))
        else:
            self.message_user(request, _(f"Non of selected orders are failed orders and has been processed."), level="WARNING")

    @admin.action(description=_("Export selected orders"))
    def export_orders(self, request, queryset):
        export = Export.objects.create(requested_by=request.user.profile)
        order_ids = list(queryset.values_list("id", flat=True))
        generate_export.delay(export.id, order_ids)
        self.message_user(request, _(f"Export {export.id} started"))

    @admin.action(description=_("Deactivate Profiles with 3+ failed orders"))
    @transaction.atomic
    def deactivate_profiles(self, request, queryset):
        if request.user.is_superuser:
            extra_filter = {}
        else:
            extra_filter = {"company": self._get_user_company(request)}
        profiles_ids = (
            Order.objects.filter(status=OrderStatus.FAILED, **extra_filter)
            .values("created_by")
            .annotate(failed_count=models.Count("created_by"))
            .filter(failed_count__gt=3)
            .values_list("created_by", flat=True)
        )
        updated = Profile.objects.filter(pk__in=profiles_ids).update(is_blocked=True)
        self.message_user(request, _(f"Deactivated {updated} profiles with 3+ failed orders."), level="SUCCESS")


class CompanyAdmin(BaseAdminModel):
    pass


class ProfileAdmin(BaseAdminModel):
    list_display = ("user", "company", "role", "is_blocked", "failed_orders_count")
    list_filter = ("is_blocked", "role", "company")
    readonly_fields = ["failed_orders_count"]

    actions = ["reset_failed_orders_count", "unblock_users"]

    @admin.action(description=_("Reset failed orders counter"))
    @transaction.atomic
    def reset_failed_orders_count(self, request, queryset):
        updated = queryset.update(failed_orders_count=0)
        self.message_user(request, _(f"Reset failed orders counter for {updated} profiles."))

    @admin.action(description=_("Unblock selected users"))
    @transaction.atomic
    def unblock_users(self, request, queryset):
        updated = queryset.update(is_blocked=False)
        self.message_user(request, _(f"Unblocked {updated} users."))


class ExportAdmin(BaseAdminModel):
    pass


admin.site.register(User, CustomUserAdmin)
admin.site.register(Company, CompanyAdmin)
admin.site.register(Profile, ProfileAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(Export, ExportAdmin)
