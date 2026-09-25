# arc-claude-gsc

ARC-Bench / Factory26 custom-agent adapter for **original Claude Code + GSC**.

This repository intentionally stays thin:

- Claude Code is the original upstream binary, pinned by version and SHA-256.
- GSC MCP and hooks remain JavaScript.
- `gsc-spec-server` is shipped as a compiled Linux x86_64 Node SEA executable.
- TypeScript LSP is bundled as the Web profile.
- A pinned small CLI proxy converts Claude's Anthropic Messages API to ARC's OpenAI-compatible Chat Completions endpoint.
- `main.py` only owns bootstrap/lifecycle. It does not reimplement agent planning, coding, testing, or GSC orchestration.

## Runtime topology

```text
ARC runner
  -> /workspace/submission/main.py
     -> unpack pinned runtime into /workspace/artifacts/runtime
     -> start anthropic-proxy
        -> ARC OpenAI-compatible API
     -> start original Claude Code
        cwd=/workspace/template
        --plugin-dir <unpacked GSC>
        -> GSC MCP (JavaScript)
           -> compiled gsc-spec-server
           -> TypeScript LSP
```

When ARC starts the submission as root, `main.py` automatically drops only the Claude/GSC process tree to a non-root workspace user. This is required because Claude Code refuses bypass-permission mode as root.

## ARC workspace contract

```text
/workspace/
  submission/
  template/
  task/
  tests/
  sdk/
  prompt/
  artifacts/
```

The agent modifies only `/workspace/template`. Runtime extraction and logs go under `/workspace/artifacts`.

## Pinned components

See `runtime.lock.json`.

Current pinned stack:

- Claude Code 2.1.281
- GSC 6.8.1733
- anthropic-proxy v1.1.0
- TypeScript Language Server 5.1.3
- TypeScript 5.9.3
- bundled Node 22.23.2

Every downloaded/runtime-critical executable is SHA-256 checked.

## Build the ARC submission

Build-machine requirements:

- Linux x86_64
- Python 3
- Node.js >= 22 + npm
- curl
- tar

Run:

```bash
git clone https://github.com/putao520/arc-claude-gsc.git
cd arc-claude-gsc
./scripts/package_submission.sh
```

Output:

```text
dist/
  submission/
  submission.zip
```

The build downloads only pinned Release assets, verifies hashes, installs the pinned original Claude Code package, keeps the single Linux executable, compresses it, and creates the final ZIP.

## Run the ARC-like smoke test

Docker is required.

To build and test in one command:

```bash
./scripts/smoke_arc_like.sh
```

If `dist/submission.zip` already exists:

```bash
ARC_SKIP_PACKAGE=1 ./scripts/smoke_arc_like.sh
```

The smoke test uses `mcr.microsoft.com/playwright/python:v1.54.0-jammy`, creates the ARC `/workspace` layout, starts a local OpenAI-compatible mock upstream, then verifies the real chain:

```text
main.py
  -> runtime extraction
  -> anthropic-proxy
  -> original Claude Code
  -> real Write tool call
  -> GSC plugin/MCP load
  -> compiled GSC server
  -> TypeScript LSP
  -> /workspace/template/ARC_SMOKE.txt
```

A pass ends with:

```text
ARC-like smoke PASS
result: ARC_CLAUDE_GSC_OK
```

## Upload to ARC-Bench

1. Build `dist/submission.zip`.
2. Run the ARC-like smoke test.
3. In ARC-Bench, create/select a **Custom Agent** submission and upload `dist/submission.zip`.
4. Run the platform's Smoke Competition first.
5. Once the Smoke task is stable, run the Factory26/target competition tasks with the same submission version.

ARC injects the model credentials/runtime variables. The adapter expects:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `MODEL`
- `ARCBENCH_TEMPLATE_DIR` (defaults to `/workspace/template`)
- `ARCBENCH_PROMPT_PATH` or `ARCBENCH_TASK_PROMPT`

No model API key is stored in this repository or submission bundle.

## Runtime Release

The GSC runtime is published under the `gsc-runtime-v1` Release. The submission embeds the compressed pinned asset, so contest runtime does **not** download GSC, Claude Code, Node, LSP, or the gateway from the internet.

## Security / IP boundary

The heavy GSC server implementation is not shipped as a source directory in the public plugin runtime; the server is delivered as a compiled Node SEA ELF. MCP/hook integration remains JavaScript by design.

Node SEA is a deployment boundary, not a claim of irreversible source-code protection.

## License

The original code in this repository is licensed under the [MIT License](./LICENSE).

Third-party components, downloaded runtime assets, and bundled upstream binaries (including Claude Code, Node.js, TypeScript tooling, and other pinned components) are **not relicensed by this repository**. They retain their respective upstream licenses and terms. The MIT license applies only to code and materials for which `putao520` has the right to grant that license.

## Factory26 competition adapter

The current Agentic Software Factory runner invokes Python agents as:

```bash
python3 main.py /path/to/requirements --output-dir /path/to/output --type web
```

`requirements.yaml` must have `id: ROOT`. The adapter copies the official Factory starter project into the output directory, installs the official ARC-Bench skills under `.claude/skills`, then runs the pinned original Claude Code + GSC once per direct ROOT child subtree. A final integration-validation turn runs after all modules unless `ARC_SKIP_FINAL_VALIDATION=1`.

The official ARC-Bench Claude Code starter ZIP is intentionally **not** committed to this public repository. Download it from the competition page and either place it at:

```text
vendor/agent-claude-code-based.zip
```

or point the build at it:

```bash
ARC_FACTORY_TEMPLATE_ZIP=/path/to/agent-claude-code-based.zip ./scripts/package_submission.sh
```

Packaging validates the official starter before copying only `template/`, `skills/`, and `arcbench-agent-runtime/` into the final submission. The resulting `dist/submission.zip` is the file to upload as a Python agent.

Run the Factory-contract smoke test with:

```bash
ARC_FACTORY_TEMPLATE_ZIP=/path/to/agent-claude-code-based.zip ./scripts/smoke_arc_like.sh
```

The smoke test exercises the real packaged Claude Code → GSC → ARC model bridge against a synthetic `requirements.yaml` and verifies that the agent modifies the shared output directory.
