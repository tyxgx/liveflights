# Teardown / cost-pause guide

Two levels: **pause** (stop recurring spend, keep the dashboard/site live to
show someone) and **destroy** (delete everything).

## Pause recurring cost, keep the site + S3 live

Two schedules generate cost on a *recurring, unattended* basis: the
ingestion schedule (now **every 1 minute** — Lambda invocations, Firehose,
DynamoDB writes; ~43,200 invocations/mo, still deep inside the 1M/mo Lambda
free tier) and the daily corridor-retrain schedule (one Lambda invocation/
day, reads all accumulated silver data). Anything the Step Functions batch
chain triggers only runs when you invoke it — it has no schedule of its own.
Disabling both schedules stops new spend accruing while leaving the
dashboard, API, and already-ingested data browsable.

```bash
# 1. Disable the EventBridge Scheduler — stops the 1-minute live poll.
aws scheduler update-schedule \
  --name liveflights-prod-ingest-schedule --group-name default \
  --state DISABLED \
  --schedule-expression "rate(1 minute)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule --name liveflights-prod-ingest-schedule --group-name default --query Target --output json)"

# 2. Disable the daily corridor-retrain schedule.
aws scheduler update-schedule \
  --name liveflights-prod-corridor-retrain --group-name default \
  --state DISABLED \
  --schedule-expression "rate(1 day)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule --name liveflights-prod-corridor-retrain --group-name default --query Target --output json)"
```

There is no crawler to disable — table partitions are registered directly by
the transform Lambda (`glue:CreateJob`/`glue:CreateCrawler` are
account-restricted; see `docs/aws-architecture.md`) — and don't manually
invoke `aws_sfn_state_machine.batch_chain` again once paused.

Everything else (S3, DynamoDB table itself, the site's S3 website endpoint,
Athena, Glue Catalog, the API Lambda) has no idle/recurring cost — DynamoDB
is on-demand, Lambda bills per invocation, S3 storage cost for a few MB of
data is fractions of a cent. Re-enable either schedule with `--state ENABLED`
(same command, flip the flag) to resume.

**Cost at the 1-minute interval**: ~43,200 ingest invocations/mo (vs. the
1M/mo free tier) at ~256MB/~1-2s each is nowhere near the 400,000 free
GB-seconds/mo; Firehose/DynamoDB bill by data volume, not call count, so 5x
more small writes doesn't 5x the bill. Net effect vs. the original 5-minute
design: projected cost moves from ~$2.50–3.50/mo to roughly **$2.60–3.85/mo**
(the extra ~$0.10–0.35 covers the added scikit-learn dependency's slightly
heavier transform Lambda image and the daily retrain's larger read of
accumulated silver data — still comfortably inside the $5/mo budget alert
configured on this account).

## Full teardown (terraform destroy)

```bash
cd infra/terraform
terraform destroy
```

Notes on things that need manual cleanup **before** `destroy` will succeed
cleanly:

- **ECR repositories**: `force_delete = true` is set on both
  `aws_ecr_repository.api` and `aws_ecr_repository.transform`, so Terraform
  will delete them and their images without a separate `aws ecr
  batch-delete-image` step.
- **S3 buckets are not force-destroyed** — Terraform will refuse to delete a
  non-empty bucket. Empty both first:
  ```bash
  aws s3 rm s3://$(terraform output -raw lake_bucket) --recursive
  aws s3 rm s3://$(terraform output -raw site_bucket) --recursive
  ```
- **SSM parameters**: destroyed along with everything else; no OpenSky
  credentials persist anywhere after this (unused since the ingest Lambda
  switched to the simulator — see `docs/aws-architecture.md`).

After `terraform destroy` completes, confirm zero billable resources remain:

```bash
aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'liveflights-prod')]"
aws s3 ls | grep liveflights-prod
aws dynamodb list-tables --query "TableNames[?starts_with(@, 'liveflights-prod')]"
```

All three should return empty.
