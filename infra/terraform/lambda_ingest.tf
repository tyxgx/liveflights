# --- Ingestion Lambda: plain zip, stdlib + boto3 only, no layer needed ---

# Vendors ingestion/simulator.py + airports.py + the adsb.lol pure-mapping
# module (repo root, two levels up from this module) into the zip alongside
# handler.py, so the Lambda can reuse the exact same simulator and adsb.lol
# field-mapping logic as the local producer without a build step or a
# pydantic dependency — simulator.tick() and map_to_flight_state_dict() both
# return plain dicts. ingestion/__init__.py and ingestion/schemas/__init__.py
# are synthetic empty files here, not the real repo files, so importing
# these two modules doesn't pull in the real ingestion/schemas/__init__.py
# (which eagerly imports pydantic-dependent modules the Lambda doesn't have).
data "archive_file" "lambda_ingest" {
  type        = "zip"
  output_path = "${path.module}/build/lambda_ingest.zip"

  source {
    content  = file("${path.module}/lambda_ingest/handler.py")
    filename = "handler.py"
  }
  source {
    content  = ""
    filename = "ingestion/__init__.py"
  }
  source {
    content  = file("${path.module}/../../ingestion/airports.py")
    filename = "ingestion/airports.py"
  }
  source {
    content  = file("${path.module}/../../ingestion/simulator.py")
    filename = "ingestion/simulator.py"
  }
  source {
    content  = ""
    filename = "ingestion/schemas/__init__.py"
  }
  source {
    content  = file("${path.module}/../../ingestion/schemas/adsb_lol_mapping.py")
    filename = "ingestion/schemas/adsb_lol_mapping.py"
  }
}

resource "aws_cloudwatch_log_group" "lambda_ingest" {
  name              = "/aws/lambda/${local.name_prefix}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name = "${local.name_prefix}-ingest"
  role          = aws_iam_role.lambda_ingest.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  # 90s, not the original 60s: fetching adsb_lol_points in parallel with a
  # stagger + retry-on-429 budget (see ADSB_LOL_* vars below) pushed a worst
  # case (several points rate-limited, each retrying) close to 60s.
  timeout     = 90
  memory_size = 256

  filename         = data.archive_file.lambda_ingest.output_path
  source_code_hash = data.archive_file.lambda_ingest.output_base64sha256

  tracing_config {
    mode = "Active" # X-Ray
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.ingest_dlq.arn
  }

  environment {
    variables = {
      FIREHOSE_STREAM_NAME = aws_kinesis_firehose_delivery_stream.lake.name
      DYNAMODB_TABLE_NAME  = aws_dynamodb_table.latest_state.name
      # OpenSky is unreachable from this Lambda's egress IP (see
      # docs/aws-architecture.md) — adsb.lol (a community aggregator) IS
      # reachable and is the primary source; the simulator below is only the
      # fallback if that live fetch fails, so the pipeline never goes dark.
      SIMULATOR_REGION         = var.simulator_region
      SIMULATOR_AIRCRAFT_COUNT = "40"
      SIMULATOR_ANOMALY_RATE   = "0.02"
      # adsb.lol's point+radius endpoint is capped at 250nm — one point
      # can't cover a continent, so the ingest Lambda fans out to every
      # point here and merges results (deduped by icao24). See
      # docs/aws-architecture.md for why these specific hub points.
      ADSB_LOL_POINTS          = jsonencode(var.adsb_lol_points)
      ADSB_LOL_MAX_WORKERS     = tostring(var.adsb_lol_max_workers)
      ADSB_LOL_STAGGER_SECONDS = tostring(var.adsb_lol_stagger_seconds)
      ADSB_LOL_RETRY_ATTEMPTS  = tostring(var.adsb_lol_retry_attempts)
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_ingest]
}

# --- EventBridge Scheduler: every 1 minute (var.schedule_expression) ---

resource "aws_scheduler_schedule" "ingest" {
  name       = "${local.name_prefix}-ingest-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = var.schedule_expression

  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

resource "aws_lambda_permission" "allow_scheduler" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.ingest.arn
}

# --- Kinesis Firehose: buffers ingestion Lambda output to S3 bronze/ ---

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_stream" "firehose" {
  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_kinesis_firehose_delivery_stream" "lake" {
  name        = "${local.name_prefix}-lake"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.lake.arn
    prefix              = "bronze/ingest_date=!{timestamp:yyyy-MM-dd}/ingest_hour=!{timestamp:HH}/"
    error_output_prefix = "bronze-errors/!{firehose:error-output-type}/"

    # 5 MB / 60s buffering: at one poll every 1 minute producing a single
    # multi-KB record, this almost always flushes on the time interval, not
    # the size threshold. 60s is the AWS-enforced minimum for
    # buffering_interval — matches the new 1-minute ingestion cadence so
    # each invocation's single PutRecord call (still one Firehose record
    # per invocation, avoiding the 5KB-per-record billing minimum) lands in
    # S3 promptly instead of sitting buffered for up to 5x its own cadence.
    buffering_size     = 5
    buffering_interval = 60

    compression_format = "GZIP" # Parquet conversion adds a Glue-schema dependency for marginal benefit at this volume; GZIP JSON is queryable by Athena as-is

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose.name
    }
  }
}
