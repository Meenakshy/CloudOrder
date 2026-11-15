from decimal import Decimal
from uuid import UUID

import strawberry
from strawberry.types import Info

from cloudorder.orders.api.authentication import principal_from_token
from cloudorder.orders.api.services import get_order_service
from cloudorder.orders.application.ports import OrderNotFound


@strawberry.type
class OrderItemType:
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal


@strawberry.type
class OrderType:
    id: strawberry.ID
    customer_id: str
    status: str
    total_amount: Decimal
    version: int
    created_at: str
    updated_at: str
    items: list[OrderItemType]


@strawberry.type
class OrderConnection:
    nodes: list[OrderType]
    next_cursor: str | None


def _order_type(order) -> OrderType:
    return OrderType(
        id=strawberry.ID(str(order.id)),
        customer_id=order.customer_id,
        status=order.status,
        total_amount=order.total_amount,
        version=order.version,
        created_at=order.created_at.isoformat(),
        updated_at=order.updated_at.isoformat(),
        items=[
            OrderItemType(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in order.items.all()
        ],
    )


def _principal(info):
    request = info.context.request
    header = request.headers.get("Authorization", "")
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise PermissionError("Authentication is required.")
    return principal_from_token(token)


@strawberry.type
class Query:
    @strawberry.field
    def order(self, info: Info, id: strawberry.ID) -> OrderType:
        principal = _principal(info)
        try:
            order = get_order_service().get_order(
                UUID(str(id)), principal.customer_id, principal.is_admin
            )
        except OrderNotFound as error:
            raise ValueError("Order not found.") from error
        return _order_type(order)

    @strawberry.field
    def my_orders(
        self, info: Info, limit: int | None = 20, cursor: str | None = None
    ) -> OrderConnection:
        principal = _principal(info)
        page = get_order_service().list_orders(
            customer_id=principal.customer_id,
            limit=20 if limit is None else limit,
            cursor=cursor,
        )
        return OrderConnection(
            nodes=[_order_type(order) for order in page.orders],
            next_cursor=page.next_cursor,
        )

    @strawberry.field
    def all_orders(
        self, info: Info, limit: int | None = 20, cursor: str | None = None
    ) -> OrderConnection:
        principal = _principal(info)
        if not principal.is_admin:
            raise PermissionError("Admin role is required.")
        page = get_order_service().list_orders(
            customer_id=principal.customer_id,
            limit=20 if limit is None else limit,
            cursor=cursor,
            all_customers=True,
        )
        return OrderConnection(
            nodes=[_order_type(order) for order in page.orders],
            next_cursor=page.next_cursor,
        )


schema = strawberry.Schema(query=Query)