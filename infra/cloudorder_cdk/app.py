import os
import sys
from pathlib import Path

import aws_cdk as cdk

CDK_ROOT = Path(__file__).resolve().parent
if str(CDK_ROOT) not in sys.path:
    sys.path.insert(0, str(CDK_ROOT))

from stacks import CloudOrderFoundationStack, CloudOrderRuntimeStack


def create_app():
    app = cdk.App()
    environment = cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", os.getenv("AWS_REGION", "us-east-1")),
    )
    foundation = CloudOrderFoundationStack(
        app,
        "CloudOrderFoundation",
        env=environment,
        description="CloudOrder network, identity, persistence, eventing, and encryption foundation.",
    )
    runtime = CloudOrderRuntimeStack(
        app,
        "CloudOrderRuntime",
        foundation=foundation,
        env=environment,
        description="CloudOrder API, outbox publisher, and asynchronous order workflow.",
    )
    cdk.Tags.of(app).add("Project", "CloudOrder")
    return app, foundation, runtime


if __name__ == "__main__":
    create_app()[0].synth()