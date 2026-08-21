# --- S3: the lakehouse bucket (bronze/silver/gold prefixes) ---

resource "aws_s3_bucket" "lake" {
  bucket = "${local.name_prefix}-lake-${data.aws_caller_identity.current.account_id}"

  # Versioning intentionally off — this is a demo dataset re-derivable from
  # OpenSky at any time, and versioning would silently accumulate storage
  # cost across the 5-minute ingestion cadence.
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

  # athena-results/ gets its own shorter rule: every API request that hits
  # Athena (/api/stats/*, /api/corridors, /api/anomalies) writes a result
  # object here, so it churns much faster than the actual lake data and was
  # found growing unbounded (18k+ objects) with no expiry applied at all —
  # this and the bronze/silver/gold rule below were declared here but never
  # actually reconciled against the live bucket, which is the drift this
  # fixes.
  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }

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

  rule {
    id     = "expire-silver"
    status = "Enabled"
    filter {
      prefix = "silver/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-gold"
    status = "Enabled"
    filter {
      prefix = "gold/"
    }

    expiration {
      days = 30
    }
  }
}

# Empty marker objects so the bronze/silver/gold layout is visible in the
# console even before the first ingestion run.
resource "aws_s3_object" "layer_markers" {
  for_each = toset(["bronze/", "silver/", "gold/", "athena-results/"])

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

# --- S3: Athena query results (separate prefix already covered above, but
#     Athena workgroups conventionally point at their own location) ---
# Uses the same bucket's athena-results/ prefix, configured in athena.tf.

# --- DynamoDB: latest state per icao24 ---

resource "aws_dynamodb_table" "latest_state" {
  name         = "${local.name_prefix}-latest-state"
  billing_mode = "PAY_PER_REQUEST" # on-demand — no provisioned-capacity cost when idle
  hash_key     = "icao24"

  attribute {
    name = "icao24"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
