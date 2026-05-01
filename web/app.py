import sys
import os
import json
import uuid
import time
import sqlite3
import re
import threading
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from tools import ALL_TOOLS, TOOL_MAP

app = Flask(__name__)

API_URL = "http://localhost:1234/v1/chat/completions"
WORKSPACE_ABS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
MEMORY_ABS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
SKILL_ABS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill")
SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the provided tools to answer questions.\n"
    f"You have a workspace directory at: {WORKSPACE_ABS_PATH}\n"
    "When you need to create, read, or modify files, operate within this workspace directory by default unless the user specifies otherwise.\n"
    "Prefer using the `read_file` and `write_file` tools for file operations instead of shell commands like cat/echo.\n"
    "You may have skills injected below that provide additional capabilities and knowledge."
)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gitclaw.db")

# ============ Dangerous Command Confirmation ============
# Stores pending confirmations: {confirm_id: threading.Event}
pending_confirms = {}
pending_confirm_results = {}  # {confirm_id: bool}


def is_dangerous_command(command):
    """Check if a shell command contains dangerous operations like rm."""
    # Normalize and check for rm command patterns
    cmd = command.strip()
    # Match 'rm' as a standalone command (not part of another word like 'chmod')
    if re.search(r'(?:^|[;&|]|\s)rm\s', cmd) or re.search(r'(?:^|[;&|]|\s)rm$', cmd):
        return True
    return False


# ============ SQLite Persistence ============

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS branches (
            name TEXT PRIMARY KEY,
            head_commit_id TEXT
        );
        CREATE TABLE IF NOT EXISTS commits (
            id TEXT PRIMARY KEY,
            branch TEXT NOT NULL,
            parent_id TEXT,
            timestamp REAL NOT NULL,
            messages TEXT NOT NULL
        );
    """)
    # Ensure 'main' branch exists
    existing = conn.execute("SELECT name FROM branches WHERE name = 'main'").fetchone()
    if not existing:
        conn.execute("INSERT INTO branches (name, head_commit_id) VALUES ('main', NULL)")
    conn.commit()
    conn.close()


init_db()


def db_get_branch_head(branch_name):
    conn = get_db()
    row = conn.execute("SELECT head_commit_id FROM branches WHERE name = ?", (branch_name,)).fetchone()
    conn.close()
    return row["head_commit_id"] if row else None


def db_get_commit(commit_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM commits WHERE id = ?", (commit_id,)).fetchone()
    conn.close()
    if row:
        return {
            "id": row["id"],
            "branch": row["branch"],
            "parent_id": row["parent_id"],
            "timestamp": row["timestamp"],
            "messages": json.loads(row["messages"])
        }
    return None


def db_branch_exists(branch_name):
    conn = get_db()
    row = conn.execute("SELECT name FROM branches WHERE name = ?", (branch_name,)).fetchone()
    conn.close()
    return row is not None


def db_list_branches():
    conn = get_db()
    rows = conn.execute("SELECT name, head_commit_id FROM branches").fetchall()
    conn.close()
    return [{"name": r["name"], "head_commit_id": r["head_commit_id"]} for r in rows]


def db_create_branch(branch_name, head_commit_id=None):
    conn = get_db()
    conn.execute("INSERT INTO branches (name, head_commit_id) VALUES (?, ?)", (branch_name, head_commit_id))
    conn.commit()
    conn.close()


def db_create_commit(commit_id, branch_name, parent_id, timestamp, messages):
    conn = get_db()
    conn.execute(
        "INSERT INTO commits (id, branch, parent_id, timestamp, messages) VALUES (?, ?, ?, ?, ?)",
        (commit_id, branch_name, parent_id, timestamp, json.dumps(messages, ensure_ascii=False))
    )
    conn.execute("UPDATE branches SET head_commit_id = ? WHERE name = ?", (commit_id, branch_name))
    conn.commit()
    conn.close()


def db_commit_exists(commit_id):
    conn = get_db()
    row = conn.execute("SELECT id FROM commits WHERE id = ?", (commit_id,)).fetchone()
    conn.close()
    return row is not None


# ============ Git-like Data Model ============

def get_branch_history(branch_name, max_commits=50):
    """Walk back from branch head and collect recent commits."""
    head_id = db_get_branch_head(branch_name)
    history = []
    current_id = head_id
    while current_id and len(history) < max_commits:
        commit = db_get_commit(current_id)
        if not commit:
            break
        history.append(commit)
        current_id = commit["parent_id"]
    history.reverse()
    return history


def sanitize_message(msg):
    """Ensure a message conforms to the OpenAI chat format expected by Qwen."""
    role = msg.get("role")
    if role == "assistant" and "tool_calls" in msg:
        # Ensure each tool_call has "type": "function"
        sanitized_tcs = []
        for tc in msg["tool_calls"]:
            sanitized_tcs.append({
                "id": tc.get("id", ""),
                "type": "function",
                "function": tc.get("function", {"name": "", "arguments": ""})
            })
        return {
            "role": "assistant",
            "content": msg.get("content") or None,
            "tool_calls": sanitized_tcs
        }
    elif role == "tool":
        # Ensure tool message has name field
        result = {"role": "tool", "tool_call_id": msg.get("tool_call_id", ""), "content": msg.get("content", "")}
        if "name" in msg:
            result["name"] = msg["name"]
        return result
    return msg


def load_selected_skills(skill_paths):
    """Load specific skill files by their relative paths and return combined content."""
    skill_dir = SKILL_ABS_PATH
    if not os.path.isdir(skill_dir) or not skill_paths:
        return ""
    skill_texts = []
    for rel_path in skill_paths:
        safe_path = os.path.normpath(os.path.join(skill_dir, rel_path))
        if not safe_path.startswith(os.path.normpath(skill_dir)):
            continue
        if not os.path.isfile(safe_path):
            continue
        try:
            with open(safe_path, 'r', encoding='utf-8') as f:
                content = f.read()
            skill_texts.append(f"--- skill: {rel_path} ---\n{content}")
        except Exception:
            continue
    return "\n\n".join(skill_texts)


def estimate_tokens(text):
    """Estimate token count for a string. Uses ~4 chars per token as a rough heuristic."""
    if not text:
        return 0
    return len(text) // 4 + 1


def estimate_message_tokens(msg):
    """Estimate token count for a single message (including role overhead)."""
    tokens = 4  # role/formatting overhead
    content = msg.get("content") or ""
    tokens += estimate_tokens(content)
    if "tool_calls" in msg and msg["tool_calls"]:
        for tc in msg["tool_calls"]:
            func = tc.get("function", {})
            tokens += estimate_tokens(func.get("name", ""))
            tokens += estimate_tokens(func.get("arguments", ""))
            tokens += 4  # tool_call overhead
    return tokens


# Maximum context window tokens (reserve space for new response)
MAX_CONTEXT_TOKENS = 28000  # Total budget for history messages
RESPONSE_RESERVE_TOKENS = 4000  # Reserved for the model's response


def build_messages_from_history(branch_name, max_commits=50, selected_skills=None):
    """Build the LLM message list from branch history with token-aware truncation.

    Strategy: Always include the system prompt and the most recent commits.
    If total tokens exceed MAX_CONTEXT_TOKENS, drop the oldest commits first.
    """
    history = get_branch_history(branch_name, max_commits)
    # Build system prompt with selected skills injected
    system_content = SYSTEM_PROMPT
    if selected_skills:
        skills_content = load_selected_skills(selected_skills)
        if skills_content:
            system_content += "\n\n# Skills\n\n" + skills_content
    system_msg = {"role": "system", "content": system_content}
    system_tokens = estimate_message_tokens(system_msg)

    # Calculate token budget for history (total - system - response reserve)
    history_budget = MAX_CONTEXT_TOKENS - system_tokens - RESPONSE_RESERVE_TOKENS

    # Build per-commit token costs (each commit has multiple messages)
    commit_token_costs = []
    for commit in history:
        cost = sum(estimate_message_tokens(sanitize_message(msg)) for msg in commit["messages"])
        commit_token_costs.append(cost)

    # Find the earliest commit we can include while staying within budget
    # Start from the most recent and work backwards
    total_tokens = 0
    start_index = len(history)
    for i in range(len(history) - 1, -1, -1):
        if total_tokens + commit_token_costs[i] > history_budget:
            break
        total_tokens += commit_token_costs[i]
        start_index = i

    # Build final message list
    messages = [system_msg]

    # If we truncated, add a note so the model knows context was trimmed
    if start_index > 0:
        truncation_note = {
            "role": "system",
            "content": f"[Note: {start_index} earlier conversation commit(s) were truncated due to context length limits. The conversation below starts from a later point.]"
        }
        messages.append(truncation_note)

    for commit in history[start_index:]:
        for msg in commit["messages"]:
            messages.append(sanitize_message(msg))

    return messages


def create_commit(branch_name, messages):
    """Create a new commit on the branch and persist it."""
    commit_id = str(uuid.uuid4())[:8]
    parent_id = db_get_branch_head(branch_name)
    timestamp = time.time()
    db_create_commit(commit_id, branch_name, parent_id, timestamp, messages)
    return {
        "id": commit_id,
        "branch": branch_name,
        "parent_id": parent_id,
        "timestamp": timestamp,
        "messages": messages
    }


# ============ Agent Logic (Streaming) ============

def call_llm_stream(messages):
    """Call LLM with streaming enabled, yields raw SSE lines."""
    payload = {
        "messages": messages,
        "tools": [t.to_openai_schema() for t in ALL_TOOLS],
        "tool_choice": "auto",
        "temperature": 0,
        "stream": True
    }
    resp = requests.post(API_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=300, stream=True)
    resp.raise_for_status()
    return resp


def parse_stream_response(response):
    """Parse streaming response, accumulate content and tool_calls."""
    content = ""
    tool_calls_map = {}  # index -> {id, function: {name, arguments}}

    # Force UTF-8 encoding to avoid Chinese character garbling
    response.encoding = "utf-8"

    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            delta = data.get("choices", [{}])[0].get("delta", {})

            # Content delta
            if delta.get("content"):
                content += delta["content"]
                yield {"type": "content_delta", "content": delta["content"]}

            # Tool calls delta
            if delta.get("tool_calls"):
                for tc_delta in delta["tool_calls"]:
                    idx = tc_delta["index"]
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc_delta.get("id", ""),
                            "function": {"name": "", "arguments": ""}
                        }
                    if tc_delta.get("id"):
                        tool_calls_map[idx]["id"] = tc_delta["id"]
                    func_delta = tc_delta.get("function", {})
                    if func_delta.get("name"):
                        tool_calls_map[idx]["function"]["name"] += func_delta["name"]
                    if func_delta.get("arguments"):
                        tool_calls_map[idx]["function"]["arguments"] += func_delta["arguments"]
                # Yield a keepalive so the SSE stream doesn't timeout
                yield {"type": "keepalive"}
    finally:
        response.close()

    # Yield final assembled message
    if tool_calls_map:
        tool_calls = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
        yield {"type": "message_done", "content": content, "tool_calls": tool_calls}
    else:
        yield {"type": "message_done", "content": content, "tool_calls": None}


def run_agent_stream(branch_name, question, max_steps=10, selected_skills=None):
    """Generator that yields SSE events during agent execution."""
    context_messages = build_messages_from_history(branch_name, selected_skills=selected_skills)
    user_msg = {"role": "user", "content": question}
    context_messages.append(user_msg)
    new_messages = [user_msg]

    try:
        for step in range(max_steps):
            try:
                response = call_llm_stream(context_messages)
            except Exception as e:
                error_msg = f"LLM request failed: {str(e)}"
                new_messages.append({"role": "assistant", "content": error_msg})
                yield f"data: {json.dumps({'type': 'assistant_done', 'content': error_msg}, ensure_ascii=False)}\n\n"
                break

            final_msg = None
            try:
                for event in parse_stream_response(response):
                    if event["type"] == "content_delta":
                        yield f"data: {json.dumps({'type': 'content_delta', 'content': event['content']}, ensure_ascii=False)}\n\n"
                    elif event["type"] == "keepalive":
                        # SSE comment to keep connection alive
                        yield ": keepalive\n\n"
                    elif event["type"] == "message_done":
                        final_msg = event
            except Exception as e:
                error_msg = f"Stream parsing error: {str(e)}"
                new_messages.append({"role": "assistant", "content": error_msg})
                yield f"data: {json.dumps({'type': 'assistant_done', 'content': error_msg}, ensure_ascii=False)}\n\n"
                break

            if not final_msg:
                break

            tool_calls = final_msg.get("tool_calls")
            content = final_msg.get("content", "")

            if not tool_calls:
                # Final answer
                assistant_msg = {"role": "assistant", "content": content}
                new_messages.append(assistant_msg)
                yield f"data: {json.dumps({'type': 'assistant_done', 'content': content}, ensure_ascii=False)}\n\n"
                break

            # Build the assistant message with tool_calls for context
            # Ensure each tool_call has the required "type": "function" field
            formatted_tool_calls = []
            for tc in tool_calls:
                formatted_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": tc["function"]
                })
            assistant_msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": formatted_tool_calls
            }
            new_messages.append(assistant_msg)
            context_messages.append(assistant_msg)

            # Execute each tool call
            for tc in tool_calls:
                name = tc["function"]["name"]
                args_str = tc["function"]["arguments"]
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {}

                # Emit tool_call event so frontend can display it
                yield f"data: {json.dumps({'type': 'tool_call', 'name': name, 'args': args}, ensure_ascii=False)}\n\n"

                # Check if this is a dangerous command that needs confirmation
                needs_confirm = False
                if name == "exec" and "command" in args and is_dangerous_command(args["command"]):
                    needs_confirm = True

                if needs_confirm:
                    # Send confirmation request to frontend and wait
                    confirm_id = str(uuid.uuid4())[:8]
                    confirm_event = threading.Event()
                    pending_confirms[confirm_id] = confirm_event
                    pending_confirm_results[confirm_id] = None

                    yield f"data: {json.dumps({'type': 'confirm_required', 'confirm_id': confirm_id, 'command': args.get('command', ''), 'message': f'Dangerous command detected: {args.get("command", "")}. Allow execution?'}, ensure_ascii=False)}\n\n"

                    # Wait for user confirmation (timeout 60s)
                    confirmed = confirm_event.wait(timeout=60)

                    # Clean up
                    confirm_event_ref = pending_confirms.pop(confirm_id, None)
                    user_confirmed = pending_confirm_results.pop(confirm_id, False)

                    if not confirmed or not user_confirmed:
                        # User rejected or timeout
                        result_str = "[blocked]: Command was rejected by user or timed out."
                        yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': result_str}, ensure_ascii=False)}\n\n"
                        tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result_str}
                        new_messages.append(tool_msg)
                        context_messages.append(tool_msg)
                        continue

                tool = TOOL_MAP.get(name)
                try:
                    result = tool.execute(**args) if tool else f"Unknown tool: {name}"
                except Exception as e:
                    result = f"[tool error]: {str(e)}"
                result_str = str(result)

                # Emit tool_result event
                yield f"data: {json.dumps({'type': 'tool_result', 'name': name, 'result': result_str}, ensure_ascii=False)}\n\n"

                tool_msg = {"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result_str}
                new_messages.append(tool_msg)
                context_messages.append(tool_msg)

            # Signal that we're continuing to next LLM call
            yield f"data: {json.dumps({'type': 'thinking'}, ensure_ascii=False)}\n\n"
        else:
            # Max steps reached
            new_messages.append({"role": "assistant", "content": "⚠️ Max reasoning steps reached."})
            yield f"data: {json.dumps({'type': 'assistant_done', 'content': '⚠️ Max reasoning steps reached.'}, ensure_ascii=False)}\n\n"

        # Commit
        commit = create_commit(branch_name, new_messages)
        yield f"data: {json.dumps({'type': 'commit', 'commit': commit}, ensure_ascii=False)}\n\n"
    except GeneratorExit:
        # Client disconnected, still try to save the commit
        try:
            create_commit(branch_name, new_messages)
        except Exception:
            pass
        return
    except Exception as e:
        # Catch-all: emit error to frontend
        import traceback
        traceback.print_exc()
        error_msg = f"Internal error: {str(e)}"
        yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
        # Still try to commit what we have
        try:
            if len(new_messages) > 1:
                new_messages.append({"role": "assistant", "content": error_msg})
                create_commit(branch_name, new_messages)
        except Exception:
            pass

    yield "data: [DONE]\n\n"


# ============ Routes ============

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/branches", methods=["GET"])
def list_branches_route():
    """List all branches with their head commit."""
    result = []
    for b in db_list_branches():
        history = get_branch_history(b["name"], max_commits=9999)
        result.append({
            "name": b["name"],
            "head_id": b["head_commit_id"],
            "commit_count": len(history),
            "last_time": history[-1]["timestamp"] if history else None
        })
    return jsonify(result)


@app.route("/api/branch/<branch_name>/history", methods=["GET"])
def branch_history(branch_name):
    """Get commit history for a branch."""
    if not db_branch_exists(branch_name):
        return jsonify({"error": "Branch not found"}), 404
    max_commits = request.args.get("limit", 50, type=int)
    history = get_branch_history(branch_name, max_commits)
    return jsonify(history)


@app.route("/api/checkout", methods=["POST"])
def checkout():
    """Create a new branch from any commit (or from scratch)."""
    data = request.json
    new_branch = data.get("branch_name")
    from_commit_id = data.get("from_commit_id")

    if not new_branch:
        return jsonify({"error": "branch_name is required"}), 400
    if db_branch_exists(new_branch):
        return jsonify({"error": f"Branch '{new_branch}' already exists"}), 400
    if from_commit_id and not db_commit_exists(from_commit_id):
        return jsonify({"error": "Commit not found"}), 404

    db_create_branch(new_branch, from_commit_id)
    return jsonify({"ok": True, "branch": new_branch, "head": from_commit_id})


@app.route("/api/commit/<commit_id>", methods=["DELETE"])
def delete_commit(commit_id):
    """Delete a commit. Re-link children to the deleted commit's parent.
    If it is the branch head, update head to its parent."""
    conn = get_db()
    row = conn.execute("SELECT * FROM commits WHERE id = ?", (commit_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Commit not found"}), 404

    parent_id = row["parent_id"]
    branch = row["branch"]

    # Re-link any child commits that point to this commit
    conn.execute("UPDATE commits SET parent_id = ? WHERE parent_id = ?", (parent_id, commit_id))

    # If this commit is the head of its branch, move head back to parent
    head_row = conn.execute("SELECT head_commit_id FROM branches WHERE name = ?", (branch,)).fetchone()
    if head_row and head_row["head_commit_id"] == commit_id:
        conn.execute("UPDATE branches SET head_commit_id = ? WHERE name = ?", (parent_id, branch))

    # Also check other branches that may have been checked out from this commit
    # (their head_commit_id == commit_id). Re-point them to parent.
    conn.execute("UPDATE branches SET head_commit_id = ? WHERE head_commit_id = ? AND name != ?",
                 (parent_id, commit_id, branch))

    # Delete the commit
    conn.execute("DELETE FROM commits WHERE id = ?", (commit_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": commit_id})


@app.route("/api/branch/<branch_name>", methods=["DELETE"])
def delete_branch(branch_name):
    """Delete a branch and all its commits. Cannot delete 'main'."""
    if branch_name == "main":
        return jsonify({"error": "Cannot delete the 'main' branch"}), 400
    if not db_branch_exists(branch_name):
        return jsonify({"error": "Branch not found"}), 404

    conn = get_db()

    # Before deleting commits, re-link any commits on OTHER branches
    # whose parent_id points to a commit on this branch.
    # Find all commit ids on this branch
    branch_commit_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM commits WHERE branch = ?", (branch_name,)
    ).fetchall()]

    if branch_commit_ids:
        # For each commit on this branch, find its parent (which may be on another branch)
        # Walk back to find the fork point (the parent_id of the first commit on this branch)
        first_commit = None
        head_row = conn.execute("SELECT head_commit_id FROM branches WHERE name = ?", (branch_name,)).fetchone()
        current_id = head_row["head_commit_id"] if head_row else None
        while current_id:
            c = conn.execute("SELECT * FROM commits WHERE id = ?", (current_id,)).fetchone()
            if not c or c["branch"] != branch_name:
                break
            first_commit = c
            current_id = c["parent_id"]
        fork_parent = first_commit["parent_id"] if first_commit else None

        # Re-link other branches' commits that point to any commit on this branch
        placeholders = ",".join("?" for _ in branch_commit_ids)
        conn.execute(
            f"UPDATE commits SET parent_id = ? WHERE parent_id IN ({placeholders}) AND branch != ?",
            [fork_parent] + branch_commit_ids + [branch_name]
        )
        # Re-link other branches whose head points to a commit on this branch
        conn.execute(
            f"UPDATE branches SET head_commit_id = ? WHERE head_commit_id IN ({placeholders}) AND name != ?",
            [fork_parent] + branch_commit_ids + [branch_name]
        )

    # Delete all commits on this branch
    conn.execute("DELETE FROM commits WHERE branch = ?", (branch_name,))
    # Delete the branch
    conn.execute("DELETE FROM branches WHERE name = ?", (branch_name,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": branch_name})


@app.route("/api/merge", methods=["POST"])
def merge_branch():
    """Merge source branch into target branch by interleaving commits by timestamp.

    This copies commits from the source branch and interleaves them with the
    target branch's commits in chronological order, forming a single linear history.
    """
    data = request.json
    target_branch = data.get("target_branch")  # current branch (merge INTO)
    source_branch = data.get("source_branch")  # branch to merge FROM

    if not target_branch or not db_branch_exists(target_branch):
        return jsonify({"error": "Invalid target branch"}), 400
    if not source_branch or not db_branch_exists(source_branch):
        return jsonify({"error": "Invalid source branch"}), 400
    if target_branch == source_branch:
        return jsonify({"error": "Cannot merge a branch into itself"}), 400

    # Get full history of both branches (oldest first)
    target_history = get_branch_history(target_branch, max_commits=9999)
    source_history = get_branch_history(source_branch, max_commits=9999)

    if not source_history:
        return jsonify({"error": "Source branch has no commits to merge"}), 400

    # Collect IDs already on target to avoid duplicating shared ancestors
    target_ids = set(c["id"] for c in target_history)

    # Filter source commits: only those NOT already in target's history
    source_only = [c for c in source_history if c["id"] not in target_ids]

    if not source_only:
        return jsonify({"error": "Nothing to merge — all commits already present"}), 400

    # Merge by timestamp: interleave target_history and source_only
    merged = []
    i, j = 0, 0
    while i < len(target_history) and j < len(source_only):
        if target_history[i]["timestamp"] <= source_only[j]["timestamp"]:
            merged.append(("keep", target_history[i]))
            i += 1
        else:
            merged.append(("copy", source_only[j]))
            j += 1
    while i < len(target_history):
        merged.append(("keep", target_history[i]))
        i += 1
    while j < len(source_only):
        merged.append(("copy", source_only[j]))
        j += 1

    # Now rebuild the chain on the target branch.
    # "keep" commits stay as-is but may need parent_id updated.
    # "copy" commits get new IDs and are inserted into the target branch.
    conn = get_db()

    prev_id = None  # track the previous commit in the new chain
    new_commit_ids = []  # track newly created commit IDs

    for action, commit in merged:
        if action == "keep":
            # Update parent_id if it changed
            if commit["parent_id"] != prev_id:
                conn.execute("UPDATE commits SET parent_id = ? WHERE id = ?", (prev_id, commit["id"]))
            prev_id = commit["id"]
        else:
            # Copy: create a new commit on the target branch
            new_id = str(uuid.uuid4())[:8]
            conn.execute(
                "INSERT INTO commits (id, branch, parent_id, timestamp, messages) VALUES (?, ?, ?, ?, ?)",
                (new_id, target_branch, prev_id, commit["timestamp"],
                 json.dumps(commit["messages"], ensure_ascii=False))
            )
            new_commit_ids.append(new_id)
            prev_id = new_id

    # Update target branch head to the last commit in the merged chain
    conn.execute("UPDATE branches SET head_commit_id = ? WHERE name = ?", (prev_id, target_branch))
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "target_branch": target_branch,
        "source_branch": source_branch,
        "merged_commits": len(new_commit_ids),
        "new_head": prev_id
    })


@app.route("/api/confirm", methods=["POST"])
def confirm_action():
    """Receive user confirmation for dangerous commands."""
    data = request.json
    confirm_id = data.get("confirm_id")
    confirmed = data.get("confirmed", False)

    if not confirm_id or confirm_id not in pending_confirms:
        return jsonify({"error": "Invalid or expired confirm_id"}), 400

    pending_confirm_results[confirm_id] = confirmed
    pending_confirms[confirm_id].set()  # Wake up the waiting thread
    return jsonify({"ok": True})


@app.route("/api/infer_stream", methods=["POST"])
def infer_stream():
    """Run inference with streaming SSE output."""
    data = request.json
    branch_name = data.get("branch")
    question = data.get("question")
    max_steps = data.get("max_steps", 10)
    selected_skills = data.get("skills", [])

    if not branch_name or not db_branch_exists(branch_name):
        return jsonify({"error": "Invalid branch"}), 400
    if not question:
        return jsonify({"error": "question is required"}), 400

    def generate():
        yield from run_agent_stream(branch_name, question, max_steps, selected_skills=selected_skills)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/api/graph", methods=["GET"])
def graph():
    """Return the full commit graph for visualization."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM commits").fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "branch": r["branch"],
            "parent_id": r["parent_id"],
            "timestamp": r["timestamp"],
            "messages": json.loads(r["messages"])
        })
    return jsonify(result)


@app.route("/api/branch_tree", methods=["GET"])
def branch_tree():
    """Return the branch tree structure showing fork relationships.

    Each branch knows its first commit's parent_id. If that parent belongs
    to another branch, it means this branch was forked from that commit.
    Returns a tree: [{name, commit_count, fork_from_branch, fork_from_commit, children: [...]}]
    """
    conn = get_db()
    branches_rows = conn.execute("SELECT name, head_commit_id FROM branches").fetchall()
    commits_rows = conn.execute("SELECT id, branch, parent_id, timestamp FROM commits").fetchall()
    conn.close()

    # Build commit lookup
    commit_map = {}
    for r in commits_rows:
        commit_map[r["id"]] = {
            "id": r["id"],
            "branch": r["branch"],
            "parent_id": r["parent_id"],
            "timestamp": r["timestamp"]
        }

    # For each branch, find its root commit (the first commit on this branch)
    # by walking back from head until we find a commit not on this branch or reach None
    branch_info = {}
    for br in branches_rows:
        name = br["name"]
        head_id = br["head_commit_id"]

        # Walk back to find the first commit of this branch
        chain = []
        current_id = head_id
        while current_id:
            c = commit_map.get(current_id)
            if not c or c["branch"] != name:
                break
            chain.append(c)
            current_id = c["parent_id"]

        first_commit = chain[-1] if chain else None
        fork_parent_id = first_commit["parent_id"] if first_commit else None

        # Determine which branch/commit this was forked from
        fork_from_branch = None
        fork_from_commit = None
        if fork_parent_id and fork_parent_id in commit_map:
            fork_from_commit = fork_parent_id
            fork_from_branch = commit_map[fork_parent_id]["branch"]

        branch_info[name] = {
            "name": name,
            "commit_count": len(chain),
            "fork_from_branch": fork_from_branch,
            "fork_from_commit": fork_from_commit,
            "children": []
        }

    # Build tree: find root branches (no fork parent or fork parent branch doesn't exist)
    roots = []
    for name, info in branch_info.items():
        parent_branch = info["fork_from_branch"]
        if parent_branch and parent_branch in branch_info:
            branch_info[parent_branch]["children"].append(info)
        else:
            roots.append(info)

    return jsonify(roots)


WORKSPACE_DIR = WORKSPACE_ABS_PATH


@app.route("/api/workspace/tree", methods=["GET"])
def workspace_tree():
    """Return the file tree structure of the workspace directory."""
    if not os.path.isdir(WORKSPACE_DIR):
        os.makedirs(WORKSPACE_DIR, exist_ok=True)

    def build_tree(dir_path, rel_prefix=""):
        entries = []
        try:
            items = sorted(os.listdir(dir_path), key=lambda x: (not os.path.isdir(os.path.join(dir_path, x)), x.lower()))
        except PermissionError:
            return entries
        for item in items:
            if item.startswith('.'):
                continue
            full_path = os.path.join(dir_path, item)
            rel_path = os.path.join(rel_prefix, item) if rel_prefix else item
            if os.path.isdir(full_path):
                children = build_tree(full_path, rel_path)
                entries.append({"name": item, "path": rel_path, "type": "dir", "children": children})
            else:
                size = os.path.getsize(full_path)
                entries.append({"name": item, "path": rel_path, "type": "file", "size": size})
        return entries

    tree = build_tree(WORKSPACE_DIR)
    return jsonify(tree)


@app.route("/api/workspace/file", methods=["GET"])
def workspace_file():
    """Read a file from the workspace directory."""
    rel_path = request.args.get("path", "")
    if not rel_path:
        return jsonify({"error": "path is required"}), 400

    # Prevent directory traversal
    safe_path = os.path.normpath(os.path.join(WORKSPACE_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.isfile(safe_path):
        return jsonify({"error": "File not found"}), 404

    # Determine if file is likely binary
    try:
        with open(safe_path, 'r', encoding='utf-8') as f:
            content = f.read(1024 * 512)  # Max 512KB
    except (UnicodeDecodeError, ValueError):
        return jsonify({"error": "Binary file cannot be displayed"}), 400

    # Get file extension for syntax highlighting hint
    _, ext = os.path.splitext(rel_path)
    return jsonify({"path": rel_path, "content": content, "extension": ext.lstrip('.')})


# ============ Memory Routes ============

MEMORY_DIR = MEMORY_ABS_PATH


@app.route("/api/memory/tree", methods=["GET"])
def memory_tree():
    """Return the file tree structure of the memory directory."""
    if not os.path.isdir(MEMORY_DIR):
        os.makedirs(MEMORY_DIR, exist_ok=True)

    def build_tree(dir_path, rel_prefix=""):
        entries = []
        try:
            items = sorted(os.listdir(dir_path), key=lambda x: (not os.path.isdir(os.path.join(dir_path, x)), x.lower()))
        except PermissionError:
            return entries
        for item in items:
            if item.startswith('.'):
                continue
            full_path = os.path.join(dir_path, item)
            rel_path = os.path.join(rel_prefix, item) if rel_prefix else item
            if os.path.isdir(full_path):
                children = build_tree(full_path, rel_path)
                entries.append({"name": item, "path": rel_path, "type": "dir", "children": children})
            else:
                size = os.path.getsize(full_path)
                entries.append({"name": item, "path": rel_path, "type": "file", "size": size})
        return entries

    tree = build_tree(MEMORY_DIR)
    return jsonify(tree)


@app.route("/api/memory/file", methods=["GET"])
def memory_file():
    """Read a file from the memory directory."""
    rel_path = request.args.get("path", "")
    if not rel_path:
        return jsonify({"error": "path is required"}), 400

    safe_path = os.path.normpath(os.path.join(MEMORY_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(MEMORY_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.isfile(safe_path):
        return jsonify({"error": "File not found"}), 404

    try:
        with open(safe_path, 'r', encoding='utf-8') as f:
            content = f.read(1024 * 512)
    except (UnicodeDecodeError, ValueError):
        return jsonify({"error": "Binary file cannot be displayed"}), 400

    _, ext = os.path.splitext(rel_path)
    return jsonify({"path": rel_path, "content": content, "extension": ext.lstrip('.')})


@app.route("/api/memory/import", methods=["POST"])
def memory_import():
    """Import a memory file. Creates the file at the specified path with given content.
    If generate_brief is false, only writes the file without brief generation."""
    data = request.json
    rel_path = data.get("path", "").strip()
    content = data.get("content", "")
    skip_brief = data.get("skip_brief", False)

    if not rel_path:
        return jsonify({"error": "path is required"}), 400

    # Prevent directory traversal
    safe_path = os.path.normpath(os.path.join(MEMORY_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(MEMORY_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    # Create parent directories if needed
    parent_dir = os.path.dirname(safe_path)
    os.makedirs(parent_dir, exist_ok=True)

    # Write the memory file
    with open(safe_path, 'w', encoding='utf-8') as f:
        f.write(content)

    if skip_brief:
        return jsonify({"ok": True, "path": rel_path, "step": "written"})

    # Trigger brief generation for the directory chain
    try:
        generate_briefs_upward(parent_dir)
    except Exception as e:
        # Brief generation is best-effort; don't fail the import
        print(f"Brief generation error: {e}")

    return jsonify({"ok": True, "path": rel_path, "step": "complete"})


def generate_briefs_upward(start_dir):
    """Generate brief.md for start_dir and recursively for each parent up to MEMORY_DIR."""
    current = os.path.normpath(start_dir)
    memory_root = os.path.normpath(MEMORY_DIR)

    while current.startswith(memory_root) and len(current) >= len(memory_root):
        generate_brief_for_dir(current)
        if current == memory_root:
            break
        current = os.path.dirname(current)


def generate_brief_for_dir(dir_path):
    """Generate a brief.md for a single directory by summarizing its contents with LLM."""
    if not os.path.isdir(dir_path):
        return

    # Collect content to summarize
    parts = []

    try:
        items = sorted(os.listdir(dir_path))
    except PermissionError:
        return

    for item in items:
        if item == 'brief.md':
            continue
        full_path = os.path.join(dir_path, item)

        if os.path.isdir(full_path):
            # Use sub-directory's brief.md if available
            sub_brief = os.path.join(full_path, 'brief.md')
            if os.path.isfile(sub_brief):
                try:
                    with open(sub_brief, 'r', encoding='utf-8') as f:
                        brief_content = f.read(4096)
                    parts.append(f"[Directory: {item}]\n{brief_content}")
                except Exception:
                    parts.append(f"[Directory: {item}] (brief unavailable)")
            else:
                parts.append(f"[Directory: {item}] (no brief)")
        elif os.path.isfile(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read(4096)
                parts.append(f"[File: {item}]\n{file_content}")
            except Exception:
                parts.append(f"[File: {item}] (unreadable)")

    if not parts:
        return

    combined = "\n\n---\n\n".join(parts)

    # Call LLM to generate brief
    prompt = (
        "You are a concise summarizer. Below are the contents of a directory (files and sub-directory briefs). "
        "Generate a brief summary (in a brief.md format) that captures the key information and purpose of this directory's contents. "
        "Keep it concise but informative. Output only the summary, no extra explanation.\n\n"
        f"Directory contents:\n\n{combined}"
    )

    try:
        resp = requests.post(API_URL, json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False
        }, headers={"Content-Type": "application/json"}, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        brief_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        brief_text = f"(Auto-summary failed: {e})"

    # Write brief.md
    brief_path = os.path.join(dir_path, 'brief.md')
    with open(brief_path, 'w', encoding='utf-8') as f:
        f.write(brief_text)


@app.route("/api/memory/delete", methods=["POST"])
def memory_delete():
    """Delete a memory file or directory."""
    data = request.json
    rel_path = data.get("path", "").strip()

    if not rel_path:
        return jsonify({"error": "Path is required"}), 400

    safe_path = os.path.normpath(os.path.join(MEMORY_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(MEMORY_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.exists(safe_path):
        return jsonify({"error": "Path not found"}), 404

    import shutil
    try:
        if os.path.isdir(safe_path):
            shutil.rmtree(safe_path)
        else:
            os.remove(safe_path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/memory/rebuild_brief", methods=["POST"])
def memory_rebuild_brief():
    """Manually trigger brief rebuild for a specific directory path."""
    data = request.json
    rel_path = data.get("path", "").strip()

    if rel_path:
        safe_path = os.path.normpath(os.path.join(MEMORY_DIR, rel_path))
        if not safe_path.startswith(os.path.normpath(MEMORY_DIR)):
            return jsonify({"error": "Invalid path"}), 403
    else:
        safe_path = MEMORY_DIR

    if not os.path.isdir(safe_path):
        return jsonify({"error": "Directory not found"}), 404

    try:
        generate_briefs_upward(safe_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/api/commit/<commit_id>/compress", methods=["POST"])
def compress_commit(commit_id):
    """Analyze a commit's messages and suggest deletions/simplifications using LLM."""
    commit = db_get_commit(commit_id)
    if not commit:
        return jsonify({"error": "Commit not found"}), 404

    messages = commit["messages"]
    if not messages:
        return jsonify({"error": "Commit has no messages"}), 400

    # Build a prompt for the LLM to analyze each message
    msg_descriptions = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls")

        desc = f"[Message {i}] role={role}"
        if tool_calls:
            names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            desc += f", tool_calls=[{', '.join(names)}]"
            if content:
                desc += f"\ncontent: {content[:500]}"
            for tc in tool_calls:
                args = tc.get("function", {}).get("arguments", "")
                desc += f"\ntool_call args ({tc.get('function', {}).get('name', '?')}): {args[:300]}"
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            desc += f", tool_name={tool_name}\ncontent: {content[:500]}"
        else:
            desc += f"\ncontent: {content[:500]}"
            if len(content) > 500:
                desc += "... (truncated)"

        msg_descriptions.append(desc)

    analysis_prompt = (
        "You are a conversation compressor. Analyze the following messages from a conversation commit "
        "and for each message, decide:\n"
        "1. Should it be KEPT as-is, DELETED (not worth keeping), or MODIFIED (can be simplified)?\n"
        "2. If MODIFIED, provide the simplified version of the content.\n\n"
        "Guidelines:\n"
        "- User messages should generally be KEPT (they provide context).\n"
        "- Tool call results (role=tool) that are very long or contain raw data dumps can often be DELETED or MODIFIED to a brief summary.\n"
        "- Assistant messages with tool_calls can be KEPT if they show reasoning, or MODIFIED to just note which tools were called.\n"
        "- Final assistant responses should generally be KEPT.\n"
        "- Intermediate thinking/reasoning that's redundant with the final answer can be DELETED or MODIFIED.\n\n"
        "Respond in JSON format as an array of objects, one per message:\n"
        '[\n  {"index": 0, "action": "keep"},\n'
        '  {"index": 1, "action": "delete", "reason": "..."},\n'
        '  {"index": 2, "action": "modify", "reason": "...", "new_content": "..."}\n]\n\n'
        "Messages to analyze:\n\n" + "\n\n".join(msg_descriptions)
    )

    try:
        resp = requests.post(API_URL, json={
            "messages": [{"role": "user", "content": analysis_prompt}],
            "temperature": 0,
            "stream": False
        }, headers={"Content-Type": "application/json"}, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        llm_response = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        return jsonify({"error": f"LLM request failed: {str(e)}"}), 500

    # Parse the JSON response from LLM
    try:
        # Try to extract JSON from the response (LLM might wrap it in markdown)
        json_str = llm_response
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]
        suggestions = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        return jsonify({"error": "Failed to parse LLM suggestions", "raw": llm_response}), 500

    # Enrich suggestions with original message info for frontend display
    enriched = []
    for s in suggestions:
        idx = s.get("index", -1)
        if idx < 0 or idx >= len(messages):
            continue
        msg = messages[idx]
        entry = {
            "index": idx,
            "action": s.get("action", "keep"),
            "reason": s.get("reason", ""),
            "new_content": s.get("new_content", ""),
            "original_role": msg.get("role", "unknown"),
            "original_content_preview": (msg.get("content") or "")[:200],
            "has_tool_calls": bool(msg.get("tool_calls")),
        }
        if msg.get("role") == "tool":
            entry["tool_name"] = msg.get("name", "")
        if msg.get("tool_calls"):
            entry["tool_call_names"] = [tc.get("function", {}).get("name", "") for tc in msg["tool_calls"]]
        enriched.append(entry)

    return jsonify({"commit_id": commit_id, "suggestions": enriched})


@app.route("/api/commit/<commit_id>/apply_compress", methods=["POST"])
def apply_compress(commit_id):
    """Apply compression actions to a commit's messages.
    Expects: {"actions": [{"index": 0, "action": "keep|delete|modify", "new_content": "..."}]}
    """
    commit = db_get_commit(commit_id)
    if not commit:
        return jsonify({"error": "Commit not found"}), 404

    data = request.json
    actions = data.get("actions", [])
    if not actions:
        return jsonify({"error": "No actions provided"}), 400

    messages = commit["messages"]

    # Build a set of indices to delete and a map of modifications
    delete_indices = set()
    modify_map = {}  # index -> new_content

    for a in actions:
        idx = a.get("index", -1)
        if idx < 0 or idx >= len(messages):
            continue
        action = a.get("action")
        if action == "delete":
            delete_indices.add(idx)
        elif action == "modify":
            modify_map[idx] = a.get("new_content", "")

    # Apply modifications
    new_messages = []
    for i, msg in enumerate(messages):
        if i in delete_indices:
            # If deleting an assistant message with tool_calls, also delete corresponding tool results
            if msg.get("tool_calls"):
                tool_call_ids = {tc.get("id") for tc in msg["tool_calls"]}
                # Mark subsequent tool messages for deletion too
                for j in range(i + 1, len(messages)):
                    if messages[j].get("role") == "tool" and messages[j].get("tool_call_id") in tool_call_ids:
                        delete_indices.add(j)
            continue
        if i in modify_map:
            modified_msg = dict(msg)
            modified_msg["content"] = modify_map[i]
            # If modifying a tool_calls message, remove tool_calls to simplify
            if "tool_calls" in modified_msg and modify_map[i]:
                del modified_msg["tool_calls"]
            new_messages.append(modified_msg)
        else:
            new_messages.append(msg)

    # Update the commit in the database
    conn = get_db()
    conn.execute(
        "UPDATE commits SET messages = ? WHERE id = ?",
        (json.dumps(new_messages, ensure_ascii=False), commit_id)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "commit_id": commit_id,
        "original_count": len(messages),
        "new_count": len(new_messages),
        "deleted": len(delete_indices),
        "modified": len(modify_map)
    })


# ============ Skill Routes ============

SKILL_DIR = SKILL_ABS_PATH


@app.route("/api/skill/tree", methods=["GET"])
def skill_tree():
    """Return the file tree structure of the skill directory."""
    if not os.path.isdir(SKILL_DIR):
        os.makedirs(SKILL_DIR, exist_ok=True)

    def build_tree(dir_path, rel_prefix=""):
        entries = []
        try:
            items = sorted(os.listdir(dir_path), key=lambda x: (not os.path.isdir(os.path.join(dir_path, x)), x.lower()))
        except PermissionError:
            return entries
        for item in items:
            if item.startswith('.'):
                continue
            full_path = os.path.join(dir_path, item)
            rel_path = os.path.join(rel_prefix, item) if rel_prefix else item
            if os.path.isdir(full_path):
                children = build_tree(full_path, rel_path)
                entries.append({"name": item, "path": rel_path, "type": "dir", "children": children})
            else:
                size = os.path.getsize(full_path)
                entries.append({"name": item, "path": rel_path, "type": "file", "size": size})
        return entries

    tree = build_tree(SKILL_DIR)
    return jsonify(tree)


@app.route("/api/skill/list", methods=["GET"])
def skill_list():
    """Return a flat list of all skill file paths (for checkbox selection)."""
    if not os.path.isdir(SKILL_DIR):
        return jsonify([])
    result = []
    for root, dirs, files in os.walk(SKILL_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in sorted(files):
            if fname.startswith('.') or not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, SKILL_DIR)
            result.append(rel)
    return jsonify(result)


@app.route("/api/skill/file", methods=["GET"])
def skill_file():
    """Read a file from the skill directory."""
    rel_path = request.args.get("path", "")
    if not rel_path:
        return jsonify({"error": "path is required"}), 400

    safe_path = os.path.normpath(os.path.join(SKILL_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(SKILL_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.isfile(safe_path):
        return jsonify({"error": "File not found"}), 404

    try:
        with open(safe_path, 'r', encoding='utf-8') as f:
            content = f.read(1024 * 512)
    except (UnicodeDecodeError, ValueError):
        return jsonify({"error": "Binary file cannot be displayed"}), 400

    _, ext = os.path.splitext(rel_path)
    return jsonify({"path": rel_path, "content": content, "extension": ext.lstrip('.')})


@app.route("/api/skill/import", methods=["POST"])
def skill_import():
    """Import a skill file. Creates the .md file at the specified path."""
    data = request.json
    rel_path = data.get("path", "").strip()
    content = data.get("content", "")

    if not rel_path:
        return jsonify({"error": "path is required"}), 400

    # Ensure .md extension
    if not rel_path.endswith('.md'):
        rel_path += '.md'

    # Prevent directory traversal
    safe_path = os.path.normpath(os.path.join(SKILL_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(SKILL_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    # Create parent directories if needed
    parent_dir = os.path.dirname(safe_path)
    os.makedirs(parent_dir, exist_ok=True)

    # Write the skill file
    with open(safe_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return jsonify({"ok": True, "path": rel_path})


@app.route("/api/skill/delete", methods=["POST"])
def skill_delete():
    """Delete a skill file or directory."""
    data = request.json
    rel_path = data.get("path", "").strip()

    if not rel_path:
        return jsonify({"error": "Path is required"}), 400

    safe_path = os.path.normpath(os.path.join(SKILL_DIR, rel_path))
    if not safe_path.startswith(os.path.normpath(SKILL_DIR)):
        return jsonify({"error": "Invalid path"}), 403

    if not os.path.exists(safe_path):
        return jsonify({"error": "Path not found"}), 404

    import shutil
    try:
        if os.path.isdir(safe_path):
            shutil.rmtree(safe_path)
        else:
            os.remove(safe_path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=8171, use_reloader=False, threaded=True)
