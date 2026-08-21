# Every role below is scoped to the specific resources this stack creates —
# no `Resource: "*"` on anything that touches data. Where an AWS-managed
# policy is used (e.g. AWSGlueServiceRole), it's because Glue/basic Lambda
# execution genuinely need broad, low-risk, AWS-curated permissions
# (CloudWatch Logs group creation, ENI cleanup) that aren't worth
# hand-rolling and carry no data-plane risk.

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
    # GetItem/BatchGetItem added for departure/arrival detection — reading
    # each aircraft's PREVIOUS state (before overwriting it) is how a
    # ground->climb or descent->ground transition gets noticed at all.
    sid       = "DynamoDbLatestState"
    actions   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [aws_dynamodb_table.latest_state.arn]
  }
  statement {
    sid       = "DynamoDbTrajectories"
    actions   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
    resources = [aws_dynamodb_table.trajectories.arn]
  }
  statement {
    sid       = "DynamoDbFlightRoutes"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.flight_routes.arn]
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
    sid       = "AthenaQuery"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"]
    resources = [aws_athena_workgroup.gold.arn]
  }
  statement {
    sid = "GlueCatalogRead"
    # GetPartitions (plural, listing) was here but not GetPartition/
    # BatchGetPartition (singular/batch, resolving a specific partition) —
    # never surfaced until a WHERE run_ts = '...' query needed Athena to
    # resolve one partition directly instead of scanning the full listing.
    actions = [
      "glue:GetTable", "glue:GetTables", "glue:GetDatabase",
      "glue:GetPartitions", "glue:GetPartition", "glue:BatchGetPartition",
    ]
    resources = [
      aws_glue_catalog_database.gold.arn,
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.gold.name}/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
    ]
  }
  statement {
    sid       = "AthenaResultsBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/athena-results/*", "${aws_s3_bucket.lake.arn}/gold/*"]
  }
  statement {
    sid       = "MlArtifactRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/models/*"]
  }
  statement {
    sid       = "DynamoDbRead"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.latest_state.arn]
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

# --- Bedrock text-to-SQL Lambda ---

resource "aws_iam_role" "lambda_bedrock_sql" {
  name               = "${local.name_prefix}-lambda-bedrock-sql"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_bedrock_sql_policy" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-bedrock-sql*"]
  }
  statement {
    sid       = "BedrockInvoke"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"]
  }
  statement {
    sid       = "AthenaQuery"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"]
    resources = [aws_athena_workgroup.gold.arn]
  }
  statement {
    sid     = "GlueCatalogRead"
    actions = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase"]
    resources = [
      aws_glue_catalog_database.gold.arn,
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.gold.name}/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
    ]
  }
  statement {
    sid       = "AthenaResultsBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/athena-results/*", "${aws_s3_bucket.lake.arn}/gold/*"]
  }
}

resource "aws_iam_role_policy" "lambda_bedrock_sql" {
  name   = "${local.name_prefix}-lambda-bedrock-sql"
  role   = aws_iam_role.lambda_bedrock_sql.id
  policy = data.aws_iam_policy_document.lambda_bedrock_sql_policy.json
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

# --- Transform Lambda role (bronze -> silver -> gold, replaces the blocked Glue Job) ---

resource "aws_iam_role" "lambda_transform" {
  name               = "${local.name_prefix}-lambda-transform"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_transform_policy" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-transform*"]
  }
  statement {
    sid       = "LakeReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    sid     = "GlueCatalog"
    actions = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase", "glue:CreatePartition", "glue:BatchCreatePartition"]
    resources = [
      aws_glue_catalog_database.gold.arn,
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.gold.name}/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
    ]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray write access has no resource-level ARN support
  }
}

resource "aws_iam_role_policy" "lambda_transform" {
  name   = "${local.name_prefix}-lambda-transform"
  role   = aws_iam_role.lambda_transform.id
  policy = data.aws_iam_policy_document.lambda_transform_policy.json
}

# --- Step Functions role ---

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name_prefix}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn_policy" {
  statement {
    sid       = "InvokeTransform"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.transform.arn]
  }
  statement {
    sid     = "ValidateTables"
    actions = ["glue:GetTables"]
    resources = [
      aws_glue_catalog_database.gold.arn,
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.gold.name}/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
    ]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"] # required verbatim by the SFN logging-to-CloudWatch feature, no ARN support
  }
  statement {
    sid       = "SnsNotify"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name_prefix}-sfn"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn_policy.json
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
    resources = [aws_lambda_function.ingest.arn, aws_lambda_function.transform.arn]
  }

  # Lets EventBridge Scheduler start the batch-chain Step Functions
  # execution directly (see aws_scheduler_schedule.batch_chain in
  # stepfunctions.tf) — originally missing entirely, so nothing ever
  # triggered bronze->silver->gold outside manual/one-off invocations.
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.batch_chain.arn]
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
      values   = ["repo:${var.github_repo}:*"]
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
