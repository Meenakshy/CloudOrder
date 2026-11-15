from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cloudwatch_actions,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_lambda as lambda_,
    aws_lambda_event_sources as event_sources,
    aws_logs as logs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct

from .foundation_stack import CloudOrderFoundationStack


class CloudOrderRuntimeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: CloudOrderFoundationStack,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        project_root = Path(__file__).resolve().parents[3]
        certificate_arn = CfnParameter(
            self,
            "AlbCertificateArn",
            type="String",
            description="ACM certificate ARN in this region for the HTTPS application load balancer.",
        )
        api_host_name = CfnParameter(
            self,
            "ApiHostName",
            type="String",
            description="DNS host covered by the ACM certificate and routed to this load balancer.",
        )

        cluster = ecs.Cluster(self, "CloudOrderCluster", vpc=foundation.vpc)
        api_security_group = ec2.SecurityGroup(
            self,
            "ApiTaskSecurityGroup",
            vpc=foundation.vpc,
            description="CloudOrder API task network access",
        )
        publisher_security_group = ec2.SecurityGroup(
            self,
            "OutboxTaskSecurityGroup",
            vpc=foundation.vpc,
            description="CloudOrder outbox publisher network access",
        )
        workflow_security_group = ec2.SecurityGroup(
            self,
            "WorkflowTaskSecurityGroup",
            vpc=foundation.vpc,
            description="CloudOrder workflow task network access",
        )
        for construct_id, security_group, description in [
            ("ApiDatabaseIngress", api_security_group, "Django API to PostgreSQL"),
            ("PublisherDatabaseIngress", publisher_security_group, "Outbox publisher to PostgreSQL"),
            ("WorkflowDatabaseIngress", workflow_security_group, "Workflow tasks to PostgreSQL"),
        ]:
            ec2.CfnSecurityGroupIngress(
                self,
                construct_id,
                group_id=foundation.database_security_group.security_group_id,
                source_security_group_id=security_group.security_group_id,
                ip_protocol="tcp",
                from_port=5432,
                to_port=5432,
                description=description,
            )

        api_image = ecr_assets.DockerImageAsset(
            self,
            "ApiContainerImage",
            directory=str(project_root),
            file="infra/cloudorder_cdk/api.Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )
        api_task = ecs.FargateTaskDefinition(
            self, "ApiTaskDefinition", cpu=512, memory_limit_mib=1024
        )
        api_container = api_task.add_container(
            "DjangoApi",
            image=ecs.ContainerImage.from_docker_image_asset(api_image),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="cloudorder-api"),
            environment=self._application_environment(
                foundation, allowed_hosts=api_host_name.value_as_string
            ),
        )
        api_container.add_secret(
            "DB_USER", ecs.Secret.from_secrets_manager(foundation.database_secret, "username")
        )
        api_container.add_secret(
            "DB_PASSWORD", ecs.Secret.from_secrets_manager(foundation.database_secret, "password")
        )
        api_container.add_secret(
            "DJANGO_SECRET_KEY", ecs.Secret.from_secrets_manager(foundation.application_secret)
        )
        api_container.add_port_mappings(ecs.PortMapping(container_port=8000))
        foundation.invoice_bucket.grant_read(api_task.task_role)
        api_service = ecs.FargateService(
            self,
            "ApiService",
            cluster=cluster,
            task_definition=api_task,
            desired_count=2,
            assign_public_ip=False,
            security_groups=[api_security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
            max_healthy_percent=200,
        )

        publisher_task = ecs.FargateTaskDefinition(
            self, "OutboxTaskDefinition", cpu=256, memory_limit_mib=512
        )
        publisher_container = publisher_task.add_container(
            "OutboxPublisher",
            image=ecs.ContainerImage.from_docker_image_asset(api_image),
            command=["python", "-m", "workers.outbox_publisher"],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="cloudorder-outbox"),
            environment=self._application_environment(foundation),
        )
        publisher_container.add_secret(
            "DB_USER", ecs.Secret.from_secrets_manager(foundation.database_secret, "username")
        )
        publisher_container.add_secret(
            "DB_PASSWORD", ecs.Secret.from_secrets_manager(foundation.database_secret, "password")
        )
        publisher_container.add_secret(
            "DJANGO_SECRET_KEY", ecs.Secret.from_secrets_manager(foundation.application_secret)
        )
        foundation.event_bus.grant_put_events_to(publisher_task.task_role)
        ecs.FargateService(
            self,
            "OutboxPublisherService",
            cluster=cluster,
            task_definition=publisher_task,
            desired_count=1,
            assign_public_ip=False,
            security_groups=[publisher_security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )

        alb_security_group = ec2.SecurityGroup(
            self,
            "LoadBalancerSecurityGroup",
            vpc=foundation.vpc,
            description="Public HTTPS ingress for CloudOrder",
        )
        alb_security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        alb_security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP redirect")
        api_security_group.add_ingress_rule(
            alb_security_group, ec2.Port.tcp(8000), "Traffic from the application load balancer"
        )
        load_balancer = elbv2.ApplicationLoadBalancer(
            self,
            "ApplicationLoadBalancer",
            vpc=foundation.vpc,
            internet_facing=True,
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )
        https_listener = load_balancer.add_listener(
            "HttpsListener",
            port=443,
            certificates=[elbv2.ListenerCertificate.from_arn(certificate_arn.value_as_string)],
            open=False,
        )
        https_listener.add_targets(
            "ApiTargets",
            port=8000,
            targets=[api_service],
            health_check=elbv2.HealthCheck(
                path="/health/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
            ),
        )
        load_balancer.add_listener(
            "HttpRedirect",
            port=80,
            open=False,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS", port="443", permanent=True
            ),
        )
        api_service.auto_scale_task_count(min_capacity=2, max_capacity=8).scale_on_cpu_utilization(
            "ApiCpuScaling", target_utilization_percent=60
        )

        workflow_environment = self._application_environment(foundation)
        workflow_environment.pop("AWS_REGION", None)
        workflow_environment.update(
            {
                "INVOICE_BUCKET": foundation.invoice_bucket.bucket_name,
                "SNS_TOPIC_ARN": foundation.notification_topic.topic_arn,
                "DYNAMODB_ORDER_PROCESSING_TABLE": foundation.order_processing_table.table_name,
                "DB_SECRET_ARN": foundation.database_secret.secret_arn,
                "APP_SECRET_ARN": foundation.application_secret.secret_arn,
            }
        )
        workflow_logs = logs.LogGroup(
            self,
            "WorkflowLambdaLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )
        workflow_function = lambda_.DockerImageFunction(
            self,
            "WorkflowTaskFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(project_root),
                file="infra/cloudorder_cdk/lambda.Dockerfile",
                cmd=["workers.lambda_handlers.workflow_handler"],
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=768,
            timeout=Duration.minutes(2),
            vpc=foundation.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[workflow_security_group],
            environment=workflow_environment,
            log_group=workflow_logs,
            tracing=lambda_.Tracing.ACTIVE,
        )
        foundation.database_secret.grant_read(workflow_function)
        foundation.application_secret.grant_read(workflow_function)
        foundation.order_processing_table.grant_read_write_data(workflow_function)
        foundation.invoice_bucket.grant_read_write(workflow_function)
        foundation.notification_topic.grant_publish(workflow_function)

        state_machine_logs = logs.LogGroup(
            self,
            "WorkflowStateMachineLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )
        validation_task = self._workflow_task(workflow_function, "validate", "Validation")
        inventory_task = self._workflow_task(workflow_function, "inventory_check", "InventoryCheck")
        payment_task = self._workflow_task(workflow_function, "payment_check", "PaymentCheck")
        complete_task = self._workflow_task(workflow_function, "complete", "CompleteAndInvoice")
        notify_task = self._workflow_task(workflow_function, "notify", "Notify")
        notify_inventory_rejection = self._workflow_task(
            workflow_function, "notify", "NotifyInventoryRejection"
        )
        notify_payment_rejection = self._workflow_task(
            workflow_function, "notify", "NotifyPaymentRejection"
        )
        reject_inventory = self._workflow_task(
            workflow_function, "reject_inventory", "RejectInventory"
        )
        reject_payment = self._workflow_task(workflow_function, "reject_payment", "RejectPayment")
        mark_failed = self._workflow_task(workflow_function, "workflow_failed", "MarkFailed")
        failed = sfn.Fail(self, "OrderWorkflowFailed", error="OrderProcessingFailed")
        mark_failed.next(failed)
        for task in [
            validation_task,
            inventory_task,
            payment_task,
            complete_task,
            notify_task,
            notify_inventory_rejection,
            notify_payment_rejection,
            reject_inventory,
            reject_payment,
        ]:
            task.add_retry(
                errors=[
                    "Lambda.ServiceException",
                    "Lambda.TooManyRequestsException",
                    "States.Timeout",
                ],
                interval=Duration.seconds(2),
                max_attempts=3,
                backoff_rate=2,
            )
            task.add_catch(mark_failed, errors=["States.ALL"], result_path="$.failure")

        completed = sfn.Succeed(self, "OrderWorkflowSucceeded")
        rejected = sfn.Succeed(self, "OrderWorkflowRejected")
        validated = sfn.Choice(self, "OrderCanBeProcessed").when(
            sfn.Condition.boolean_equals("$.validate.accepted", True), inventory_task
        ).otherwise(completed)
        inventory_choice = sfn.Choice(self, "InventoryAvailable").when(
            sfn.Condition.boolean_equals("$.inventory_check.available", True), payment_task
        ).otherwise(reject_inventory.next(notify_inventory_rejection).next(rejected))
        payment_choice = sfn.Choice(self, "PaymentApproved").when(
            sfn.Condition.boolean_equals("$.payment_check.approved", True), complete_task
        ).otherwise(reject_payment.next(notify_payment_rejection).next(rejected))
        definition = validation_task.next(validated)
        inventory_task.next(inventory_choice)
        payment_task.next(payment_choice)
        complete_task.next(notify_task).next(completed)

        state_machine = sfn.StateMachine(
            self,
            "OrderProcessingStateMachine",
            state_machine_name="CloudOrderOrderProcessing",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(1),
            logs=sfn.LogOptions(
                destination=state_machine_logs,
                level=sfn.LogLevel.ALL,
                include_execution_data=False,
            ),
            tracing_enabled=True,
        )

        starter_logs = logs.LogGroup(
            self,
            "StarterLambdaLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )
        starter_function = lambda_.DockerImageFunction(
            self,
            "OrderStarterFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(project_root),
                file="infra/cloudorder_cdk/lambda.Dockerfile",
                cmd=["workers.lambda_handlers.starter_handler"],
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "DYNAMODB_ORDER_PROCESSING_TABLE": foundation.order_processing_table.table_name,
                "ORDER_STATE_MACHINE_ARN": state_machine.state_machine_arn,
            },
            log_group=starter_logs,
            tracing=lambda_.Tracing.ACTIVE,
        )
        foundation.order_processing_table.grant_read_write_data(starter_function)
        state_machine.grant_start_execution(starter_function)
        starter_function.add_event_source(
            event_sources.SqsEventSource(
                foundation.order_queue,
                batch_size=10,
                report_batch_item_failures=True,
                max_concurrency=10,
            )
        )

        dead_letter_alarm = cloudwatch.Alarm(
            self,
            "OrderDlqVisibleAlarm",
            metric=foundation.order_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(1)
            ),
            threshold=1,
            evaluation_periods=1,
            alarm_description="One or more CloudOrder messages require DLQ investigation.",
        )
        dead_letter_alarm.add_alarm_action(
            cloudwatch_actions.SnsAction(foundation.notification_topic)
        )
        cloudwatch.Alarm(
            self,
            "OrderWorkflowFailureAlarm",
            metric=state_machine.metric_failed(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="CloudOrder Step Functions executions are failing.",
        ).add_alarm_action(cloudwatch_actions.SnsAction(foundation.notification_topic))
        cloudwatch.Alarm(
            self,
            "OrderStarterErrorsAlarm",
            metric=starter_function.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="CloudOrder SQS starter Lambda reported an error.",
        ).add_alarm_action(cloudwatch_actions.SnsAction(foundation.notification_topic))

        CfnOutput(self, "ApiBaseUrl", value=f"https://{api_host_name.value_as_string}")
        CfnOutput(self, "ApiLoadBalancerDnsName", value=load_balancer.load_balancer_dns_name)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "NotificationTopicArn", value=foundation.notification_topic.topic_arn)

    @staticmethod
    def _application_environment(
        foundation: CloudOrderFoundationStack,
        allowed_hosts: str = "localhost,127.0.0.1",
    ) -> dict[str, str]:
        issuer = (
            f"https://cognito-idp.{Stack.of(foundation).region}.amazonaws.com/"
            f"{foundation.user_pool.user_pool_id}"
        )
        return {
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": allowed_hosts,
            "AWS_REGION": Stack.of(foundation).region,
            "DB_HOST": foundation.database.db_instance_endpoint_address,
            "DB_PORT": foundation.database.db_instance_endpoint_port,
            "DB_NAME": "cloudorder",
            "DATABASE_SSL_REQUIRE": "true",
            "COGNITO_USER_POOL_ID": foundation.user_pool.user_pool_id,
            "COGNITO_APP_CLIENT_ID": foundation.user_pool_client.user_pool_client_id,
            "COGNITO_ISSUER": issuer,
            "INVOICE_BUCKET": foundation.invoice_bucket.bucket_name,
            "EVENT_BUS_NAME": foundation.event_bus.event_bus_name,
        }

    @staticmethod
    def _workflow_task(function, action: str, construct_id: str):
        return sfn_tasks.LambdaInvoke(
            function.stack,
            construct_id,
            lambda_function=function,
            payload=sfn.TaskInput.from_object(
                {
                    "action": action,
                    "order_id": sfn.JsonPath.string_at("$.order_id"),
                }
            ),
            payload_response_only=True,
            result_path=f"$.{action}",
        )