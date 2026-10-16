"""WebStack: the frontend static site (private S3 bucket + CloudFront + OAC).

- Private S3 bucket (BLOCK_ALL), accessed only by CloudFront via OAC (no direct public read).
- CloudFront: default root object index.html; SPA route fallback (403/404 -> /index.html, 200).
- Security response headers (HSTS / X-Content-Type-Options / frame-deny).
- Deploys the frontend/dist static assets; config.json is written by the deploy script once the CloudFront domain is known.

Depends on: the frontend has already run `npm run build` to produce dist/. The CloudFront domain is only known after deployment,
so the Cognito callback URL and the final dist/config.json values are back-filled by the deploy process (see README/deploy script).
"""
from __future__ import annotations

import os

from aws_cdk import (
    Stack, RemovalPolicy, Duration, CfnOutput,
    aws_s3 as s3,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3d,
)
from constructs import Construct

FRONTEND_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))


class WebStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cfg: dict, **kw):
        super().__init__(scope, cid, **kw)

        # --- Private site bucket ---
        site = s3.Bucket(
            self, "SiteBucket",
            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True)

        # --- Security response headers ---
        headers = cf.ResponseHeadersPolicy(
            self, "SecHeaders",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.SAME_ORIGIN, override=True),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True, override=True),
            ))

        # --- CloudFront + OAC（S3BucketOrigin.with_origin_access_control）---
        dist = cf.Distribution(
            self, "Distribution",
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
            ),
            # SPA: client-side routing, unmatched paths fall back to index.html
            error_responses=[
                cf.ErrorResponse(http_status=403, response_http_status=200,
                                 response_page_path="/index.html", ttl=Duration.minutes(5)),
                cf.ErrorResponse(http_status=404, response_http_status=200,
                                 response_page_path="/index.html", ttl=Duration.minutes(5)),
            ],
            comment="Kiro usage dashboard SPA",
        )

        # --- Deploy dist (if already built) ---
        if os.path.isdir(FRONTEND_DIST):
            s3d.BucketDeployment(
                self, "DeploySite",
                sources=[s3d.Source.asset(FRONTEND_DIST)],
                destination_bucket=site,
                distribution=dist,
                distribution_paths=["/*"],  # invalidate the cache after deploy
            )

        self.distribution = dist
        self.site_bucket = site
        CfnOutput(self, "CloudFrontDomain", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "SiteBucketName", value=site.bucket_name)
        CfnOutput(self, "DistributionId", value=dist.distribution_id)
