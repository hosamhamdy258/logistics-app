import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from logistics.models import Company, Export, Order, OrderStatus, Product, Profile, Roles
from logistics.tasks import generate_export, process_order


class HealthCheckTests(TestCase):
    def test_health_check_ok(self):
        url = reverse("health-check")
        response = self.client.get(url)
        self.assertIn(response.status_code, [200, 503])
        self.assertIn("status", response.json())


class BaseLogisticsAPITestCase(APITestCase):
    def create_user_with_profile(self, username, password, email, company, role, **kwargs):
        user, created = User.objects.get_or_create(username=username, defaults={"email": email, "password": password, **kwargs})

        if created and password:
            user.set_password(password)
            user.save()

        profile, created = Profile.objects.get_or_create(user=user, defaults={"company": company, "role": role, "failed_orders_count": kwargs.get("failed_orders_count", 0)})

        if not created:
            profile.company = company
            profile.role = role
            if "failed_orders_count" in kwargs:
                profile.failed_orders_count = kwargs["failed_orders_count"]
            profile.save()

        token, _ = Token.objects.get_or_create(user=user)

        return user, profile, token.key

    def setUp(self):
        self.company_a = Company.objects.create(name="CompanyA", domain="companya.com")
        self.company_b = Company.objects.create(name="CompanyB", domain="companyb.com")

        self.admin_a, self.admin_a_profile, self.admin_a_token = self.create_user_with_profile("admin_a", "pass", "admin_a@a.com", self.company_a, "admin")
        self.operator_a, self.operator_a_profile, self.operator_a_token = self.create_user_with_profile("operator_a", "pass", "operator_a@a.com", self.company_a, "operator")
        self.viewer_a, self.viewer_a_profile, self.viewer_a_token = self.create_user_with_profile("viewer_a", "pass", "viewer_a@a.com", self.company_a, "viewer")
        self.admin_a_blocked, self.admin_a_profile_blocked, self.admin_a_token_blocked = self.create_user_with_profile("admin_a_blocked", "pass", "admin_a_blocked@a.com", self.company_a, "admin")
        self.admin_a_profile_blocked.is_blocked = True
        self.admin_a_profile_blocked.save()

        self.admin_b, self.admin_b_profile, self.admin_b_token = self.create_user_with_profile("admin_b", "pass", "admin_b@b.com", self.company_b, "admin")
        self.operator_b, self.operator_b_profile, self.operator_b_token = self.create_user_with_profile("operator_b", "pass", "operator_b@b.com", self.company_b, "operator")

        self.product_a1 = Product.objects.create(company=self.company_a, sku="A1", name="Product A1", stock_quantity=10, is_active=True)
        self.product_a2 = Product.objects.create(company=self.company_a, sku="A2", name="Product A2", stock_quantity=5, is_active=True)
        self.product_b1 = Product.objects.create(company=self.company_b, sku="B1", name="Product B1", stock_quantity=20, is_active=True)

        self.order_a1 = Order.objects.create(company=self.company_a, product=self.product_a1, quantity=2, created_by=self.operator_a_profile, status="pending")
        self.order_b1 = Order.objects.create(company=self.company_b, product=self.product_b1, quantity=3, created_by=self.operator_b_profile, status="pending")

        self.superuser = User.objects.create_superuser(username="superuser", password="pass", email="super@root.com")
        self.superuser_token = Token.objects.create(user=self.superuser)

    def test_blocked_user_cannot_login(self):
        self.assertTrue(self.admin_a_profile_blocked.is_blocked)
        url = reverse("api-token-auth")
        response = self.client.post(url, {"username": "admin_a_blocked", "password": "pass"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("non_field_errors", response.data)
        self.assertIn("This account is blocked.", str(response.data))

    def test_blocked_user_cannot_login_to_admin(self):
        self.assertTrue(self.admin_a_profile_blocked.is_blocked)
        login_url = reverse("admin:login")
        response = self.client.post(login_url, {"username": "admin_a_blocked", "password": "pass", "next": "/admin/"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("site_title", response.context)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertTrue(any("blocked" in str(error) for error in form.non_field_errors()))


class ProductAPITests(BaseLogisticsAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("product-list")

    def test_list_products_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_products_as_admin_company_a(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_a_token}")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data]
        self.assertIn(self.product_a1.name, names)
        self.assertIn(self.product_a2.name, names)
        self.assertNotIn(self.product_b1.name, names)

    def test_list_products_as_superuser(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.superuser_token}")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data]
        self.assertIn(self.product_a1.name, names)
        self.assertIn(self.product_a2.name, names)
        self.assertIn(self.product_b1.name, names)


class OrderAPITests(BaseLogisticsAPITestCase):
    def setUp(self):
        super().setUp()
        self.orders_url = reverse("order-list")
        self.bulk_url = reverse("order-bulk-create")

    def test_create_order_as_operator(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.operator_a_token}")
        payload = {"product_id": self.product_a1.id, "quantity": 3}
        response = self.client.post(self.orders_url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["quantity"], 3)
        self.assertEqual(response.data["status"], "pending")
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.product.id, self.product_a1.id)
        self.assertEqual(order.created_by.id, self.operator_a_profile.id)

    def test_create_order_insufficient_stock(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.operator_a_token}")
        data = {"product_id": self.product_a1.id, "quantity": 100}
        response = self.client.post(self.orders_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", str(response.data))

    def test_operator_cannot_see_other_company_orders(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.operator_a_token}")
        response = self.client.get(self.orders_url)
        for order in response.data:
            self.assertEqual(order["company_name"], self.company_a.name)

    def test_admin_can_see_all_company_orders(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_a_token}")
        response = self.client.get(self.orders_url)
        company_names = set(order["company_name"] for order in response.data)
        self.assertEqual(company_names, {self.company_a.name})

    def test_superuser_can_see_all_company_orders(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.superuser_token.key}")
        response = self.client.get(self.orders_url)
        company_names = set(order["company_name"] for order in response.data)
        self.assertEqual(company_names, {self.company_a.name, self.company_b.name})

    def test_bulk_create_orders(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.operator_a_token}")
        payload = [{"product_id": self.product_a1.id, "quantity": 2}, {"product_id": self.product_a2.id, "quantity": 1}]
        response = self.client.post(self.bulk_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 2)
        for data in response.data:
            self.assertEqual(data["status"], "pending")

    def test_retry_failed_order(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_a_token}")
        failed_order = Order.objects.create(company=self.company_a, product=self.product_a1, quantity=1, created_by=self.operator_a_profile, status="failed", has_been_processed=True)
        retry_url = reverse("order-retry", kwargs={"pk": failed_order.id})
        response = self.client.post(retry_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Order retry initiated", str(response.data))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CeleryTaskTests(BaseLogisticsAPITestCase):
    def setUp(self):
        super().setUp()
        self.company = self.company_a
        self.user = self.operator_a
        self.profile = self.operator_a_profile
        self.product = Product.objects.create(company=self.company, sku="CELERY1", name="Celery Product", stock_quantity=10)

    @patch("logistics.tasks.time.sleep")
    @patch("logistics.tasks.random.random")
    def test_process_order_success(self, mock_random, mock_sleep):
        mock_random.return_value = 0.9  # Success
        order = Order.objects.create(company=self.company, product=self.product, quantity=2, created_by=self.profile, status="pending")
        process_order.delay(order.id)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "approved")
        self.assertEqual(self.product.stock_quantity, 8)
        self.assertTrue(order.has_been_processed)

    @patch("logistics.tasks.time.sleep")
    @patch("logistics.tasks.random.random")
    def test_process_order_fail_insufficient_stock(self, mock_random, mock_sleep):
        mock_random.return_value = 0.9  # Success
        order = Order.objects.create(company=self.company, product=self.product, quantity=100, created_by=self.profile, status="pending")
        process_order.delay(order.id)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "failed")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertTrue(order.has_been_processed)

    @patch("logistics.tasks.time.sleep")
    @patch("logistics.tasks.random.random")
    def test_process_order_fail_external(self, mock_random, mock_sleep):
        mock_random.return_value = 0.1  # Fail
        order = Order.objects.create(company=self.company, product=self.product, quantity=1, created_by=self.profile, status="pending")
        process_order.delay(order.id)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "failed")
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertTrue(order.has_been_processed)

    @patch("logistics.tasks.time.sleep")
    @patch("logistics.tasks.random.random")
    def test_generate_export_success(self, mock_random, mock_sleep):
        export = Export.objects.create(company=self.company, requested_by=self.profile, status="pending")
        order1 = Order.objects.create(company=self.company, product=self.product, quantity=2, created_by=self.profile, status="approved")
        order2 = Order.objects.create(company=self.company, product=self.product, quantity=1, created_by=self.profile, status="approved")
        generate_export.delay(export.id, [order1.id, order2.id])
        export.refresh_from_db()
        self.assertEqual(export.status, "ready")
        self.assertTrue(export.file.name.endswith(".csv"))


class ExportAPITests(BaseLogisticsAPITestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_a_token}")
        self.export = Export.objects.create(company=self.company_a, requested_by=self.admin_a_profile, status="ready")
        self.url = reverse("export-download", kwargs={"pk": self.export.id})
        # Export for company B
        self.export_b = Export.objects.create(company=self.company_b, requested_by=self.admin_b_profile, status="ready")
        self.url_b = reverse("export-download", kwargs={"pk": self.export_b.id})

    def test_operator_cannot_access_other_company_export(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.operator_a_token}")
        response = self.client.get(self.url_b)
        self.assertIn(response.status_code, [403, 404])

    def test_export_download_ready(self):
        self.export.file.save("test.csv", ContentFile("id,product,quantity\n1,Product,2\n"))
        self.export.status = "ready"
        self.export.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

    def test_export_download_not_ready(self):
        self.export.file.save("test.csv", ContentFile("id,product,quantity\n1,Product,2\n"))
        self.export.status = "pending"
        self.export.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not ready", str(response.data))

    def test_export_download_not_found(self):
        self.export.file.delete(save=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)


class BaseProfileBlockingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Test Company", domain="test.com")
        cls.product = Product.objects.create(company=cls.company, sku="TEST-SKU", name="Test Product", stock_quantity=100, is_active=True)

    def create_failed_orders(self, profile, count, has_been_processed=True):
        for i in range(count):
            order = Order.objects.create(company=self.company, product=self.product, quantity=1, status=OrderStatus.PENDING, created_by=profile, has_been_processed=has_been_processed)
            order.status = OrderStatus.FAILED
            order.save()


class AutomaticProfileBlockingTests(BaseProfileBlockingTest):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.profile, _ = Profile.objects.get_or_create(user=self.user, defaults={"company": self.company, "role": Roles.OPERATOR})

    def test_auto_block_after_three_failed_orders(self):
        self.create_failed_orders(self.profile, 2)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_orders_count, 2)
        self.assertFalse(self.profile.is_blocked)

        self.create_failed_orders(self.profile, 1)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_orders_count, 3)
        self.assertTrue(self.profile.is_blocked)

    def test_unprocessed_failed_orders_dont_trigger_blocking(self):
        self.create_failed_orders(self.profile, 3, has_been_processed=False)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_orders_count, 0)
        self.assertFalse(self.profile.is_blocked)


class AdminActionTests(BaseProfileBlockingTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
        cls.admin_profile, _ = Profile.objects.get_or_create(user=cls.admin_user, defaults={"company": cls.company, "role": Roles.ADMIN})
        cls.order1 = Order.objects.create(product=cls.product, company=cls.company, quantity=1, created_by=cls.admin_profile, status=OrderStatus.PENDING)
        cls.order2 = Order.objects.create(product=cls.product, company=cls.company, quantity=2, created_by=cls.admin_profile, status=OrderStatus.PENDING)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_approve_orders_action(self):
        url = "/admin/logistics/order/"
        self.client.post(url, {"action": "approve_orders", "_selected_action": [self.order1.pk, self.order2.pk]}, follow=True)

        process_order(self.order1.id)
        process_order(self.order2.id)

        self.order1.refresh_from_db()
        self.order2.refresh_from_db()
        self.assertIn(self.order1.status, ["approved", "failed"])
        self.assertIn(self.order2.status, ["approved", "failed"])

    def test_deactivate_profiles_action(self):
        test_user = User.objects.create_user(username=f"testuser_deactivate_{time.time()}", password="testpass123")
        profile, _ = Profile.objects.get_or_create(user=test_user, defaults={"company": self.company, "role": Roles.OPERATOR, "failed_orders_count": 0, "is_blocked": False})

        self.create_failed_orders(profile, 3)
        response = self.client.post("/admin/logistics/order/", {"action": "deactivate_profiles", "_selected_action": [self.order1.pk]}, follow=True)

        profile.refresh_from_db()
        self.assertTrue(profile.is_blocked)
        self.assertEqual(response.status_code, 200)
