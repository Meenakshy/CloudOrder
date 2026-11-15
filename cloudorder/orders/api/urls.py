from django.urls import path

from .views import OrderCancelView, OrderDetailView, OrderInvoiceView, OrderListCreateView


app_name = "orders"
urlpatterns = [
    path("orders/", OrderListCreateView.as_view(), name="order-list-create"),
    path("orders/<uuid:order_id>/", OrderDetailView.as_view(), name="order-detail"),
    path("orders/<uuid:order_id>/cancel/", OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<uuid:order_id>/invoice/", OrderInvoiceView.as_view(), name="order-invoice"),
]