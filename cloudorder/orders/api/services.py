from django.conf import settings

from cloudorder.orders.application import OrderApplicationService
from cloudorder.orders.infrastructure.repository import DjangoOrderRepository
from cloudorder.orders.infrastructure.storage import S3InvoiceStorage


def get_order_service() -> OrderApplicationService:
    invoice_storage = (
        S3InvoiceStorage(settings.INVOICE_BUCKET) if settings.INVOICE_BUCKET else None
    )
    return OrderApplicationService(DjangoOrderRepository(), invoice_storage)