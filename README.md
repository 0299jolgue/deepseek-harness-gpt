# Jolgue AI

A small Python AI workspace designed for constrained hosting such as Shardcloud.

## Start

```bash
python main.py
```

The app binds to `0.0.0.0` and uses `PORT` when provided, otherwise port `80`.

## Provider

The web UI accepts any OpenAI-compatible API endpoint. For NVIDIA NIM / NVIDIA API compatible deployments, configure:

- Base URL: `https://integrate.api.nvidia.com/v1`
- API key: your NVIDIA API key
- Model: your chosen NVIDIA-hosted model

Configuration is saved locally in `data.json` after first use.

## Features

- Projects represented by independent chats
- Persistent local chat history
- Custom provider URL/model/API key
- Custom skills stored as instructions
- Browser UI with no Node.js build step
- One-file Python server with no frontend build toolchain

For production, put the API key in an environment variable or secret manager rather than storing it in browser-sent state.
