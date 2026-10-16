"""ApiStack: dashboard REST API (API Gateway HTTP API + Lambda(FastAPI/Mangum)).

- Lambda: flat packaging of backend/api/* + backend/shared/* (handler.py uses relative-import fallbacks).
  Dependencies (fastapi/mangum/pydantic/...) come from a locally built Lambda layer (no Docker, see build.py).
- HTTP API: Cognito JWT authorizer (validates issuer/audience) on every route except GET /api/health. This is the only
  authentication; there is no application-layer authorization (any user of the Cognito user pool is authorized).
- Data-plane permissions: Athena query + read curated bucket + read Athena results bucket + Glue metadata.

Depends on DataStack (curated bucket / workgroup / glue db) and AuthStack (user pool / client).
"""
from __future__ import annotations

from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_apigatewayv2_authorizers as authorizers,
)
from constructs import Construct


class ApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict,
                 data_stack, auth_stack, **kw):
        super().__init__(scope, cid, **kw)

        credit_price = str(cfg.get("creditUnitPrice", "0.04"))
        top_n = str(cfg.get("topN", "15"))
        agent_runtime_arn = str(cfg.get("agentRuntimeArn", "") or "")

        curated = data_stack.curated_bucket
        results_bucket_name = data_stack.results_bucket.bucket_name
        workgroup = data_stack.workgroup_name
        glue_db = data_stack.glue_db

        # --- Dependency layer (locally built, no Docker) ---
        from build import build_api_assets  # infra/build.py
        deps_layer_dir, app_code_dir = build_api_assets()

        deps_layer = lambda_.LayerVersion(
            self, "ApiDepsLayer",
            code=lambda_.Code.from_asset(deps_layer_dir),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.X86_64],
            description="fastapi + mangum + pydantic for dashboard API")

        api_fn = lambda_.Function(
            self, "ApiFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.seconds(60), memory_size=512,
            layers=[deps_layer],
            code=lambda_.Code.from_asset(app_code_dir),
            environment={
                "GLUE_DB": glue_db,
                "ATHENA_WORKGROUP": workgroup,
                "CREDIT_UNIT_PRICE": credit_price,
                "TOP_N": top_n,
                "DEPT_TABLE": data_stack.dept_table.table_name,
                "AGENT_RUNTIME_ARN": agent_runtime_arn,
                # Model multiplier maintenance (ModelMultipliers)
                "MODEL_MULTIPLIER_TABLE": data_stack.multiplier_table.table_name,
                # The FastAPI CORSMiddleware and the HTTP API CORS below use the same corsOrigins (comma-separated);
                # falls back to "*" when unset. A real deployment should set corsOrigins to the CloudFront site URL.
                "CORS_ORIGINS": ",".join(_as_list(cfg.get("corsOrigins")) or ["*"]),
            },
        )

        # --- Data-plane IAM (least privilege) ---
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:GetQueryResults", "athena:StopQueryExecution",
                     "athena:GetWorkGroup"],
            resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/{workgroup}"]))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                     "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/{glue_db}",
                f"arn:aws:glue:{self.region}:{self.account}:table/{glue_db}/*",
            ]))
        curated.grant_read(api_fn)
        # Total registered users (activation-rate denominator): scan the department table to count.
        data_stack.dept_table.grant_read_data(api_fn)
        # Model multiplier maintenance: read/write scoped to this table ARN.
        data_stack.multiplier_table.grant_read_write_data(api_fn)
        # AgentCore Runtime invocation (/api/chat forwards NL Q&A)
        if agent_runtime_arn:
            api_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[agent_runtime_arn, agent_runtime_arn + "/*"]))
        # Athena results read/write (query results bucket)
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket",
                     "s3:GetBucketLocation"],
            resources=[
                f"arn:aws:s3:::{results_bucket_name}",
                f"arn:aws:s3:::{results_bucket_name}/*",
            ]))

        # --- HTTP API + JWT authorizer ---
        authorizer = authorizers.HttpJwtAuthorizer(
            "CognitoJwtAuthorizer",
            jwt_issuer=auth_stack.issuer,
            identity_source=["$request.header.Authorization"],
            jwt_audience=[auth_stack.user_pool_client.user_pool_client_id],
        )

        http_api = apigw.HttpApi(
            self, "HttpApi",
            api_name="kiro-dashboard-api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_headers=["Authorization", "Content-Type"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST,
                               apigw.CorsHttpMethod.PUT, apigw.CorsHttpMethod.OPTIONS],
                allow_origins=_as_list(cfg.get("corsOrigins")) or ["*"],
                # Let the browser read the CSV export filename; the HTTP API CORS is generated by the gateway,
                # so it must be exposed here, otherwise the frontend fetch cannot read Content-Disposition (falls back to the default name).
                expose_headers=["Content-Disposition"],
            ),
        )

        integration = integrations.HttpLambdaIntegration("ApiIntegration", api_fn)

        # Protected routes: /api/* require JWT (GET + POST + PUT; POST for /api/chat,
        # PUT for /api/model-multipliers multiplier editing)
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT],
            integration=integration,
            authorizer=authorizer,
        )
        # The only route without the JWT authorizer (liveness probe)
        http_api.add_routes(
            path="/api/health",
            methods=[apigw.HttpMethod.GET],
            integration=integration,
        )

        self.api_endpoint = http_api.api_endpoint
        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "ApiFnName", value=api_fn.function_name)


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]
