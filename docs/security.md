# Security posture

What is protected, what is deliberately public, and which known risks are accepted
(and why). Written for a public read-only demo running on AWS.

## What is public on purpose

- The dashboard (S3 static website) and the read-only JSON API (API Gateway -> Lambda).
- All data is public ADS-B / simulator flight data. There are no user accounts, no
  personal data and no write endpoints.

## Controls in place

| Area | Control |
|---|---|
| Secrets | None in the repo. OpenSky credentials (optional) live in SSM Parameter Store as `SecureString`; `.env` is gitignored, `.env.example` holds placeholders only |
| IAM | One role per Lambda, scoped to the specific S3 keys / Firehose stream it uses. Scheduler and Firehose have their own minimal roles |
| API abuse | Stage-level throttle (5 req/s, burst 10) bounds the worst case; see "Abuse cost" |
| CORS | Restricted to the dashboard origins and `localhost:3000`. This is browser-side hardening only, not access control |
| Data at rest | S3 buckets have public-access blocks (the site bucket is the only intentionally public one) and SSE encryption |
| Cost guardrail | Gross-spend budget with credits excluded (`infra/terraform/budgets.tf`); see `docs/postmortems.md` |
| Supply chain | Dependabot (uv, npm, GitHub Actions, Terraform, Docker); `pip-audit` is a blocking CI job; frontend `pnpm audit` is reported on every run |
| CI | Lint, tests, `dbt parse`, `terraform fmt/validate`, API image build. CI holds no AWS credentials and never applies infrastructure |

## Abuse cost (worst case)

At the stage throttle of 5 req/s the theoretical ceiling is ~13M requests/month,
about $13 of API Gateway plus Lambda duration. That is a bound, not an expectation;
the budget alert exists to catch it early.

## Accepted risks

**Next.js 14.2.35 advisories (frontend).** `pnpm audit` reports advisories against
this version and the fixes require the Next 15 major upgrade. The site is a **static
export** (`output: "export"`) served from S3, so the Next *server* features these
advisories target (middleware, image optimizer, server actions, RSC runtime) are not
running in production. Build-time dependencies (`postcss`, `nanoid`) only execute on
the build machine. Risk accepted for now; the Next 15 / React 19 migration is tracked as
follow-up work and needs its own visual regression pass.

**Public unauthenticated API.** By design. If it ever exposes anything beyond public
flight data, add an authorizer before that change ships.

## Not done (and why)

- **WAF / CloudFront:** WAF costs a fixed monthly fee; CloudFront is unavailable on
  this account (see `docs/aws-architecture.md`). Throttling plus the budget alert are
  the cost-appropriate substitute for a demo.
- **Remote Terraform state:** state is local and gitignored. Tracked as follow-up
  because a local state file is a single point of loss.
