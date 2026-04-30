import subprocess
from tools.base import Tool


def exec_shell(command):
    """Execute a shell command and return its output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, stdin=subprocess.DEVNULL
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code]: {result.returncode}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "[error]: Command timed out after 30 seconds"
    except Exception as e:
        return f"[error]: {str(e)}"


tool = Tool(
    name="exec",
    description="Execute a shell command on the system and return its output",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"}
        },
        "required": ["command"]
    },
    impl=exec_shell
)
