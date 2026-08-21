# Glue Catalog database + explicit tables, no Job or Crawler.
#
# `glue:CreateJob` and `glue:CreateCrawler` are blocked by an account-level
# restriction on this account (confirmed: IAM allows both, reads work, only
# these two create calls are denied — see docs/aws-architecture.md). Catalog
# writes (`glue:CreateTable`, `glue:CreateDatabase`) are unaffected, so the
# bronze -> silver -> gold transform runs as a Lambda (lambda_transform.tf)
# instead of a Glue Job, and each table's partitions are registered by that
# Lambda directly (`glue:BatchCreatePartition`) instead of by a Crawler.

resource "aws_glue_catalog_database" "gold" {
  name = local.db_name
}

locals {
  # Column set written by lambda_transform/handler.py's build_silver().
  silver_columns = [
    { name = "icao24", type = "string" },
    { name = "callsign", type = "string" },
    { name = "origin_country", type = "string" },
    { name = "time_position", type = "bigint" },
    { name = "last_contact", type = "bigint" },
    { name = "longitude", type = "double" },
    { name = "latitude", type = "double" },
    { name = "baro_altitude", type = "double" },
    { name = "on_ground", type = "boolean" },
    { name = "velocity", type = "double" },
    { name = "true_track", type = "double" },
    { name = "vertical_rate", type = "double" },
    { name = "geo_altitude", type = "double" },
    { name = "squawk", type = "string" },
    { name = "spi", type = "boolean" },
    { name = "position_source", type = "int" },
    { name = "ingest_ts", type = "string" },
    { name = "source", type = "string" },
    { name = "region", type = "string" },
    { name = "flight_phase", type = "string" },
    { name = "speed_kmh", type = "double" },
    { name = "altitude_ft", type = "double" },
    { name = "data_quality_flags", type = "array<string>" },
    { name = "ingest_date", type = "string" },
    { name = "ingest_hour", type = "string" },
  ]

  # Gold aggregate tables, keyed by table name -> column list, matching
  # build_gold() in lambda_transform/handler.py.
  gold_tables = {
    traffic_by_hour = [
      { name = "hour_bucket", type = "string" },
      { name = "flight_count", type = "bigint" },
      { name = "avg_altitude_ft", type = "double" },
      { name = "avg_speed_kmh", type = "double" },
    ]
    traffic_by_country = [
      { name = "origin_country", type = "string" },
      { name = "flight_count", type = "bigint" },
    ]
    airline_activity = [
      { name = "airline", type = "string" },
      { name = "flight_count", type = "bigint" },
    ]
    altitude_band_distribution = [
      { name = "altitude_band", type = "string" },
      { name = "flight_count", type = "bigint" },
    ]
    anomaly_events = [
      { name = "icao24", type = "string" },
      { name = "callsign", type = "string" },
      { name = "time_position", type = "bigint" },
      { name = "latitude", type = "double" },
      { name = "longitude", type = "double" },
      { name = "altitude_ft", type = "double" },
      { name = "true_track", type = "double" },
      { name = "corridor_id", type = "bigint" },
      { name = "lateral_distance_km", type = "double" },
      { name = "heading_deviation_deg", type = "double" },
      { name = "altitude_z", type = "double" },
      { name = "anomaly_score", type = "double" },
      { name = "anomaly_reason", type = "string" },
      { name = "is_ml_anomaly", type = "boolean" },
      { name = "ingest_ts", type = "string" },
    ]
  }

  all_tables = merge(
    { silver = local.silver_columns },
    local.gold_tables,
  )
}

resource "aws_glue_catalog_table" "lake" {
  for_each = local.all_tables

  name          = each.key
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "EXTERNAL"         = "TRUE"
    "parquet.compress" = "SNAPPY"
  }

  # One partition per transform run (run_ts=<YYYYMMDDTHHMMSS>), registered by
  # the transform Lambda's glue:CreatePartition call — no crawler needed.
  partition_keys {
    name = "run_ts"
    type = "string"
  }

  storage_descriptor {
    location      = each.key == "silver" ? "s3://${aws_s3_bucket.lake.id}/silver/" : "s3://${aws_s3_bucket.lake.id}/gold/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
