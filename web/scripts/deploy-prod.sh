#!/usr/bin/env bash
# Build the web app for production and publish it to the S3 site bucket, compressed and cached.
#   pnpm deploy:prod            (build + deploy + verify)
#   SKIP_BUILD=1 pnpm deploy:prod   (reuse the existing out/)
#
# Why not a plain `aws s3 sync out/`: S3 website hosting does not compress and sends no cache headers, so every
# visit re-downloaded ~1.2 MB of JavaScript uncompressed. Here text assets are gzipped before upload (stored with
# Content-Encoding: gzip, which every browser decodes), hashed `_next/static` files are cached for a year
# (immutable: the file name changes when the content does), and HTML is always revalidated so a new deploy is
# picked up immediately.
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET="${BUCKET:-liveflights-prod-site-922120357133}"
SITE_URL="${SITE_URL:-https://${BUCKET}.s3.us-east-1.amazonaws.com}"

if [ -z "${SKIP_BUILD:-}" ]; then
  bash scripts/build-prod.sh
fi
[ -f out/live.html ] || { echo "out/live.html missing - build first" >&2; exit 1; }

# the build must contain the real API URL, never the local-dev one (see build-prod.sh for the history)
if grep -rq "localhost:8000" out/_next/static 2>/dev/null; then
  echo "REFUSING TO DEPLOY: build contains the local-dev API URL (localhost:8000)" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R out/. "$STAGE/"

# gzip text assets in place (keep file names so content types and URLs are unchanged)
before=$(du -sk "$STAGE" | cut -f1)
while IFS= read -r -d '' f; do
  gzip -9 -n -c "$f" > "$f.gz" && mv "$f.gz" "$f"
done < <(find "$STAGE" -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.txt' -o -name '*.json' -o -name '*.svg' \) -print0)
after=$(du -sk "$STAGE" | cut -f1)
echo "staged: ${before} KB -> ${after} KB after gzip"

S3="s3://${BUCKET}"
# 1. hashed assets: long-lived, immutable (no --delete: older hashed files stay so pages cached mid-deploy keep working)
aws s3 cp "$STAGE/_next/" "$S3/_next/" --recursive --only-show-errors \
  --content-encoding gzip --exclude '*' --include '*.js' --include '*.css' \
  --cache-control 'public, max-age=31536000, immutable'
aws s3 cp "$STAGE/_next/" "$S3/_next/" --recursive --only-show-errors \
  --exclude '*.js' --exclude '*.css' \
  --cache-control 'public, max-age=31536000, immutable'
# 2. HTML and RSC payloads: always revalidate
aws s3 cp "$STAGE/" "$S3/" --recursive --only-show-errors \
  --content-encoding gzip --cache-control 'no-cache' --exclude '*' --include '*.html' --include '*.txt' --exclude '_next/*'
# 3. everything else at the root (icons etc.): a day
aws s3 cp "$STAGE/" "$S3/" --recursive --only-show-errors --exclude '_next/*' --exclude '*.html' --exclude '*.txt' \
  --cache-control 'public, max-age=86400'

echo "uploaded to $S3"
echo "verify: curl -sI -H 'Accept-Encoding: gzip' $SITE_URL/live.html"
