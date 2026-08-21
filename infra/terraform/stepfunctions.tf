resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${local.name_prefix}-batch-chain"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "batch_chain" {
  name     = "${local.name_prefix}-batch-chain"
  role_arn = aws_iam_role.sfn.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  definition = jsonencode({
    Comment = "bronze -> silver/gold transform Lambda (Glue Job/Crawler are account-restricted, see docs/aws-architecture.md), then validate tables."
    StartAt = "RunTransform"
    States = {
      RunTransform = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.transform.arn
        }
        Retry = [{
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "States.TaskFailed"]
          IntervalSeconds = 30
          MaxAttempts     = 2
          BackoffRate     = 2.0
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "NotifyFailure"
          ResultPath  = "$.error"
        }]
        Next = "ValidateTables"
      }
      ValidateTables = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:getTables"
        Parameters = {
          DatabaseName = aws_glue_catalog_database.gold.name
        }
        ResultPath = "$.tables"
        Next       = "Succeed"
      }
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn = aws_sns_topic.alarms.arn
          Message  = "liveflights batch-chain failed"
        }
        Next = "Fail"
      }
      Succeed = { Type = "Succeed" }
      Fail    = { Type = "Fail" }
    }
  })
}
