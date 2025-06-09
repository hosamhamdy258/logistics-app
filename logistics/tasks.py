import csv
import logging
import os
import random
import tempfile
import time

from celery import shared_task
from django.core.files import File
from django.db.models import F
from django.utils.translation import gettext_lazy as _

from .models import Export, ExportStatus, Order, OrderStatus, Product

logger = logging.getLogger(__name__)


@shared_task
def process_order(order_id):
    try:
        order = Order.objects.get(id=order_id)
        logger.info(f"Processing order {order_id}")

        order.status = OrderStatus.PROCESSING
        order.save()

        time.sleep(2)
        logger.info("Simulate external call")
        success = random.random() > 0.5  # random success rate

        if success:
            updated_rows = Product.objects.filter(pk=order.product.pk, stock_quantity__gte=order.quantity).update(stock_quantity=F("stock_quantity") - order.quantity)

            if updated_rows > 0:
                order.status = OrderStatus.APPROVED
                logger.info(f"Order {order_id} approved and stock updated atomically.")
            else:
                order.status = OrderStatus.FAILED
                logger.warning(f"Order {order_id} failed: Insufficient stock.")

        else:
            order.status = OrderStatus.FAILED
            logger.warning(f"Order {order_id} failed: External call simulation reply with failed.")

        order.has_been_processed = True
        order.save()

        logger.info(f"Notification: Order {order_id} status changed to {order.status}")

    except Exception as e:
        logger.error(f"Error processing order {order_id}: {str(e)}")


@shared_task
def generate_export(export_id, order_ids):
    export = Export.objects.get(id=export_id)
    logger.info(f"Generating export {export_id}")
    export.status = ExportStatus.PENDING
    export.save(update_fields=["status"])
    try:
        tmpf = tempfile.NamedTemporaryFile(mode="w+", newline="", delete=False)
        writer = csv.writer(tmpf)

        writer.writerow([_("Reference Code"), _("Product SKU"), _("Quantity"), _("Status"), _("Created By")])

        qs = Order.objects.filter(id__in=order_ids).select_related("product", "created_by__user").iterator()

        for order in qs:
            writer.writerow([order.reference_code, order.product.sku, order.quantity, order.status, order.created_by.user.username])

        tmpf.flush()
        tmpf.seek(0)
        file_name = f"export_{str(time.time_ns())[:5]}{export_id}.csv"
        export.file.save(file_name, File(tmpf), save=False)

        tmpf.close()
        os.unlink(tmpf.name)

        export.status = ExportStatus.READY
        export.save(update_fields=["file", "status"])
        logger.info(f"Successfully generated export {export_id}")
    except Exception as e:
        export.status = OrderStatus.FAILED
        export.note = str(e)
        export.save(update_fields=["note", "status"])
        logger.exception(f"Error generating export {export_id}")
