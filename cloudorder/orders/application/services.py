import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from django.core import signing

from cloudorder.orders.domain import InvalidOrderTransition, OrderStatus
from .ports import NewOrderItem, OrderNotFound, OrderRepository


@dataclass(frozen=True)
class OrderPage:
    orders: list
    next_cursor: str | None


class OrderApplicationService:
    def __init__(self, repository: OrderRepository, invoice_storage=None):
        self.repository = repository
        self.invoice_storage = invoice_storage

    def create_order(self, customer_id: str, raw_items: list[dict]):
        if not customer_id:
            raise ValueError("A customer identity is required.")
        if not raw_items:
            raise ValueError("An order must contain at least one item.")

        items = []
        total = Decimal("0.00")
        for raw_item in raw_items:
            try:
                quantity = int(raw_item["quantity"])
                unit_price = Decimal(str(raw_item["unit_price"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                product_id = str(raw_item["product_id"]).strip()
                product_name = str(raw_item["product_name"]).strip()
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise ValueError("Each item requires valid product, quantity, and price fields.") from error
            if quantity < 1 or not product_id or not product_name or unit_price < 0:
                raise ValueError("Items require a product, positive quantity, and non-negative price.")
            items.append(NewOrderItem(product_id, product_name, quantity, unit_price))
            total += unit_price * quantity

        return self.repository.create_order(
            customer_id=customer_id,
            items=items,
            total_amount=total.quantize(Decimal("0.01")),
        )

    def get_order(self, order_id: UUID, customer_id: str, is_admin: bool = False):
        order = self._get(order_id)
        self._authorize(order, customer_id, is_admin)
        return order

    def cancel_order(self, order_id: UUID, customer_id: str, is_admin: bool = False):
        order = self._get(order_id)
        self._authorize(order, customer_id, is_admin)
        return self.repository.transition(
            order_id,
            OrderStatus.CANCELLED.value,
            expected_status=OrderStatus.PENDING.value,
        )

    def list_orders(
        self,
        customer_id: str,
        limit: int = 20,
        cursor: str | None = None,
        all_customers: bool = False,
    ) -> OrderPage:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100.")
        if all_customers and not customer_id:
            raise PermissionError("An authenticated admin is required.")
        before = self._decode_cursor(cursor) if cursor else None
        rows = self.repository.list_orders(
            customer_id=None if all_customers else customer_id,
            before=before,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = self._encode_cursor(page[-1]) if has_more and page else None
        return OrderPage(page, next_cursor)

    def invoice_url(self, order_id: UUID, customer_id: str, is_admin: bool = False) -> str:
        order = self.get_order(order_id, customer_id, is_admin)
        if order.status != OrderStatus.COMPLETED.value or not order.invoice_s3_key:
            raise InvalidOrderTransition("An invoice is available only for completed orders.")
        if self.invoice_storage is None:
            raise RuntimeError("Invoice storage is not configured.")
        return self.invoice_storage.create_download_url(order.invoice_s3_key)

    def _get(self, order_id: UUID):
        try:
            return self.repository.get_order(order_id)
        except OrderNotFound:
            raise

    @staticmethod
    def _authorize(order, customer_id: str, is_admin: bool):
        if not is_admin and order.customer_id != customer_id:
            raise PermissionError("You cannot access this order.")

    @staticmethod
    def _encode_cursor(order) -> str:
        value = signing.dumps(
            {"created_at": order.created_at.isoformat(), "id": str(order.id)},
            salt="cloudorder.order.cursor",
        )
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = base64.urlsafe_b64decode(padded.encode()).decode()
            payload = signing.loads(value, salt="cloudorder.order.cursor", max_age=86400)
            return datetime.fromisoformat(payload["created_at"]), UUID(payload["id"])
        except (ValueError, KeyError, TypeError, signing.BadSignature) as error:
            raise ValueError("Cursor is invalid or expired.") from error