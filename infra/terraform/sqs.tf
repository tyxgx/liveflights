# Dead-letter queue for the ingestion Lambda — mirrors the local Kafka DLQ
# pattern (flights.raw.dlq): failed invocations land here instead of being
# silently dropped or endlessly retried.

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name_prefix}-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days, SQS max
}
