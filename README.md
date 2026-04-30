# GitClaw

> After long-running agent sessions, the chaotic and tangled context management drove me crazy. Inspired by Git, I built this project.

GitClaw is a **Git-like ReAct Reasoning Agent** — it manages LLM reasoning sessions as branches and commits, giving you full control over divergent thought chains.

## Core Idea

- Each **inference** is a **commit** — a complete reasoning step (User → Tool Calls → Assistant).
- Conversations live on **branches**. **Checkout** from any commit to explore alternative paths.
- Context is built by walking commit history, just like `git log`.

## Features

- 🌿 **Branch & Commit** — Create, delete, checkout from any commit, manage parallel reasoning chains.
- 🔀 **Merge** — Merge branches by interleaving commits chronologically.
- 🔧 **Tool Use (ReAct)** — Agent calls tools in a loop until reaching a final answer.
- ⚠️ **Dangerous Command Guard** — `rm` commands require explicit user confirmation before execution.
- 📁 **Workspace** — Built-in file browser with syntax-highlighted code viewer.
- 📡 **Streaming** — Real-time SSE streaming of LLM responses and tool invocations.
- 🗄️ **SQLite Persistence** — All branches and commits durably stored.
- 🎨 **Commit Graph** — SourceTree-style visual graph showing branch forks and history.
- 🌗 **Light/Dark Theme** — Toggle between day and night mode.

## Screenshots

| Branch View | Commit Graph | Workspace |
|:---:|:---:|:---:|
| ![Branch View](p1.png) | ![Commit Graph](p2.png) | ![Workspace](p3.png) |

## Quick Start

```bash
pip install flask requests
cd web
python app.py
```

Open `http://localhost:8171`.

> **Note:** Requires an OpenAI-compatible LLM API at `http://localhost:1234/v1/chat/completions` (e.g. LM Studio, Ollama, etc.)

## Project Structure

```
git-claw/
├── tools/              # ReAct tool definitions
│   ├── base.py
│   └── exec.py         # Shell execution (with rm guard)
├── web/
│   ├── app.py          # Flask backend (agent + API)
│   ├── templates/
│   │   └── index.html  # Single-page frontend
│   └── workspace/      # Agent's file workspace
└── react.py            # Standalone CLI agent
```

## License

MIT
