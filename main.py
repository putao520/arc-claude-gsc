#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


SUBMISSION_DIR = Path(os.environ.get("ARCBENCH_SUBMISSION_DIR", Path(__file__).resolve().parent))
TEMPLATE_DIR = Path(os.environ.get("ARCBENCH_TEMPLATE_DIR", "/workspace/template"))
TASK_DIR = Path(os.environ.get("ARCBENCH_TASK_DIR", "/workspace/task"))
ARTIFACTS_DIR = Path(os.environ.get("ARCBENCH_ARTIFACTS_DIR", "/workspace/artifacts"))
RUNTIME_DIR = SUBMISSION_DIR / "runtime"
GSC_DIR = RUNTIME_DIR / "gsc"
CLAUDE_DIR = RUNTIME_DIR / "claude"
GATEWAY_BIN = RUNTIME_DIR / "gateway" / "anthropic-proxy"
CLAUDE_BIN = CLAUDE_DIR / "node_modules" / ".bin" / "claude"

_children: list[subprocess.Popen] = []


def die(message: str, code: int = 2) -> None:
    print(f"[arc-claude-gsc] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        die(f"{label} not found: {path}")


def task_prompt() -> str:
    direct = os.environ.get("ARCBENCH_TASK_PROMPT")
    if direct:
        return direct

    prompt_path = os.environ.get("ARCBENCH_PROMPT_PATH")
    if prompt_path and Path(prompt_path).is_file():
        return Path(prompt_path).read_text(encoding="utf-8")

    parts: list[str] = []
    for name in ("requirements.md", "prerequisites.md"):
        path = TASK_DIR / name
        if path.is_file():
            parts.append(f"# {name}\n\n{path.read_text(encoding='utf-8')}")
    if not parts:
        die("no ARC task prompt or task requirements were found")

    parts.append(
        "\nApply the requested changes directly inside /workspace/template. "
        "Use the loaded GSC plugin when useful. Finish only after validating the implementation."
    )
    return "\n\n".join(parts)


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


def cleanup(*_: object) -> None:
    for child in reversed(_children):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 3
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
    require_file(GATEWAY_BIN, "anthropic-proxy")
    require_file(CLAUDE_BIN, "Claude Code")
    require_file(GSC_DIR / "bin" / "gsc-spec-server", "compiled GSC server")
    require_file(GSC_DIR / "mcp" / "src" / "bootstrap.mjs", "GSC MCP bootstrap")

    if not TEMPLATE_DIR.is_dir():
        die(f"ARC template directory not found: {TEMPLATE_DIR}")

    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("MODEL")
    if not base_url or not api_key or not model:
        die("OPENAI_BASE_URL, OPENAI_API_KEY, and MODEL are required")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    home_dir = ARTIFACTS_DIR / "home"
    plugin_data = ARTIFACTS_DIR / "gsc-plugin-data"
    home_dir.mkdir(parents=True, exist_ok=True)
    plugin_data.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["GSC_ARC_PACKAGED_RUNTIME"] = "1"
    env["GSC_RUNTIME_SERVER_BIN"] = str(GSC_DIR / "bin" / "gsc-spec-server")
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    env["PATH"] = os.pathsep.join(
        [
            str(GSC_DIR / "bin"),
            str(GSC_DIR / "lsp" / "web" / "node_modules" / ".bin"),
            str(CLAUDE_DIR / "node_modules" / ".bin"),
            env.get("PATH", ""),
        ]
    )

    gateway_env = env.copy()
    gateway_env.update(
        {
            "ANTHROPIC_PROXY_LISTEN_ADDR": "127.0.0.1:8787",
            "ANTHROPIC_PROXY_UPSTREAM_URL": upstream_chat_url(base_url),
            "ANTHROPIC_PROXY_UPSTREAM_API_KEY": api_key,
            "ANTHROPIC_PROXY_DEFAULT_MODEL": model,
            "ANTHROPIC_PROXY_FORCE_MODEL": "1",
            "ANTHROPIC_PROXY_TOOL_FORMAT": "native",
            "ANTHROPIC_PROXY_CLIENT_KEY": "arc-local",
            "ANTHROPIC_PROXY_LOG_LEVEL": os.environ.get("ARC_GATEWAY_LOG_LEVEL", "info"),
            "ANTHROPIC_PROXY_REQUEST_TIMEOUT_SEC": "600",
        }
    )

    gateway_log = (ARTIFACTS_DIR / "gateway.log").open("w", encoding="utf-8")
    gateway = subprocess.Popen(
        [str(GATEWAY_BIN), "serve"],
        cwd=SUBMISSION_DIR,
        env=gateway_env,
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _children.append(gateway)
    wait_http("http://127.0.0.1:8787/health", gateway)

    claude_env = env.copy()
    claude_env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:8787"
    claude_env["ANTHROPIC_API_KEY"] = "arc-local"

    command = [
        str(CLAUDE_BIN),
        "-p",
        task_prompt(),
        "--plugin-dir",
        str(GSC_DIR),
        "--model",
        "sonnet",
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
    ]

    print("[arc-claude-gsc] starting Claude Code", flush=True)
    claude = subprocess.Popen(command, cwd=TEMPLATE_DIR, env=claude_env)
    _children.append(claude)
    return claude.wait()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(143)))
    signal.signal(signal.SIGINT, lambda *_: (cleanup(), sys.exit(130)))
    try:
        raise SystemExit(main())
    finally:
        cleanup()
