from aws_cdk.assertions import Template

from infra.cloudorder_cdk.app import create_app


def test_foundation_stack_contains_encrypted_private_data_services():
    _, foundation, _ = create_app()
    template = Template.from_stack(foundation)

    template.resource_count_is("AWS::RDS::DBInstance", 1)
    template.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "Engine": "postgres",
            "PubliclyAccessible": False,
            "StorageEncrypted": True,
            "MultiAZ": True,
        },
    )
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "OrderProcessing",
            "SSESpecification": {"SSEEnabled": True},
        },
    )
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}
                ]
            },
        },
    )
    template.resource_count_is("AWS::Cognito::UserPool", 1)
    template.resource_count_is("AWS::Events::EventBus", 1)
    template.resource_count_is("AWS::SQS::Queue", 2)


def test_runtime_stack_contains_tls_fargate_and_standard_workflow():
    _, _, runtime = create_app()
    template = Template.from_stack(runtime)

    template.resource_count_is("AWS::ECS::Service", 2)
    template.resource_count_is("AWS::Lambda::Function", 2)
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {"Port": 443, "Protocol": "HTTPS"},
    )
    template.has_resource_properties(
        "AWS::StepFunctions::StateMachine",
        {"StateMachineType": "STANDARD"},
    )
    template.resource_count_is("AWS::CloudWatch::Alarm", 3)