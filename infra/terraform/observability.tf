resource "aws_sns_topic" "alarms" {
  name = "${local.name_prefix}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.alarm_notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_notification_email
}

# --- Lambda error alarms ---

resource "aws_cloudwatch_metric_alarm" "lambda_ingest_errors" {
  alarm_name          = "${local.name_prefix}-ingest-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Ingestion Lambda raised an error in the last 5 minutes."
  dimensions = {
    FunctionName = aws_lambda_function.ingest.function_name
  }
  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_api_errors" {
  alarm_name          = "${local.name_prefix}-api-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "API Lambda raised an error in the last 5 minutes."
  dimensions = {
    FunctionName = aws_lambda_function.api.function_name
  }
  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# --- Firehose delivery failures ---

resource "aws_cloudwatch_metric_alarm" "firehose_delivery_failures" {
  alarm_name          = "${local.name_prefix}-firehose-delivery-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DeliveryToS3.DataFreshness"
  namespace           = "AWS/Firehose"
  period              = 900
  statistic           = "Maximum"
  threshold           = 900 # seconds — data sitting in the buffer > 15 min means delivery is stuck
  alarm_description   = "Firehose has not delivered to S3 within the expected freshness window."
  dimensions = {
    DeliveryStreamName = aws_kinesis_firehose_delivery_stream.lake.name
  }
  alarm_actions      = [aws_sns_topic.alarms.arn]
  treat_missing_data = "notBreaching"
}

# --- Freshness alarm: no ingestion Lambda invocation in 30 minutes ---

resource "aws_cloudwatch_metric_alarm" "ingestion_freshness" {
  alarm_name          = "${local.name_prefix}-no-data-30min"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 1800
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "No successful ingestion Lambda invocation in the last 30 minutes — the 5-minute schedule should produce at least 6."
  dimensions = {
    FunctionName = aws_lambda_function.ingest.function_name
  }
  alarm_actions      = [aws_sns_topic.alarms.arn]
  treat_missing_data = "breaching"
}

# --- Dashboard ---

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name_prefix}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Ingestion Lambda invocations"
          region  = var.aws_region
          metrics = [["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.ingest.function_name]]
          stat    = "Sum"
          period  = 300
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Lambda duration (ms)"
          region = var.aws_region
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.ingest.function_name],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.api.function_name],
          ]
          stat   = "Average"
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Errors across all Lambdas"
          region = var.aws_region
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.ingest.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.api.function_name],
          ]
          stat   = "Sum"
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "API Gateway requests / 4xx / 5xx"
          region = var.aws_region
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.api.id],
            ["AWS/ApiGateway", "4xx", "ApiId", aws_apigatewayv2_api.api.id],
            ["AWS/ApiGateway", "5xx", "ApiId", aws_apigatewayv2_api.api.id],
          ]
          stat   = "Sum"
          period = 300
        }
      },
    ]
  })
}
