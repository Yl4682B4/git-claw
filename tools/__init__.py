from tools.base import Tool
from tools.exec import tool as exec_tool
from tools.read_file import tool as read_file_tool
from tools.write_file import tool as write_file_tool

ALL_TOOLS = [exec_tool, read_file_tool, write_file_tool]
TOOL_MAP = {t.name: t for t in ALL_TOOLS}
