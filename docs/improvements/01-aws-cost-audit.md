# 01. AWS cost and consumption audit (2026-10-04)

## Situation
The AWS account hosts this project (us-east-1) and StreamPulse (ap-south-1). A stopped EC2 had quietly cost money in September, five budgets existed,
and nobody had checked what each remaining service actually consumes against its free tier. Cost Explorer shows costs net of credits by default, which
hides real spend, so the check had to use gross usage.

## Task
For every service in use: how much it is consumed, what it costs gross, how much of the free tier is left, and whether anything is risky or wasteful.

## Action
- Cost Explorer, gross (credits and refunds excluded), last 30 and last 7 days, grouped by service and usage type with cost *and* quantity.
- CloudWatch metrics for the last 7 days: Lambda invocations, duration, errors and throttles per function (converted to GB-seconds); API Gateway request
  count; Firehose incoming bytes.
- Reading the code and the logs where a number looked odd (the ingest Lambda's duration).
- All 17 regions were scanned for forgotten resources earlier the same day: only these two stacks exist.

## Result
### Money (gross, credits not netted)
| | Last 30 days | Notes |
|---|---|---|
| Total | $47.27 | $36.41 of it is the old EC2 (terminated 2026-10-02), $8.21 more is that instance's idle IP, disk and VPC charges |
| Run-rate now | **about $3 per month** | what is left after removing the EC2 leftovers and the $0.15 a week that audits themselves cost (Cost Explorer API calls are $0.01 each) |

### Per service, today
| Service | Consumption (last 7 days) | Gross per month | Free tier | Verdict |
|---|---|---|---|---|
| Kinesis Data Firehose | 10.0 GB ingested (43 GB/month) | **$1.25** | none | the largest remaining line; it feeds the 30-day bronze archive |
| S3 requests | 61,580 PUT/LIST per week in us-east-1, 6,165 in ap-south-1, 49,541 GET | **about $1.5** | none | every minute the ingest writes about 4 objects and the predict Lambda about 4 more |
| S3 storage | 2.3 GB (liveflights lake), 2.8 GB (StreamPulse lake), under 10 MB (two sites) | about $0.15 | not relied on | fine |
| ECR | 2 liveflights images plus the StreamPulse image | about $0.10 | 500 MB | fine |
| API Gateway (HTTP) | 3,562 requests (15,300 per month) | $0.015 | 1M for 12 months | negligible |
| Lambda | see below | $0 | 400,000 GB-s and 1M requests | **74% of the GB-s allowance used** |
| EventBridge Scheduler | 2 schedules every minute (86,400 invocations/month) | $0 | 14M | fine |
| CloudWatch | 4 alarms, about 14 MB of logs (7-day retention) | $0 | 10 alarms, 5 GB | fine |
| DynamoDB (StreamPulse limiter) | a handful of writes | $0 | none on-demand, but cents | fine |
| X-Ray | tracing is active on the liveflights Lambdas (every invocation sampled) | $0 so far | 100,000 traces/month | close to the allowance, watch it |

### Lambda, the one that matters
| Function | Memory | Invocations / 7 days | Average | Slowest | Errors | Throttles | GB-s per month |
|---|---|---|---|---|---|---|---|
| liveflights-prod-ingest | 256 MB | 10,079 | **18.4 s** | 72.6 s (timeout 90 s) | 0 | 0 | 198,000 |
| liveflights-prod-predict | 1 GB | 8,187 | 2.6 s | 67.3 s (timeout 60 s) | **32** | 0 | 92,000 |
| liveflights-prod-api | 512 MB | 3,358 | 0.48 s | 36.9 s | 1 | **202** | 3,400 |
| streampulse-chat | 1.5 GB | 19 | 4.0 s | 16.8 s | 0 | 0 | 490 |
| **Total** | | | | | | | **about 294,000 of 400,000 (74%)** |

### What the numbers say
1. **No surprise bill, but little headroom.** The account is $0 net only because Lambda stays inside its free allowance. Growth in traffic or a slower ingest would
   start billing at about $0.0000167 per GB-s.
2. **The ingest Lambda is two thirds of all Lambda usage and takes 18 seconds a run.** Logs show about 11 s fetching from adsb.lol (rate limits answer HTTP 429 and
   force retries) and about 8 s afterwards, most of it reading, parsing, updating and writing back `live/history.json`, which is about 10 MB, on a 256 MB function
   (roughly 0.15 vCPU). Max memory used is 190 MB, so memory cannot simply be cut, but it can be raised: Lambda CPU scales with memory.
3. **Users really were being failed.** The API Lambda was throttled 202 times in a week. The account's total Lambda concurrency is 10, shared with the other
   project, and one dashboard load fires several slow requests at once. This is a direct cause of the "glitch" users see (a failed poll shows as "reconnecting").
4. **The predict Lambda times out occasionally** (32 errors out of 8,187, 0.4%), consistent with a cold start plus the big history file.
5. **Nothing risky is running:** no EC2, EBS, Elastic IP, NAT, RDS or load balancer in any region.

### Budgets
Five budgets exist. Two (`streampulse-ec2-safety-budget`, `Monthly-Budget`) are obsolete. All of them are account-wide, so the same forecast ($7.60 to $8.29)
appears in every one; that forecast still counts the EC2 days that are gone. The StreamPulse budget is $3, slightly under the true run-rate, so it will warn
at month end; $5 would match reality.

## What this led to
Steps 06 and 07 of the [plan](README.md) (ingest speed and the static map file) come directly from findings 2 and 3. Deleting the two obsolete budgets and
setting the StreamPulse budget to $5 are small Terraform/CLI changes left to the owner.

## How to repeat this audit
Cost Explorer calls cost $0.01 each, so do it rarely. The commands are in the session log; the essentials are `aws ce get-cost-and-usage` grouped by
`SERVICE` and `USAGE_TYPE` with `--metrics UnblendedCost UsageQuantity` and a filter that excludes `Credit` and `Refund`, plus `aws cloudwatch get-metric-statistics`
for `AWS/Lambda` (Invocations, Duration, Errors, Throttles).
