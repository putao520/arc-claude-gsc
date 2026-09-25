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
from arcbench_agent_runtime import AgentRuntime


SUBMISSION_DIR = Path(os.environ.get("ARCBENCH_SUBMISSION_DIR", Path(__file__).resolve().parent))
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
    parser = argparse.ArgumentParser(description="ARC-Bench Factory agent: original Claude Code + GSC")
    parser.add_argument("requirement_path", nargs="?", default=os.environ.get("ARCBENCH_TASK_DIR", "requirements"))
    parser.add_argument("--output-dir", default=os.environ.get("ARCBENCH_OUTPUT_DIR", "."))
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
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"invalid runtime.lock.json: {exc}")


def extract_gsc(lock: dict, cache_root: Path) -> Path:
    expected = lock["gsc"]["sha256"]
    target = cache_root / "gsc"
    marker = target / ".arc-payload-sha256"
    server = target / "bin" / "gsc-spec-server"
    bootstrap = target / "mcp" / "src" / "bootstrap.mjs"

    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected:
        if server.is_file() and bootstrap.is_file():
            return target

    verify_sha256(GSC_PAYLOAD, expected, "GSC runtime payload")

    temp = cache_root / f".gsc-{os.getpid()}.tmp"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)

    zstd = subprocess.Popen(
        [str(ZSTD_BIN), "-dc", str(GSC_PAYLOAD)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert zstd.stdout is not None
    tar = subprocess.run(
        ["tar", "-xf", "-", "-C", str(temp)],
        stdin=zstd.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    zstd.stdout.close()
    zstd_stderr = (zstd.stderr.read() if zstd.stderr else b"").decode("utf-8", "replace")
    zstd_rc = zstd.wait()

    if zstd_rc != 0 or tar.returncode != 0:
        shutil.rmtree(temp, ignore_errors=True)
        die(
            "failed to extract GSC runtime: "
            f"zstd={zstd_rc} {zstd_stderr[-500:]!r}; "
            f"tar={tar.returncode} {tar.stderr[-500:]!r}"
        )

    unpacked = temp / "plugin-final"
    if not unpacked.is_dir():
        shutil.rmtree(temp, ignore_errors=True)
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

    if target.is_file() and marker.is_file():
        if marker.read_text(encoding="utf-8").strip() == expected:
            return target

    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target_dir / f".claude-{os.getpid()}.tmp"
    temp.unlink(missing_ok=True)

    result = subprocess.run(
        [str(ZSTD_BIN), "-d", "-f", str(CLAUDE_PAYLOAD), "-o", str(temp)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        die(f"failed to extract Claude Code: {result.stderr[-500:]}")

    verify_sha256(temp, expected, "Claude Code binary")
    temp.chmod(0o755)
    temp.replace(target)
    marker.write_text(expected + "\n", encoding="utf-8")
    return target


def prepare_runtime(lock: dict, artifacts_dir: Path) -> tuple[Path, Path]:
    require_file(ZSTD_BIN, "zstd")
    require_file(GSC_PAYLOAD, "GSC runtime payload")
    require_file(CLAUDE_PAYLOAD, "Claude Code payload")
    require_file(GATEWAY_BIN, "anthropic-proxy")

    verify_sha256(ZSTD_BIN, lock["zstd"]["sha256"], "zstd")
    verify_sha256(GATEWAY_BIN, lock["gateway"]["binarySha256"], "anthropic-proxy")

    ZSTD_BIN.chmod(0o755)
    GATEWAY_BIN.chmod(0o755)

    cache_root = artifacts_dir / "runtime"
    cache_root.mkdir(parents=True, exist_ok=True)
    gsc_dir = extract_gsc(lock, cache_root)
    claude_bin = extract_claude(lock, cache_root)
    return gsc_dir, claude_bin


def copy_template_contents(output_dir: Path) -> None:
    template_dir = SUBMISSION_DIR / "template"
    if not template_dir.is_dir():
        die(f"Factory starter template not found: {template_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(template_dir.iterdir()):
        if source.name == "template.yaml":
            continue
        destination = output_dir / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


def copy_arc_skills(output_dir: Path) -> Path | None:
    source = SUBMISSION_DIR / "skills"
    if not source.is_dir():
        return None
    destination = output_dir / ".claude" / "skills"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def load_requirement_tree(requirements_dir: Path) -> dict[str, Any]:
    path = requirements_dir / "requirements.yaml"
    require_file(path, "requirements.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("id") or "").strip() != "ROOT":
        die("requirements.yaml must contain a ROOT mapping")
    return payload


def load_root_modules(payload: dict[str, Any]) -> list[RequirementModule]:
    children = [item for item in payload.get("children", []) if isinstance(item, dict)]
    if not children:
        die("ROOT must contain at least one child module")
    result = []
    for index, subtree in enumerate(children, start=1):
        node_id = str(subtree.get("id") or subtree.get("req_id") or "").strip()
        if not node_id:
            die(f"ROOT child {index} has no id")
        result.append(RequirementModule(index, len(children), node_id, str(subtree.get("name") or node_id).strip(), subtree))
    return result


def ensure_gsc_spec(output_dir: Path, module: RequirementModule) -> Path:
    """Materialize the current ARC requirement subtree into GSC's SPEC-first workspace."""
    spec_dir = output_dir / "SPEC" / "arcbench"
    spec_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in module.node_id)
    path = spec_dir / f"{safe_id}.md"
    path.write_text(
        f"# {module.node_id}: {module.name}\n\n"
        "Source: ARC-Bench requirements.yaml ROOT child subtree.\n\n"
        "```json\n" + json.dumps(module.subtree, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    return path


def module_prompt(module: RequirementModule, requirements_dir: Path, skills_dir: Path | None, completed: list[str], task_type: str) -> str:
    completed_text = ", ".join(completed) if completed else "none"
    skills_text = f"ARC skills are installed at {skills_dir}." if skills_dir else "The adapter emits baseline ARC runtime states and checkpoints."
    return textwrap.dedent(f"""
        You are implementing an ARC-Bench Agentic Software Factory task using original Claude Code with the GSC plugin loaded.

        Target task type: {task_type}
        Implement ROOT module {module.index}/{module.total}: {module.node_id} - {module.name}
        Previously completed ROOT modules: {completed_text}
        Requirement source directory: {requirements_dir}

        The current working directory is the persistent generated project. Preserve working features from earlier modules.
        Use GSC actively for requirements/specification, implementation planning, coding, validation, and state tracking rather than bypassing it.

        {skills_text}
        If ARC skills are present, read the runtime-signals, traceability, and checkpoint skill instructions and record detailed requirement-to-interface/file/test traceability.
        Run focused validation/tests. Do not start a long-running server. Do not erase work from earlier modules.

        Do not read the complete requirements.yaml. Work only from this complete ROOT-child subtree:
        ```json
        {json.dumps(module.subtree, ensure_ascii=False, indent=2)}
        ```

        Finish only after implementation and validation are complete. Summarize changed files and validation performed.
    """).strip()


def upstream_chat_url(base: str) -> str:
    clean = base.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return clean + "/chat/completions"


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
    if not path.is_dir():
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            try:
                os.chown(item, uid, gid, follow_symlinks=False)
            except (FileNotFoundError, PermissionError):
                pass


def choose_agent_identity(output_dir: Path, home_dir: Path, plugin_data: Path) -> tuple[int, int, str] | None:
    if os.geteuid() != 0:
        return None

    template_stat = output_dir.stat()
    if template_stat.st_uid != 0:
        uid, gid = template_stat.st_uid, template_stat.st_gid
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            username = f"uid-{uid}"
    else:
        account = None
        for candidate in ("pwuser", "node", "nobody"):
            try:
                found = pwd.getpwnam(candidate)
            except KeyError:
                continue
            if found.pw_uid != 0:
                account = found
                break
        if account is None:
            die("ARC is running as root and no non-root execution user is available")
        uid, gid, username = account.pw_uid, account.pw_gid, account.pw_name
        chown_tree(output_dir, uid, gid)

    chown_tree(home_dir, uid, gid)
    chown_tree(plugin_data, uid, gid)
    return uid, gid, username


def privilege_dropper(identity: tuple[int, int, str] | None):
    if identity is None:
        return None
    uid, gid, _ = identity

    def drop() -> None:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)

    return drop


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
    requirements_dir = Path(args.requirement_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not requirements_dir.is_dir():
        die(f"requirement directory not found: {requirements_dir}")

    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("MODEL", "").strip()
    if not base_url or not api_key or not model:
        die("OPENAI_BASE_URL, OPENAI_API_KEY, and MODEL are required")

    copy_template_contents(output_dir)
    skills_dir = copy_arc_skills(output_dir)
    requirement_tree = load_requirement_tree(requirements_dir)
    modules = load_root_modules(requirement_tree)

    # ARC runners may mount the output directory with a host uid while the adapter starts as root.
    # Mark only this project as safe so AgentRuntime git operations work across that ownership boundary.
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(output_dir)], check=False)
    runtime = AgentRuntime.from_env(project_dir=str(output_dir))
    runtime.traceability.init_store(reset=False)
    runtime.traceability.store_requirement_tree(requirement_tree)
    runtime.git.ensure_repo(create_initial_commit=True)
    runtime.events.mark_run_started("arc-claude-gsc Factory26 run started")

    artifacts_dir = Path(os.environ.get("ARCBENCH_ARTIFACTS_DIR", str(output_dir.parent / "artifacts"))).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    gsc_dir, claude_bin = prepare_runtime(load_lock(), artifacts_dir)
    require_file(gsc_dir / "bin" / "gsc-spec-server", "compiled GSC server")
    require_file(gsc_dir / "mcp" / "src" / "bootstrap.mjs", "GSC MCP bootstrap")

    home_dir = artifacts_dir / "home"
    plugin_data = artifacts_dir / "gsc-plugin-data"
    home_dir.mkdir(parents=True, exist_ok=True)
    plugin_data.mkdir(parents=True, exist_ok=True)
    identity = choose_agent_identity(output_dir, home_dir, plugin_data)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["GSC_ARC_PACKAGED_RUNTIME"] = "1"
    env["GSC_RUNTIME_SERVER_BIN"] = str(gsc_dir / "bin" / "gsc-spec-server")
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    env["PATH"] = os.pathsep.join([str(gsc_dir / "bin"), str(gsc_dir / "lsp" / "web" / "node_modules" / ".bin"), env.get("PATH", "")])

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
    gateway_log = (artifacts_dir / "gateway.log").open("w", encoding="utf-8")
    gateway = subprocess.Popen([str(GATEWAY_BIN), "serve"], cwd=SUBMISSION_DIR, env=gateway_env, stdout=gateway_log, stderr=subprocess.STDOUT, text=True)
    _children.append(gateway)
    wait_http("http://127.0.0.1:8787/health", gateway)

    claude_env = env.copy()
    claude_env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:8787"
    claude_env["ANTHROPIC_API_KEY"] = "arc-local"
    for key in ("SUDO_USER", "SUDO_UID", "SUDO_GID"):
        claude_env.pop(key, None)
    if identity is not None:
        _, _, username = identity
        claude_env["USER"] = username
        claude_env["LOGNAME"] = username

    completed: list[str] = []
    try:
        for module in modules:
            print(f"[arc-claude-gsc] module {module.index}/{module.total}: {module.node_id} - {module.name}", flush=True)
            spec_path = ensure_gsc_spec(output_dir, module)
            runtime.events.mark_design_started(module.node_id, f"Planning {module.name} from {spec_path.relative_to(output_dir)}")
            command = [
                str(claude_bin), "-p", module_prompt(module, requirements_dir, skills_dir, completed, args.task_type),
                "--plugin-dir", str(gsc_dir), "--model", "sonnet",
                "--permission-mode", "bypassPermissions", "--no-session-persistence",
                "--output-format", "stream-json", "--verbose",
            ]
            runtime.events.mark_design_done(module.node_id, f"Delegated {module.name} to Claude Code + GSC")
            runtime.events.mark_implementation_started(module.node_id, f"Implementing {module.name}")
            claude = subprocess.Popen(command, cwd=output_dir, env=claude_env, preexec_fn=privilege_dropper(identity))
            _children.append(claude)
            rc = claude.wait()
            if rc != 0:
                runtime.events.mark_implementation_failed(module.node_id, f"Claude Code exited with {rc}")
                runtime.events.mark_test_failed(module.node_id, "Module did not complete")
                runtime.events.mark_run_failed(f"Module {module.node_id} failed")
                return rc
            runtime.events.mark_implementation_done(module.node_id, f"Implemented {module.name}")
            runtime.events.mark_test_passed(module.node_id, "Agent completed module validation")
            runtime.git.commit(f"{module.node_id}: {module.name}")
            completed.append(module.node_id)
        runtime.events.mark_run_completed("All ROOT modules completed")
        return 0
    except Exception as exc:
        runtime.events.mark_run_failed(str(exc))
        raise


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