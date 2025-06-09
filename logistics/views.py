import logging

from celery import current_app
from django.conf import settings
from django.db import connection, transaction
from django.http import FileResponse, JsonResponse
from django.utils.translation import gettext_lazy as _
from redis import Redis
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ExportStatus, OrderStatus, Roles
from .permissions import IsCompanyMember
from .serializers import ExportSerializer, OrderCreateSerializer, OrderListRetrieveSerializer, ProductSerializer
from .tasks import process_order

logger = logging.getLogger(__name__)


class CompanyScopedViewSetMixin(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsCompanyMember]

    def get_queryset(self):
        Model = self.get_serializer_class().Meta.model
        if self.request.user.is_superuser:
            return Model.objects.all()
        return Model.objects.filter(company=self.request.user.profile.company)


class ProductViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, CompanyScopedViewSetMixin):
    serializer_class = ProductSerializer


class OrderViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, CompanyScopedViewSetMixin):
    def get_serializer_class(self):
        if self.action in ["create", "bulk_create"]:
            return OrderCreateSerializer
        return OrderListRetrieveSerializer

    def get_queryset(self):
        user = self.request.user
        base_queryset = super().get_queryset()

        if hasattr(user, "profile") and user.profile.role in [Roles.OPERATOR]:
            return base_queryset.filter(created_by=user.profile)
        elif hasattr(user, "profile"):
            return base_queryset
        return base_queryset.none()

    @action(detail=False, methods=["post"], url_path="bulk-create")
    @transaction.atomic
    def bulk_create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        order = self.get_object()
        if order.status == OrderStatus.FAILED and order.has_been_processed == True:
            process_order.delay(order.id)
            return Response({"status": _("Order retry initiated in the background")}, status=status.HTTP_200_OK)
        return Response({"error": _("Only orders with 'failed' status and has been processed can be retried.")}, status=status.HTTP_400_BAD_REQUEST)


class ExportViewSet(mixins.RetrieveModelMixin, CompanyScopedViewSetMixin):
    serializer_class = ExportSerializer

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        export = self.get_object()
        if export.status != ExportStatus.READY:
            return Response({"error": _("Export file not ready for download.")}, status=status.HTTP_400_BAD_REQUEST)
        if not export.file:
            return Response({"error": _("Export file not available.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            response = FileResponse(export.file.open("rb"), as_attachment=True, filename=export.file.name)
            logger.info(f"Successfully Responded export file {export.id}")
            return response
        except FileNotFoundError:
            return Response({"error": _("Export file not found on storage.")}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": _("Could not initiate export download.")}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def health_check(request):
    response_status = {"status": "ok"}
    http_status_code = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as e:
        response_status["database"] = "unavailable"
        response_status["status"] = "offline"
        http_status_code = 503

    try:
        redis_conn = Redis(host="redis", port=settings.REDIS_PORT)
        redis_conn.ping()
    except Exception as e:
        response_status["redis"] = "unavailable"
        response_status["status"] = "offline"
        http_status_code = 503

    try:
        worker_stats = current_app.control.inspect().stats()
        if not worker_stats:
            raise ConnectionError("No Celery workers found or responding.")
    except Exception as e:
        response_status["celery"] = "unavailable"
        response_status["status"] = "offline"
        http_status_code = 503

    return JsonResponse(response_status, status=http_status_code)
