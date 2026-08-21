resource "aws_ecr_repository" "api" {
  name                 = "${local.name_prefix}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # demo repo — allow `terraform destroy` to clean it up without a manual image purge first

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
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

# Building and pushing the image is part of `terraform apply` itself (via
# local-exec) rather than a separate manual step, so the whole stack really
# does come up from one command. Re-runs only rebuild/push when the image
# source actually changed, via the sha256 trigger below.
resource "null_resource" "api_image" {
  triggers = {
    app_hash          = filesha256("${path.module}/../../api/cloud/app.py")
    dockerfile_hash   = filesha256("${path.module}/../../api/cloud/Dockerfile")
    requirements_hash = filesha256("${path.module}/../../api/cloud/requirements.txt")
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../.."
    command     = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.api.repository_url}
      # --provenance=false --sbom=false alone still leave the containerd
      # snapshotter's default OCI *image index* (manifest list) output, which
      # Lambda's CreateFunction rejects ("image manifest ... not supported");
      # --output type=docker forces a flat single manifest instead.
      docker build --platform linux/amd64 --provenance=false --sbom=false --output type=docker -f api/cloud/Dockerfile -t ${aws_ecr_repository.api.repository_url}:latest .
      docker push ${aws_ecr_repository.api.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.api]
}

data "aws_ecr_image" "api_latest" {
  repository_name = aws_ecr_repository.api.name
  image_tag       = "latest"
  depends_on      = [null_resource.api_image]
}

resource "aws_cloudwatch_log_group" "lambda_api" {
  name              = "/aws/lambda/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name_prefix}-api"
  role          = aws_iam_role.lambda_api.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}@${data.aws_ecr_image.api_latest.image_digest}"
  timeout       = 30
  memory_size   = 512

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE_NAME   = aws_dynamodb_table.latest_state.name
      ATHENA_DATABASE       = aws_glue_catalog_database.gold.name
      ATHENA_WORKGROUP      = aws_athena_workgroup.gold.name
      LAKE_BUCKET           = aws_s3_bucket.lake.id
      CORRIDOR_ARTIFACT_KEY = "models/corridors_v1.json"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_api, null_resource.api_image]
}

# --- API Gateway HTTP API (cheaper than REST API for this traffic level) ---

resource "aws_apigatewayv2_api" "api" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"] # read-only, no-auth demo endpoints; tighten to the CloudFront domain if this becomes more than a demo
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "api_default" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_cloudwatch_log_group" "apigw" {
  name              = "/aws/apigateway/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "api" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  # Throttling: a demo endpoint on a $5 budget should fail loud (429) long
  # before it racks up a real Lambda/Athena bill from a traffic spike or
  # accidental loop.
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw.arn
    format = jsonencode({
      requestId = "$context.requestId"
      status    = "$context.status"
      path      = "$context.path"
      latency   = "$context.responseLatency"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
