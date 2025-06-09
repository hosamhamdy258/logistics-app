import os

import django
from django.contrib.auth import get_user_model

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType

# Import models after setup
from logistics.models import Company, Export, Order, Product, Profile

COMPANIES = ["seaport", "airport"]
DOMAIN = ".net"
ROLES = ["admin", "operator", "viewer"]
PRODUCTS = ["small package", "large package"]
ORDER_STATUS = ["pending", "processing", "approved", "failed"]

User = get_user_model()


def create_demo_data():

    print("Deleting old demo data...")
    Export.objects.all().delete()
    Order.objects.all().delete()
    Product.objects.all().delete()
    Profile.objects.all().delete()
    Company.objects.all().delete()
    User.objects.filter(email__startswith="demo_").delete()

    if User.objects.filter(username="admin").exists():
        superuser = User.objects.get(username="admin")
    else:
        superuser = User.objects.create_superuser(username="admin", email="admin@admin.com", password="admin", first_name="admin", last_name="admin")
        
    default_company, state = Company.objects.get_or_create(name="main", domain="main.com")
    if not hasattr(superuser,"profile"):
        Profile.objects.create(user=superuser, company=default_company, role="admin")

    companies = []
    for name in COMPANIES:
        company = Company.objects.create(name=name, domain=f"{name}{DOMAIN}")
        companies.append(company)
        print(f"Created company: {company.name} ({company.domain})")

    group_name = "Profile Group"
    group, created = Group.objects.get_or_create(name=group_name)
    models = [Company, Profile, Product, Order, Export]
    content_types = [ContentType.objects.get_for_model(model) for model in models]
    permissions = Permission.objects.filter(content_type__in=content_types)
    group.permissions.set(permissions)

    for company in companies:
        profiles = []
        for role in ROLES:
            user_count = 2 if role == "operator" else 1
            for i in range(user_count):
                suffix = f"_{i+1}" if user_count > 1 else ""
                user = User.objects.create_user(
                    username=f"demo_{company.name}_{role}{suffix}",
                    email=f"demo_{company.name}_{role}{suffix}@{company.domain}",
                    password="demo123",
                    is_staff=True,
                )
                user.groups.add(group)
                user.save()

                profile = Profile.objects.create(
                    user=user,
                    company=company,
                    role=role,
                )
                if role == "operator":
                    profiles.append(profile)
                print(f"Created {role} profile: {user.email}")

        products = []
        for i in range(len(PRODUCTS)):
            product = Product.objects.create(
                sku=f"{company.name}-{i+1}",
                name=f"{PRODUCTS[i]}",
                stock_quantity=100,
                company=company,
            )
            products.append(product)
            print(f"Created product: {product.name} (SKU: {product.sku})")

        orders = []
        for profile in profiles:
            for status in ORDER_STATUS:
                for product in products:
                    order = Order.objects.create(product=product, quantity=5, status=status, created_by=profile, has_been_processed=True if status == "approved" else False)
                    orders.append(order)
                    print(f"Created order: {order.reference_code} for {product.sku}")

    print("\nDemo data created successfully!")


if __name__ == "__main__":
    create_demo_data()
