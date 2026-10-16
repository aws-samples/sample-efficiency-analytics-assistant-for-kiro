"""DataStack: data-layer infrastructure.

- S3: raw (Kiro CSV / mock), curated (Parquet long tables), athena-results
- DynamoDB: user_department
- Glue: DB + fact_user_daily / fact_user_model_daily external tables
- Athena: workgroup
- Lambda: ETL, identity-sync (AWS SDK for pandas layer)
- EventBridge: daily triggers for identity-sync and ETL
"""
from __future__ import annotations

import os

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_s3 as s3,
    aws_dynamodb as ddb,
    aws_glue as glue,
    aws_athena as athena,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
)
from constructs import Construct

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)
        mock_mode = str(cfg.get("mockMode", "true")).lower()
        credit_price = str(cfg.get("creditUnitPrice", "0.15"))
        identity_store_id = cfg.get("identityStoreId", "")
        identity_store_region = cfg.get("identityStoreRegion", "") or ""
        sso_instance_arn = cfg.get("ssoInstanceArn", "") or ""
        subscription_app_arn = cfg.get("subscriptionAppArn", "") or ""
        group_csv_bucket = (cfg.get("groupCsvBucket", "") or "").strip()
        group_csv_key = (cfg.get("groupCsvKey", "") or "").strip()
        # Monthly report (SES): an SES-verified sender email + recipients (comma-separated; empty falls back to the sender).
        ses_sender = (cfg.get("sesSender", "") or "").strip()
        report_recipients = (cfg.get("reportRecipients", "") or "").strip()
        raw_prefix = "user-activity-reports"
        # Real mode: the external source bucket exported by Kiro (may be cross-region, e.g. us-east-1).
        # When empty, the ETL reads the internal raw bucket (mock); when set, the ETL reads that external bucket + its prefix.
        kiro_source_bucket = (cfg.get("kiroSourceBucket", "") or "").strip()
        kiro_source_prefix = (cfg.get("kiroSourcePrefix", "") or raw_prefix).strip("/")

        # --- S3 ---
        raw = s3.Bucket(self, "RawBucket",
                        removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                        encryption=s3.BucketEncryption.S3_MANAGED,
                        enforce_ssl=True)
        curated = s3.Bucket(self, "CuratedBucket",
                            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                            encryption=s3.BucketEncryption.S3_MANAGED,
                            enforce_ssl=True)
        results = s3.Bucket(self, "AthenaResults",
                            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                            encryption=s3.BucketEncryption.S3_MANAGED,
                            enforce_ssl=True)

        # --- DynamoDB ---
        dept_table = ddb.Table(self, "UserDepartment",
                               partition_key=ddb.Attribute(name="user_key", type=ddb.AttributeType.STRING),
                               billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                               removal_policy=RemovalPolicy.DESTROY)

        # Model multiplier maintenance (ModelMultipliers): partition key model (the real Athena
        # model string). Multiplier seed/edit are read/written by the API.
        multiplier_table = ddb.Table(self, "ModelMultipliers",
            partition_key=ddb.Attribute(name="model", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY)
        self.multiplier_table = multiplier_table

        # --- Glue DB + external tables ---
        gdb = glue.CfnDatabase(self, "GlueDb",
                               catalog_id=self.account,
                               database_input=glue.CfnDatabase.DatabaseInputProperty(name="kiro_usage"))

        def _table(name: str, columns: list[tuple[str, str]]):
            cols = [glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in columns]
            loc = f"s3://{curated.bucket_name}/{name}/"
            # [Performance decision] Do not use partition projection.
            # Measured: partition projection (year/month/day) adds huge per-query partition enumeration overhead at this data size;
            # with 5 concurrent queries, a single query's EngineExecutionTime spiked to ~22s (amplified by concurrency), causing API 503.
            # Switched to a non-partitioned external table (Athena recursively reads the Parquet under year=/month=/day= subdirectories from LOCATION),
            # bringing the same 5-concurrent query down to ~400ms. Date filtering uses the in-data `date` column (not a partition key).
            # If partition pruning is needed as data grows, reintroduce partitions and re-evaluate the projection ranges.
            tbl = glue.CfnTable(
                self, f"Tbl{name}",
                catalog_id=self.account,
                database_name="kiro_usage",
                table_input=glue.CfnTable.TableInputProperty(
                    name=name,
                    table_type="EXTERNAL_TABLE",
                    parameters={"classification": "parquet"},
                    storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                        columns=cols,
                        location=loc,
                        input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                        serde_info=glue.CfnTable.SerdeInfoProperty(
                            serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"),
                    ),
                ))
            tbl.add_dependency(gdb)
            return tbl

        _table("fact_user_daily", [
            ("date", "string"), ("user_id", "string"), ("email", "string"),
            ("display_name", "string"), ("department", "string"),
            ("subscription_tier", "string"), ("chat_conversations", "bigint"),
            ("credits_used", "double"), ("overage_cap", "double"),
            ("overage_credits_used", "double"), ("overage_enabled", "boolean"),
            ("total_messages", "bigint"), ("new_user", "boolean"),
            ("usage_rate", "double"), ("balance", "double"),
            ("est_cost", "double"), ("profile_id", "string"),
            ("usage_limit", "double"), ("is_overage", "boolean"),
        ])
        _table("fact_user_model_daily", [
            ("date", "string"), ("email", "string"),
            ("model", "string"), ("model_messages", "bigint"),
        ])

        # --- Athena workgroup ---
        wg = athena.CfnWorkGroup(self, "Workgroup", name=f"{cid}-wg",
                                 recursive_delete_option=True,
                                 work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                                     result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                                         output_location=f"s3://{results.bucket_name}/query-results/")))

        # --- pandas/pyarrow layer (locally built Linux wheels, avoiding dependence on a public layer version) ---
        pandas_layer_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "build", "pandas_layer"))
        pandas_layer = lambda_.LayerVersion(
            self, "PandasLayer",
            code=lambda_.Code.from_asset(pandas_layer_dir),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.X86_64],
            description="pandas + pyarrow for ETL")

        # ETL data source: real mode points to the external Kiro bucket, otherwise the internal raw bucket.
        etl_raw_bucket = kiro_source_bucket or raw.bucket_name
        etl_raw_prefix = kiro_source_prefix if kiro_source_bucket else raw_prefix
        common_env = {
            "RAW_BUCKET": raw.bucket_name,
            "RAW_PREFIX": raw_prefix,
            "CURATED_BUCKET": curated.bucket_name,
            "DEPT_TABLE": dept_table.table_name,
            "CREDIT_UNIT_PRICE": credit_price,
            "MOCK_MODE": mock_mode,
        }

        # ETL Lambda: locally assemble handler + transform + derivations into the build directory (no Docker)
        etl_build = os.path.join(os.path.dirname(__file__), "..", "build", "etl")
        etl_build = os.path.abspath(etl_build)
        os.makedirs(etl_build, exist_ok=True)
        import shutil
        for src in [os.path.join(BACKEND, "etl", "handler.py"),
                    os.path.join(BACKEND, "etl", "transform.py"),
                    os.path.join(BACKEND, "shared", "derivations.py")]:
            shutil.copy2(src, etl_build)

        etl_fn = lambda_.Function(
            self, "EtlFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=1024,
            layers=[pandas_layer],
            environment={**common_env, "RAW_BUCKET": etl_raw_bucket, "RAW_PREFIX": etl_raw_prefix},
            code=lambda_.Code.from_asset(etl_build),
        )
        raw.grant_read(etl_fn)
        curated.grant_read_write(etl_fn)
        dept_table.grant_read_data(etl_fn)
        # Real mode: grant the ETL read access to the external Kiro source bucket (cross-region S3 reads do not require the same region but need IAM permission).
        if kiro_source_bucket:
            etl_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[f"arn:aws:s3:::{kiro_source_bucket}",
                           f"arn:aws:s3:::{kiro_source_bucket}/*"]))

        # identity-sync Lambda (no pandas needed)
        idsync_fn = lambda_.Function(
            self, "IdentitySyncFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=256,
            environment={**common_env, "IDENTITY_STORE_ID": identity_store_id,
                         "IDENTITY_STORE_REGION": identity_store_region,
                         "SSO_INSTANCE_ARN": sso_instance_arn,
                         "SUBSCRIPTION_APP_ARN": subscription_app_arn,
                         "GROUP_CSV_BUCKET": group_csv_bucket,
                         "GROUP_CSV_KEY": group_csv_key,
                         "IDENTITY_JSON_KEY": "identity_groups.json"},
            code=lambda_.Code.from_asset(os.path.join(BACKEND, "identity_sync")),
        )
        raw.grant_read(idsync_fn)
        dept_table.grant_read_write_data(idsync_fn)
        # Department source = the Kiro-exported group CSV: grant identity-sync read access to that bucket.
        if group_csv_bucket:
            idsync_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[f"arn:aws:s3:::{group_csv_bucket}",
                           f"arn:aws:s3:::{group_csv_bucket}/*"]))
        # Non-mock mode: requires Identity Center / SSO read-only permissions.
        #  - subscription-app assignments (authoritative roster): need SSO ListApplicationAssignments/DescribeApplication + group memberships/descriptions/users;
        #  - group CSV / walking groups: need group descriptions (department name) + user DisplayName.
        # All are read-only, granted as a single set (no write operations).
        if mock_mode != "true":
            idsync_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["identitystore:ListGroups", "identitystore:ListUsers",
                         "identitystore:ListGroupMemberships",
                         "identitystore:DescribeUser", "identitystore:DescribeGroup",
                         "sso:ListApplicationAssignments", "sso:DescribeApplication"],
                resources=["*"]))

        # --- Monthly report Lambda (SES): reuses the shared query layer, no pandas dependency ---
        report_build = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "build", "report"))
        os.makedirs(report_build, exist_ok=True)
        for src in [os.path.join(BACKEND, "report", "handler.py"),
                    os.path.join(BACKEND, "shared", "athena_client.py"),
                    os.path.join(BACKEND, "shared", "recommendations.py"),
                    os.path.join(BACKEND, "shared", "derivations.py")]:
            shutil.copy2(src, report_build)

        report_fn = lambda_.Function(
            self, "MonthlyReportFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            timeout=Duration.minutes(5), memory_size=256,
            environment={
                "GLUE_DB": "kiro_usage",
                "ATHENA_WORKGROUP": wg.name,
                "DEPLOY_REGION": self.region,
                "DEPT_TABLE": dept_table.table_name,
                "CREDIT_UNIT_PRICE": credit_price,
                "SES_SENDER": ses_sender,
                "REPORT_RECIPIENTS": report_recipients,
            },
            code=lambda_.Code.from_asset(report_build),
        )
        # Data-plane permissions: Athena + Glue + read curated + read/write the query-results bucket + read the department table
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"],
            resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/{wg.name}"]))
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTables", "glue:GetDatabase",
                     "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/kiro_usage",
                f"arn:aws:glue:{self.region}:{self.account}:table/kiro_usage/*",
            ]))
        curated.grant_read(report_fn)
        results.grant_read_write(report_fn)
        dept_table.grant_read_data(report_fn)
        # SES send: scoped to the SES verified identities in this account/region (the sender/domain must be SES-verified)
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendRawEmail", "ses:SendEmail"],
            resources=[f"arn:aws:ses:{self.region}:{self.account}:identity/*"]))
        # Check verification status (filter unverified recipients before sending): this action does not support resource-level permissions, so "*"
        report_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:GetIdentityVerificationAttributes"], resources=["*"]))

        # --- EventBridge daily: identity-sync first, then ETL (each on its own schedule here, for simplicity) ---
        events.Rule(self, "DailyIdentitySync",
                    schedule=events.Schedule.cron(minute="30", hour="2"),
                    targets=[targets.LambdaFunction(idsync_fn)])
        events.Rule(self, "DailyEtl",
                    schedule=events.Schedule.cron(minute="45", hour="2"),
                    targets=[targets.LambdaFunction(etl_fn)])
        # Monthly report: triggered daily, the Lambda internally checks "last day of month - 6" before sending (7 days before month-end).
        events.Rule(self, "MonthlyReport",
                    schedule=events.Schedule.cron(minute="0", hour="3"),
                    targets=[targets.LambdaFunction(report_fn)])

        # --- Outputs (for other stacks / manual verification) ---
        self.raw_bucket = raw
        self.curated_bucket = curated
        self.results_bucket = results
        self.dept_table = dept_table
        self.workgroup_name = wg.name
        self.glue_db = "kiro_usage"
        CfnOutput(self, "RawBucketName", value=raw.bucket_name)
        CfnOutput(self, "CuratedBucketName", value=curated.bucket_name)
        CfnOutput(self, "DeptTableName", value=dept_table.table_name)
        CfnOutput(self, "ModelMultiplierTableName", value=multiplier_table.table_name)
        CfnOutput(self, "AthenaWorkgroup", value=wg.name)
        CfnOutput(self, "EtlFnName", value=etl_fn.function_name)
        CfnOutput(self, "IdentitySyncFnName", value=idsync_fn.function_name)
        CfnOutput(self, "MonthlyReportFnName", value=report_fn.function_name)
