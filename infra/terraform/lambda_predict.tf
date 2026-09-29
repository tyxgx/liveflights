# --- Trajectory-prediction Lambda: container image, same pattern as lambda_api.tf ---
#
# A container image, not the ingest Lambda's plain zip, because this one needs numpy +
# onnxruntime (predict/Dockerfile bundles them + the ONNX model's normalisation stats + the VRS
# route lookup CSVs) — no pure-stdlib way to run an ONNX model. Runs every minute, right after the
# ingest Lambda (both on the same rate(1 minute) schedule; there is no built-in stagger between two
# separate EventBridge Scheduler rate() rules, so predict occasionally reads live/history.json a
# few seconds before that minute's ingest write lands — harmless, it just builds that aircraft's
# window from readings anchored one poll further back, still all ~60s apart).

resource "aws_ecr_repository" "predict" {
  name                 = "${local.name_prefix}-predict"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # demo repo — allow `terraform destroy` to clean it up without a manual image purge first

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "predict" {
  repository = aws_ecr_repository.predict.name
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

resource "null_resource" "predict_image" {
  triggers = {
    handler_hash      = filesha256("${path.module}/../../predict/handler.py")
    dockerfile_hash   = filesha256("${path.module}/../../predict/Dockerfile")
    requirements_hash = filesha256("${path.module}/../../predict/requirements.txt")
    features_hash     = filesha256("${path.module}/../../ml/features.py")
    routelookup_hash  = filesha256("${path.module}/../../ml/route_lookup.py")
    routes_csv_hash   = filesha256("${path.module}/../../data/vrs/routes.csv")
    airports_csv_hash = filesha256("${path.module}/../../data/vrs/airports.csv")
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../.."
    command     = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.predict.repository_url}
      docker build --platform linux/amd64 --provenance=false --sbom=false --output type=docker -f predict/Dockerfile -t ${aws_ecr_repository.predict.repository_url}:latest .
      docker push ${aws_ecr_repository.predict.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.predict]
}

data "aws_ecr_image" "predict_latest" {
  repository_name = aws_ecr_repository.predict.name
  image_tag       = "latest"
  depends_on      = [null_resource.predict_image]
}

resource "aws_cloudwatch_log_group" "lambda_predict" {
  name              = "/aws/lambda/${local.name_prefix}-predict"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "predict" {
  function_name = "${local.name_prefix}-predict"
  role          = aws_iam_role.lambda_predict.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.predict.repository_url}@${data.aws_ecr_image.predict_latest.image_digest}"
  # 512 MB was the first guess and it was wrong: the first real deploy (2026-09-28) hit both a
  # 60s timeout AND Runtime.OutOfMemory at 512 MB, root-caused to (1) calling the ONNX model once
  # per aircraft instead of batched, and (2) the route lookup table alone using 200-450 MB. Both
  # are fixed in code now (predict/handler.py's _predict_batch, ml/route_lookup.py's RouteTable) -
  # local end-to-end replay of the same real data after the fix: 1.59s, 327 MB RSS. Raised to 1024
  # anyway as a safety margin (Lambda's real runtime overhead differs a bit from local testing, and
  # more memory also means more allocated CPU, which is itself part of the timeout fix).
  timeout     = 60
  memory_size = 1024

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      LAKE_BUCKET_NAME = aws_s3_bucket.lake.id
      MODEL_KEY        = "models/trajectory.onnx"
      NORM_KEY         = "models/trajectory_norm.json"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_predict, null_resource.predict_image]
}

# --- EventBridge Scheduler: every 1 minute, same rate as the ingest schedule ---

resource "aws_scheduler_schedule" "predict" {
  name       = "${local.name_prefix}-predict-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = var.schedule_expression

  target {
    arn      = aws_lambda_function.predict.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

resource "aws_lambda_permission" "allow_scheduler_predict" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.predict.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.predict.arn
}
