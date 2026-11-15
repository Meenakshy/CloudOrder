import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from cloudorder.orders.domain import InvalidOrderTransition, OrderStatus
from cloudorder.orders.infrastructure.repository import DjangoOrderRepository
from cloudorder.orders.models import Order, OutboxEvent
from workers import lambda_handlers, outbox_publisher


pytestmark = pytest.mark.django_db


class FakeOutboxRepository:
    def __init__(self, event):
        self.event = event

    def pending_outbox_ids(self, limit):
        return [self.event.id]

    def publish_outbox_event(self, event_id, callback):
        callback(self.event)
        return True


class FakeEventBridge:
    def __init__(self):
        self.entries = []

    def put_events(self, Entries):
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{}]}


def test_outbox_publisher_sends_order_event(monkeypatch):
    event = SimpleNamespace(
        id=1,
        event_type="OrderCreated.v1",
        payload={"order_id": "order-1"},
    )
    bridge = FakeEventBridge()
    monkeypatch.setattr("django.conf.settings.EVENT_BUS_NAME", "cloudorder-test")

    published, failed = outbox_publisher.publish_pending_once(
        eventbridge=bridge,
        repository=FakeOutboxRepository(event),
    )

    assert (published, failed) == (1, 0)
    assert bridge.entries[0]["EventBusName"] == "cloudorder-test"
    assert json.loads(bridge.entries[0]["Detail"])["order_id"] == "order-1"


def test_outbox_publish_failure_increments_persisted_retry_count():
    order = Order.objects.create(customer_id="customer-1", total_amount="1.00")
    event = OutboxEvent.objects.create(
        order=order,
        event_type="OrderCreated.v1",
        payload={"order_id": str(order.id)},
    )

    published = DjangoOrderRepository().publish_outbox_event(
        event.id, lambda current: (_ for _ in ()).throw(RuntimeError("temporary"))
    )
    event.refresh_from_db()

    assert published is False
    assert event.retry_count == 1
    assert event.published_at is None


class FakeTable:
    def __init__(self):
        self.item = None

    def put_item(self, Item, ConditionExpression):
        if self.item is not None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.item = dict(Item)

    def get_item(self, Key, ConsistentRead):
        return {"Item": self.item} if self.item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        self.item["status"] = ExpressionAttributeValues[":started"]
        self.item["execution_arn"] = ExpressionAttributeValues[":arn"]


class FakeStepFunctions:
    def __init__(self):
        self.starts = []

    def start_execution(self, **kwargs):
        self.starts.append(kwargs)
        return {"executionArn": "arn:aws:states:local:123:execution:orders:one"}


def test_starter_deduplicates_repeated_sqs_delivery(monkeypatch):
    table = FakeTable()
    stepfunctions = FakeStepFunctions()
    monkeypatch.setenv("ORDER_STATE_MACHINE_ARN", "arn:aws:states:local:123:stateMachine:orders")
    monkeypatch.setattr(lambda_handlers, "_table", lambda: table)
    monkeypatch.setattr(lambda_handlers, "_client", lambda service: stepfunctions)
    message = {
        "messageId": "sqs-message-1",
        "body": json.dumps({"detail": {"order_id": str(uuid4())}}),
    }

    first = lambda_handlers.starter_handler({"Records": [message]}, None)
    second = lambda_handlers.starter_handler({"Records": [message]}, None)

    assert first["batchItemFailures"] == []
    assert second["batchItemFailures"] == []
    assert len(stepfunctions.starts) == 1
    assert table.item["status"] == "STARTED"


class FakeItems:
    def __init__(self, items):
        self.values = items

    def all(self):
        return self.values


class FakeOrder:
    def __init__(self, status=OrderStatus.PENDING.value, customer_id="customer-1"):
        self.id = uuid4()
        self.status = status
        self.customer_id = customer_id
        self.invoice_s3_key = None
        self.items = FakeItems([SimpleNamespace(product_id="sku-1")])


class FakeOrderRepository:
    def __init__(self, order):
        self.order = order

    def get_order(self, order_id):
        return self.order

    def transition(self, order_id, target_status, expected_status=None, invoice_s3_key=None):
        if expected_status != self.order.status:
            raise InvalidOrderTransition("status changed")
        self.order.status = target_status
        self.order.invoice_s3_key = invoice_s3_key
        return self.order


@pytest.fixture
def workflow_fakes(monkeypatch):
    order = FakeOrder()
    repository = FakeOrderRepository(order)
    services = (
        InvalidOrderTransition,
        OrderStatus,
        lambda: repository,
        LookupError,
        object,
        object,
        object,
    )
    monkeypatch.setattr(lambda_handlers, "_django_services", lambda: services)
    monkeypatch.setattr(lambda_handlers, "_mark_step", lambda *args: None)
    return order


def test_workflow_validation_transition_is_idempotent(workflow_fakes):
    order = workflow_fakes

    first = lambda_handlers.workflow_handler({"action": "validate", "order_id": str(order.id)}, None)
    second = lambda_handlers.workflow_handler({"action": "validate", "order_id": str(order.id)}, None)

    assert first == {"accepted": True}
    assert second == {"accepted": True}
    assert order.status == OrderStatus.PROCESSING.value


def test_inventory_simulation_can_reject_configured_product(monkeypatch, workflow_fakes):
    order = workflow_fakes
    monkeypatch.setenv("SIMULATED_OUT_OF_STOCK_PRODUCT_IDS", "sku-1")

    result = lambda_handlers.workflow_handler(
        {"action": "inventory_check", "order_id": str(order.id)}, None
    )

    assert result == {"available": False, "unavailable_product_ids": ["sku-1"]}


def test_payment_simulation_can_decline_configured_customer(monkeypatch, workflow_fakes):
    order = workflow_fakes
    monkeypatch.setenv("SIMULATED_DECLINED_CUSTOMER_IDS", "customer-1")

    result = lambda_handlers.workflow_handler(
        {"action": "payment_check", "order_id": str(order.id)}, None
    )

    assert result == {"approved": False}


def test_lambda_workflow_services_initialize_without_aws_secret_configuration(monkeypatch):
    monkeypatch.delenv("DB_SECRET_ARN", raising=False)
    monkeypatch.delenv("APP_SECRET_ARN", raising=False)

    services = lambda_handlers._django_services()

    assert services[2] is DjangoOrderRepository
    assert services[3].__name__ == "OrderNotFound"