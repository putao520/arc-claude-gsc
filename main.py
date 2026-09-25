#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SUBMISSION_DIR = Path(os.environ.get("ARCBENCH_SUBMISSION_DIR", Path(__file__).resolve().parent))
ARTIFACTS_DIR = Path(os.environ.get("ARCBENCH_ARTIFACTS_DIR", "/workspace/artifacts"))
LOCK_PATH = SUBMISSION_DIR / "runtime.lock.json"
RUNTIME_DIR = SUBMISSION_DIR / "runtime"
PAYLOAD_DIR = RUNTIME_DIR / "payloads"
ZSTD_BIN = RUNTIME_DIR / "bin" / "zstd"
GSC_PAYLOAD = PAYLOAD_DIR / "gsc-runtime.tar.zst"
CLAUDE_PAYLOAD = PAYLOAD_DIR / "claude.zst"
GATEWAY_BIN = RUNTIME_DIR / "gateway" / "anthropic-proxy"
_children: list[subprocess.Popen] = []

@dataclass(frozen=True)
class RequirementModule:
    index: int
    total: int
    node_id: str
    name: str
    subtree: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARC-Bench Factory agent powered by Claude Code + GSC.")
    parser.add_argument("requirement_path", nargs="?", default=os.environ.get("ARCBENCH_TASK_DIR", "requirements"))
    parser.add_argument("--output-dir", default=os.environ.get("ARCBENCH_OUTPUT_DIR", "/workspace/template"))
    parser.add_argument("--type", dest="task_type", default=os.environ.get("ARCBENCH_TASK_TYPE", "web"))
    return parser.parse_args()


def die(message: str, code: int = 2) -> None:
    print(f"[arc-claude-gsc] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        die(f"{label} not found: {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        die(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def load_lock() -> dict:
    require_file(LOCK_PATH, "runtime lock")
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def extract_gsc(lock: dict, cache_root: Path) -> Path:
    expected = lock["gsc"]["sha256"]
    target = cache_root / "gsc"
    marker = target / ".arc-payload-sha256"
    server = target / "bin" / "gsc-spec-server"
    bootstrap = target / "mcp" / "src" / "bootstrap.mjs"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected and server.is_file() and bootstrap.is_file():
        return target
    verify_sha256(GSC_PAYLOAD, expected, "GSC runtime payload")
    temp = cache_root / f".gsc-{os.getpid()}.tmp"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    zstd = subprocess.Popen([str(ZSTD_BIN), "-dc", str(GSC_PAYLOAD)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert zstd.stdout is not None
    tar = subprocess.run(["tar", "-xf", "-", "-C", str(temp)], stdin=zstd.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    zstd.stdout.close()
    zstd_stderr = (zstd.stderr.read() if zstd.stderr else b"").decode("utf-8", "replace")
    zstd_rc = zstd.wait()
    if zstd_rc != 0 or tar.returncode != 0:
        shutil.rmtree(temp, ignore_errors=True)
        die(f"failed to extract GSC runtime: zstd={zstd_rc} {zstd_stderr[-500:]!r}; tar={tar.returncode} {tar.stderr[-500:]!r}")
    unpacked = temp / "plugin-final"
    if not unpacked.is_dir():
        die("GSC runtime archive is missing plugin-final/")
    (unpacked / ".arc-payload-sha256").write_text(expected + "\n", encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    unpacked.rename(target)
    shutil.rmtree(temp, ignore_errors=True)
    return target


def extract_claude(lock: dict, cache_root: Path) -> Path:
    expected = lock["claudeCode"]["binarySha256"]
    target_dir = cache_root / "claude"
    target = target_dir / "claude"
    marker = target_dir / ".arc-binary-sha256"
    if target.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected:
        return target
    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target_dir / f".claude-{os.getpid()}.tmp"
    temp.unlink(missing_ok=True)
    result = subprocess.run([str(ZSTD_BIN), "-d", "-f", str(CLAUDE_PAYLOAD), "-o", str(temp)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        die(f"failed to extract Claude Code: {result.stderr[-500:]}")
    verify_sha256(temp, expected, "Claude Code binary")
    temp.chmod(0o755)
    temp.replace(target)
    marker.write_text(expected + "\n", encoding="utf-8")
    return target


def prepare_runtime(lock: dict) -> tuple[Path, Path]:
    for path, label in ((ZSTD_BIN, "zstd"), (GSC_PAYLOAD, "GSC runtime payload"), (CLAUDE_PAYLOAD, "Claude Code payload"), (GATEWAY_BIN, "anthropic-proxy")):
        require_file(path, label)
    verify_sha256(ZSTD_BIN, lock["zstd"]["sha256"], "zstd")
    verify_sha256(GATEWAY_BIN, lock["gateway"]["binarySha256"], "anthropic-proxy")
    ZSTD_BIN.chmod(0o755)
    GATEWAY_BIN.chmod(0o755)
    cache_root = ARTIFACTS_DIR / "runtime"
    cache_root.mkdir(parents=True, exist_ok=True)
    return extract_gsc(lock, cache_root), extract_claude(lock, cache_root)


def copy_template_contents(output_dir: Path) -> None:
    template_dir = SUBMISSION_DIR / "template"
    if not template_dir.is_dir():
        die(f"Factory starter template directory not found: {template_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(template_dir.iterdir()):
        if source.name == "template.yaml":
            continue
        destination = output_dir / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


def copy_skills(output_dir: Path) -> Path:
    source = SUBMISSION_DIR / "skills"
    if not source.is_dir():
        die(f"ARC-Bench skills directory not found: {source}")
    destination = output_dir / ".claude" / "skills"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def load_root_modules(requirement_path: Path) -> tuple[Path, list[RequirementModule]]:
    requirements_dir = requirement_path if requirement_path.is_dir() else requirement_path.parent
    requirements_file = requirement_path if requirement_path.is_file() else requirements_dir / "requirements.yaml"
    require_file(requirements_file, "requirements.yaml")
    payload = yaml.safe_load(requirements_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("id") or "").strip() != "ROOT":
        die("requirements.yaml must contain a ROOT mapping")
    children = [item for item in payload.get("children", []) if isinstance(item, dict)]
    if not children:
        die("ROOT must contain at least one child module")
    modules: list[RequirementModule] = []
    for index, subtree in enumerate(children, 1):
        node_id = str(subtree.get("id") or subtree.get("req_id") or "").strip()
        if not node_id:
            die(f"ROOT child {index} has no id")
        modules.append(RequirementModule(index, len(children), node_id, str(subtree.get("name") or node_id).strip(), subtree))
    return requirements_dir, modules


def upstream_chat_url(base: str) -> str:
    clean = base.rstrip("/")
    return clean if clean.endswith("/chat/completions") else clean + "/chat/completions"


def wait_http(url: str, process: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            die(f"gateway exited before becoming ready (code={process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    die(f"gateway health check timed out: {last_error}")


def chown_tree(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except (FileNotFoundError, PermissionError):
        return
    if path.is_dir():
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                try:
                    os.chown(Path(root) / name, uid, gid, follow_symlinks=False)
                except (FileNotFoundError, PermissionError):
                    pass


def choose_agent_identity(output_dir: Path, home_dir: Path, plugin_data: Path) -> tuple[int, int, str] | None:
    if os.geteuid() != 0:
        return None
    stat = output_dir.stat()
    if stat.st_uid != 0:
        uid, gid = stat.st_uid, stat.st_gid
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            username = f"uid-{uid}"
    else:
        account = next((pwd.getpwnam(name) for name in ("pwuser", "node", "nobody") if _user_exists(name)), None)
        if account is None:
            die("ARC is running as root and no non-root execution user is available")
        uid, gid, username = account.pw_uid, account.pw_gid, account.pw_name
        chown_tree(output_dir, uid, gid)
    chown_tree(home_dir, uid, gid)
    chown_tree(plugin_data, uid, gid)
    return uid, gid, username


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def privilege_dropper(identity: tuple[int, int, str] | None):
    if identity is None:
        return None
    uid, gid, _ = identity
    def drop() -> None:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return drop


def module_prompt(module: RequirementModule, requirements_dir: Path, task_type: str, completed_ids: list[str], skills_dir: Path) -> str:
    completed = ", ".join(completed_ids) if completed_ids else "none"
    return textwrap.dedent(f"""
        You are implementing an ARC-Bench Factory task in the current project using GSC.
        Preserve existing work. Implement exactly this ROOT child subtree, including descendants.
        Do not read the complete requirements.yaml; this subtree is the authoritative scope for this turn.

        Task type: {task_type}
        Requirement source directory: {requirements_dir}
        Module {module.index}/{module.total}: {module.node_id} - {module.name}
        Previously completed ROOT modules: {completed}

        ARC-Bench skills are installed in {skills_dir}.
        Use arcbench-runtime-signals for useful progress updates, arcbench-traceability to record
        requirement/interface/test links, and arcbench-checkpoint for coherent git checkpoints.
        Run the provided skill scripts instead of hand-writing ARC metadata.

        Requirement subtree:
        ```json
        {json.dumps(module.subtree, ensure_ascii=False, indent=2)}
        ```

        Use the loaded GSC plugin to plan, implement, and validate the module. Make real code changes,
        run focused validation, keep the application runnable, and do not start a long-running server.
    """).strip()


def final_validation_prompt(modules: list[RequirementModule], task_type: str) -> str:
    ids = ", ".join(m.node_id for m in modules)
    return textwrap.dedent(f"""
        Perform the final integration pass for this ARC-Bench {task_type} project.
        Completed ROOT modules: {ids}.
        Inspect the current project rather than the full requirements.yaml. Use GSC to find integration gaps,
        run the fastest meaningful frontend/backend tests or build checks, fix regressions you discover,
        update ARC traceability/checkpoints where appropriate, and leave the repository runnable.
        Do not start a long-running server.
    """).strip()


def run_claude_turn(claude_bin: Path, gsc_dir: Path, output_dir: Path, env: dict[str, str], identity, prompt: str) -> int:
    command = [str(claude_bin), "-p", prompt, "--plugin-dir", str(gsc_dir), "--model", "sonnet", "--permission-mode", "bypassPermissions", "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    process = subprocess.Popen(command, cwd=output_dir, env=env, preexec_fn=privilege_dropper(identity))
    _children.append(process)
    rc = process.wait()
    if rc != 0:
        die(f"Claude Code turn failed with exit code {rc}", rc)
    return rc


def cleanup() -> None:
    for child in reversed(_children):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 3.0
    for child in reversed(_children):
        if child.poll() is None:
            try:
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass


def main() -> int:
    args = parse_args()
    requirement_path = Path(args.requirement_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    requirements_dir, modules = load_root_modules(requirement_path)
    copy_template_contents(output_dir)
    skills_dir = copy_skills(output_dir)

    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("MODEL")
    if not base_url or not api_key or not model:
        die("OPENAI_BASE_URL, OPENAI_API_KEY, and MODEL are required")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    gsc_dir, claude_bin = prepare_runtime(load_lock())
    home_dir = ARTIFACTS_DIR / "home"
    plugin_data = ARTIFACTS_DIR / "gsc-plugin-data"
    home_dir.mkdir(parents=True, exist_ok=True)
    plugin_data.mkdir(parents=True, exist_ok=True)
    identity = choose_agent_identity(output_dir, home_dir, plugin_data)

    env = os.environ.copy()
    env.update({
        "HOME": str(home_dir),
        "GSC_ARC_PACKAGED_RUNTIME": "1",
        "GSC_RUNTIME_SERVER_BIN": str(gsc_dir / "bin" / "gsc-spec-server"),
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "PATH": os.pathsep.join([str(gsc_dir / "bin"), str(gsc_dir / "lsp" / "web" / "node_modules" / ".bin"), env.get("PATH", "")]),
    })
    if identity is not None:
        env["USER"] = identity[2]
        env["LOGNAME"] = identity[2]
        for key in ("SUDO_USER", "SUDO_UID", "SUDO_GID"):
            env.pop(key, None)

    gateway_env = env.copy()
    gateway_env.update({
        "ANTHROPIC_PROXY_LISTEN_ADDR": "127.0.0.1:8787",
        "ANTHROPIC_PROXY_UPSTREAM_URL": upstream_chat_url(base_url),
        "ANTHROPIC_PROXY_UPSTREAM_API_KEY": api_key,
        "ANTHROPIC_PROXY_DEFAULT_MODEL": model,
        "ANTHROPIC_PROXY_FORCE_MODEL": "1",
        "ANTHROPIC_PROXY_TOOL_FORMAT": "native",
        "ANTHROPIC_PROXY_CLIENT_KEY": "arc-local",
        "ANTHROPIC_PROXY_LOG_LEVEL": os.environ.get("ARC_GATEWAY_LOG_LEVEL", "info"),
        "ANTHROPIC_PROXY_REQUEST_TIMEOUT_SEC": "600",
    })
    gateway_log = (ARTIFACTS_DIR / "gateway.log").open("w", encoding="utf-8")
    gateway = subprocess.Popen([str(GATEWAY_BIN), "serve"], cwd=SUBMISSION_DIR, env=gateway_env, stdout=gateway_log, stderr=subprocess.STDOUT, text=True)
    _children.append(gateway)
    wait_http("http://127.0.0.1:8787/health", gateway)

    claude_env = env.copy()
    claude_env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:8787"
    claude_env["ANTHROPIC_API_KEY"] = "arc-local"
    completed: list[str] = []
    for module in modules:
        print(f"[arc-claude-gsc] module {module.index}/{module.total}: {module.node_id}", flush=True)
        run_claude_turn(claude_bin, gsc_dir, output_dir, claude_env, identity, module_prompt(module, requirements_dir, args.task_type, completed, skills_dir))
        completed.append(module.node_id)
    if os.environ.get("ARC_SKIP_FINAL_VALIDATION", "0") != "1":
        print("[arc-claude-gsc] final integration validation", flush=True)
        run_claude_turn(claude_bin, gsc_dir, output_dir, claude_env, identity, final_validation_prompt(modules, args.task_type))
    return 0


def _signal_exit(code: int) -> None:
    cleanup()
    raise SystemExit(code)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: _signal_exit(143))
    signal.signal(signal.SIGINT, lambda *_: _signal_exit(130))
    try:
        raise SystemExit(main())
    finally:
        cleanup()
