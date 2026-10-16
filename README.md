# Efficiency Analytics Assistant for Kiro

A self-hosted analytics dashboard for tracking AI coding assistant usage across an organization, built on
Athena, Lambda, and React. It aggregates and visualizes how an organization uses Kiro, with
natural-language Q&A, model-concentration analysis, monthly tier suggestions (upgrade/downgrade + suggested
shutdown), and an admin center for model-multiplier maintenance. The whole solution is serverless and deploys to your own AWS account.

> This is sample code for demonstration and evaluation only. It is **not** a production-ready service: review and
> harden security, scaling, availability, compliance, and cost before any production use, in line with the AWS Shared
> Responsibility Model. It is provided without warranty under the MIT-0 license.

> Demo mode (`mockMode=true`) runs without any real data source, which is handy for a quick first look.

---

## 1. Features (for end users)

### 1.1 Usage dashboard
- **KPI overview**: registered / active users, activation rate, daily active (avg/peak), total credits, overage users, estimated cost, credits per active user.
- **Subscription tier distribution**: headcount across Pro / Pro+ / Pro Max / Power.
- **Charts**: daily credits usage trend (dual axis), model usage donut, credits by department / per capita, Top N users horizontal bars.
- **User details table**: filter by department, sort, paginate, with a usage-rate progress bar and status tags (normal / near limit / overage / unused); drill down into a single user's per-model usage.
- **Filters**: by data period (month) or a custom date range.
- **English UI**: the published build is English-only.

### 1.2 Model concentration analysis
A standalone page that analyzes how concentrated employees' usage of "expensive models" is: multi-select models +
a histogram of each employee's "selected-model messages as a share of their total model messages". Click any
histogram column to drill down into that range's members (name/department/email/messages/share), exportable as CSV.
By default it preselects models with a credit multiplier ≥ 2.0x.

### 1.3 Natural-language Q&A
The dashboard has a built-in chat box; ask in natural language (e.g. "which department used the most credits this
month?", "who triggered overage?"), answered by an Agent deployed on **Amazon Bedrock AgentCore Runtime** that calls
controlled query tools, citing the metric and time range used.

### 1.4 Monthly usage report (tier suggestions + suggested shutdown + automated email)
- The in-dashboard "Monthly report" page lists **upgrade / downgrade / suggested-shutdown** groups by usage rate, each
  with a **draggable threshold slider** (defaults: upgrade ≥ 80%, downgrade < 40%, shutdown ≤ 0% i.e. zero usage); dragging updates the list instantly.
- **Suggested shutdown**: lists low-usage subscriptions and identifies **multiple subscriptions of the same employee**
  (grouped by the Identity Center employee number, keeping the earliest-created subscription and suggesting the rest for shutdown next month).
- Before each month-end, a usage report is emailed via **Amazon SES** to the configured recipients (`reportRecipients`) (summary KPIs + tier suggestions); the report is also viewable in the page and downloadable as CSV.

### 1.5 Admin center (multiplier maintenance)
- **Multiplier maintenance**: maintain each model's Kiro credit multiplier (preloaded with official values, editable per model); the model-concentration "expensive model" preselect uses these values.

---

## 2. Deployment guide (for deployers)

Deploy the whole dashboard to your own AWS account. Placeholders below: account `<ACCOUNT_ID>`, region `<REGION>`,
AWS CLI profile `<PROFILE>`.

### 2.1 Prerequisites
- **Node.js 20 or later** and the **AWS CDK CLI** (`npm install -g aws-cdk@2.1142.0`, or use `npx aws-cdk@2.1142.0`)
- **Python 3.12**
- **AWS CLI v2**, configured with target-account credentials:
  ```bash
  aws configure --profile <PROFILE>
  aws sts get-caller-identity --profile <PROFILE>   # confirm the account is correct
  ```
- (Real mode) the target account has a Kiro enterprise subscription with daily usage CSVs exported to an
  S3 bucket; to pull departments/roster from Identity Center you also need the SSO instance info. The demo can skip this (see mock mode).

#### Minimum IAM permissions for deployment

The IAM identity used to deploy (**a user or a role, any name**) needs a set of permissions to run `cdk bootstrap` /
`cdk deploy` and (optionally) the Agent deploy. A ready-made least-privilege policy is provided at
`docs/least-privilege-deploy-policy.json`.

> This is a **single** customer-managed policy. It scopes permissions by service (not `*:*`); for services that need
> many actions (`cloudformation` / `s3` / `lambda` / `cognito-idp` / `apigateway` / `cloudfront` / `ecr`) it uses
> service-level wildcards but scopes resources to this application's naming patterns: stacks `KiroDash-*` and
> `CDKToolkit`, buckets `kirodash-*` and `cdk-hnb659fds-*`, functions `KiroDash-*`, DynamoDB tables `KiroDash-*`, Athena
> workgroup `*-wg`, IAM roles/policies `KiroDash-*` / `cdk-hnb659fds-*` / `KiroDashAgentRuntimeRole`, ECR repositories
> `cdk-hnb659fds-container-assets-*` and `kiro-*`. `iam:PassRole` is scoped to those roles, with a condition restricting
> it to lambda/events/cloudformation/bedrock-agentcore.
>
> The `AccountWideReadOnlyAndUnscopable` statement keeps `"Resource": "*"`: these actions do not support resource-level
> scoping; they are read/describe or create-time calls required by CDK bootstrap (e.g. `cloudformation:ValidateTemplate`,
> `cognito-idp:CreateUserPool`, `ecr:GetAuthorizationToken`, `logs:DescribeLogGroups`, `sts:GetCallerIdentity`). Besides
> that, only `IdentityCenterReadOnly` (identitystore / sso read-only List/Describe) keeps `*`; the write/delete actions for
> glue / events / logs / bedrock-agentcore are each scoped to database `kiro_usage`, rules `KiroDash-*`, log groups `/aws/lambda/KiroDash-*` / `KiroDash-*`, and AgentCore `runtime/*`.
> IAM policies do not support a custom `_comment`-style field (Access Analyzer reports `INVALID_POLICY_ELEMENT`), so the
> design trade-offs for each statement are documented in
> [`docs/least-privilege-deploy-policy.md`](docs/least-privilege-deploy-policy.md).
>
> **Note on the wildcards:** `docs/least-privilege-deploy-policy.json` is a deploy-time administrative bootstrap policy,
> used only by the identity that runs `cdk deploy`. The service-level wildcard actions (`cloudformation` / `s3` / `lambda`
> / `cognito-idp` / `apigateway` / `cloudfront` / `ecr`) and some create/delete actions are intentionally broad: the
> specific resource ARNs only exist after CDK creates them within the same deployment, so they cannot be hardcoded in
> advance. Before production use, scope them down to the exact actions and resource ARNs your deployment actually uses (see
> [`docs/least-privilege-deploy-policy.md`](docs/least-privilege-deploy-policy.md) for the approach and AWS CDK references).
> The runtime Lambda / role policies that the CDK stacks create are already resource-scoped.

**Steps:**

1. **Fill placeholders**: replace `<KIRO_SOURCE_BUCKET>` in the policy with your Kiro daily-usage source bucket name
   (real mode). In mock mode (`mockMode=true`), the external source bucket is not read, so you can delete the whole
   `KiroSourceBucketReadCrossRegion` statement.

2. **Create the managed policy** (replace `<PROFILE>`):
   ```bash
   aws iam create-policy --profile <PROFILE> \
     --policy-name kiro-dashboard-deploy \
     --policy-document file://docs/least-privilege-deploy-policy.json
   ```

3. **Attach it to your deploy identity** (user or role, using the Policy ARN from the previous step):
   ```bash
   # Attach to an IAM user
   aws iam attach-user-policy --profile <PROFILE> \
     --user-name <YOUR_DEPLOY_USER> --policy-arn <POLICY_ARN>
   # Or attach to an IAM role
   aws iam attach-role-policy --profile <PROFILE> \
     --role-name <YOUR_DEPLOY_ROLE> --policy-arn <POLICY_ARN>
   ```

4. Then run the bootstrap / deploy below with that identity's credentials. Success criterion: no `AccessDenied` anywhere,
   and each stack reaches `CREATE_COMPLETE`.

> If you do not deploy the NL Q&A Agent, you can remove the `ecr:*` and `bedrock-agentcore:*` actions from the policy to scope it further.

### 2.2 Python environment
From the repository root:
```bash
python3.12 -m venv .venv
.venv/bin/pip install -r infra/requirements.txt
# Optional (run backend unit tests locally):
.venv/bin/pip install -r backend/requirements-dev.txt
```
> The CDK app runs with the Python in `.venv`, so later commands pass `--app "../.venv/bin/python app.py"`.

### 2.3 Fill in deployment parameters
Edit the `context` in `infra/cdk.json` (field meanings and an example are in `infra/cdk.context.example.json`). Key parameters:

| Parameter | Meaning |
|------|------|
| `deployRegion` | Deployment region, e.g. `us-east-1` |
| `mockMode` | `true` = demo with mock data (the data-source fields below can be blank); production = `false` |
| `kiroSourceBucket` / `kiroSourcePrefix` | S3 bucket and prefix of the Kiro usage CSVs (real mode) |
| `identityStoreId` / `identityStoreRegion` / `ssoInstanceArn` / `subscriptionAppArn` | Identity Center / subscription-app info (real mode, for the roster and departments) |
| `groupCsvBucket` / `groupCsvKey` | Alternative department source: a Kiro-exported user→group CSV |
| `creditUnitPrice` | Credit unit price (Kiro's official overage price) |
| `sesSender` | An **SES-verified** sender email (for the monthly report emails) |
| `reportRecipients` | (Optional) report recipients, comma-separated; when empty the report goes to `sesSender` |
| `agentRuntimeArn` | The NL Q&A Agent's Runtime ARN; leave blank first and back-fill after deploying the Agent |
| `corsOrigins` | The cross-origin origins the API allows (comma-separated or a list), used by both API Gateway CORS and the FastAPI `CORS_ORIGINS`. When unset it is `*`; **for a real deployment set it to the CloudFront site URL** (e.g. `https://dxxxx.cloudfront.net`) |

> `infra/cdk.json` and `frontend/public/config.json` are **placeholder templates** in the repo; after filling your
> account's real values, **do not commit those local changes** (e.g. `git update-index --skip-worktree infra/cdk.json`).

### 2.4 Bootstrap (once per account+region)
```bash
cd infra
AWS_PROFILE=<PROFILE> CDK_DEFAULT_REGION=<REGION> \
  npx cdk bootstrap aws://<ACCOUNT_ID>/<REGION> --app "../.venv/bin/python app.py"
```

### 2.5 Deploy Data / Auth / Api
```bash
export AWS_PROFILE=<PROFILE> CDK_DEFAULT_REGION=<REGION>
npx cdk deploy KiroDash-Data KiroDash-Auth KiroDash-Api --app "../.venv/bin/python app.py" --require-approval never
```
Note the outputs: `UserPoolId`, `UserPoolClientId`, `HostedUiDomain`, `ApiEndpoint`, etc.

> **Rebuild the Lambda build assets before deploy.** `infra/build.py` builds the API dependency layer and app code
> locally, and the Agent uses a deploy-time `build.sh` to assemble its container layer; these build outputs are gitignored,
> so a deployer must (re)generate them before `cdk deploy` (the CDK app invokes `build.py` at synth time; ensure its
> inputs resolve on your machine).

### 2.6 First Web deploy (to get the CloudFront domain)
```bash
cd ../frontend && npm install && npm run build && cd ../infra
npx cdk deploy KiroDash-Web --app "../.venv/bin/python app.py" --require-approval never
```
Note the output `CloudFrontDomain`.

### 2.7 Back-fill callbacks/CORS and redeploy
With the CloudFront domain, add `callbackUrls` / `logoutUrls` / `corsOrigins` to `infra/cdk.json` and redeploy Auth + Api.
Then fill `frontend/public/config.json` from the outputs of 2.5–2.6 (see `config.example.json`), rebuild with
`npm run build`, and `cdk deploy KiroDash-Web`.

### 2.8 Initialize data (real mode)
```bash
# Sync the roster/departments into DynamoDB
aws lambda invoke --function-name <IdentitySyncFnName> --profile <PROFILE> --region <REGION> \
  --cli-binary-format raw-in-base64-out --payload '{}' /tmp/idsync.json && cat /tmp/idsync.json
# Back-fill historical CSV → Parquet
aws lambda invoke --function-name <EtlFnName> --profile <PROFILE> --region <REGION> \
  --cli-binary-format raw-in-base64-out --payload '{"backfill": true}' --cli-read-timeout 900 /tmp/etl.json && cat /tmp/etl.json
```

### 2.9 Create a dashboard user and sign in
The User Pool has self-signup disabled, so the deployer creates every dashboard user with the Cognito console or the
AWS CLI (use the `UserPoolId` output from 2.5):
```bash
aws cognito-idp admin-create-user --user-pool-id <pool-id> --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
  --profile <PROFILE> --region <REGION>
```
Cognito emails the user a temporary password. The user signs in at `https://<cloudfront-domain>` and is prompted to set
a new password on first sign-in.

**Every user in this pool has full dashboard access** (organization-wide usage data and model-multiplier editing), so
only create users who should have it. To revoke access, run `aws cognito-idp admin-user-global-sign-out` and then
`admin-disable-user` or `admin-delete-user` for that user.

### 2.10 (Optional) Deploy the NL Q&A Agent
This requires building an arm64 container with Docker, pushing it to ECR, then deploying to AgentCore Runtime via
`backend/agent/deploy.py`, and back-filling the output runtime ARN into `agentRuntimeArn` in `cdk.json` and redeploying
`KiroDash-Api`. When not deployed, `/api/chat` returns "not configured" and other features are unaffected.

### Cost note
This solution uses billable AWS services (Lambda, Athena, S3, DynamoDB, API Gateway, CloudFront, Cognito, SES, and
optionally Bedrock AgentCore). Costs depend on data volume and traffic. Estimate your spend with the
[AWS Pricing Calculator](https://calculator.aws) before deploying.

### Architecture
See the architecture diagram at [`docs/architecture.drawio`](docs/architecture.drawio) (open with
[diagrams.net](https://www.drawio.com/)) for the serverless component layout and data flow.

---

## 3. Dashboard users

Dashboard sign-in users are the users of the Cognito User Pool. The pool has self-signup disabled; the deployer creates
users with the Cognito console or the AWS CLI (see 2.9). Password policy: ≥ 12 chars, with upper/lowercase, digits, and
symbols. There are no groups or roles: every user in the pool has the same full access.

> The Kiro users "counted" in the dashboard charts are not these sign-in users — they are assigned Kiro seats on the AWS
> side and, after actual use, are brought in automatically by identity-sync / ETL, with no manual addition needed.

---

## 4. Security

- **Authentication**: Amazon Cognito Hosted UI with an OAuth2 authorization-code + PKCE public SPA client. The User Pool
  has self-signup disabled; only the deployer creates users (console / CLI).
- **One boundary for every entry point**: the API Gateway HTTP API validates Cognito JWTs issued by this user pool for
  this app client on every route except `GET /api/health` (liveness probe). `/api/chat` forwards the user's access token
  to the AgentCore Runtime, which validates it with a `customJWTAuthorizer` bound to the same user pool (discovery URL)
  and the same app client (`allowedClients`).
- **Authorization**: any user in the pool can view organization-wide usage data and change model multipliers. There is
  no application-level role or group model; the deployer controls access by deciding who gets a user in the pool.
- **Query safety**: the NL Q&A Agent and API only reach data through the shared, parameterized query layer; `query_usage`
  validates `metric`/`group_by` against an allowlist, and CSV exports neutralize spreadsheet formula injection.
- **Deploy policy**: the deploy identity's permissions are documented in
  [`docs/least-privilege-deploy-policy.md`](docs/least-privilege-deploy-policy.md); the remaining `Resource:"*"` entries are
  genuinely unscopable account-wide / create-time actions.

---

## 5. Local development

```bash
# Backend unit tests
.venv/bin/python -m pytest backend/tests/ -q
# Frontend local dev (reads public/config.json in the same dir, can point at a deployed API/Cognito)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

---

## 6. Metric notes (data accuracy)

- **Credit unit price**: configured by `creditUnitPrice` (Kiro's official overage/add-on price).
- **Derived fields**: usage_rate / balance / overage are all based on each user's `Usage_Limit` (PRO=1000 / PRO+=2000 / PRO_MAX=5000 / POWER=10000).
- **Overage**: cumulative in-range credits > usage_limit.
- **Daily active**: the avg / peak of distinct active users per day in the range.
- **Activation rate**: active users / total registered (total registered = the deduplicated count assigned to the SSO subscription app).
- **Tier**: the user's most recent day's subscription_tier.

---

## 7. SES prerequisites (monthly report)

Emails are sent via Amazon SES, so first **verify the sender email** (`sesSender`) in the deployment region:
```bash
aws ses verify-email-identity --email-address <sesSender> --profile <PROFILE> --region <REGION>
```
A new SES account is in **sandbox mode** by default and can only send to verified recipients; to send to any address,
request production access in the SES console. The sandbox only affects email delivery.

---

## 8. Personal data and compliance

This solution processes employee personal data: emails, display names, and departments from IAM Identity Center, plus
Kiro usage activity (daily usage, models, credits, etc.). This data is stored in **the deployer's own AWS account**
(S3 / Athena / DynamoDB); NL Q&A is processed by Amazon Bedrock in this account, and the monthly report
is sent via Amazon SES to the recipients you configure (the email body and CSV attachments contain these fields).

The deployer (data controller) is responsible for applicable data-protection obligations (e.g. GDPR and local privacy
laws), for following their organization's privacy policy, and for deciding data retention (S3 lifecycle / DynamoDB cleanup)
and access control (who gets a user in the Cognito user pool, IAM permissions). The deployer controls who can
access the dashboard by deciding who gets a user in the pool; every pool user can see organization-wide usage data. Under the GDPR and similar regimes the deployer
determines the purposes and means of processing this personal data. AWS secures the cloud itself, while the customer is
responsible for security of their data and configuration in the cloud — see the
[AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).
