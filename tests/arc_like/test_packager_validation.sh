#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
python3 - "$tmp/missing.zip" "$tmp/traversal.zip" <<'PY'
import sys, zipfile
missing, traversal = sys.argv[1:]
with zipfile.ZipFile(missing, "w") as zf:
    zf.writestr("template/template.yaml", "type: web\n")
with zipfile.ZipFile(traversal, "w") as zf:
    for name in (
        "template/template.yaml",
        "skills/arcbench-checkpoint/SKILL.md",
        "skills/arcbench-runtime-signals/SKILL.md",
        "skills/arcbench-traceability/SKILL.md",
        "arcbench-agent-runtime/pyproject.toml",
    ):
        zf.writestr(name, "x\n")
    zf.writestr("template/../../escape.txt", "bad\n")
PY
run_reject() {
  local archive="$1" pattern="$2" log="$3"
  set +e
  ARC_FACTORY26_STARTER_ZIP="$archive" "$ROOT/scripts/package_submission.sh" >"$log" 2>&1
  local rc=$?
  set -e
  [[ $rc -ne 0 ]] || { echo "packager unexpectedly accepted $archive" >&2; exit 1; }
  grep -q "$pattern" "$log" || { cat "$log" >&2; exit 1; }
}
run_reject "$tmp/missing.zip" "official starter ZIP is missing" "$tmp/missing.log"
run_reject "$tmp/traversal.zip" "unsafe path in Factory starter ZIP" "$tmp/traversal.log"
echo "Factory starter validation PASS"
