#!/usr/bin/env python3
"""Read-only MCP adapter around the user's existing cppsg query implementation.

No copied index, automatic increment, schema migration, or original-source changes.
The Codex config separately selects these tools; this adapter enforces the same list.
"""
import os
import importlib.util
from pathlib import Path
import sqlite3
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = SOURCE_ROOT / 'semantic_graph_full.db'
ALLOWED = {
    'cpp_symbol_lookup', 'cpp_relationship', 'cpp_traverse_graph',
    'cpp_get_file_symbols', 'cpp_get_include_impact', 'cpp_get_inheritance',
}


def readonly_connection(database, *args, **kwargs):
    """Force persistent database opens through SQLite's actual read-only mode."""
    if args:
        raise ValueError('Adapter requires keyword-only SQLite connection options')
    path = Path(database).resolve(strict=True)
    kwargs['uri'] = True
    conn = ORIGINAL_CONNECT(path.as_uri() + '?mode=ro', **kwargs)
    conn.execute('PRAGMA query_only=ON')
    return conn


ORIGINAL_CONNECT = sqlite3.connect


def readonly_graph_init(self, db_path):
    """Bypass GraphDB's write-oriented CREATE/migrate/version initialization."""
    self.db_path = Path(db_path).resolve(strict=True)
    self.conn = readonly_connection(self.db_path)
    self.conn.row_factory = sqlite3.Row
    self._autocommit = True
    version = self.conn.execute('PRAGMA user_version').fetchone()[0]
    if version != 5:
        self.conn.close()
        raise RuntimeError(f'Review required: expected existing cppsg schema v5, got {version}')


def configure():
    # Load this checkout even when its directory uses the GitHub hyphenated name.
    if 'cpp_semantic_graph' not in sys.modules:
        spec = importlib.util.spec_from_file_location('cpp_semantic_graph', SOURCE_ROOT / '__init__.py', submodule_search_locations=[str(SOURCE_ROOT)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
    from cpp_semantic_graph.db.graph_db import GraphDB
    from cpp_semantic_graph.mcp_server import server

    # All persistent SQLite opens in this process, including include queries, are RO.
    sqlite3.connect = readonly_connection
    GraphDB.__init__ = readonly_graph_init
    server._ensure_fresh = lambda: None
    server._db_path = str(Path(os.environ.get('CPP_GRAPH_DB', str(DEFAULT_DB))).resolve(strict=True))
    server._PROJECT_NAME = os.environ.get('CPP_GRAPH_PROJECT', 'existing index') + ' (read-only)'

    telemetry = Path(os.environ.get('CPP_GRAPH_TELEMETRY',
                                    str(Path.home() / '.codex/log/cppsg-readonly.jsonl')))
    telemetry.parent.mkdir(parents=True, exist_ok=True)
    server._telemetry_path = lambda: telemetry
    # Register only reviewed query tools. No build/impact-analysis or arbitrary SQL.
    for tool in list(server.mcp._tool_manager.list_tools()):
        if tool.name not in ALLOWED:
            server.mcp.remove_tool(tool.name)
    server.mcp._mcp_server.instructions = (
        '仅查询配置的现有 C++ 索引，不代表新项目或运行时事实。'
        '数据库强制只读；不会自动增量、重建或迁移。索引可能落后源码。'
        '重要调用关系需核对返回的源文件、版本及行号；空结果不证明没有调用。'
        '先 cpp_symbol_lookup 定位，再用 cpp_relationship 或 cpp_traverse_graph 查关系。'
        '同名/重载与覆盖范围仍以原索引能力为限。'
    )
    return server


if __name__ == '__main__':
    configure().mcp.run()
