from datetime import datetime
from decimal import Decimal
import logging
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cloudorder.orders.application.ports import OrderNotFound
from cloudorder.orders.domain import InvalidOrderTransition, OrderStatus, allowed_transition
from cloudorder.orders.models import Order, OrderItem, OutboxEvent


logger = logging.getLogger(__name__)


class DjangoOrderRepository:
    def create_order(self, customer_id, items, total_amount: Decimal) -> Order:
        with transaction.atomic():
            order = Order.objects.create(
                customer_id=customer_id,
                status=OrderStatus.PENDING.value,
                total_amount=total_amount,
            )
            OrderItem.objects.bulk_create(
                [
                    OrderItem(
                        order=order,
                        product_id=item.product_id,
                        product_name=item.product_name,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                    )
                    for item in items
                ]
            )
            OutboxEvent.objects.create(
                order=order,
                event_type="OrderCreated.v1",
                payload={
                    "order_id": str(order.id),
                    "customer_id": customer_id,
                    "items": [
                        {
                            "product_id": item.product_id,
                            "product_name": item.product_name,
                            "quantity": item.quantity,
                            "unit_price": str(item.unit_price),
                        }
                        for item in items
                    ],
                    "total_amount": str(total_amount),
                },
            )
        return order

    def get_order(self, order_id: UUID) -> Order:
        try:
            return Order.objects.prefetch_related("items").get(id=order_id)
        except Order.DoesNotExist as error:
            raise OrderNotFound(str(order_id)) from error

    def list_orders(
        self,
        customer_id: str | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Order]:
        queryset = Order.objects.prefetch_related("items")
        if customer_id is not None:
            queryset = queryset.filter(customer_id=customer_id)
        if before:
            timestamp, order_id = before
            queryset = queryset.filter(
                Q(created_at__lt=timestamp) | Q(created_at=timestamp, id__lt=order_id)
            )
        return list(queryset.order_by("-created_at", "-id")[:limit])

    def transition(
        self,
        order_id: UUID,
        target_status: str,
        expected_status: str | None = None,
        invoice_s3_key: str | None = None,
    ) -> Order:
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(id=order_id)
            except Order.DoesNotExist as error:
                raise OrderNotFound(str(order_id)) from error
            if expected_status is not None and order.status != expected_status:
                raise InvalidOrderTransition(
                    f"Expected {expected_status}, found {order.status}."
                )
            if not allowed_transition(order.status, target_status):
                raise InvalidOrderTransition(
                    f"Cannot change order from {order.status} to {target_status}."
                )
            order.status = target_status
            order.version += 1
            update_fields = ["status", "version", "updated_at"]
            if invoice_s3_key is not None:
                order.invoice_s3_key = invoice_s3_key
                update_fields.append("invoice_s3_key")
            order.save(update_fields=update_fields)
            return order

    @transaction.atomic
    def publish_outbox_event(self, event_id: int, publish):
        try:
            event = OutboxEvent.objects.select_for_update().get(
                id=event_id, published_at__isnull=True
            )
        except OutboxEvent.DoesNotExist:
            return False
        try:
            publish(event)
        except Exception:
            event.retry_count += 1
            event.save(update_fields=["retry_count"])
            logger.exception("Outbox event %s failed to publish", event_id)
            return False
        event.published_at = timezone.now()
        event.save(update_fields=["published_at"])
        return True

    def pending_outbox_ids(self, limit: int = 100) -> list[int]:
        return list(
            OutboxEvent.objects.filter(published_at__isnull=True)
            .order_by("id")
            .values_list("id", flat=True)[:limit]
        )