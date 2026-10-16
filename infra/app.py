#!/usr/bin/env python3
"""CDK app entry point. Assembles the Data/Auth/Api/Web stacks."""
import os

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.auth_stack import AuthStack
from stacks.api_stack import ApiStack
from stacks.web_stack import WebStack


def _cfg(app: cdk.App) -> dict:
    keys = ["deployRegion", "mockMode", "identityStoreId", "identityStoreRegion",
            "ssoInstanceArn", "subscriptionAppArn",
            "groupCsvBucket", "groupCsvKey",
            "kiroSourceBucket", "kiroSourcePrefix",
            "creditUnitPrice", "topN",
            "sesSender", "reportRecipients",
            "callbackUrls", "logoutUrls", "cognitoDomainPrefix", "corsOrigins",
            "agentRuntimeArn"]
    return {k: app.node.try_get_context(k) for k in keys}


app = cdk.App()
cfg = _cfg(app)

region = cfg.get("deployRegion") or os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=region,
)

# Data layer
data = DataStack(app, "KiroDash-Data", cfg=cfg, env=env)

# Auth + dashboard API
auth = AuthStack(app, "KiroDash-Auth", cfg=cfg, env=env)
api = ApiStack(app, "KiroDash-Api", cfg=cfg, data_stack=data, auth_stack=auth, env=env)
api.add_dependency(data)
api.add_dependency(auth)

# Frontend static site (S3 + CloudFront + OAC)
WebStack(app, "KiroDash-Web", cfg=cfg, env=env)

app.synth()
