# Jolgue AI

Minimal Python-first AI workspace designed for constrained hosting such as Shardcloud.

## Start

```bash
python main.py
```

The server binds to `0.0.0.0` on port `80`.

## Workspace

The web UI is organized around reusable **Templates**:

- General
- Coding Agent
- Research
- Writer
- Debugger
- Custom

A chat is created from a template and belongs to a project. This gives the workspace a simple hierarchy:

**Templates → Projects → Chats**

Custom **Skills** can be stored and injected into the active agent prompt. Provider settings support NVIDIA's OpenAI-compatible endpoint by default, plus other OpenAI-compatible providers.

## Autonomous background agent

Chat requests are turned into server-side jobs and return a job ID immediately. The worker continues independently of the browser request.

Closing the tab, refreshing the page, or reopening the site does not cancel an active agent job. The UI reconnects to active jobs after reload and retrieves the final result when it completes.

The default runtime has no timeout for provider requests, shell commands, or web-search requests. Agent steps are unlimited by default; set `MAX_AGENT_STEPS` to a positive value to impose a limit.

## Runtime

The app uses Python's standard library only. No Node.js, pnpm, Flask, frontend build step, or external package is required.

On first startup, `data.json` is created automatically for local persistence.

For production, prefer setting `NVIDIA_API_KEY` (or another secret mechanism) instead of storing an API key in browser-managed state.
