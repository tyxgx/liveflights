resource "aws_athena_workgroup" "gold" {
  name = "${local.name_prefix}-gold"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    # Cost guardrail: any single query scanning more than this aborts
    # instead of silently billing $5/TB-scanned against a $5 total budget.
    bytes_scanned_cutoff_per_query = var.athena_query_scan_limit_bytes

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.id}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
