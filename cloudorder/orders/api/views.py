from uuid import UUID

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cloudorder.orders.application.ports import OrderNotFound
from cloudorder.orders.domain import InvalidOrderTransition

from .serializers import OrderCreateSerializer, OrderOutputSerializer
from .services import get_order_service


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The order cannot be changed from its current state."
    default_code = "conflict"


class OrderListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", "20"))
            page = get_order_service().list_orders(
                customer_id=request.user.customer_id,
                limit=limit,
                cursor=request.query_params.get("cursor"),
                all_customers=request.user.is_admin,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
        return Response(
            {
                "orders": OrderOutputSerializer(page.orders, many=True).data,
                "next_cursor": page.next_cursor,
            }
        )

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = get_order_service().create_order(
                request.user.customer_id, serializer.validated_data["items"]
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
        return Response(
            OrderOutputSerializer(order).data,
            status=status.HTTP_201_CREATED,
            headers={"Location": f"/api/v1/orders/{order.id}/"},
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id: UUID):
        try:
            order = get_order_service().get_order(
                order_id, request.user.customer_id, request.user.is_admin
            )
        except OrderNotFound as error:
            raise NotFound("Order not found.") from error
        except PermissionError as error:
            raise PermissionDenied(str(error)) from error
        return Response(OrderOutputSerializer(order).data)


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id: UUID):
        try:
            order = get_order_service().cancel_order(
                order_id, request.user.customer_id, request.user.is_admin
            )
        except OrderNotFound as error:
            raise NotFound("Order not found.") from error
        except PermissionError as error:
            raise PermissionDenied(str(error)) from error
        except InvalidOrderTransition as error:
            raise Conflict(str(error)) from error
        return Response(OrderOutputSerializer(order).data)


class OrderInvoiceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id: UUID):
        try:
            url = get_order_service().invoice_url(
                order_id, request.user.customer_id, request.user.is_admin
            )
        except OrderNotFound as error:
            raise NotFound("Order not found.") from error
        except PermissionError as error:
            raise PermissionDenied(str(error)) from error
        except InvalidOrderTransition as error:
            raise Conflict(str(error)) from error
        return Response({"url": url, "expires_in": 300})