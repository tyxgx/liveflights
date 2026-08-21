# SSM Parameter Store, Standard tier (free) — OpenSky credentials.
# No secrets in code, .env files, or Terraform state beyond this resource
# itself (which Terraform necessarily tracks as state for any secret it
# manages; the value is passed in via TF_VAR_* / a gitignored tfvars file,
# never committed).

resource "aws_ssm_parameter" "opensky_client_id" {
  name  = "/${var.project}/${var.environment}/opensky/client_id"
  type  = "String"
  value = var.opensky_client_id != "" ? var.opensky_client_id : "unset"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "opensky_client_secret" {
  name  = "/${var.project}/${var.environment}/opensky/client_secret"
  type  = "SecureString"
  value = var.opensky_client_secret != "" ? var.opensky_client_secret : "unset"

  lifecycle {
    ignore_changes = [value]
  }
}
