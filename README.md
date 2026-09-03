# Jolgue AI

Minimal Python-first AI workspace designed for constrained hosting such as Shardcloud.

## Start

```bash
python main.py
```

The server binds to `0.0.0.0` and uses port `80` by default. Set `PORT` to override it.

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

## Runtime

The app uses Python's standard library only. No Node.js, pnpm, Flask, frontend build step, or external package is required.

On first startup, `data.json` is created automatically for local persistence.

For production, prefer setting `NVIDIA_API_KEY` (or another secret mechanism) instead of storing an API key in browser-managed state.
