import uuid

from django.db import models
from django.db.models import Q

from cloudorder.orders.domain import OrderStatus


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.CharField(max_length=128)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value.title()) for status in OrderStatus],
        default=OrderStatus.PENDING,
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    version = models.PositiveBigIntegerField(default=1)
    invoice_s3_key = models.CharField(max_length=512, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        constraints = [models.CheckConstraint(condition=Q(total_amount__gte=0), name="order_total_nonnegative")]
        indexes = [models.Index(fields=["customer_id", "-created_at"], name="orders_customer_created_idx")]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product_id = models.CharField(max_length=128)
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "order_items"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_item_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="order_item_price_nonnegative"),
        ]


class OutboxEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="outbox_events")
    event_type = models.CharField(max_length=128)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "outbox_events"
        indexes = [models.Index(fields=["published_at", "id"], name="outbox_unpublished_idx")]