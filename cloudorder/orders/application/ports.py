from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID


class OrderNotFound(LookupError):
    pass


@dataclass(frozen=True)
class NewOrderItem:
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderRepository(Protocol):
    def create_order(
        self, customer_id: str, items: list[NewOrderItem], total_amount: Decimal
    ):
        ...

    def get_order(self, order_id: UUID):
        ...

    def list_orders(
        self,
        customer_id: str | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list:
        ...

    def transition(
        self,
        order_id: UUID,
        target_status: str,
        expected_status: str | None = None,
        invoice_s3_key: str | None = None,
    ):
        ...