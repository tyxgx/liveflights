locals {
  common_tags = {
    project = var.project
    env     = var.environment
  }

  name_prefix = "${var.project}-${var.environment}"

  # Athena/Glue identifiers can't contain hyphens in some contexts; keep a
  # underscore variant handy for database/table names.
  db_name = replace(local.name_prefix, "-", "_")
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
