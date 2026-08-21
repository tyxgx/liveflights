# --- Transform Lambda: bronze -> silver -> gold (replaces the blocked Glue Job) ---

resource "aws_ecr_repository" "transform" {
  name                 = "${local.name_prefix}-transform"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # demo repo — allow `terraform destroy` to clean it up without a manual image purge first

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "transform" {
  repository = aws_ecr_repository.transform.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 3 images only"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "null_resource" "transform_image" {
  triggers = {
    handler_hash      = filesha256("${path.module}/lambda_transform/handler.py")
    dockerfile_hash   = filesha256("${path.module}/lambda_transform/Dockerfile")
    requirements_hash = filesha256("${path.module}/lambda_transform/requirements.txt")
  }

  provisioner "local-exec" {
    working_dir = path.module
    command     = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.transform.repository_url}
      docker build --platform linux/amd64 --provenance=false --sbom=false --output type=docker -f lambda_transform/Dockerfile -t ${aws_ecr_repository.transform.repository_url}:latest .
      docker push ${aws_ecr_repository.transform.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.transform]
}

data "aws_ecr_image" "transform_latest" {
  repository_name = aws_ecr_repository.transform.name
  image_tag       = "latest"
  depends_on      = [null_resource.transform_image]
}

resource "aws_cloudwatch_log_group" "lambda_transform" {
  name              = "/aws/lambda/${local.name_prefix}-transform"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "transform" {
  function_name = "${local.name_prefix}-transform"
  role          = aws_iam_role.lambda_transform.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.transform.repository_url}@${data.aws_ecr_image.transform_latest.image_digest}"
  timeout       = 300
  memory_size   = 1024

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      LAKE_BUCKET   = aws_s3_bucket.lake.id
      INPUT_PREFIX  = "bronze/"
      GLUE_DATABASE = aws_glue_catalog_database.gold.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_transform, null_resource.transform_image]
}

# --- Batch chain schedule: bronze -> silver -> gold, on a timer ---
#
# Originally missing entirely — nothing scheduled ever triggered
# aws_sfn_state_machine.batch_chain, so bronze accumulated continuously
# while silver/gold only ever got the handful of manual/one-off executions
# run during initial setup. This closes that gap.
resource "aws_scheduler_schedule" "batch_chain" {
  name       = "${local.name_prefix}-batch-chain-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = var.batch_chain_schedule_expression

  target {
    arn      = aws_sfn_state_machine.batch_chain.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

# --- Daily corridor retrain: separate schedule from the batch chain above ---
#
# DBSCAN over the full accumulated silver history on every batch-chain run
# would be wasteful (corridors are a slowly-changing structure, not
# something that needs re-fitting every 10 minutes) — this invokes the same
# transform Lambda directly (bypassing Step Functions) with a
# {"retrain_corridors": true} payload that routes to retrain_corridors()
# instead of the normal bronze->silver->gold path.
resource "aws_scheduler_schedule" "corridor_retrain" {
  name       = "${local.name_prefix}-corridor-retrain"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "rate(1 day)"

  target {
    arn      = aws_lambda_function.transform.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ retrain_corridors = true, region = var.simulator_region })

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}

resource "aws_lambda_permission" "allow_scheduler_retrain" {
  statement_id  = "AllowEventBridgeSchedulerRetrain"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transform.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.corridor_retrain.arn
}
