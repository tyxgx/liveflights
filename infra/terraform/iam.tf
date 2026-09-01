# Every role below is scoped to the specific resources this stack creates —
# no `Resource: "*"` on anything that touches data.
#
# NOTE (Aug 2026): this stack was simplified down to a live-data-only MVP —
# ML (corridors/anomalies/forecast/trajectory-tracking) is paused, along
# with everything that only existed to serve it: DynamoDB (3 tables),
# Step Functions, the transform Lambda, Glue Catalog, Athena, and the
# Bedrock text-to-SQL Lambda. Removed here rather than left dangling and
# unused — see docs/aws-architecture.md for the reasoning (this was also
# the fix for a real ~$155/mo DynamoDB write-cost problem, not just a
# scope cut). Re-add when ML work resumes.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# --- Ingestion Lambda ---

resource "aws_iam_role" "lambda_ingest" {
  name               = "${local.name_prefix}-lambda-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_ingest_policy" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-ingest*"]
  }
  statement {
    sid       = "Firehose"
    actions   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [aws_kinesis_firehose_delivery_stream.lake.arn]
  }
  statement {
    # Replaces the old DynamoDB latest-state write: one small JSON object,
    # overwritten every poll, instead of a full-table item-by-item rewrite
    # (the actual root cause of the ~$155/mo DynamoDB bill this replaced —
    # ~4,600 items x 60 writes/hour vs. one S3 PUT/min here).
    sid       = "LiveStateWrite"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/live/*", "${aws_s3_bucket.lake.arn}/stats/*"]
  }
  statement {
    # Same reasoning as api's LiveStateList: _update_hourly_stats does a
    # read-modify-write on stats/hourly.json, and without ListBucket a
    # GetObject on that key before it exists for the first time comes back
    # AccessDenied instead of NoSuchKey, so the handler's except-NoSuchKey
    # fallback never fires. No s3:prefix condition here (tried that first —
    # S3's implicit "does this key exist" check that triggers this whole
    # 403-vs-404 situation doesn't populate an s3:prefix value, so a
    # StringLike condition on it evaluates false and the ListBucket grant
    # never actually applies). This is metadata-listing only (object keys,
    # not contents), on a bucket that already has narrowly-scoped read/write
    # elsewhere, so bucket-wide is an acceptable trade for it actually working.
    sid       = "LiveStateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }
  statement {
    sid       = "SsmRead"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.opensky_client_id.arn, aws_ssm_parameter.opensky_client_secret.arn]
  }
  statement {
    sid       = "Dlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray write access has no resource-level ARN support
  }
}

resource "aws_iam_role_policy" "lambda_ingest" {
  name   = "${local.name_prefix}-lambda-ingest"
  role   = aws_iam_role.lambda_ingest.id
  policy = data.aws_iam_policy_document.lambda_ingest_policy.json
}

# --- API Lambda (Mangum/FastAPI) ---

resource "aws_iam_role" "lambda_api" {
  name               = "${local.name_prefix}-lambda-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_api_policy" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-api*"]
  }
  statement {
    # Read-only: the live snapshot + the small rolling hourly-stats file.
    # Every stat the API serves is computed on the fly from these, in
    # Python, in the Lambda itself — no Athena/Glue query round-trip.
    # models/* added when ML (corridors/anomalies/forecast) resumed as small
    # static artifacts (corridors.json, anomaly_threshold.txt,
    # forecast_gbr.joblib) — same read-only, no-warehouse philosophy, just
    # a few more tiny S3 objects.
    sid       = "LiveStateRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/live/*", "${aws_s3_bucket.lake.arn}/stats/*", "${aws_s3_bucket.lake.arn}/models/*"]
  }
  statement {
    # Without ListBucket, a GetObject on a *missing* key (e.g. stats/hourly.json
    # before the ingest Lambda has written it once) comes back as an opaque
    # AccessDenied instead of NoSuchKey — S3 hides whether the object exists
    # from callers with no listing rights. app.py's _load_json only handles
    # NoSuchKey, so without this the API 500s until that file happens to exist.
    # No s3:prefix condition (tried that first — the implicit existence check
    # that triggers this doesn't populate s3:prefix, so a StringLike condition
    # on it always evaluates false and silently never grants anything).
    sid       = "LiveStateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda_api" {
  name   = "${local.name_prefix}-lambda-api"
  role   = aws_iam_role.lambda_api.id
  policy = data.aws_iam_policy_document.lambda_api_policy.json
}

# --- Firehose delivery role (writes to S3) ---

data "aws_iam_policy_document" "firehose_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${local.name_prefix}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json
}

data "aws_iam_policy_document" "firehose_policy" {
  statement {
    sid       = "S3Write"
    actions   = ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/bronze/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/kinesisfirehose/${local.name_prefix}*:*"]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.name_prefix}-firehose"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose_policy.json
}

# --- EventBridge Scheduler role (invokes the ingestion Lambda) ---

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_policy" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.ingest.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name_prefix}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_policy.json
}

# --- GitHub Actions OIDC (CI/CD, no long-lived access keys) ---

resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_repo != "" ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_assume" {
  count = var.github_repo != "" ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      # Not just "repo:${var.github_repo}:*" — every CI run has been denied
      # with "Not authorized to perform sts:AssumeRoleWithWebIdentity" since
      # this stack's very first deploy, and CloudTrail shows why: GitHub
      # actually sends `sub` as "repo:tyxgx@175643418/liveflights@1315793014:
      # ref:refs/heads/main", not the plain "repo:owner/repo:..." form. GitHub
      # permanently switches to this owner-id/repo-id-suffixed format for any
      # repo (or owner) that's ever been renamed, specifically so a renamed-
      # away login can't be re-registered by someone else to steal a role
      # that still trusts the old name. github_repo's plain "owner/repo"
      # value can't express that suffix, so it's hardcoded here instead.
      values = ["repo:tyxgx@175643418/liveflights@1315793014:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  count = var.github_repo != "" ? 1 : 0

  name               = "${local.name_prefix}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume[0].json
}

data "aws_iam_policy_document" "github_actions_policy" {
  count = var.github_repo != "" ? 1 : 0

  statement {
    sid = "EcrPushApiImage"
    actions = [
      "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:PutImage",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
    ]
    resources = ["*"] # GetAuthorizationToken has no resource ARN; the push actions are further scoped by repo policy on aws_ecr_repository.api
  }
  statement {
    sid       = "DeploySite"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
  }
  statement {
    sid       = "UpdateLambda"
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunction"]
    resources = [aws_lambda_function.api.arn]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  count = var.github_repo != "" ? 1 : 0

  name   = "${local.name_prefix}-github-actions"
  role   = aws_iam_role.github_actions[0].id
  policy = data.aws_iam_policy_document.github_actions_policy[0].json
}
