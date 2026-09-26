# arc-claude-gsc

ARC-Bench / Factory26 custom-agent adapter for **original Claude Code + GSC**.

The competition upload is intentionally tiny. Heavy runtime components are no longer embedded in `submission.zip`.

- `claude-agent-sdk==0.2.159` is installed by the ARC runner and supplies the original Claude Code 2.1.281 CLI.
- `main.py` executes that bundled Claude Code binary directly; it does **not** replace Claude Code with a custom SDK agent loop.
- GSC 6.8.1733 is downloaded from the pinned GitHub Release at agent startup, SHA-256 verified, and extracted under the ARC artifacts directory.
- GSC MCP/hooks remain JavaScript; `gsc-spec-server` remains the compiled Linux x86_64 Node SEA executable.
- The official ARC-Bench `template/`, `skills/`, and `arcbench-agent-runtime` are imported into the submission at build time.
- ARC's injected `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `MODEL` are mapped the same way as the official Claude Code starter. No production protocol proxy is bundled.

## Runtime topology

```text
ARC runner
  -> install requirements.txt
     -> local arcbench-agent-runtime
     -> PyYAML
     -> claude-agent-sdk 0.2.159
        -> bundled original Claude Code 2.1.281
  -> main.py requirements --output-dir output --type web
     -> copy official starter template + ARC skills
     -> download + SHA-256 verify pinned GSC runtime
     -> extract GSC under artifacts/runtime/gsc
     -> start original Claude Code CLI
        --plugin-dir <GSC runtime>
        --model <ARC MODEL>
        -> GSC MCP
           -> compiled gsc-spec-server
           -> TypeScript LSP
```

When ARC starts the submission as root, `main.py` drops only the Claude/GSC process tree to a non-root workspace user. Claude Code refuses bypass-permission mode as root.

## Factory26 runner contract

The current Agentic Software Factory runner invokes Python submissions as:

```bash
python3 main.py /path/to/requirements --output-dir /path/to/output --type web
```

`requirements.yaml` must contain an `id: ROOT` tree. The adapter processes each direct ROOT child in order in the same persistent output worktree. Before each module starts it materializes that subtree under:

```text
SPEC/arcbench/<REQ-ID>.md
```

It also maintains:

```text
.arc/runner-events.jsonl
.arc/traceability/*
git checkpoints
```

## Pinned components

See `runtime.lock.json`.

Current important pins:

- Claude Agent SDK 0.2.159
- bundled Claude Code 2.1.281 (also verified by binary SHA-256)
- GSC 6.8.1733
- TypeScript Language Server 5.1.3
- TypeScript 5.9.3
- GSC bundled Node 22.23.2

`anthropic-proxy` remains in `runtime.lock.json` only as a deterministic **test helper** for local ARC-like smoke tests. It is not included in the production submission.

## Build the slim ARC submission

Download the current official Claude Code starter ZIP, then run:

```bash
ARC_FACTORY26_STARTER_ZIP=/path/to/agent-claude-code-based.zip \
  ./scripts/package_submission.sh
```

Output:

```text
dist/submission.zip
```

The packager enforces a 50 MiB maximum by default:

```text
ARC_SUBMISSION_MAX_BYTES=52428800
```

The current slim package is roughly a few hundred KiB, not hundreds of MiB. It contains source/bootstrap assets only; it does **not** contain the GSC runtime, Claude Code binary, protocol bridge, or compressed runtime payloads.

## Runtime downloads

At execution time:

1. ARC installs `claude-agent-sdk==0.2.159`; its wheel supplies Claude Code 2.1.281.
2. `main.py` downloads the pinned zstd helper and pinned GSC runtime from `gsc-runtime-v1`.
3. Every downloaded runtime-critical file is SHA-256 verified before use.
4. GSC is cached under `ARCBENCH_ARTIFACTS_DIR/runtime` for the lifetime of the run.

This means the uploaded ZIP stays far below the competition's 50 MiB limit while the runtime remains version-pinned.

## Run the Factory26-style smoke test

Docker is required:

```bash
./scripts/smoke_arc_like.sh
```

For a repeat run against an already-built ZIP:

```bash
ARC_SKIP_PACKAGE=1 ./scripts/smoke_arc_like.sh
```

The smoke verifies:

- `submission.zip` is below the configured upload-size limit;
- no heavy `runtime/payloads`, bundled gateway, or zstd runtime accidentally enters the ZIP;
- the official ARC runtime package matches the Claude Code starter file-for-file;
- `claude-agent-sdk==0.2.159` supplies the expected Claude Code 2.1.281 binary;
- GSC downloads, verifies, extracts, and its MCP connects;
- multiple ROOT modules preserve the same worktree;
- `SPEC/arcbench/*`, runner events, traceability, and git checkpoints are produced;
- a real Claude Code tool call modifies the requested output directory.

The smoke uses `anthropic-proxy` only to emulate ARC's Anthropic-compatible endpoint in front of a deterministic local OpenAI mock. The proxy is test-only and is not packaged.

## Upload to ARC-Bench

1. Build the current slim `dist/submission.zip`.
2. Run the Factory26 smoke test.
3. Upload the ZIP as a **Python** custom agent.
4. Run Smoke Competition before spending official hackathon budget.
5. Reuse the exact Smoke-tested submission for the formal Agentic Software Factory evaluation.

ARC injects model credentials at runtime. No model API key is stored in this repository or submission archive.

## Release verification

Release metadata can be checked against `runtime.lock.json` with:

```bash
python3 scripts/verify_release_assets.py
```

The packager rejects incomplete Factory starter archives, ZIP path traversal, and packages above the configured upload-size limit.

## Security / IP boundary

The heavy GSC server implementation is not published here as a source directory. The runtime Release contains the compiled Node SEA server plus the plugin runtime required for execution. Node SEA is a deployment boundary, not a claim of irreversible source-code protection.

## License

The original code in this repository is licensed under the [MIT License](./LICENSE).

Third-party components, downloaded runtime assets, and upstream binaries (including Claude Code, Node.js, TypeScript tooling, and other pinned components) are **not relicensed by this repository**. They retain their respective upstream licenses and terms. The MIT license applies only to code and materials for which `putao520` has the right to grant that license.
