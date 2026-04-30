# 🧠 GitClaw

> Agent 在长期运行后，混乱而复杂的上下文管理让我苦恼，受 Git 的启发，我编写了这个项目。

GitClaw 是一个**类 Git 的 ReAct 推理器** —— 它将 LLM 推理会话以分支和提交的方式管理，让你对发散的思维链拥有完整的控制力。

## 核心思想

- 每次**推理**就是一次 **commit** —— 不是聊天消息，而是一个完整的推理步骤（User → 工具调用 → Assistant）。
- 对话存在于**分支**上。你可以从任意 commit **checkout** 出新分支，探索不同的推理路径。
- 上下文通过回溯 commit 历史构建，就像 `git log` 一样。

## 功能

- 🌿 **分支与提交** —— 创建分支、从任意 commit 分叉、管理并行推理链。
- 🔧 **工具调用 (ReAct)** —— Agent 循环调用工具（如 shell 执行），直到得出最终答案。
- 📡 **流式输出** —— 实时 SSE 流式传输 LLM 响应和工具调用过程。
- 🗄️ **SQLite 持久化** —— 所有分支和提交持久存储。
- 🎨 **SourceTree 风格图** —— 可视化 commit 图，展示分支分叉与历史。

## 截图

| Commit Graph | Branch View |
|:---:|:---:|
| ![Commit Graph](p1.png) | ![Branch View](p2.png) |

## 快速开始

```bash
# 安装依赖
pip install flask requests

# 启动 Web 服务
cd web
python app.py
```

然后打开 `http://localhost:8171`。

> **注意：** GitClaw 需要一个 OpenAI 兼容的 LLM API 运行在 `http://localhost:1234/v1/chat/completions`（如 LM Studio、Ollama + OpenAI adapter 等）。

## 项目结构

```
git-claw/
├── tools/            # 工具定义（ReAct 工具）
│   ├── base.py       # Tool 基类
│   └── exec.py       # Shell 执行工具
├── web/
│   ├── app.py        # Flask 后端（Agent 逻辑 + API）
│   └── templates/
│       └── index.html  # 单页前端
├── react.py          # 独立 CLI agent（无 Web）
└── README.md
```

## License

MIT
