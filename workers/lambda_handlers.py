import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


def _client(service: str):
    return boto3.client(
        service,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
    )


def _table():
    return boto3.resource(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
    ).Table(os.environ["DYNAMODB_ORDER_PROCESSING_TABLE"])


def _load_runtime_secrets() -> None:
    database_secret_arn = os.getenv("DB_SECRET_ARN")
    application_secret_arn = os.getenv("APP_SECRET_ARN")
    if not database_secret_arn and not application_secret_arn:
        return
    secretsmanager = _client("secretsmanager")
    if database_secret_arn:
        database_secret = json.loads(
            secretsmanager.get_secret_value(SecretId=database_secret_arn)["SecretString"]
        )
        os.environ.setdefault("DB_USER", database_secret["username"])
        os.environ.setdefault("DB_PASSWORD", database_secret["password"])
    if application_secret_arn:
        application_secret = secretsmanager.get_secret_value(
            SecretId=application_secret_arn
        )["SecretString"]
        os.environ.setdefault("DJANGO_SECRET_KEY", application_secret)


def _event_detail(record: dict) -> dict:
    body = json.loads(record["body"])
    return body.get("detail", body)


def starter_handler(event, context):
    state_machine_arn = os.environ["ORDER_STATE_MACHINE_ARN"]
    table = _table()
    stepfunctions = _client("stepfunctions")
    failures = []

    for record in event.get("Records", []):
        try:
            detail = _event_detail(record)
            order_id = str(detail["order_id"])
            now = datetime.now(timezone.utc).isoformat()
            try:
                table.put_item(
                    Item={
                        "order_id": order_id,
                        "record_type": "workflow",
                        "status": "STARTING",
                        "created_at": now,
                        "updated_at": now,
                    },
                    ConditionExpression="attribute_not_exists(order_id)",
                )
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                existing = table.get_item(
                    Key={"order_id": order_id, "record_type": "workflow"},
                    ConsistentRead=True,
                ).get("Item", {})
                if existing.get("status") == "STARTED":
                    continue

            try:
                execution = stepfunctions.start_execution(
                    stateMachineArn=state_machine_arn,
                    name=f"order-{order_id}",
                    input=json.dumps(detail, separators=(",", ":"), default=str),
                )
                execution_arn = execution["executionArn"]
            except ClientError as error:
                if error.response["Error"]["Code"] != "ExecutionAlreadyExists":
                    raise
                execution_arn = "already-started"
            table.update_item(
                Key={"order_id": order_id, "record_type": "workflow"},
                UpdateExpression="SET #status = :started, execution_arn = :arn, updated_at = :now",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":started": "STARTED",
                    ":arn": execution_arn,
                    ":now": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.exception("Unable to start order workflow for SQS message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def _django_services():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cloudorder.config.settings")
    _load_runtime_secrets()
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()

    from cloudorder.orders.domain import InvalidOrderTransition, OrderStatus
    from cloudorder.orders.application.ports import OrderNotFound
    from cloudorder.orders.infrastructure.repository import DjangoOrderRepository
    from cloudorder.orders.infrastructure.storage import S3InvoiceStorage
    from cloudorder.orders.models import Order
    from workers.invoices import render_invoice

    return InvalidOrderTransition, OrderStatus, DjangoOrderRepository, OrderNotFound, S3InvoiceStorage, Order, render_invoice


def _mark_step(order_id: str, action: str, result: dict) -> None:
    _table().update_item(
        Key={"order_id": order_id, "record_type": "workflow"},
        UpdateExpression="SET last_action = :action, last_result = :result, updated_at = :now",
        ExpressionAttributeValues={
            ":action": action,
            ":result": json.dumps(result, default=str),
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )


def workflow_handler(event, context):
    (
        InvalidOrderTransition,
        OrderStatus,
        Repository,
        OrderNotFound,
        S3InvoiceStorage,
        Order,
        render_invoice,
    ) = _django_services()
    repository = Repository()
    action = event["action"]
    order_id = event["order_id"]
    order = repository.get_order(order_id)
    result = {}

    if action == "validate":
        if order.status == OrderStatus.PENDING.value:
            repository.transition(
                order.id,
                OrderStatus.PROCESSING.value,
                expected_status=OrderStatus.PENDING.value,
            )
            result = {"accepted": True}
        else:
            result = {"accepted": order.status == OrderStatus.PROCESSING.value}
    elif action == "inventory_check":
        unavailable = {
            product.strip()
            for product in os.getenv("SIMULATED_OUT_OF_STOCK_PRODUCT_IDS", "").split(",")
            if product.strip()
        }
        missing = [item.product_id for item in order.items.all() if item.product_id in unavailable]
        result = {"available": not missing, "unavailable_product_ids": missing}
    elif action == "payment_check":
        declined = {
            customer.strip()
            for customer in os.getenv("SIMULATED_DECLINED_CUSTOMER_IDS", "").split(",")
            if customer.strip()
        }
        result = {"approved": order.customer_id not in declined}
    elif action in {"reject_inventory", "reject_payment"}:
        if order.status == OrderStatus.PROCESSING.value:
            repository.transition(
                order.id,
                OrderStatus.REJECTED.value,
                expected_status=OrderStatus.PROCESSING.value,
            )
        result = {"status": OrderStatus.REJECTED.value, "reason": action}
    elif action == "complete":
        key = f"invoices/{order.id}.pdf"
        if order.status == OrderStatus.COMPLETED.value and order.invoice_s3_key:
            result = {"status": order.status, "invoice_s3_key": order.invoice_s3_key}
        else:
            pdf = render_invoice(order)
            S3InvoiceStorage(os.environ["INVOICE_BUCKET"]).s3.put_object(
                Bucket=os.environ["INVOICE_BUCKET"],
                Key=key,
                Body=pdf,
                ContentType="application/pdf",
            )
            try:
                completed = repository.transition(
                    order.id,
                    OrderStatus.COMPLETED.value,
                    expected_status=OrderStatus.PROCESSING.value,
                    invoice_s3_key=key,
                )
            except InvalidOrderTransition:
                completed = repository.get_order(order.id)
                if completed.status != OrderStatus.COMPLETED.value or completed.invoice_s3_key != key:
                    raise
            result = {"status": completed.status, "invoice_s3_key": key}
    elif action == "notify":
        sns = _client("sns")
        sns.publish(
            TopicArn=os.environ["SNS_TOPIC_ARN"],
            Subject=f"CloudOrder {order.status.lower()}",
            Message=json.dumps(
                {"order_id": str(order.id), "customer_id": order.customer_id, "status": order.status}
            ),
            MessageAttributes={
                "order_id": {"DataType": "String", "StringValue": str(order.id)}
            },
        )
        result = {"notified": True, "status": order.status}
    elif action == "workflow_failed":
        if order.status == OrderStatus.PROCESSING.value:
            repository.transition(
                order.id,
                OrderStatus.FAILED.value,
                expected_status=OrderStatus.PROCESSING.value,
            )
        result = {"status": OrderStatus.FAILED.value}
    else:
        raise ValueError(f"Unsupported workflow action: {action}")

    _mark_step(str(order.id), action, result)
    return result