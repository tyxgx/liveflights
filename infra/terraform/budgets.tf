# Spend guardrail. Two postmortem lessons are baked in (docs/postmortems.md):
#   * `include_credit = false` -- Cost Explorer/Budgets default to *net of credits*,
#     which shows $0 while credits last and hid a real ~$39 EC2 bill. This budget
#     tracks gross usage so credits cannot mask a runaway resource.
#   * The alert fires on the forecast too, so it triggers before the money is spent.
#
# Steady-state cost of this stack is roughly $1.5-2/month, so a $5 limit with an
# early actual-spend warning at 40% ($2) leaves a clear signal and no false alarms.

variable "monthly_budget_usd" {
  description = "Monthly gross-usage budget in USD (credits are NOT netted out)."
  type        = string
  default     = "5"
}

resource "aws_sns_topic" "budget_alerts" {
  name = "${local.name_prefix}-budget-alerts"
}

# Dedicated topic so this policy cannot replace the default one the CloudWatch
# alarm topic relies on.
data "aws_iam_policy_document" "budget_alerts_publish" {
  statement {
    sid       = "AllowBudgetsToPublish"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.budget_alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts_publish.json
}

resource "aws_sns_topic_subscription" "budget_alerts_email" {
  count     = var.alarm_notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_notification_email
}

resource "aws_budgets_budget" "monthly_gross" {
  name         = "${local.name_prefix}-monthly-gross"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
    include_refund = false
  }

  # Early warning on real (gross) spend.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 40
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  # Fires before the month ends if the trend will exceed the limit.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}
