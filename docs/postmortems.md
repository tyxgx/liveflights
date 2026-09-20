# Postmortems

Blameless write-ups of the two cost incidents on this project's AWS account, what
each one taught, and which guardrails came out of them. Numbers are from Cost
Explorer, CloudWatch and CloudTrail unless stated otherwise. Legend: [x] done, [~] written
but not yet applied, [ ] open.

---

## 1. DynamoDB write cost (2026-08-23)

**Summary.** A routine budget audit found the `latest-state` DynamoDB table being
rewritten item-by-item on every 1-minute ingest poll. Projected cost was ~**$155/month
for DynamoDB writes alone**. Caught two days after the Europe rollout; the actual
August bill was **$2.40 gross** for the whole account.

**Impact.** No outage, no user impact. Money at risk, not money spent.

**Timeline.**
- Europe multi-point coverage went live, raising the live-item count roughly 100x
  compared with the original India region.
- 2026-08-23: a budget audit compared CloudWatch write metrics with the bill forecast.
  Measured ~**170,571 consumed write capacity units/hour** (~4,600 items x 60 polls/hour).
- All three EventBridge schedules were disabled first to stop the spend, then the
  account was audited to confirm nothing else was running. 24 hours later CloudWatch
  showed 0 invocations and 0 DynamoDB writes.
- The service was redesigned the same week (below).

**Root cause.** The design stored every live aircraft as its own DynamoDB item and
rewrote the whole set each poll. Cost scaled with `aircraft x polls`, and nothing in
the design capped either factor. Adding a bigger region multiplied `aircraft` by ~100
without any code change, so the cost model was invisible until the bill forecast
exposed it.

**Detection.** Manual audit. There was no alert; it was found because someone looked.

**Resolution.** Removed DynamoDB, Athena, Glue, Step Functions and the transform
Lambda entirely. The live state became two small S3 objects the ingest Lambda
overwrites (`live/latest.json` and `stats/hourly.json`): one PUT per poll instead of
thousands of item writes. Steady-state cost fell to roughly $1.5-2/month (mostly
Firehose archive and S3 requests).

**What went well.** Stopped the spend first and investigated second; verified the stop
with metrics rather than assuming; removed the unused pieces instead of leaving them
idle.

**What went badly.** Cost was a function of data volume, but volume was never load
tested against the pricing model. No budget alert existed.

**Action items.**
- [x] Replace per-item writes with a single overwritten object per poll.
- [x] Tear down every service that was only there to serve the removed design.
- [~] Budget alert as code (`infra/terraform/budgets.tf`): written and validated, not yet applied to AWS.
- [ ] Load-test the ingest path with a region-sized payload and record cost per
  1,000 polls next to the latency numbers.

---

## 2. Cost hidden by account credits (2026-09)

**Summary.** Cost Explorer's default view is **net of credits**, so a running
`m5.large` in another region looked like $0.00/day while it accrued **$39.39** of
real usage. Total gross usage for September reached **$48.14** by the 19th
(**$2.40** in August). All of it was absorbed by promotional credits, so no card
charge occurred, but roughly $48 of a finite credit balance was consumed unnoticed.

**Impact.** ~$48 of credits burned in under three weeks. No customer impact.

**Timeline (CloudTrail / Cost Explorer).**
- 2026-09-02 18:54 IST: an `m5.large` was launched from the CLI in `ap-south-1`.
- 2026-09-09: a cost check used the default net view and reported $0.
- 2026-09-19: re-querying with `RECORD_TYPE = Usage` showed ~$2.9/day gross since
  Sep 9: 390 instance-hours (`BoxUsage:m5.large`, $39.39), gp3 volume $3.02, public
  IPv4 $4.03 (of which $1.94 was an Elastic IP left idle).
- 2026-09-19 13:59 IST: the instance was terminated. Later the same day the account
  was emptied and only this project's stack was redeployed.

**Root cause.** Three things had to line up:
1. A large instance was left running with no owner-visible reminder.
2. The reporting view we relied on (net of credits) is designed to show $0 while
   credits last.
3. There was no alert on **gross** spend or on running-instance count.

**Detection.** Manual: a deeper query during a full account review.

**Resolution.** Terminated the instance, released the idle Elastic IP, and deleted
everything not needed. Went region by region to verify nothing else was running.

**What went well.** CloudTrail gave exact launch/terminate times, and Cost Explorer
gave usage type and hours, so the bill could be reconstructed to the dollar.

**What went badly.** A "$0" result was accepted as proof there was no spend. It was
a statement about the credit balance, not about usage.

**Action items.**
- [x] Always query gross usage (`RECORD_TYPE = Usage`) and credits separately.
- [~] Budget alert with `include_credit = false` so credits cannot mask spend
  (`infra/terraform/budgets.tf`): written and validated, not yet applied to AWS.
- [ ] Add a weekly scheduled check that fails loudly if any EC2 instance or Elastic
  IP exists outside this project's expected resources.
- [ ] Tag every resource with `project` and `owner` so orphaned resources are
  attributable.

---

## Takeaways

- **Model the cost of the data path, not just the compute.** Price = quantity x
  unit price; find the unbounded quantity.
- **Alert on gross spend.** Credits, free tier and net views all hide real usage.
- **Verify a shutdown with metrics**, then verify again region by region.
- **Prefer designs whose cost does not grow with input size** (one overwritten object
  per poll, not one item per aircraft).
