#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ARC_DIST_DIR:-$ROOT/dist}"
IMAGE="${ARC_SMOKE_IMAGE:-mcr.microsoft.com/playwright/python:v1.54.0-jammy}"
WORK="$DIST/arc-smoke-workspace"

if [[ "${ARC_SKIP_PACKAGE:-0}" != "1" ]]; then "$ROOT/scripts/package_submission.sh"; fi
test -f "$DIST/submission.zip"
if [[ -e "$WORK" ]]; then
  docker run --rm -v "$DIST:/dist" "$IMAGE" bash -lc 'rm -rf /dist/arc-smoke-workspace'
fi
mkdir -p "$WORK"/{submission,requirements,template,tests,artifacts}
python3 - "$DIST/submission.zip" "$WORK/submission" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf: zf.extractall(sys.argv[2])
PY
cp "$ROOT/tests/arc_like/mock_openai.py" "$WORK/tests/"
cat > "$WORK/requirements/requirements.yaml" <<'YAML'
id: ROOT
name: Smoke Root
children:
  - id: SMOKE-1
    name: Create smoke marker
    children:
      - id: SMOKE-1.1
        name: Write marker file
        description: Create ARC_SMOKE.txt in the project root containing exactly ARC_CLAUDE_GSC_OK.
YAML

set +e
docker run --rm --cpus=6 --memory=12g -v "$WORK:/workspace" "$IMAGE" bash -lc '
set +e
python3 /workspace/tests/mock_openai.py >/workspace/artifacts/mock.log 2>&1 & mockpid=$!
sleep 1
python3 -m pip install -q -r /workspace/submission/requirements.txt
cd /workspace/output
env OPENAI_BASE_URL=http://127.0.0.1:19091/v1 OPENAI_API_KEY=mock-key MODEL=mock-model \
  ARCBENCH_SUBMISSION_DIR=/workspace/submission ARCBENCH_ARTIFACTS_DIR=/workspace/artifacts ARC_SKIP_FINAL_VALIDATION=1 \
  python3 /workspace/submission/main.py /workspace/requirements --output-dir /workspace/template --type web \
  >/workspace/artifacts/main.out 2>/workspace/artifacts/main.err
rc=$?; kill "$mockpid" 2>/dev/null || true; exit "$rc"
'
rc=$?; set -e
if [[ "$rc" -ne 0 ]] || ! grep -Fxq ARC_CLAUDE_GSC_OK "$WORK/template/ARC_SMOKE.txt" 2>/dev/null; then
  echo "Factory ARC-like smoke FAILED (main rc=$rc)" >&2
  for f in main.err gateway.log mock.log; do echo "--- $f ---" >&2; tail -120 "$WORK/artifacts/$f" >&2 || true; done
  exit 1
fi
echo "Factory ARC-like smoke PASS"
echo "workspace: $WORK"
echo "result:    $(cat "$WORK/template/ARC_SMOKE.txt")"
