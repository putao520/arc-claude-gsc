# arc-claude-gsc

ARC-Bench / Factory26 custom-agent adapter for **original Claude Code + GSC**.

The repository is intentionally thin:

- Claude Code remains upstream/original.
- GSC MCP and hooks remain JavaScript.
- `gsc-spec-server` is distributed as a compiled Linux x86_64 Node SEA executable.
- A pinned lightweight Anthropic-to-OpenAI compatibility proxy bridges Claude Code to ARC's OpenAI-compatible model endpoint.
- The ARC adapter only owns bootstrap/lifecycle; it does not reimplement planning, coding, testing, or GSC orchestration.

## Runtime topology

```text
ARC runner
  -> /workspace/submission/main.py
  -> anthropic-proxy
  -> ARC OpenAI-compatible API

  -> Claude Code
     cwd=/workspace/template
     --plugin-dir runtime/gsc

       -> GSC MCP (JavaScript)
          -> compiled gsc-spec-server
          -> TypeScript LSP profile
```

## ARC workspace contract

The adapter expects the standard ARC layout:

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

It edits only `/workspace/template`.

## Required ARC environment

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `MODEL`
- `ARCBENCH_TEMPLATE_DIR` (defaults to `/workspace/template`)
- `ARCBENCH_PROMPT_PATH` or `ARCBENCH_TASK_PROMPT`

## Pinned components

See `runtime.lock.json`.

The runtime is published as a GitHub Release asset and copied into the final ARC submission package. The submission does not download dependencies at contest runtime.

## Local ARC-like smoke

The project includes an ARC-like Docker smoke environment using the same workspace layout and a Jammy/Playwright-style base. It verifies:

1. bundled Node 22;
2. TypeScript LSP;
3. JS MCP initialize;
4. MCP tools/list;
5. compiled GSC server autostart;
6. GSC /health;
7. a real MCP tool call.

## Security / IP boundary

The compiled GSC server is shipped as an ELF executable. The heavy server implementation is not included as source in the public runtime package. MCP/hook integration stays JavaScript by design.
