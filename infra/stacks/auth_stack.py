"""AuthStack: Cognito authentication for the dashboard.

- User Pool: self-signup disabled (self_sign_up_enabled=False), email sign-in, strong password.
  Users are created by the deployer (Cognito console / AWS CLI); every user in this pool is an authorized dashboard user.
- App Client: OAuth2 authorization code + PKCE (public client, no secret), with variable callback/logout URLs.
- Hosted UI domain: Cognito-provided <prefix>.auth.<region>.amazoncognito.com.

Outputs userPoolId / clientId / hostedUiDomain / issuer for ApiStack and the frontend.
"""
from __future__ import annotations

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_cognito as cognito,
)
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)

        # Callback URLs: the frontend CloudFront domain only exists after WebStack; v1 uses local + placeholder first,
        # and after deploying WebStack you can override callbackUrls / logoutUrls via context.
        callback_urls = _as_list(cfg.get("callbackUrls")) or [
            "http://localhost:5173/callback", "http://localhost:5173/"]
        logout_urls = _as_list(cfg.get("logoutUrls")) or [
            "http://localhost:5173/", "http://localhost:5173/logout"]
        domain_prefix = cfg.get("cognitoDomainPrefix") or f"kirodash-{self.account}"

        # --- User Pool ---
        pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="kiro-dashboard-admins",
            self_sign_up_enabled=False,                 # disable self-signup
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,        # for the test environment; use RETAIN in production
        )

        # --- Hosted UI domain ---
        domain = pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # --- App Client (OAuth2 code + PKCE, public, no secret) ---
        client = pool.add_client(
            "WebClient",
            user_pool_client_name="kiro-dashboard-web",
            generate_secret=False,                       # SPA public client
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL,
                        cognito.OAuthScope.PROFILE],
                callback_urls=callback_urls,
                logout_urls=logout_urls,
            ),
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            enable_token_revocation=True,
        )

        # --- Expose to other stacks ---
        self.user_pool = pool
        self.user_pool_client = client
        self.issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}"

        hosted_ui = f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com"
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "HostedUiDomain", value=hosted_ui)
        CfnOutput(self, "Issuer", value=self.issuer)


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]
