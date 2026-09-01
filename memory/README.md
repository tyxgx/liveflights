# Session Memory

Date-stamped logs of what a Claude Code session actually did on this repo — separate from
`docs/engineering-notes.md` (which documents the *shipped* engineering story for an interview/
portfolio audience). This folder is a working log: what was found, what was fixed, what's still
open, and exactly what state the repo/AWS deployment was left in — so a future session (or a
future me) doesn't have to re-derive it from git log + guesswork.

## Convention

- One file per session/date: `YYYY-MM-DD.md`
- Write what changed, why, and — critically — **what's committed vs. pushed vs. actually
  deployed to AWS**, since those three have drifted apart before on this project
- Don't edit past entries; append a new file for a new session
- If a fix spans multiple sessions before it's fully deployed, the newest file should link back
  to the older one and say what's still outstanding

See also the broader daily job-search log at `~/job/memory/YYYY-MM-DD.md` and the deep-dive
reference doc at `~/.claude/projects/-Users-uttkarshtyagi-job/memory/liveflights_full_context.md`
— that file is the authoritative full-repo-state snapshot; this folder is the session-by-session
trail that feeds it.

## Index

- [2026-09-01](2026-09-01.md) — corridor ML fix (26 → 1,150 corridors, airport-connected)
  committed and pushed to GitHub; **not yet deployed to AWS** (needs fresh AWS Academy
  credentials — S3 artifact upload + `terraform apply` for 2 new ingest points)
