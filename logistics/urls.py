from django.urls import path

from .views import ExportViewSet, OrderViewSet, ProductViewSet, health_check

urlpatterns = [
    path("orders/", OrderViewSet.as_view({"get": "list", "post": "create"}), name="order-list"),
    path("orders/bulk/", OrderViewSet.as_view({"post": "bulk_create"}), name="order-bulk-create"),
    path("orders/<int:pk>/retry/", OrderViewSet.as_view({"post": "retry"}), name="order-retry"),
    path("products/", ProductViewSet.as_view({"get": "list"}), name="product-list"),
    path("exports/<int:pk>/download/", ExportViewSet.as_view({"get": "download"}), name="export-download"),
    path("health/", health_check, name="health-check"),
]
