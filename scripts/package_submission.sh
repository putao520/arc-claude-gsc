#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ARC_DIST_DIR:-$ROOT/dist}"
STAGE="$DIST/submission"
LOCK="$ROOT/runtime.lock.json"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 2
  }
}

for cmd in python3 curl tar zstd sha256sum npm; do
  require "$cmd"
done

read_json() {
  python3 - "$LOCK" "$1" <<'PY'
import json, sys
obj=json.load(open(sys.argv[1], encoding="utf-8"))
cur=obj
for part in sys.argv[2].split("."):
    cur=cur[part]
print(cur)
PY
}

CLAUDE_VERSION="$(read_json claudeCode.version)"
GATEWAY_VERSION="$(read_json gateway.version)"
GATEWAY_ASSET="$(read_json gateway.asset)"
GATEWAY_SHA="$(read_json gateway.sha256)"
GSC_TAG="$(read_json gsc.releaseTag)"
GSC_ASSET="$(read_json gsc.asset)"
GSC_SHA="$(read_json gsc.sha256)"

rm -rf "$STAGE"
mkdir -p "$STAGE/runtime/gateway" "$STAGE/runtime/claude"

cp "$ROOT/main.py" "$ROOT/runtime.lock.json" "$STAGE/"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[1/4] fetch pinned GSC runtime"
gsc_url="https://github.com/putao520/arc-claude-gsc/releases/download/$GSC_TAG/$GSC_ASSET"
curl -fL --retry 3 --retry-delay 2 "$gsc_url" -o "$tmp/$GSC_ASSET"
echo "$GSC_SHA  $tmp/$GSC_ASSET" | sha256sum -c -
zstd -dc "$tmp/$GSC_ASSET" | tar -xf - -C "$tmp"
test -d "$tmp/plugin-final"
mv "$tmp/plugin-final" "$STAGE/runtime/gsc"

echo "[2/4] install pinned original Claude Code"
npm install \
  --prefix "$STAGE/runtime/claude" \
  --omit=dev \
  --no-audit \
  --no-fund \
  "@anthropic-ai/claude-code@$CLAUDE_VERSION"

test -x "$STAGE/runtime/claude/node_modules/.bin/claude"

echo "[3/4] fetch pinned Anthropic/OpenAI protocol bridge"
gateway_url="https://github.com/thomas-illiet/anthropic-proxy/releases/download/$GATEWAY_VERSION/$GATEWAY_ASSET"
curl -fL --retry 3 --retry-delay 2 "$gateway_url" -o "$tmp/$GATEWAY_ASSET"
echo "$GATEWAY_SHA  $tmp/$GATEWAY_ASSET" | sha256sum -c -
mkdir -p "$tmp/gateway"
tar -xzf "$tmp/$GATEWAY_ASSET" -C "$tmp/gateway"
gateway_bin="$(find "$tmp/gateway" -type f -name anthropic-proxy -perm -u+x | head -1)"
test -n "$gateway_bin"
cp "$gateway_bin" "$STAGE/runtime/gateway/anthropic-proxy"
chmod +x "$STAGE/runtime/gateway/anthropic-proxy"

echo "[4/4] package ARC submission"
(
  cd "$STAGE"
  sha256sum \
    runtime/gateway/anthropic-proxy \
    runtime/gsc/bin/gsc-spec-server \
    > RUNTIME_SHA256SUMS
)

rm -f "$DIST/submission.zip"
python3 - "$STAGE" "$DIST/submission.zip" <<'PY'
from pathlib import Path
import sys, zipfile

root = Path(sys.argv[1])
out = Path(sys.argv[2])
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            zf.write(path, path.relative_to(root))
PY

echo
echo "submission directory: $STAGE"
echo "submission zip:       $DIST/submission.zip"
du -sh "$STAGE" "$DIST/submission.zip"
