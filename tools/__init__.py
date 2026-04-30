from tools.base import Tool
from tools.exec import tool as exec_tool

ALL_TOOLS = [exec_tool]
TOOL_MAP = {t.name: t for t in ALL_TOOLS}
