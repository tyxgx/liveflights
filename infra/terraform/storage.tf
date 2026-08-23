# --- S3: the lakehouse bucket ---
#
# NOTE (Aug 2026): simplified to a live-data-only MVP — ML is paused, so
# silver/gold/athena-results/models/ (all ML-pipeline outputs) are gone.
# What's left: bronze/ (raw historical archive, kept cheap for when ML
# resumes) and two new prefixes that replace DynamoDB entirely for the
# live dashboard — live/ (one small overwritten JSON snapshot) and
# stats/ (one small rolling hourly-aggregate JSON). See iam.tf and
# docs/aws-architecture.md for why: DynamoDB's full-table rewrite every
# poll was a real ~$155/mo problem, and a single overwritten S3 object is
# functionally equivalent for "what does the dashboard show right now."

resource "aws_s3_bucket" "lake" {
  bucket = "${local.name_prefix}-lake-${data.aws_caller_identity.current.account_id}"

  # Versioning intentionally off — this is a demo dataset re-derivable from
  # OpenSky at any time, and versioning would silently accumulate storage
  # cost across the 1-minute ingestion cadence.
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, not SSE-KMS: a customer-managed KMS key is ~$1/month flat
      # regardless of usage, which alone would eat 20% of the $5 budget.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  # bronze/ is the only prefix that actually accumulates unboundedly (one
  # object per poll) — live/ and stats/ are single objects, overwritten in
  # place every run, so they never grow and need no expiry rule.
  rule {
    id     = "expire-bronze"
    status = "Enabled"
    filter {
      prefix = "bronze/"
    }

    expiration {
      days = 30
    }
  }
}

# Empty marker objects so the bronze/live/stats layout is visible in the
# console even before the first ingestion run.
resource "aws_s3_object" "layer_markers" {
  for_each = toset(["bronze/", "live/", "stats/"])

  bucket  = aws_s3_bucket.lake.id
  key     = "${each.value}.keep"
  content = ""
}

# --- S3: Next.js static export + CloudFront origin ---

resource "aws_s3_bucket" "site" {
  bucket = "${local.name_prefix}-site-${data.aws_caller_identity.current.account_id}"
}

# Public access block + bucket policy for this bucket are in site_hosting.tf
# (must allow public reads for S3 static website hosting, unlike the lake
# bucket above).

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
