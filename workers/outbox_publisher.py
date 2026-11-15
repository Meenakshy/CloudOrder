import json
import logging
import os
from threading import Event

import boto3
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cloudorder.config.settings")
django.setup()

from django.conf import settings

from cloudorder.orders.infrastructure.repository import DjangoOrderRepository


logger = logging.getLogger(__name__)


def publish_pending_once(eventbridge=None, repository=None, limit: int = 100) -> tuple[int, int]:
    eventbridge = eventbridge or boto3.client(
        "events", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
    )
    repository = repository or DjangoOrderRepository()
    published = 0
    failed = 0

    for event_id in repository.pending_outbox_ids(limit):
        def publish(event):
            response = eventbridge.put_events(
                Entries=[
                    {
                        "EventBusName": settings.EVENT_BUS_NAME,
                        "Source": "cloudorder.orders",
                        "DetailType": event.event_type,
                        "Detail": json.dumps(event.payload),
                    }
                ]
            )
            if response.get("FailedEntryCount", 0):
                detail = response.get("Entries", [{}])[0]
                raise RuntimeError(detail.get("ErrorMessage", "EventBridge rejected event"))

        if repository.publish_outbox_event(event_id, publish):
            published += 1
        else:
            failed += 1
    return published, failed


def run_forever(poll_interval: float = 3.0) -> None:
    stop = Event()
    logger.info("Starting CloudOrder outbox publisher")
    while not stop.is_set():
        published, failed = publish_pending_once()
        if published or failed:
            logger.info("Outbox batch finished: published=%s failed=%s", published, failed)
        stop.wait(poll_interval)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_forever()