from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_events as events,
    aws_events_targets as event_targets,
    aws_kms as kms,
    aws_rds as rds,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sqs as sqs,
)
from constructs import Construct


class CloudOrderFoundationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "CloudOrderVpc",
            max_azs=2,
            nat_gateways=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="application",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="data",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        self.database_security_group = ec2.SecurityGroup(
            self,
            "DatabaseSecurityGroup",
            vpc=self.vpc,
            description="Private PostgreSQL access for CloudOrder application tasks",
            allow_all_outbound=False,
        )
        self.database = rds.DatabaseInstance(
            self,
            "OrdersDatabase",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_4
            ),
            instance_type=ec2.InstanceType("t4g.micro"),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[self.database_security_group],
            database_name="cloudorder",
            credentials=rds.Credentials.from_generated_secret("cloudorder_app"),
            allocated_storage=20,
            max_allocated_storage=100,
            storage_encrypted=True,
            multi_az=True,
            backup_retention=Duration.days(7),
            deletion_protection=True,
            publicly_accessible=False,
            auto_minor_version_upgrade=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.database_secret = self.database.secret
        self.application_secret = secretsmanager.Secret(
            self,
            "DjangoApplicationSecret",
            description="CloudOrder Django signing secret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=64,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.order_processing_table = dynamodb.Table(
            self,
            "OrderProcessingTable",
            table_name="OrderProcessing",
            partition_key=dynamodb.Attribute(
                name="order_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="record_type", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.invoice_bucket = s3.Bucket(
            self,
            "InvoiceBucket",
            encryption=s3.BucketEncryption.KMS_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    abort_incomplete_multipart_upload_after=Duration.days(7)
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.user_pool = cognito.UserPool(
            self,
            "CustomerUserPool",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.OPTIONAL,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.user_pool_client = self.user_pool.add_client(
            "CloudOrderApiClient",
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )
        cognito.CfnUserPoolGroup(
            self,
            "CustomerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Customer",
            description="CloudOrder customer access group",
        )
        cognito.CfnUserPoolGroup(
            self,
            "AdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Admin",
            description="CloudOrder administrator access group",
        )
        self.event_bus = events.EventBus(
            self,
            "OrderEventBus",
            event_bus_name="cloudorder-orders",
        )
        self.order_dlq = sqs.Queue(
            self,
            "OrderDeadLetterQueue",
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        self.order_queue = sqs.Queue(
            self,
            "OrderProcessingQueue",
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.minutes(3),
            retention_period=Duration.days(4),
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=self.order_dlq,
                max_receive_count=5,
            ),
        )
        events.Rule(
            self,
            "OrderCreatedRule",
            event_bus=self.event_bus,
            event_pattern=events.EventPattern(
                source=["cloudorder.orders"],
                detail_type=["OrderCreated.v1"],
            ),
            targets=[
                event_targets.SqsQueue(
                    self.order_queue,
                    dead_letter_queue=self.order_dlq,
                    max_event_age=Duration.hours(24),
                    retry_attempts=10,
                )
            ],
        )
        self.notification_topic = sns.Topic(
            self,
            "OrderNotifications",
            master_key=kms.Alias.from_alias_name(self, "SnsManagedKey", "alias/aws/sns"),
            enforce_ssl=True,
        )

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(self, "DatabaseEndpoint", value=self.database.db_instance_endpoint_address)
        CfnOutput(self, "DatabaseSecretArn", value=self.database_secret.secret_arn)
        CfnOutput(self, "OrderProcessingTableName", value=self.order_processing_table.table_name)
        CfnOutput(self, "InvoiceBucketName", value=self.invoice_bucket.bucket_name)
        CfnOutput(self, "CognitoUserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "CognitoClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "OrderEventBusName", value=self.event_bus.event_bus_name)