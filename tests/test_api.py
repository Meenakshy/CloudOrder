import json

import pytest
from rest_framework.test import APIClient

from cloudorder.orders.models import Order, OrderItem, OutboxEvent


pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client(monkeypatch):
    def fake_verify(token):
        customer_id, _, group = token.partition(":")
        groups = [group] if group else []
        return {"sub": customer_id, "cognito:groups": groups}

    monkeypatch.setattr(
        "cloudorder.orders.api.authentication.verify_cognito_token", fake_verify
    )
    return APIClient()


def auth_header(customer_id="customer-1", group="Customer"):
    token = f"{customer_id}:{group}" if group else customer_id
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def order_payload():
    return {
        "items": [
            {
                "product_id": "sku-1",
                "product_name": "Desk lamp",
                "quantity": 2,
                "unit_price": "12.50",
            }
        ]
    }


def test_create_order_persists_items_and_outbox_atomically(api_client):
    response = api_client.post(
        "/api/v1/orders/", order_payload(), format="json", **auth_header()
    )

    assert response.status_code == 201
    assert response.data["status"] == "PENDING"
    assert response.data["total_amount"] == "25.00"
    assert Order.objects.count() == 1
    assert OrderItem.objects.count() == 1
    event = OutboxEvent.objects.get(order_id=response.data["id"])
    assert event.event_type == "OrderCreated.v1"
    assert event.payload["order_id"] == response.data["id"]


def test_order_detail_and_list_are_scoped_to_customer(api_client):
    created = api_client.post(
        "/api/v1/orders/", order_payload(), format="json", **auth_header()
    )
    order_id = created.data["id"]

    forbidden = api_client.get(
        f"/api/v1/orders/{order_id}/", **auth_header("customer-2")
    )
    own_list = api_client.get("/api/v1/orders/", **auth_header())
    other_list = api_client.get("/api/v1/orders/", **auth_header("customer-2"))

    assert forbidden.status_code == 403
    assert len(own_list.data["orders"]) == 1
    assert own_list.data["orders"][0]["id"] == order_id
    assert other_list.data["orders"] == []


def test_cancel_is_pending_only_and_versioned(api_client):
    created = api_client.post(
        "/api/v1/orders/", order_payload(), format="json", **auth_header()
    )
    order_id = created.data["id"]

    cancelled = api_client.post(
        f"/api/v1/orders/{order_id}/cancel/", {}, format="json", **auth_header()
    )
    repeated = api_client.post(
        f"/api/v1/orders/{order_id}/cancel/", {}, format="json", **auth_header()
    )

    assert cancelled.status_code == 200
    assert cancelled.data["status"] == "CANCELLED"
    assert cancelled.data["version"] == 2
    assert repeated.status_code == 409


def test_graphql_all_orders_requires_admin_role(api_client):
    api_client.post("/api/v1/orders/", order_payload(), format="json", **auth_header())
    query = {"query": "{ allOrders { nodes { id } } }"}

    response = api_client.post(
        "/graphql/", data=json.dumps(query), content_type="application/json", **auth_header()
    )

    assert response.status_code == 200
    assert "Admin role is required" in response.json()["errors"][0]["message"]


def test_graphql_admin_can_read_all_orders(api_client):
    api_client.post(
        "/api/v1/orders/", order_payload(), format="json", **auth_header("customer-1")
    )
    query = {"query": "{ allOrders { nodes { id status } } }"}

    response = api_client.post(
        "/graphql/",
        data=json.dumps(query),
        content_type="application/json",
        **auth_header("operator", "Admin"),
    )

    assert response.status_code == 200
    assert response.json()["data"]["allOrders"]["nodes"][0]["status"] == "PENDING"


def test_graphql_rejects_zero_page_size(api_client):
    response = api_client.post(
        "/graphql/",
        data=json.dumps({"query": "{ myOrders(limit: 0) { nodes { id } } }"}),
        content_type="application/json",
        **auth_header(),
    )

    assert "Limit must be between 1 and 100" in response.json()["errors"][0]["message"]