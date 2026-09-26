#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

import arcbench_agent_runtime

manifest_path = Path(__file__).with_name("arcbench_runtime_sha256.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
version = importlib.metadata.version("arcbench-agent-runtime")
if version != manifest["version"]:
    raise SystemExit(f"arcbench-agent-runtime version mismatch: expected {manifest['version']}, got {version}")
root = Path(arcbench_agent_runtime.__file__).resolve().parent
for name, expected in manifest["files"].items():
    path = root / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"ARC runtime mismatch for {name}: expected {expected}, got {actual}")
print(f"ARC runtime {version} matches official Claude Code starter ({len(manifest['files'])} files)")
