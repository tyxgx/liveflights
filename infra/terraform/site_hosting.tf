# S3 static website hosting for the Next.js static export.
#
# `cloudfront:CreateDistribution` is blocked by the same account-level
# restriction as Glue Job/Crawler (IAM allows it, the API call is denied —
# see docs/aws-architecture.md). S3 website hosting gives up a CDN (no edge
# caching, no HTTPS on the bucket endpoint itself, no SPA-style rewrite to a
# 200 status) but still serves the exact same static export at a public URL,
# which is what this demo needs.

resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  index_document {
    suffix = "index.html"
  }

  # S3 website hosting can't rewrite the status code the way CloudFront's
  # custom_error_response did (client routes still 404, just render the app
  # shell instead of a raw S3 XML error) — a real CDN limitation, not a bug.
  error_document {
    key = "index.html"
  }
}

data "aws_iam_policy_document" "site_bucket_policy" {
  statement {
    sid       = "PublicReadForWebsiteHosting"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket     = aws_s3_bucket.site.id
  policy     = data.aws_iam_policy_document.site_bucket_policy.json
  depends_on = [aws_s3_bucket_public_access_block.site]
}
