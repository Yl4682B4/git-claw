import os
from tools.base import Tool


def read_file_impl(path, offset=None, limit=None):
    """Read a file and return its content. Supports optional line offset and limit."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        return f"[error]: Path must be absolute: {path}"
    if not os.path.exists(path):
        return f"[error]: File not found: {path}"
    if not os.path.isfile(path):
        return f"[error]: Not a file: {path}"

    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"[error]: Cannot read binary file: {path}"
    except PermissionError:
        return f"[error]: Permission denied: {path}"
    except Exception as e:
        return f"[error]: {str(e)}"

    total_lines = len(lines)

    # Apply offset and limit
    start = 0
    if offset is not None:
        start = max(0, int(offset) - 1)  # 1-based to 0-based

    end = total_lines
    if limit is not None:
        end = min(total_lines, start + int(limit))

    selected = lines[start:end]

    # Add line numbers
    numbered_lines = []
    for i, line in enumerate(selected, start=start + 1):
        numbered_lines.append(f"{i:>4} | {line.rstrip()}")

    header = f"[{path}] (lines {start+1}-{end} of {total_lines})"
    content = "\n".join(numbered_lines)

    # Truncate if too large (max 100KB output)
    if len(content) > 100_000:
        content = content[:100_000] + "\n... [truncated, file too large]"

    return f"{header}\n{content}"


tool = Tool(
    name="read_file",
    description="Read a file and return its content with line numbers. Supports reading specific line ranges for large files.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read"
            },
            "offset": {
                "type": "integer",
                "description": "Starting line number (1-based). Optional, defaults to beginning of file."
            },
            "limit": {
                "type": "integer",
                "description": "Number of lines to read from offset. Optional, defaults to entire file."
            }
        },
        "required": ["path"]
    },
    impl=read_file_impl
)
