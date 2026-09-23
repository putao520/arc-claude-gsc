#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ARC_DIST_DIR:-$ROOT/dist}"
IMAGE="${ARC_SMOKE_IMAGE:-mcr.microsoft.com/playwright/python:v1.54.0-jammy}"
WORK="$DIST/arc-smoke-workspace"

if [[ "${ARC_SKIP_PACKAGE:-0}" != "1" ]]; then
  "$ROOT/scripts/package_submission.sh"
fi

test -f "$DIST/submission.zip"
rm -rf "$WORK"
mkdir -p "$WORK"/{submission,template,task,tests,artifacts,prompt}

python3 - "$DIST/submission.zip" "$WORK/submission" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    zf.extractall(sys.argv[2])
PY

cp "$ROOT/tests/arc_like/mock_openai.py" "$WORK/tests/"
cat > "$WORK/template/package.json" <<'JSON'
{"name":"arc-smoke","private":true,"type":"module"}
JSON
cat > "$WORK/task/requirements.md" <<'EOF'
# Smoke requirement
Create ARC_SMOKE.txt in the template root containing exactly ARC_CLAUDE_GSC_OK.
EOF
cat > "$WORK/prompt/task_prompt.txt" <<'EOF'
Create ARC_SMOKE.txt in /workspace/template containing exactly ARC_CLAUDE_GSC_OK. Use available tools and then finish.
EOF

set +e
docker run --rm --cpus=6 --memory=12g \
  -v "$WORK:/workspace" \
  -w /workspace/template \
  "$IMAGE" bash -lc '
set +e
python3 /workspace/tests/mock_openai.py >/workspace/artifacts/mock.log 2>&1 &
mockpid=$!
sleep 1
env \
  OPENAI_BASE_URL=http://127.0.0.1:19091/v1 \
  OPENAI_API_KEY=mock-key \
  MODEL=mock-model \
  ARCBENCH_SUBMISSION_DIR=/workspace/submission \
  ARCBENCH_TEMPLATE_DIR=/workspace/template \
  ARCBENCH_TASK_DIR=/workspace/task \
  ARCBENCH_ARTIFACTS_DIR=/workspace/artifacts \
  ARCBENCH_PROMPT_PATH=/workspace/prompt/task_prompt.txt \
  python3 /workspace/submission/main.py \
  >/workspace/artifacts/main.out 2>/workspace/artifacts/main.err
rc=$?
kill "$mockpid" 2>/dev/null || true
exit "$rc"
'
rc=$?
set -e

if [[ "$rc" -ne 0 ]] || ! grep -Fxq ARC_CLAUDE_GSC_OK "$WORK/template/ARC_SMOKE.txt" 2>/dev/null; then
  echo "ARC-like smoke FAILED (main rc=$rc)" >&2
  echo "--- main.err ---" >&2
  tail -120 "$WORK/artifacts/main.err" >&2 || true
  echo "--- gateway.log ---" >&2
  tail -120 "$WORK/artifacts/gateway.log" >&2 || true
  echo "--- mock.log ---" >&2
  tail -120 "$WORK/artifacts/mock.log" >&2 || true
  exit 1
fi

echo "ARC-like smoke PASS"
echo "workspace: $WORK"
echo "result:    $(cat "$WORK/template/ARC_SMOKE.txt")"
