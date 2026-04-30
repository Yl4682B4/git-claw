import requests
import json

from tools import ALL_TOOLS, TOOL_MAP


API_URL = "http://localhost:1234/v1/chat/completions"


# ============ Agent ============

def call_llm(messages):
    payload = {
        "messages": messages,
        "tools": [t.to_openai_schema() for t in ALL_TOOLS],
        "tool_choice": "auto",
        "temperature": 0
    }
    resp = requests.post(API_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def run_agent(question, max_steps=10):
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Use the provided tools to answer questions."},
        {"role": "user", "content": question}
    ]

    for _ in range(max_steps):
        msg = call_llm(messages)
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            print(msg.get("content", ""))
            return msg.get("content", "")

        messages.append(msg)
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            tool = TOOL_MAP.get(name)
            result = tool.execute(**args) if tool else f"Unknown tool: {name}"
            print(f"🔧 {name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})

    print("⚠️ Max steps reached.")
    return None


if __name__ == "__main__":
    run_agent("Use exec tool to write a python script to print 'Hello, World' and run it.")