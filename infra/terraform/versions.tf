terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # Local state on purpose: this is a $5-budget demo project, not a team
  # environment. An S3 backend + DynamoDB lock table would themselves cost
  # a (tiny) always-on amount and add setup steps with no benefit here.
  # backend "local" is the implicit default — left unconfigured.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
