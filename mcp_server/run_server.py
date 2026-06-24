#!/usr/bin/env python3
"""C++ 语义图谱 MCP Server 启动脚本

用于 Claude Code settings.json 中的 MCP server 命令。
设置正确的 sys.path 后启动 FastMCP server。

用法:
  CPP_GRAPH_DB=/path/to/db python3 /path/to/run_server.py
"""

import sys
from pathlib import Path

# 将 cpp_semantic_graph 包的父目录加入 sys.path
_pkg_dir = Path(__file__).resolve().parent.parent  # cpp_semantic_graph/
_parent = str(_pkg_dir.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from cpp_semantic_graph.mcp_server.server import main

if __name__ == "__main__":
    main()
