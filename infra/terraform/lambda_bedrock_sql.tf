data "archive_file" "lambda_bedrock_sql" {
  type        = "zip"
  source_file = "${path.module}/lambda_bedrock_sql/handler.py"
  output_path = "${path.module}/build/lambda_bedrock_sql.zip"
}

resource "aws_cloudwatch_log_group" "lambda_bedrock_sql" {
  name              = "/aws/lambda/${local.name_prefix}-bedrock-sql"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "bedrock_sql" {
  function_name = "${local.name_prefix}-bedrock-sql"
  role          = aws_iam_role.lambda_bedrock_sql.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.lambda_bedrock_sql.output_path
  source_code_hash = data.archive_file.lambda_bedrock_sql.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      ATHENA_DATABASE  = aws_glue_catalog_database.gold.name
      ATHENA_WORKGROUP = aws_athena_workgroup.gold.name
      BEDROCK_MODEL_ID = var.bedrock_model_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_bedrock_sql]
}

resource "aws_apigatewayv2_integration" "bedrock_sql" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.bedrock_sql.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "bedrock_sql" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /api/ask"
  target    = "integrations/${aws_apigatewayv2_integration.bedrock_sql.id}"
}

resource "aws_lambda_permission" "apigw_bedrock_sql" {
  statement_id  = "AllowApiGatewayInvokeBedrockSql"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bedrock_sql.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
