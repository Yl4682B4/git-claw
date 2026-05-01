import os
from tools.base import Tool


def write_file_impl(path, content, append=False):
    """Write content to a file. Creates parent directories if needed."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        return f"[error]: Path must be absolute: {path}"

    try:
        # Create parent directories if they don't exist
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        mode = 'a' if append else 'w'
        with open(path, mode, encoding='utf-8') as f:
            f.write(content)

        # Report result
        action = "appended to" if append else "written to"
        size = os.path.getsize(path)
        return f"[ok]: Content {action} {path} ({size} bytes)"
    except PermissionError:
        return f"[error]: Permission denied: {path}"
    except Exception as e:
        return f"[error]: {str(e)}"


tool = Tool(
    name="write_file",
    description="Write content to a file. Creates the file and parent directories if they don't exist. Can overwrite or append.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to write"
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file"
            },
            "append": {
                "type": "boolean",
                "description": "If true, append to the file instead of overwriting. Defaults to false."
            }
        },
        "required": ["path", "content"]
    },
    impl=write_file_impl
)
