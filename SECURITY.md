# Security

## Reporting
Please report security problems privately by email to uttkarsh25tyagi@gmail.com instead of opening a public issue.

## Where the details are
The threat model, the controls in place (GitHub OIDC instead of stored AWS keys, least-privilege IAM, budget alerts, the public read-only API
and its limits) and the known gaps are written up in [docs/security.md](docs/security.md). GitHub secret scanning with push protection and
Dependabot alerts are enabled; the full git history was scanned for credentials and contains none.
