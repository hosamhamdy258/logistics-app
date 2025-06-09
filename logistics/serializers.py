from django.db.models import F
from rest_framework import serializers

from .models import Export, Order, Product


class ProductSerializer(serializers.ModelSerializer):
    company = serializers.PrimaryKeyRelatedField(read_only=True, source="company.name")

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "stock_quantity", "is_active", "company"]

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["company"] = request.user.profile.company
        return super().create(validated_data)


class OrderBaseSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(source="created_by.user.username", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(queryset=Product.objects.none(), source="product", write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            self.fields["product_id"].queryset = Product.objects.filter(is_active=True, company=request.user.profile.company)

    class Meta:
        model = Order
        fields = ["id", "reference_code", "product_id", "quantity", "status", "created_at", "updated_at", "created_by", "company_name"]
        read_only_fields = ["reference_code", "status", "created_at", "updated_at", "created_by", "company_name"]


class OrderCreateSerializer(OrderBaseSerializer):

    def validate(self, data):
        product = data.get("product")
        quantity = data.get("quantity")
        if product and quantity is not None:
            if product.stock_quantity < quantity:
                raise serializers.ValidationError({"quantity": f"Insufficient stock for '{product.name}'. Requested: {quantity}, Available: {product.stock_quantity}."})

        return data

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["created_by"] = request.user.profile
        validated_data["company"] = request.user.profile.company
        product = validated_data["product"]
        quantity = validated_data["quantity"]
        instance = super().create(validated_data)
        # update product stock quantity
        Product.objects.filter(pk=product.pk).update(stock_quantity=F("stock_quantity") - quantity)
        return instance


class OrderListRetrieveSerializer(OrderBaseSerializer):
    pass


class ExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Export
        fields = ["id"]
