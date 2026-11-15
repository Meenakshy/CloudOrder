# CloudOrder Implementation Plan

## Scope and guardrails

CloudOrder is a backend-only order management service. Django REST and Strawberry GraphQL call the same application services; PostgreSQL is authoritative for orders; an outbox makes publishing order-created events atomic with order creation. AWS CDK defines the full production architecture, but this project will only synthesize and test templates locally. No AWS resources will be deployed or created without explicit approval.

The PostgreSQL schema is intentionally limited to `orders`, `order_items`, and `outbox_events`. DynamoDB has one `OrderProcessing` table for workflow/idempotency records. Invoices are generated as PDFs and written to a private, encrypted S3 bucket; APIs return short-lived presigned URLs only after an ownership/admin check.

## Project structure

```text
cloudorder/
  config/                         Django settings, URLs, WSGI/ASGI
  orders/
    domain/                       Order state rules and domain types
    application/                  Shared use cases and repository contracts
    infrastructure/               Django ORM and AWS adapters
    api/                          DRF and Strawberry GraphQL transport
workers/                          Outbox publisher and workflow Lambda handlers
infra/cloudorder_cdk/
  app.py                          CDK entry point
  stacks/                         Network/data, application, and workflow stacks
  lambda_src/                     Deployable Lambda package
  cdk.json, requirements.txt
 tests/                            Unit, API/auth, and workflow tests
 docs/architecture/                Mermaid sources and rendered diagrams
```

## Stages and verification

1. **Foundation and architecture artifacts**: lay out packages; write the architecture, data ERD, and order workflow diagrams; render SVG and PNG; record API rationale and staged plan here. Verify Mermaid compilation and that all image files are non-empty.
2. **Django foundation and persistence**: configure Django, PostgreSQL, the three ORM tables, migrations, repository interfaces/adapters, transaction-safe order creation, guarded state transitions, and outbox writes. Verify migration checks and repository/service unit tests.
3. **REST, GraphQL, and identity**: add create/cancel/retrieve/invoice REST endpoints, health check, the `order`, `myOrders`, and admin-only `allOrders` GraphQL queries, shared services, Cognito JWT verification, ownership/admin policy, and cursor pagination. Verify schema checks, API tests, authorization tests, and invalid-token cases.
4. **Asynchronous processing**: implement outbox polling/publishing to EventBridge, SQS starter Lambda, Step Functions workflow tasks, DynamoDB idempotency, simulated inventory/payment, PDF invoice generation, SNS notification, retries, terminal failure handling, and DLQ metrics/alarms. Verify handlers and orchestration behavior with AWS SDK stubs and local tests.
5. **AWS CDK**: define VPC/subnets/security groups, RDS, DynamoDB, S3, Cognito, ECR/ECS Fargate/ALB, EventBridge/SQS/DLQ, Lambda, Standard Step Functions, SNS, Secrets Manager, KMS encryption, IAM grants, logging/alarms, and outputs. Verify `cdk synth` and template assertions only; do not deploy.
6. **Local developer experience and documentation**: add Docker Compose for Django, PostgreSQL, and DynamoDB Local; pytest configuration and test suites; detailed README and Postman collection. Verify Compose configuration, pytest, and documented local startup.

Each stage is kept small enough to test before proceeding. Any required live AWS integration test is explicitly excluded from local verification and must not create resources without user approval.

## API style decisions

- **REST create/cancel/invoice/health**: these are command or resource-oriented HTTP operations with clear status codes, caching/security semantics, and straightforward integration from a future web client. Cancellation is a state-changing command; invoice retrieval is an authorization-sensitive resource operation; health is a simple operational probe.
- **GraphQL order/myOrders/allOrders**: these are read-oriented queries where clients benefit from selecting fields and composing order/item data without over-fetching. Cursor pagination suits changing order collections. `allOrders` is separately admin-gated.
- **Shared behavior**: both transports call the same application service and repository contracts; neither transport contains business state-transition logic.

## Key operational invariants

- Order creation and its outbox row commit in one PostgreSQL transaction.
- The outbox publisher marks an event published only after EventBridge accepts it; retries may republish, so consumers are idempotent.
- Every status change uses an expected current state and increments `version`; PostgreSQL remains the source of truth.
- Workflow executions use stable idempotency keys in DynamoDB and conditional writes; duplicate delivery is safe.
- Customer access is always constrained by authenticated `customer_id`; only the Admin role can use `allOrders` or bypass ownership checks.
- RDS is private. Public ingress terminates at the HTTPS ALB. Secrets are sourced from Secrets Manager, and data services use encryption at rest.
