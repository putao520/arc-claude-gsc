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

for cmd in python3 curl tar sha256sum npm; do
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
CLAUDE_SHA="$(read_json claudeCode.binarySha256)"
GATEWAY_VERSION="$(read_json gateway.version)"
GATEWAY_ASSET="$(read_json gateway.asset)"
GATEWAY_ARCHIVE_SHA="$(read_json gateway.archiveSha256)"
GATEWAY_BINARY_SHA="$(read_json gateway.binarySha256)"
GSC_TAG="$(read_json gsc.releaseTag)"
GSC_ASSET="$(read_json gsc.asset)"
GSC_SHA="$(read_json gsc.sha256)"
ZSTD_TAG="$(read_json zstd.releaseTag)"
ZSTD_ASSET="$(read_json zstd.asset)"
ZSTD_SHA="$(read_json zstd.sha256)"

rm -rf "$STAGE"
mkdir -p "$STAGE/runtime/bin" "$STAGE/runtime/payloads" "$STAGE/runtime/gateway"
cp "$ROOT/main.py" "$ROOT/runtime.lock.json" "$STAGE/"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[1/5] fetch pinned zstd runtime helper"
zstd_url="https://github.com/putao520/arc-claude-gsc/releases/download/$ZSTD_TAG/$ZSTD_ASSET"
curl -fL --retry 3 --retry-delay 2 "$zstd_url" -o "$tmp/$ZSTD_ASSET"
echo "$ZSTD_SHA  $tmp/$ZSTD_ASSET" | sha256sum -c -
cp "$tmp/$ZSTD_ASSET" "$STAGE/runtime/bin/zstd"
chmod +x "$STAGE/runtime/bin/zstd"

echo "[2/5] fetch pinned compressed GSC runtime"
gsc_url="https://github.com/putao520/arc-claude-gsc/releases/download/$GSC_TAG/$GSC_ASSET"
curl -fL --retry 3 --retry-delay 2 "$gsc_url" -o "$STAGE/runtime/payloads/gsc-runtime.tar.zst"
echo "$GSC_SHA  $STAGE/runtime/payloads/gsc-runtime.tar.zst" | sha256sum -c -

echo "[3/5] install and compress pinned original Claude Code"
npm install \
  --prefix "$tmp/claude-install" \
  --omit=dev \
  --no-audit \
  --no-fund \
  "@anthropic-ai/claude-code@$CLAUDE_VERSION"
claude_bin="$tmp/claude-install/node_modules/@anthropic-ai/claude-code-linux-x64/claude"
test -x "$claude_bin"
echo "$CLAUDE_SHA  $claude_bin" | sha256sum -c -
"$STAGE/runtime/bin/zstd" -15 -T0 -f "$claude_bin" -o "$STAGE/runtime/payloads/claude.zst"

echo "[4/5] fetch pinned Anthropic/OpenAI protocol bridge"
gateway_url="https://github.com/thomas-illiet/anthropic-proxy/releases/download/$GATEWAY_VERSION/$GATEWAY_ASSET"
curl -fL --retry 3 --retry-delay 2 "$gateway_url" -o "$tmp/$GATEWAY_ASSET"
echo "$GATEWAY_ARCHIVE_SHA  $tmp/$GATEWAY_ASSET" | sha256sum -c -
mkdir -p "$tmp/gateway"
tar -xzf "$tmp/$GATEWAY_ASSET" -C "$tmp/gateway"
gateway_bin="$(find "$tmp/gateway" -type f -name anthropic-proxy -perm -u+x | head -1)"
test -n "$gateway_bin"
echo "$GATEWAY_BINARY_SHA  $gateway_bin" | sha256sum -c -
cp "$gateway_bin" "$STAGE/runtime/gateway/anthropic-proxy"
chmod +x "$STAGE/runtime/gateway/anthropic-proxy"

echo "[5/5] package ARC submission"
(
  cd "$STAGE"
  sha256sum \
    runtime/bin/zstd \
    runtime/payloads/gsc-runtime.tar.zst \
    runtime/payloads/claude.zst \
    runtime/gateway/anthropic-proxy \
    > RUNTIME_SHA256SUMS
)

rm -f "$DIST/submission.zip"
python3 - "$STAGE" "$DIST/submission.zip" <<'PY'
from pathlib import Path
import sys, zipfile

root = Path(sys.argv[1])
out = Path(sys.argv[2])
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        arcname = path.relative_to(root)
        compression = zipfile.ZIP_STORED if path.suffix == ".zst" else zipfile.ZIP_DEFLATED
        zf.write(path, arcname, compress_type=compression)
PY

echo
echo "submission directory: $STAGE"
echo "submission zip:       $DIST/submission.zip"
du -sh "$STAGE" "$DIST/submission.zip"
