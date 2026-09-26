#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ARC_DIST_DIR:-$ROOT/dist}"
IMAGE="${ARC_SMOKE_IMAGE:-mcr.microsoft.com/playwright/python:v1.54.0-jammy}"
CPUS="${ARC_SMOKE_CPUS:-4}"
MEMORY="${ARC_SMOKE_MEMORY:-8g}"
WORK="$DIST/arc-smoke-workspace"
FIXTURE="$DIST/factory26-smoke-starter.zip"

if [[ -d "$WORK" ]]; then
  docker run --rm -v "$DIST:/dist" "$IMAGE" bash -lc 'rm -rf /dist/arc-smoke-workspace'
fi
mkdir -p "$WORK"/{submission,requirements,output,tests,artifacts}
python3 - "$FIXTURE" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('template/template.yaml', 'type: web\n')
    zf.writestr('template/package.json', '{"name":"arc-smoke","private":true,"type":"module"}\n')
    zf.writestr('template/SPEC/README.md', '# ARC smoke SPEC\n')
    zf.writestr('skills/arcbench-checkpoint/SKILL.md', '# Checkpoint\n')
    zf.writestr('skills/arcbench-runtime-signals/SKILL.md', '# Runtime signals\n')
    zf.writestr('skills/arcbench-traceability/SKILL.md', '# Traceability\n')
PY
if [[ "${ARC_SKIP_PACKAGE:-0}" != "1" ]]; then
  ARC_FACTORY26_STARTER_ZIP="$FIXTURE" "$ROOT/scripts/package_submission.sh"
fi
test -f "$DIST/submission.zip"
python3 - "$DIST/submission.zip" "$WORK/submission" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf: zf.extractall(sys.argv[2])
PY
cp "$ROOT/tests/arc_like/mock_openai.py" "$ROOT/tests/arc_like/verify_arcbench_runtime.py" "$ROOT/tests/arc_like/arcbench_runtime_sha256.json" "$WORK/tests/"
cat > "$WORK/requirements/requirements.yaml" <<'YAML'
id: ROOT
name: Smoke root
children:
  - id: REQ-SMOKE-A
    name: First smoke requirement
    description: Create ARC_SMOKE.txt in the project root containing exactly ARC_CLAUDE_GSC_OK.
  - id: REQ-SMOKE-B
    name: Second smoke requirement
    description: Preserve ARC_SMOKE.txt and validate that previous module work remains available.
YAML

set +e
docker run --rm --cpus="$CPUS" --memory="$MEMORY" -v "$WORK:/workspace" -w /workspace/output "$IMAGE" bash -lc '
set -e
python3 -m pip install -q -r /workspace/submission/requirements.txt
python3 /workspace/tests/verify_arcbench_runtime.py
python3 /workspace/tests/mock_openai.py >/workspace/artifacts/mock.log 2>&1 & mockpid=$!
sleep 1
set +e
env OPENAI_BASE_URL=http://127.0.0.1:19091/v1 OPENAI_API_KEY=mock-key MODEL=mock-model \
  ARCBENCH_SUBMISSION_DIR=/workspace/submission ARCBENCH_ARTIFACTS_DIR=/workspace/artifacts \
  python3 /workspace/submission/main.py /workspace/requirements --output-dir /workspace/output --type web \
  >/workspace/artifacts/main.out 2>/workspace/artifacts/main.err
rc=$?
kill "$mockpid" 2>/dev/null || true
exit "$rc"
'
rc=$?
set -e
if [[ "$rc" -ne 0 ]] || ! grep -Fxq ARC_CLAUDE_GSC_OK "$WORK/output/ARC_SMOKE.txt" 2>/dev/null; then
  echo "Factory26 ARC-like smoke FAILED (main rc=$rc)" >&2
  for f in main.err main.out gateway.log mock.log; do echo "--- $f ---" >&2; tail -120 "$WORK/artifacts/$f" >&2 2>/dev/null || true; done
  exit 1
fi
test -f "$WORK/output/.arc/runner-events.jsonl"
test -f "$WORK/output/.arc/traceability/requirements.json"
test -f "$WORK/output/SPEC/arcbench/REQ-SMOKE-A.md"
test -f "$WORK/output/SPEC/arcbench/REQ-SMOKE-B.md"
grep -q 'REQ-SMOKE-A' "$WORK/output/.arc/traceability/requirements.json"
grep -q 'REQ-SMOKE-B' "$WORK/output/.arc/traceability/requirements.json"
grep -q '"state": "completed"' "$WORK/output/.arc/runner-events.jsonl"
commit_count="$(git -c safe.directory="$WORK/output" -C "$WORK/output" rev-list --count HEAD)"
[[ "$commit_count" -ge 3 ]] || { echo "expected at least init + two module commits, got $commit_count" >&2; exit 1; }
git -c safe.directory="$WORK/output" -C "$WORK/output" log --oneline -5
echo "Factory26 ARC-like smoke PASS"
echo "result: $(cat "$WORK/output/ARC_SMOKE.txt")"
