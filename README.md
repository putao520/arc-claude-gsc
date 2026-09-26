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

## Factory26 current competition contract

The current Agentic Software Factory runner invokes Python submissions as:

```bash
python3 main.py /path/to/requirements --output-dir /path/to/output --type web
```

`requirements.yaml` must have an `id: ROOT` tree. The adapter copies the official ARC-Bench starter project into the output directory, imports the official ARC skills when present, stores requirement traceability through `arcbench-runtime`, initializes git checkpoints, and then processes each direct ROOT child with original Claude Code + GSC in the same persistent worktree. Each subtree is also materialized under `SPEC/arcbench/` before Claude Code starts so GSC's SPEC-first gate is satisfied.

## Build the ARC submission

Download the current **Claude Code** starter ZIP from the competition page first. Keep that downloaded ZIP outside git and pass its path when packaging:

```bash
ARC_FACTORY26_STARTER_ZIP=/path/to/agent-claude-code-based.zip \
  ./scripts/package_submission.sh
```

Output:

```text
dist/submission.zip
```

The packager imports only the starter's `template/` and `skills/` directories. The original Claude Code, GSC runtime, zstd helper and protocol bridge remain version-pinned and SHA-256 verified. The competition ZIP itself is not committed to this repository.

## Run the Factory26-style smoke test

Docker is required. The smoke creates a minimal starter fixture, packages the agent, and executes the exact current CLI shape with `requirements.yaml`:

```bash
./scripts/smoke_arc_like.sh
```

A pass verifies all of these at once:

- original Claude Code starts through the OpenAI-compatible ARC model bridge;
- GSC plugin/MCP loads;
- a real write happens inside `--output-dir`;
- `.arc/runner-events.jsonl` is produced;
- ARC traceability files contain every ROOT child requirement;
- GSC's `SPEC/` gate is satisfied for every module;
- multiple ROOT modules preserve the same worktree and create separate checkpoints;
- the installed `arcbench-runtime==0.1.0` source matches the runtime shipped in the official Claude Code starter file-for-file;
- git contains an initial checkpoint and per-module checkpoints.

For a repeat run using an already-built `dist/submission.zip`:

```bash
ARC_SKIP_PACKAGE=1 ./scripts/smoke_arc_like.sh
```

## Upload to ARC-Bench

1. Build `dist/submission.zip` using the current official Claude Code starter ZIP.
2. Run the Factory26-style smoke test.
3. Upload `dist/submission.zip` as a **Python** agent submission.
4. Validate on the Smoke Competition before spending the official hackathon budget.
5. Use the same tested submission version for the two Agentic Software Factory task packs.

ARC injects `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `MODEL`; no model API key is embedded in the repository or submission archive.

## Runtime Release

The GSC runtime is published under the `gsc-runtime-v1` Release. The submission embeds the compressed pinned asset, so contest runtime does **not** download GSC, Claude Code, Node, LSP, or the gateway from the internet.

Release metadata can be checked against `runtime.lock.json` without downloading the large payloads:

```bash
python3 scripts/verify_release_assets.py
```

The packager also rejects incomplete Factory starter archives and ZIP path traversal before fetching any large runtime asset.

## Security / IP boundary

The heavy GSC server implementation is not shipped as a source directory in the public plugin runtime; the server is delivered as a compiled Node SEA ELF. MCP/hook integration remains JavaScript by design.

Node SEA is a deployment boundary, not a claim of irreversible source-code protection.

## License

The original code in this repository is licensed under the [MIT License](./LICENSE).

Third-party components, downloaded runtime assets, and bundled upstream binaries (including Claude Code, Node.js, TypeScript tooling, and other pinned components) are **not relicensed by this repository**. They retain their respective upstream licenses and terms. The MIT license applies only to code and materials for which `putao520` has the right to grant that license.
