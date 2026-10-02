#!/usr/bin/env bash
# `pnpm build` alone is NOT safe for a real deploy: Next.js always loads .env.local with higher
# precedence than .env.production, even during a production build - so a plain `next build` bakes
# .env.local's local-dev values (NEXT_PUBLIC_API_BASE_URL=http://localhost:8000,
# NEXT_PUBLIC_DEFAULT_REGION=india) into the live static export instead of the real ones. Hit this
# for real on 2026-09-29's deploy - shipped a broken live site, caught only by opening the actual
# URL afterward. See the memory file liveflights_web_deploy_gotchas.md.
#
# Fix: shell-exported env vars win over EVERY .env* file in Next's precedence order, so this
# script exports .env.production's values as real env vars before calling `next build` - no need
# to move .env.local aside and back by hand every time.
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091
source .env.production
set +a

echo "Building with NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL NEXT_PUBLIC_DEFAULT_REGION=$NEXT_PUBLIC_DEFAULT_REGION"
next build
