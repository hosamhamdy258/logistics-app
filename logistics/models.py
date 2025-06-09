import logging
import uuid
from enum import EnumMeta

from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from .custom_validator import DomainNameValidator, PositiveValueValidator

logger = logging.getLogger(__name__)


UserModel = get_user_model()

LONG_TEXT = 1000
LONG_STRING = 255
MEDIUM_STRING = 100
SHORT_STRING = 30



class Roles(EnumMeta):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class OrderStatus(EnumMeta):
    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    FAILED = "failed"


class ExportStatus(EnumMeta):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Company(models.Model):
    name = models.CharField(verbose_name=_("Company Name"), max_length=LONG_STRING)
    domain = models.CharField(verbose_name=_("Domain"), max_length=MEDIUM_STRING, validators=[DomainNameValidator()])
    is_active = models.BooleanField(verbose_name=_("Is Active ?"), default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(models.functions.Lower("domain"), name="unique_lower_domain", violation_error_message=_("Company with this domain already exists.")),
            models.UniqueConstraint(
                fields=["name", "domain"],
                name="unique_name_domain",
            ),
        ]

    def __str__(self):
        return self.name


class BaseModel(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, verbose_name=_("Company"))

    class Meta:
        abstract = True


class Profile(BaseModel):
    ROLE_CHOICES = (
        (Roles.ADMIN, _("Admin")),
        (Roles.OPERATOR, _("Operator")),
        (Roles.VIEWER, _("Viewer")),
    )
    user = models.OneToOneField(UserModel, on_delete=models.CASCADE, verbose_name=_("User"))
    role = models.CharField(verbose_name=_("Role"), max_length=SHORT_STRING, choices=ROLE_CHOICES, default=Roles.ADMIN)
    is_blocked = models.BooleanField(verbose_name=_("Is Blocked ?"), default=False)
    failed_orders_count = models.PositiveIntegerField(verbose_name=_("Failed Orders Count"), default=0)

    def __str__(self):
        return self.user.email

    def increment_failed_orders(self):
        self.failed_orders_count += 1
        if self.failed_orders_count >= 3:
            self.is_blocked = True
            self.save(update_fields=["is_blocked", "failed_orders_count"])
            return True
        self.save(update_fields=["failed_orders_count"])
        return False


@receiver(post_save, sender=UserModel)
def create_user_profile(sender, instance, created, **kwargs):
    if created and not str(instance.username).startswith("demo_") and not hasattr(instance, "profile"):
        default_company, state = Company.objects.get_or_create(name="main", domain="main.com")
        Profile.objects.create(user=instance, company=default_company, role=Roles.ADMIN)


class Product(BaseModel):
    sku = models.CharField(verbose_name=_("SKU"), max_length=MEDIUM_STRING, unique=True)
    name = models.CharField(verbose_name=_("Name"), max_length=LONG_STRING)
    stock_quantity = models.PositiveIntegerField(verbose_name=_("Stock Quantity"), default=0)
    is_active = models.BooleanField(verbose_name=_("Is Active ?"), default=True)

    def __str__(self):
        return f"{self.name} {self.company.name}"


class Order(BaseModel):
    STATUS_CHOICES = (
        (OrderStatus.PENDING, _("Pending")),
        (OrderStatus.PROCESSING, _("Processing")),
        (OrderStatus.APPROVED, _("Approved")),
        (OrderStatus.FAILED, _("Failed")),
    )
    reference_code = models.UUIDField(verbose_name=_("Reference Code"), default=uuid.uuid4, editable=False, unique=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name=_("Product"))
    quantity = models.PositiveIntegerField(verbose_name=_("Quantity"), validators=[PositiveValueValidator()])
    status = models.CharField(verbose_name=_("Status"), max_length=SHORT_STRING, choices=STATUS_CHOICES, default=OrderStatus.PENDING)
    created_by = models.ForeignKey(Profile, on_delete=models.CASCADE, verbose_name=_("Created By"))
    has_been_processed = models.BooleanField(verbose_name=_("Has Been Processed ?"), default=False)
    created_at = models.DateTimeField(verbose_name=_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(verbose_name=_("Updated At"), auto_now=True)

    def __str__(self):
        return str(self.reference_code)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if self.company_id == None:
            self.company = self.product.company
        return super().save(force_insert, force_update, using, update_fields)


@receiver(post_save, sender=Order)
def handle_order_status_change(sender, instance, created, **kwargs):
    if instance.status == OrderStatus.FAILED and instance.has_been_processed == True:
        was_blocked = instance.created_by.increment_failed_orders()
        if was_blocked:
            logger.info(f"Profile {instance.created_by} has been blocked due to 3 or more failed orders.")


class Export(BaseModel):
    STATUS_CHOICES = (
        (ExportStatus.PENDING, _("Pending")),
        (ExportStatus.READY, _("Ready")),
        (ExportStatus.FAILED, _("Failed")),
    )
    requested_by = models.ForeignKey(Profile, on_delete=models.CASCADE, verbose_name=_("Requested By"))
    created_at = models.DateTimeField(verbose_name=_("Created At"), auto_now_add=True)
    status = models.CharField(verbose_name=_("Status"), max_length=SHORT_STRING, choices=STATUS_CHOICES, default=ExportStatus.PENDING)
    file = models.FileField(verbose_name=_("File"), upload_to="exports/", null=True, blank=True)
    note = models.TextField(verbose_name=_("Note"), max_length=LONG_TEXT, blank=True, null=True)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if self.company_id is None:
            self.company = self.requested_by.company
        return super().save(force_insert, force_update, using, update_fields)
