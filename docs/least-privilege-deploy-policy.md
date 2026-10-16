# Deploy least-privilege policy notes (`least-privilege-deploy-policy.json`)

IAM policy JSON does not support a comment field (something like `_comment` is reported by Access Analyzer as
`INVALID_POLICY_ELEMENT`), so the design trade-offs are documented here.

## Purpose

`least-privilege-deploy-policy.json` grants permissions only to the **deploy identity** (an IAM user or role) that runs
`cdk bootstrap` / `cdk deploy` (and optionally `backend/agent/deploy.py`). It is not a runtime policy: each Lambda
execution role and the Agent Runtime execution role created by the CDK stacks have their own resource-scoped policies,
unrelated to this file.

## The 7 statements that intentionally keep action-level wildcards

| Sid | Actions | Resource scope |
|---|---|---|
| `CloudFormationAppAndToolkitStacks` | `cloudformation:*` | stacks `KiroDash-*`, `CDKToolkit` |
| `S3AppAndCdkBuckets` | `s3:*` | buckets `kirodash-*`, `cdk-hnb659fds-*` |
| `LambdaAppFunctionsAndLayers` | `lambda:*` | functions `KiroDash-*`, layers |
| `CognitoUserPools` | `cognito-idp:*` | user pool |
| `ApiGatewayHttpApis` | `apigateway:*` | HTTP API (`/apis`, `/apis/*`, `/tags/*`) |
| `CloudFrontDistributionResources` | `cloudfront:*` | distribution / OAC / response-headers-policy / cache-policy |
| `EcrCdkAndAgentRepos` | `ecr:*` | repositories `cdk-hnb659fds-container-assets-*`, `kiro-*` |

These statements have their **resources already scoped** to this application (`KiroDash-*`) and CDK bootstrap
(`cdk-hnb659fds-*` / `CDKToolkit`) naming patterns, but **the actions are service-level wildcards**: CloudFormation calls a
large set of actions during create / update / rollback / delete, and that set changes with CDK and CloudFormation resource
handler versions, so enumerating it in advance easily causes `AccessDenied` during a stack rollback or delete, leaving the
stack stuck in states like `UPDATE_ROLLBACK_FAILED`.

**Production recommendation**: after one complete deployment (including an update and a delete), use the actual calls
recorded by CloudTrail (optionally via
[IAM Access Analyzer policy generation](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-policy-generation.html))
to replace the 7 statements above with an exact action list.

## All other statements are scoped by both action and resource

- DynamoDB (tables `KiroDash-*`), Athena (workgroup `*-wg`), Glue (database `kiro_usage` and its tables),
  EventBridge (rules `KiroDash-*`), CloudWatch Logs
  (log groups `/aws/lambda/KiroDash-*`, `KiroDash-*`), AgentCore Runtime (`runtime/*`), IAM roles/policies
  (`KiroDash-*` / `cdk-hnb659fds-*` / `KiroDashAgentRuntimeRole`), SSM bootstrap parameters,
  `sts:AssumeRole` (the CDK bootstrap roles), and read-only access to the Kiro source bucket: all are exact actions + scoped resources.
- `iam:PassRole` is scoped to the roles above, with an `iam:PassedToService` condition restricting it to
  lambda / events / cloudformation / bedrock-agentcore.
- Only the following two keep `"Resource": "*"`:
  - `AccountWideReadOnlyAndUnscopable`: actions that do not support resource-level permissions (read-only/list, or
    create-time calls required by CDK bootstrap), such as `cloudformation:ValidateTemplate`, `cognito-idp:CreateUserPool`,
    `ecr:GetAuthorizationToken`, `logs:DescribeLogGroups`,
    `bedrock-agentcore:ListAgentRuntimes`, `sts:GetCallerIdentity`.
  - `IdentityCenterReadOnly`: read-only List/Describe actions on Identity Store / SSO.

## References

- [AWS CDK bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html)
- [Customize AWS CDK bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-customizing.html)
  (scoping down the CloudFormation execution role permissions, trust policy, etc.)
- [Create and apply permissions boundaries for the AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/customize-permissions-boundaries.html)
