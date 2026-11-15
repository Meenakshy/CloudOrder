#!/bin/sh
set -eu

if ! awslocal dynamodb describe-table --table-name OrderProcessing >/dev/null 2>&1; then
  awslocal dynamodb create-table \
    --table-name OrderProcessing \
    --attribute-definitions AttributeName=order_id,AttributeType=S AttributeName=record_type,AttributeType=S \
    --key-schema AttributeName=order_id,KeyType=HASH AttributeName=record_type,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST
fi

if ! awslocal s3api head-bucket --bucket cloudorder-invoices >/dev/null 2>&1; then
  awslocal s3api create-bucket --bucket cloudorder-invoices
fi
if ! awslocal events describe-event-bus --name cloudorder-orders >/dev/null 2>&1; then
  awslocal events create-event-bus --name cloudorder-orders
fi
awslocal sns create-topic --name cloudorder-notifications

DLQ_URL=$(awslocal sqs create-queue --queue-name cloudorder-orders-dlq --query QueueUrl --output text)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
QUEUE_URL=$(awslocal sqs create-queue --queue-name cloudorder-orders --attributes VisibilityTimeout=180 --query QueueUrl --output text)
QUEUE_ARN=$(awslocal sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
REDRIVE_POLICY=$(printf '{"deadLetterTargetArn":"%s","maxReceiveCount":"5"}' "$DLQ_ARN")
REDRIVE_POLICY_ESCAPED=$(printf '%s' "$REDRIVE_POLICY" | sed 's/"/\\"/g')
printf '{"RedrivePolicy":"%s"}\n' "$REDRIVE_POLICY_ESCAPED" >/tmp/cloudorder-redrive-attributes.json
awslocal sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes file:///tmp/cloudorder-redrive-attributes.json

RULE_ARN=$(awslocal events put-rule \
  --event-bus-name cloudorder-orders \
  --name cloudorder-order-created \
  --event-pattern '{"source":["cloudorder.orders"],"detail-type":["OrderCreated.v1"]}' \
  --state ENABLED \
  --query RuleArn \
  --output text)
awslocal events put-targets \
  --event-bus-name cloudorder-orders \
  --rule cloudorder-order-created \
  --targets "Id=OrderQueue,Arn=$QUEUE_ARN"

QUEUE_POLICY=$(printf '{"Version":"2012-10-17","Statement":[{"Sid":"AllowLocalEventBridge","Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sqs:SendMessage","Resource":"%s","Condition":{"ArnEquals":{"aws:SourceArn":"%s"}}}]}' "$QUEUE_ARN" "$RULE_ARN")
QUEUE_POLICY_ESCAPED=$(printf '%s' "$QUEUE_POLICY" | sed 's/"/\\"/g')
printf '{"Policy":"%s"}\n' "$QUEUE_POLICY_ESCAPED" >/tmp/cloudorder-queue-attributes.json
awslocal sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes file:///tmp/cloudorder-queue-attributes.json
touch /tmp/cloudorder-init-complete