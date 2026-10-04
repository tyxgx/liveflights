# Improvements log (liveflights)

Every change made after the 2026-10-03 audit is written up here in the **STAR** format used in interviews, one file per step:

- **S**ituation: what was wrong or missing, with the measurement that shows it.
- **T**ask: what had to be true afterwards.
- **A**ction: what was done, including decisions and alternatives rejected.
- **R**esult: the measured outcome, how to verify it, how to roll it back, and what is still open.

The usual reference docs (architecture, model card, journal) are updated alongside; these files tell the story of *why* each change was made.

## Plan and status

Steps marked **apply** need a `terraform apply` run by the owner (the agent environment cannot apply infrastructure).

| # | Step | Kind | Status |
|---|---|---|---|
| 01 | [AWS cost and consumption audit](01-aws-cost-audit.md) | audit | done 2026-10-04 |
| 02 | [Baseline performance measurement](02-baseline-measurement.md) (headless-Chrome probe) | measure | done 2026-10-04 |
| 03 | [Stop the 500 ms full-page re-render](03-stop-the-full-rerender.md) (imperative interpolation) | frontend | done 2026-10-04, ships with 05 |
| 04 | Poll hygiene: in-flight guard, pause when the tab is hidden | frontend | planned |
| 05 | Real `build:prod` plus compressed, cached site deploy | deploy | planned |
| 06 | Ingest Lambda: memory, phase timings, history file size | infra, **apply** | planned |
| 07 | Static pre-gzipped map JSON straight from S3 (no Lambda in the hot path) | infra + frontend, **apply** | planned |
| 08 | Canvas renderer behind a flag, compare, then switch | frontend | planned |
| 09 | New features: live accuracy page, replay slider, deep links and skeletons, status page | features | planned |

Rule for every step: measure before, change one thing, measure after, write the result here, and keep a rollback.
