output "api_endpoint" {
  description = "API Gateway HTTP API base URL."
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "site_url" {
  description = "HTTPS URL for the dashboard, via the S3 REST endpoint (see docs/aws-architecture.md — the S3 *website* endpoint is HTTP-only and many browsers force-upgrade to HTTPS, which fails outright against it; the REST endpoint serves the identical objects over real TLS). No CDN — cloudfront:CreateDistribution is account-restricted."
  value       = "https://${aws_s3_bucket.site.id}.s3.${var.aws_region}.amazonaws.com/index.html"
}

output "site_url_http_website_endpoint" {
  description = "The S3 static website endpoint (HTTP only, no TLS) — kept for reference/scripts that don't force HTTPS. Browsers should use `site_url` instead."
  value       = "http://${aws_s3_bucket_website_configuration.site.website_endpoint}"
}

output "lake_bucket" {
  value = aws_s3_bucket.lake.id
}

output "site_bucket" {
  value = aws_s3_bucket.site.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "github_actions_role_arn" {
  description = "Role for GitHub Actions OIDC — set as AWS_ROLE_ARN in repo secrets/vars."
  value       = var.github_repo != "" ? aws_iam_role.github_actions[0].arn : null
}
