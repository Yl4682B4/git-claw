# 🧠 GitClaw

> After long-running agent sessions, the chaotic and tangled context management drove me crazy. Inspired by Git, I built this project.

GitClaw is a **Git-like React Reasoning Agent** — it manages LLM reasoning sessions as branches and commits, giving you full control over divergent thought chains.

## Core Idea

- Each **inference** is a **commit** — not a chat message, but a complete reasoning step (User → Tool Calls → Assistant).
- Conversations live on **branches**. You can **checkout** from any commit to explore alternative reasoning paths.
- Context is built by walking the commit history, just like `git log`.

## Features

- 🌿 **Branch & Commit** — Create branches, fork from any commit, manage parallel reasoning chains.
- 🔧 **Tool Use (ReAct)** — Agent calls tools (e.g. shell execution) in a loop until it reaches a final answer.
- 📡 **Streaming Output** — Real-time SSE streaming of LLM responses and tool invocations.
- 🗄️ **SQLite Persistence** — All branches and commits are durably stored.
- 🎨 **SourceTree-style Graph** — Visual commit graph showing branch forks and history.

## Screenshots

| Commit Graph | Branch View |
|:---:|:---:|
| ![Commit Graph](p1.png) | ![Branch View](p2.png) |

## Quick Start

```bash
# Install dependencies
pip install flask requests

# Run the web server
cd web
python app.py
```

Then open `http://localhost:8171`.

> **Note:** GitClaw expects an OpenAI-compatible LLM API at `http://localhost:1234/v1/chat/completions` (e.g. LM Studio, Ollama with OpenAI adapter, etc.)

## Project Structure

```
git-claw/
├── tools/            # Tool definitions (ReAct tools)
│   ├── base.py       # Tool base class
│   └── exec.py       # Shell execution tool
├── web/
│   ├── app.py        # Flask backend (agent logic + API)
│   └── templates/
│       └── index.html  # Single-page frontend
├── react.py          # Standalone CLI agent (no web)
└── README.md
```

## License

MIT
