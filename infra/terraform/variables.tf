variable "aws_region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project tag value, used for cost allocation."
  type        = string
  default     = "liveflights"
}

variable "environment" {
  description = "Environment tag value."
  type        = string
  default     = "prod"
}

variable "region_bbox" {
  description = "OpenSky bounding box to poll: lamin,lomin,lamax,lomax. Dead in the cloud path — OpenSky is unreachable from Lambda egress (see docs/aws-architecture.md) — kept only because ingestion/config.py (the local producer) still reads it. Not referenced by any resource in this module."
  type = object({
    lamin = number
    lomin = number
    lamax = number
    lomax = number
  })
  default = {
    lamin = 6.0
    lomin = 68.0
    lamax = 37.0
    lomax = 97.5
  }
}

variable "simulator_region" {
  description = "Region passed to the simulator fallback (FlightSimulator's airport pool) and, historically, to the single-point adsb.lol default. Only affects the simulator now that adsb_lol_points below drives the real fetch."
  type        = string
  default     = "europe"
}

variable "adsb_lol_points" {
  description = <<-EOT
    Points to poll on adsb.lol's /v2/lat/{lat}/lon/{lon}/dist/{nm} endpoint,
    fanned out and merged (deduped by icao24) by the ingest Lambda. That
    endpoint is a single point+radius circle capped at 250nm by the API
    itself — one point can never cover a continent, so this is a curated
    set of air-traffic hub regions rather than an exact tiling of the whole
    Europe bbox (which would need dozens of 250nm circles). Chosen to match
    routes a viewer actually expects on a "Europe" map.
  EOT
  type = list(object({
    lat  = number
    lon  = number
    dist = number
  }))
  default = [
    { lat = 53.0, lon = -2.0, dist = 250 }, # British Isles
    { lat = 50.0, lon = 2.5, dist = 250 },  # France / Benelux
    { lat = 50.5, lon = 10.0, dist = 250 }, # Germany / Central Europe
    { lat = 59.0, lon = 15.0, dist = 250 }, # Scandinavia
    { lat = 40.0, lon = -3.5, dist = 250 }, # Iberia
    { lat = 42.0, lon = 12.5, dist = 250 }, # Italy
    { lat = 50.5, lon = 22.0, dist = 250 }, # Poland / Eastern Europe
    { lat = 40.0, lon = 22.0, dist = 250 }, # Balkans / Greece
  ]
}

variable "adsb_lol_max_workers" {
  description = "Concurrent adsb.lol fetches. adsb.lol rate-limited (HTTP 420/429) 2-3 of 8 points per run at max_workers=8 (all fired at once) — 3 plus the stagger/retry below fixed it."
  type        = number
  default     = 3
}

variable "adsb_lol_stagger_seconds" {
  description = "Delay between successive point-fetch submissions, spreading a burst of concurrent requests out over time."
  type        = number
  default     = 0.35
}

variable "adsb_lol_retry_attempts" {
  description = "Retries on adsb.lol HTTP 420/429 (rate-limited) before giving up on that point for this invocation."
  type        = number
  default     = 2
}

variable "batch_chain_schedule_expression" {
  description = "EventBridge Scheduler rate that starts the bronze->silver->gold Step Functions execution. Originally undeployed entirely (no schedule ever triggered it outside manual/one-off runs) until this was added; 10 minutes balances fresh gold data against the transform Lambda's per-run cost at Europe's much higher (~8x vs. India) data volume."
  type        = string
  default     = "rate(10 minutes)"
}

variable "opensky_client_id" {
  description = "OAuth2 client ID for OpenSky (empty string = anonymous polling, same as local default)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "opensky_client_secret" {
  description = "OAuth2 client secret for OpenSky."
  type        = string
  default     = ""
  sensitive   = true
}

variable "schedule_expression" {
  description = "EventBridge Scheduler rate for the ingestion Lambda."
  type        = string
  default     = "rate(1 minute)"
}

variable "alarm_notification_email" {
  description = "Email address subscribed to the SNS alarm topic. Leave empty to skip the subscription (topic is still created)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention applied to every log group in this stack."
  type        = number
  default     = 7
}

variable "athena_query_scan_limit_bytes" {
  description = "Per-query data-scan limit enforced by the Athena workgroup, as a cost guardrail."
  type        = number
  default     = 1073741824 # 1 GB
}

variable "github_repo" {
  description = "GitHub repo in 'owner/name' form, used to scope the OIDC trust policy for CI/CD. Leave empty to skip creating the OIDC role."
  type        = string
  default     = "tyxgx/liveflights"
}

variable "bedrock_model_id" {
  description = "Bedrock model used for the text-to-SQL Lambda."
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}
