"""Deploy the NL Q&A Agent to AgentCore Runtime (CLI/boto3 path, no CDK alpha dependency).

Why not CDK: the Python aws-cdk bedrock-agentcore construct is not usable with this project's pinned
aws-cdk-lib (the construct is alpha, npm-only, and its version does not match the pinned lib). The design
doc reserves "fall back to L1/CLI when construct support is insufficient". This script idempotently:
  1. Creates/reuses the IAM execution role (Bedrock invoke + Athena + Glue + S3 read); resource ARNs are
     resolved from the KiroDash-Data stack outputs and the Athena workgroup config (the Data stack must be deployed first);
  2. Creates/updates the AgentCore Runtime (container image = ECR arm64 v1), configuring Cognito inbound JWT auth.

Usage:
  python backend/agent/deploy.py --image <ecr-uri>:v1 \
    --user-pool-id us-west-2_xxx --client-id xxx --region us-west-2
Output: the runtime ARN (written to stdout, for the frontend config / WebStack).

Note: the tools call the shared query layer directly inside the Runtime container. The Runtime validates the inbound
Cognito JWT (this user pool's discovery URL + this app client); the dashboard HTTP API enforces the same boundary.
There is no group or role check anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import re

import boto3

ROLE_NAME = "KiroDashAgentRuntimeRole"
DATA_STACK = "KiroDash-Data"
# Keep consistent with the MODEL_ID default in backend/agent/agent.py (a cross-region inference profile ID).
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
# Region prefixes of a cross-region inference profile ID (us./eu./apac./global. ...); strip it to get the underlying foundation-model ID.
_PROFILE_PREFIXES = ("us.", "eu.", "apac.", "global.", "jp.", "au.", "ca.", "us-gov.")


def bedrock_model_arns(model_id: str, account: str, region: str) -> list[str]:
    """The Bedrock resource ARNs the Agent invokes.

    A cross-region inference profile routes requests to foundation models in other regions, so both the profile ARN
    and the per-region foundation-model ARN must be authorized (region as *).
    """
    for p in _PROFILE_PREFIXES:
        if model_id.startswith(p):
            base = model_id[len(p):]
            return [f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}",
                    f"arn:aws:bedrock:*::foundation-model/{base}"]
    return [f"arn:aws:bedrock:{region}::foundation-model/{model_id}"]


# ECR image URI: <account>.dkr.ecr.<region>.amazonaws.com/<repo>[:<tag> | @sha256:<digest>]
_ECR_IMAGE_RE = re.compile(
    r"^(?P<account>\d{12})\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com/"
    r"(?P<repo>[a-z0-9][a-z0-9._/-]*?)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})|@sha256:(?P<digest>[0-9a-f]{64}))?$")


def parse_ecr_image(image: str) -> tuple[str, str, str]:
    """Parse --image into (repo_account, repo_region, repo_name); a non-ECR private registry URI raises ValueError.

    AgentCore Runtime only accepts ECR images, so the ECR format is required here to scope pull permissions to that repository.
    """
    m = _ECR_IMAGE_RE.match((image or "").strip())
    if not m:
        raise ValueError(
            f"--image is not an ECR image URI: {image!r}; expected "
            "<account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag> (or @sha256:<digest>)")
    return m.group("account"), m.group("region"), m.group("repo")


def resolve_data_resources(sess, stack: str = DATA_STACK) -> dict:
    """Resolve the workgroup / curated bucket / query-results bucket from the DataStack outputs + Athena workgroup config."""
    outs = sess.client("cloudformation").describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    out = {o["OutputKey"]: o["OutputValue"] for o in outs}
    workgroup = out.get("AthenaWorkgroup") or f"{stack}-wg"
    curated = out["CuratedBucketName"]
    wg = sess.client("athena").get_work_group(WorkGroup=workgroup)["WorkGroup"]
    loc = wg["Configuration"]["ResultConfiguration"]["OutputLocation"]  # s3://bucket/prefix/
    results = loc.split("://", 1)[1].split("/", 1)[0]
    return {"workgroup": workgroup, "curated_bucket": curated, "results_bucket": results}


def build_policy(account: str, region: str, model_id: str, workgroup: str,
                 curated_bucket: str, results_bucket: str, image: str) -> dict:
    """Inline policy for the Agent Runtime execution role (scoped to the resources actually used).

    image: the Agent container image ECR URI; image-pull permissions are granted only to that repository.
    """
    repo_account, repo_region, repo_name = parse_ecr_image(image)
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "Bedrock", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": bedrock_model_arns(model_id, account, region)},
            {"Sid": "Athena", "Effect": "Allow",
             "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution",
                        "athena:GetQueryResults", "athena:StopQueryExecution",
                        "athena:GetWorkGroup"],
             "Resource": f"arn:aws:athena:{region}:{account}:workgroup/{workgroup}"},
            # Intentionally keeps "*": Glue read-only catalog access (reading table metadata during Athena queries).
            {"Sid": "Glue", "Effect": "Allow",
             "Action": ["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                        "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
             "Resource": "*"},
            {"Sid": "S3", "Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket",
                        "s3:GetBucketLocation"],
             "Resource": [f"arn:aws:s3:::{curated_bucket}", f"arn:aws:s3:::{curated_bucket}/*",
                          f"arn:aws:s3:::{results_bucket}", f"arn:aws:s3:::{results_bucket}/*"]},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/*"},
            # Intentionally keeps "*": ecr:GetAuthorizationToken does not support resource-level permissions.
            {"Sid": "EcrAuth", "Effect": "Allow",
             "Action": ["ecr:GetAuthorizationToken"],
             "Resource": "*"},
            # Image pull is granted only to the repository specified by --image.
            {"Sid": "EcrPullAgentImage", "Effect": "Allow",
             "Action": ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"],
             "Resource": f"arn:aws:ecr:{repo_region}:{repo_account}:repository/{repo_name}"},
        ],
    }


def ensure_role(iam, account: str, region: str, policy: dict) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
            },
        }],
    }
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"[iam] role exists: {role['Arn']}")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Kiro dashboard NL agent runtime execution role",
        )["Role"]
        print(f"[iam] created role: {role['Arn']}")

    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="KiroDashAgentInline",
                        PolicyDocument=json.dumps(policy))
    print("[iam] inline policy applied")
    return role["Arn"]


def find_runtime(ctrl, name: str):
    paginator = ctrl.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for rt in page.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == name:
                return rt
    return None


def deploy(image: str, user_pool_id: str, client_id: str, region: str,
           name: str = "kiro_usage_agent") -> str:
    parse_ecr_image(image)  # validate the image URI first; on failure, touch no AWS resources
    sess = boto3.Session(region_name=region)
    account = sess.client("sts").get_caller_identity()["Account"]
    iam = sess.client("iam")
    ctrl = sess.client("bedrock-agentcore-control")

    model_id = os.environ.get("AGENT_MODEL_ID", DEFAULT_MODEL_ID)
    data = resolve_data_resources(sess)
    policy = build_policy(account, region, model_id, data["workgroup"],
                          data["curated_bucket"], data["results_bucket"], image)
    role_arn = ensure_role(iam, account, region, policy)
    # Wait until IAM reports the role (replaces a fixed sleep)
    iam.get_waiter("role_exists").wait(RoleName=ROLE_NAME)

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    # Inbound auth is the same boundary as the HTTP API JWT authorizer: a JWT issued by this user pool (discoveryUrl)
    # to this app client (allowedClients). The pool has self-signup disabled and only contains users the deployer
    # created, so any valid token from it belongs to an authorized dashboard user.
    authorizer = {
        "customJWTAuthorizer": {
            "discoveryUrl": f"{issuer}/.well-known/openid-configuration",
            "allowedClients": [client_id],
        }
    }
    artifact = {"containerConfiguration": {"containerUri": image}}
    network = {"networkMode": "PUBLIC"}
    # The runtime and the execution-role policy use the same workgroup / model to avoid permission/config mismatch.
    env = {"AWS_REGION": region, "GLUE_DB": "kiro_usage",
           "ATHENA_WORKGROUP": data["workgroup"], "AGENT_MODEL_ID": model_id}

    existing = find_runtime(ctrl, name)
    if existing:
        rid = existing["agentRuntimeId"]
        print(f"[runtime] updating existing {rid}")
        resp = ctrl.update_agent_runtime(
            agentRuntimeId=rid,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration=network,
            authorizerConfiguration=authorizer,
            environmentVariables=env,
        )
    else:
        print("[runtime] creating new")
        resp = ctrl.create_agent_runtime(
            agentRuntimeName=name,
            description="Efficiency Analytics Assistant for Kiro NL Q&A Agent",
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration=network,
            authorizerConfiguration=authorizer,
            environmentVariables=env,
        )
    arn = resp["agentRuntimeArn"]
    print(f"[runtime] ARN: {arn}")
    return arn


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--user-pool-id", required=True)
    p.add_argument("--client-id", required=True)
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--name", default="kiro_usage_agent")
    a = p.parse_args()
    try:
        parse_ecr_image(a.image)
    except ValueError as e:
        p.error(str(e))
    arn = deploy(a.image, a.user_pool_id, a.client_id, a.region, a.name)
    print(arn)
