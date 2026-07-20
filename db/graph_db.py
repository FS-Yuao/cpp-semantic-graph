"""
SQLite 图谱库操作封装

项目无关设计：所有操作基于通用表结构，不绑定特定项目。
入库时从 ParseResult 数据模型写入，查询时返回结构化结果。
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from ..parser.models import (
    NodeInfo, EdgeInfo, IncludeDep, ParseResult,
    NodeType, RelationType,
)

logger = logging.getLogger(__name__)

# P2-6：schema 版本号，写入 SQLite PRAGMA user_version。
# 变更 schema（表结构 / unique_key 格式 / 边语义）时递增，便于未来迁移检测。
# 历史：1 = 初始；2 = unique_key 加参数签名区分重载（E-3，function key 含 params 后缀）
#        3 = extra_info JSON 拆入独立列（docs/extra_info_columnar_design.md）
#        4 = 清 needs_resolution 陈旧标记 + DROP extra_info 列（列成为唯一数据源，
#            docs/task/p1_needs_resolution_drop_extrainfo_design.md）
SCHEMA_VERSION = 5


# ── v3: extra_info → 列 映射 ──
# extra_info JSON key → 数据库列名（key 名与列名不同时映射，相同则省略）
# 写入时从 extra_info dict 提取值写入对应列；读取时反向合并回 extra_info dict

# node: extra_info key → 列名（仅列名与 key 不同的需要映射）
_NODE_EXTRA_KEY_TO_COL = {
    "is_template_specialization": "is_template_spec",
    # 其余 key 与列名相同：is_abstract, is_type_alias, is_typedef, template_params,
    # target_type, is_virtual, is_pure_virtual, is_override, is_static, is_const,
    # access, parent_class, signature, result_type, param_types, is_project,
    # doc_title, heading, section_level, content_preview, content_hash, word_count, tags
}

# node: 列名 → extra_info key（反向映射，用于 hydrate）
_NODE_COL_TO_EXTRA_KEY = {v: k for k, v in _NODE_EXTRA_KEY_TO_COL.items()}

# edge: extra_info key → 列名
_EDGE_EXTRA_KEY_TO_COL = {
    "_needs_resolution": "needs_resolution",
    "_resolve_hint": "resolve_hint",
    "method": "match_method",
    # 其余 key 与列名相同：callee_name, callee_namespace, callee_parent_class,
    # callee_file, callee_param_types, callee_is_const, call_type,
    # function_name, derived_class, base_namespace,
    # confidence, matched_name, code_type, link_text, access
}

# edge: 列名 → extra_info key（反向映射）
_EDGE_COL_TO_EXTRA_KEY = {v: k for k, v in _EDGE_EXTRA_KEY_TO_COL.items()}

# node 表所有 v3 新列（用于 INSERT/UPDATE 语句构造）
_NODE_V3_COLUMNS = [
    "is_abstract", "is_template_spec", "is_type_alias", "is_typedef",
    "template_params", "target_type",
    "is_virtual", "is_pure_virtual", "is_override", "is_static", "is_const",
    "access", "parent_class", "signature", "result_type", "param_types", "is_project",
    "doc_title", "heading", "section_level", "content_preview", "content_hash",
    "word_count", "tags",
]
_NODE_V3_COLUMNS_JOIN = ", ".join(_NODE_V3_COLUMNS)

# edge 表所有 v3 新列
_EDGE_V3_COLUMNS = [
    "callee_name", "callee_namespace", "callee_parent_class", "callee_file",
    "callee_param_types", "callee_is_const", "call_type",
    "alias_name", "target_simple_name", "target_type",
    "function_name", "derived_class", "base_namespace",
    "needs_resolution", "resolve_hint",
    "confidence", "match_method", "matched_name", "code_type", "link_text",
    "access",
]
_EDGE_V3_COLUMNS_JOIN = ", ".join(_EDGE_V3_COLUMNS)


def _flatten_node_extra(extra_info: dict | None) -> dict:
    """从 node.extra_info dict 提取已知 key → 返回 {列名: 值} 映射

    布尔值转 INTEGER（True→1, False→0），其余原样保留。
    未知 key 不提取（留在 extra_info JSON 中）。
    """
    if not extra_info:
        return {col: None for col in _NODE_V3_COLUMNS}

    result = {}
    for col in _NODE_V3_COLUMNS:
        # 找到对应的 extra_info key
        # 反向查找：列名 → key 名
        key = _NODE_COL_TO_EXTRA_KEY.get(col, col)
        value = extra_info.get(key)

        if value is None:
            result[col] = None
        elif isinstance(value, bool):
            result[col] = 1 if value else 0
        else:
            # list/dict 仍序列化为 JSON 字符串存入 TEXT 列
            if isinstance(value, (list, dict)):
                result[col] = json.dumps(value, ensure_ascii=False)
            else:
                result[col] = value

    return result


def _flatten_edge_extra(extra_info: dict | None) -> dict:
    """从 edge.extra_info dict 提取已知 key → 返回 {列名: 值} 映射"""
    if not extra_info:
        return {col: None for col in _EDGE_V3_COLUMNS}

    result = {}
    for col in _EDGE_V3_COLUMNS:
        key = _EDGE_COL_TO_EXTRA_KEY.get(col, col)
        value = extra_info.get(key)

        if value is None:
            result[col] = None
        elif isinstance(value, bool):
            result[col] = 1 if value else 0
        else:
            if isinstance(value, (list, dict)):
                result[col] = json.dumps(value, ensure_ascii=False)
            else:
                result[col] = value

    return result


def _hydrate_node(row: sqlite3.Row) -> dict:
    """将数据库行转为 dict，从列重建 extra_info dict

    v4 起 extra_info 列已 DROP，列是唯一数据源，纯从列重建。
    对下游查询层透明：它们仍通过 d["extra_info"]["is_virtual"] 取值。
    （v3 兼容：若行中仍有 extra_info 列，作为 fallback 底座合并。）
    """
    d = dict(row)

    # v3 兼容：extra_info 列若存在则作为底座（v4 已 DROP，此时为 None → 空 dict）
    raw_extra = d.get("extra_info")
    if isinstance(raw_extra, str):
        try:
            extra = json.loads(raw_extra)
        except (json.JSONDecodeError, TypeError):
            extra = {}
    elif isinstance(raw_extra, dict):
        extra = raw_extra
    else:
        extra = {}

    # 从列值重建 extra_info（列值优先）
    for col in _NODE_V3_COLUMNS:
        col_val = d.get(col)
        if col_val is not None:
            # 找到对应的 extra_info key
            key = _NODE_COL_TO_EXTRA_KEY.get(col, col)
            # TEXT 列存 JSON array/dict 的需要反序列化
            if isinstance(col_val, str) and col in (
                "template_params", "param_types", "tags",
            ):
                try:
                    extra[key] = json.loads(col_val)
                except (json.JSONDecodeError, TypeError):
                    extra[key] = col_val
            # INTEGER 列的布尔值转回 bool
            elif isinstance(col_val, int) and col in (
                "is_abstract", "is_template_spec", "is_type_alias", "is_typedef",
                "is_virtual", "is_pure_virtual", "is_override", "is_static", "is_const",
                "is_project",
            ):
                extra[key] = bool(col_val)
            else:
                extra[key] = col_val
        # 列值为 NULL 且 extra_info 中有值 → 保留 extra_info 值（fallback）

    d["extra_info"] = extra if extra else None
    return d


def _hydrate_edge(row: sqlite3.Row) -> dict:
    """将数据库行转为 dict，从列重建 extra_info dict

    v4 起 extra_info 列已 DROP，纯从列重建（v3 兼容：extra_info 列存在时作底座）。
    """
    d = dict(row)

    raw_extra = d.get("extra_info")
    if isinstance(raw_extra, str):
        try:
            extra = json.loads(raw_extra)
        except (json.JSONDecodeError, TypeError):
            extra = {}
    elif isinstance(raw_extra, dict):
        extra = raw_extra
    else:
        extra = {}

    for col in _EDGE_V3_COLUMNS:
        col_val = d.get(col)
        if col_val is not None:
            key = _EDGE_COL_TO_EXTRA_KEY.get(col, col)
            if isinstance(col_val, str) and col in ("callee_param_types",):
                try:
                    extra[key] = json.loads(col_val)
                except (json.JSONDecodeError, TypeError):
                    extra[key] = col_val
            elif isinstance(col_val, int) and col in (
                "callee_is_const", "needs_resolution",
            ):
                extra[key] = bool(col_val)
            else:
                extra[key] = col_val

    d["extra_info"] = extra if extra else None
    return d


class GraphDB:
    """SQLite 图谱数据库操作封装"""

    def __init__(self, db_path: str):
        """初始化数据库连接并建表

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")      # 并发读写
        self.conn.execute("PRAGMA foreign_keys=ON")        # 外键约束
        # 性能调优（主题C）：WAL 下 synchronous=NORMAL 安全且更快（默认 FULL 每事务 fsync）；
        # 内存临时存储 + 大页缓存 + mmap 加速查询；WAL 大小限制防无限增长
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-65536")            # 64MB 页缓存（负值=KB）
        self.conn.execute("PRAGMA mmap_size=268435456")          # 256MB 内存映射读
        self.conn.execute("PRAGMA journal_size_limit=67108864")  # WAL 文件上限 64MB
        self.conn.row_factory = sqlite3.Row
        self._autocommit = True  # False 时 _commit() 变 no-op，由外部事务控制
        self._init_schema()

    def _init_schema(self):
        """初始化表结构"""
        schema_path = Path(__file__).parent / "schema.sql"
        if schema_path.exists():
            with open(schema_path) as f:
                self.conn.executescript(f.read())
        else:
            # Fallback: inline schema
            self._create_tables_inline()
        # v3: 先迁移旧库，再设版本号
        self._auto_migrate()
        self._apply_schema_version()
        self._commit()

    def _apply_schema_version(self):
        """P2-6：记录/校验 schema 版本（PRAGMA user_version）。

        - 新库（user_version=0）：写入当前 SCHEMA_VERSION。
        - v4->v5：仅加 incremental_state 表（_init_schema IF NOT EXISTS 已建），
          结构兼容，自动升版本不告警。
        - 更低旧库版本（<4，涉及列变更）：仅告警，提示重建。
        """
        cur_ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if cur_ver == 0:
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif cur_ver == 4 and SCHEMA_VERSION >= 5:
            # v4->v5 仅加 incremental_state 表，_init_schema 已 IF NOT EXISTS 建。
            # 确认表存在再升（防 _init_schema 异常未建表的极端情况）。
            tbl = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='incremental_state'"
            ).fetchone()
            if tbl:
                self.conn.execute("PRAGMA user_version=5")
                logger.info("DB schema v4->v5：incremental_state 表已就绪，自动升版本")
            else:
                logger.warning(
                    "DB schema v4 但 incremental_state 表缺失，建议 full-parse 重建: %s",
                    self.db_path)
        elif cur_ver < SCHEMA_VERSION:
            logger.warning(
                "DB schema 版本 %d < 当前 %d，可能与新代码不兼容（如 unique_key 格式），"
                "建议 full-parse 重建库。路径: %s",
                cur_ver, SCHEMA_VERSION, self.db_path,
            )

    def schema_version(self) -> int:
        """返回当前 DB 的 schema 版本（PRAGMA user_version）"""
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _auto_migrate(self):
        """自动迁移：检测旧版本并逐级执行迁移脚本

        新库由 schema.sql / inline DDL 直接建成 v4（无 extra_info 列），
        此时 user_version 虽为 0 但无需迁移——以 extra_info 列是否存在为准：
        列不存在 = 已是 v4 结构，跳过迁移（版本号由 _apply_schema_version 落定）。
        """
        from .migrate_v3 import migrate_v2_to_v3, migrate_v3_to_v31
        cur_ver = self.conn.execute("PRAGMA user_version").fetchone()[0]

        # 新建的 v4 库没有 extra_info 列，v2→v3 回填会失败——直接跳过
        node_cols = [r[1] for r in self.conn.execute("PRAGMA table_info(node)")]
        if "extra_info" not in node_cols:
            return

        if cur_ver < 3:
            logger.info("检测到 schema 版本 %d < 3，执行 v2→v3 迁移", cur_ver)
            migrate_v2_to_v3(self.conn)
            cur_ver = 3
        if cur_ver < 4:
            logger.info("检测到 schema 版本 %d < 4，执行 v3→v4 迁移", cur_ver)
            migrate_v3_to_v31(self.conn)

    def _create_tables_inline(self):
        """内联建表（schema.sql 不可用时的 fallback，v4：列是唯一数据源，无 extra_info）"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS node (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                namespace TEXT DEFAULT '',
                file_path TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                unique_key TEXT NOT NULL UNIQUE,
                is_abstract INTEGER DEFAULT 0,
                is_template_spec INTEGER DEFAULT 0,
                is_type_alias INTEGER DEFAULT 0,
                is_typedef INTEGER DEFAULT 0,
                template_params TEXT,
                target_type TEXT,
                is_virtual INTEGER DEFAULT 0,
                is_pure_virtual INTEGER DEFAULT 0,
                is_override INTEGER DEFAULT 0,
                is_static INTEGER DEFAULT 0,
                is_const INTEGER DEFAULT 0,
                access TEXT,
                parent_class TEXT,
                signature TEXT,
                result_type TEXT,
                param_types TEXT,
                is_project INTEGER,
                doc_title TEXT,
                heading TEXT,
                section_level INTEGER,
                content_preview TEXT,
                content_hash TEXT,
                word_count INTEGER,
                tags TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS edge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                call_line INTEGER DEFAULT 0,
                callee_name TEXT,
                callee_namespace TEXT,
                callee_parent_class TEXT,
                callee_file TEXT,
                callee_param_types TEXT,
                callee_is_const INTEGER DEFAULT 0,
                call_type TEXT,
                alias_name TEXT,
                target_simple_name TEXT,
                target_type TEXT,
                function_name TEXT,
                derived_class TEXT,
                base_namespace TEXT,
                needs_resolution INTEGER DEFAULT 0,
                resolve_hint TEXT,
                confidence REAL,
                match_method TEXT,
                matched_name TEXT,
                code_type TEXT,
                link_text TEXT,
                access TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(from_id) REFERENCES node(id) ON DELETE CASCADE,
                FOREIGN KEY(to_id) REFERENCES node(id) ON DELETE CASCADE,
                UNIQUE(from_id, to_id, relation_type, call_line)
            );
            CREATE TABLE IF NOT EXISTS include_dep (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                included_file TEXT NOT NULL,
                is_system INTEGER DEFAULT 0,
                UNIQUE(source_file, included_file)
            );
            CREATE TABLE IF NOT EXISTS parse_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                node_count INTEGER DEFAULT 0,
                edge_count INTEGER DEFAULT 0,
                parsed_at TEXT DEFAULT (datetime('now')),
                file_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_node_name ON node(name);
            CREATE INDEX IF NOT EXISTS idx_node_type ON node(type);
            CREATE INDEX IF NOT EXISTS idx_node_file_path ON node(file_path);
            CREATE INDEX IF NOT EXISTS idx_node_unique_key ON node(unique_key);
            CREATE INDEX IF NOT EXISTS idx_node_is_virtual ON node(is_virtual) WHERE is_virtual = 1;
            CREATE INDEX IF NOT EXISTS idx_node_doc_title ON node(doc_title);
            CREATE INDEX IF NOT EXISTS idx_node_parent_class ON node(parent_class);
            CREATE INDEX IF NOT EXISTS idx_node_is_project ON node(is_project) WHERE is_project IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_edge_from_id ON edge(from_id);
            CREATE INDEX IF NOT EXISTS idx_edge_to_id ON edge(to_id);
            CREATE INDEX IF NOT EXISTS idx_edge_relation_type ON edge(relation_type);
            CREATE INDEX IF NOT EXISTS idx_edge_from_type ON edge(from_id, relation_type);
            CREATE INDEX IF NOT EXISTS idx_edge_to_type ON edge(to_id, relation_type);
            CREATE INDEX IF NOT EXISTS idx_edge_callee_name ON edge(callee_name);
            CREATE INDEX IF NOT EXISTS idx_edge_needs_resolution ON edge(needs_resolution) WHERE needs_resolution = 1;
            CREATE INDEX IF NOT EXISTS idx_include_source ON include_dep(source_file);
            CREATE INDEX IF NOT EXISTS idx_include_included ON include_dep(included_file);
        """)

    def close(self):
        """关闭数据库连接"""
        self.conn.close()

    # ── 增量状态（task_4_5: MCP 惰性增量节流）──
    def get_last_incremented_ref(self) -> str:
        """读取上次增量到的 commit hash（惰性增量 rev-parse 节流用）

        返回空串表示首次/未记录（调用方应记录当前 HEAD 不增量）。
        """
        row = self.conn.execute(
            "SELECT value FROM incremental_state WHERE key='last_incremented_ref'"
        ).fetchone()
        return row[0] if row else ""

    def set_last_incremented_ref(self, ref: str) -> None:
        """记录上次增量到的 commit hash（增量成功后调用）"""
        self.conn.execute(
            "INSERT OR REPLACE INTO incremental_state(key, value, updated_at) "
            "VALUES('last_incremented_ref', ?, datetime('now'))",
            (ref,)
        )
        self._commit()

    def _commit(self):
        """提交事务（当 _autocommit=True 时生效，否则由外部事务控制）"""
        if self._autocommit:
            self.conn.commit()

    # P2-7：外部事务控制的公共接口，替代直接改私有属性 _autocommit。
    # 用法：begin_manual_transaction() → 多次写操作（_commit 变 no-op）
    #       → commit_manual_transaction() / rollback_manual_transaction()
    def begin_manual_transaction(self):
        """进入手动事务模式：内部 _commit() 变 no-op，由调用方统一提交/回滚。"""
        self._autocommit = False

    def commit_manual_transaction(self):
        """提交手动事务并恢复自动提交模式。"""
        self.conn.commit()
        self._autocommit = True

    def rollback_manual_transaction(self):
        """回滚手动事务并恢复自动提交模式。"""
        self.conn.rollback()
        self._autocommit = True

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def upsert_node(self, node: NodeInfo) -> int:
        """插入或更新节点，返回 node id

        - unique_key 不存在 → INSERT
        - unique_key 已存在 → UPDATE 可变字段（行号、命名空间、v3 新列等）
        """
        type_val = node.type.value if isinstance(node.type, NodeType) else node.type
        flat = _flatten_node_extra(node.extra_info)
        v3_cols = ", ".join(_NODE_V3_COLUMNS)
        v3_placeholders = ", ".join("?" * len(_NODE_V3_COLUMNS))
        v3_values = [flat[c] for c in _NODE_V3_COLUMNS]

        # Try insert first（v4：extra_info 列已 DROP，仅写列）
        try:
            cursor = self.conn.execute(
                f"""INSERT INTO node (type, name, namespace, file_path, start_line, end_line,
                                       unique_key, {_NODE_V3_COLUMNS_JOIN})
                   VALUES (?, ?, ?, ?, ?, ?, ?, {v3_placeholders})""",
                (type_val, node.name, node.namespace, node.file_path,
                 node.start_line, node.end_line, node.unique_key,
                 *v3_values)
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # unique_key conflict → update 可变字段 + v3 新列
            v3_set = ", ".join(f"{c}=?" for c in _NODE_V3_COLUMNS)
            self.conn.execute(
                f"""UPDATE node SET namespace=?, file_path=?, start_line=?, end_line=?,
                          {v3_set}, updated_at=datetime('now')
                   WHERE unique_key=?""",
                (node.namespace, node.file_path, node.start_line, node.end_line,
                 *v3_values, node.unique_key)
            )
            row = self.conn.execute(
                "SELECT id FROM node WHERE unique_key=?", (node.unique_key,)
            ).fetchone()
            return row["id"]

    def get_node_by_key(self, unique_key: str) -> dict | None:
        """按 unique_key 查询节点"""
        row = self.conn.execute(
            "SELECT * FROM node WHERE unique_key=?", (unique_key,)
        ).fetchone()
        return _hydrate_node(row) if row else None

    def get_node_by_id(self, node_id: int) -> dict | None:
        """按 id 查询节点"""
        row = self.conn.execute(
            "SELECT * FROM node WHERE id=?", (node_id,)
        ).fetchone()
        return _hydrate_node(row) if row else None

    def find_node_by_name(self, name: str, node_type: str = None,
                          exact: bool = True) -> list[dict]:
        """按名称搜索节点

        Args:
            name: 节点名称
            node_type: 可选，限定节点类型
            exact: True=精确匹配, False=模糊匹配
        """
        if exact:
            sql = "SELECT * FROM node WHERE name=?"
            params = [name]
        else:
            sql = "SELECT * FROM node WHERE name LIKE ?"
            params = [f"%{name}%"]

        if node_type:
            sql += " AND type=?"
            params.append(node_type)

        sql += " ORDER BY name LIMIT 100"
        return [_hydrate_node(row) for row in self.conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def insert_edge(self, from_id: int, to_id: int, relation_type: str,
                    extra_info: dict = None, call_line: int = 0) -> int | None:
        """插入边，已存在则更新 extra_info + v3 新列

        Args:
            call_line: 调用行号，用于区分同一函数内多次调用同一目标的多个调用点

        Returns:
            edge id，或 None（不应发生）
        """
        flat = _flatten_edge_extra(extra_info)
        v3_placeholders = ", ".join("?" * len(_EDGE_V3_COLUMNS))
        v3_values = [flat[c] for c in _EDGE_V3_COLUMNS]

        try:
            cursor = self.conn.execute(
                f"""INSERT INTO edge (from_id, to_id, relation_type, call_line,
                                      {_EDGE_V3_COLUMNS_JOIN})
                   VALUES (?, ?, ?, ?, {v3_placeholders})""",
                (from_id, to_id, relation_type, call_line, *v3_values)
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # (from_id, to_id, relation_type, call_line) conflict → update v3 新列
            v3_set = ", ".join(f"{c}=?" for c in _EDGE_V3_COLUMNS)
            self.conn.execute(
                f"""UPDATE edge SET {v3_set}
                   WHERE from_id=? AND to_id=? AND relation_type=? AND call_line=?""",
                (*v3_values, from_id, to_id, relation_type, call_line)
            )
            row = self.conn.execute(
                """SELECT id FROM edge
                   WHERE from_id=? AND to_id=? AND relation_type=? AND call_line=?""",
                (from_id, to_id, relation_type, call_line)
            ).fetchone()
            return row["id"] if row else None

    def get_edges_from(self, node_id: int, relation_type: str = None) -> list[dict]:
        """查询从指定节点出发的边"""
        if relation_type:
            sql = """SELECT e.*, n.name as to_name, n.type as to_type, n.namespace as to_namespace
                     FROM edge e JOIN node n ON e.to_id=n.id
                     WHERE e.from_id=? AND e.relation_type=?"""
            params = [node_id, relation_type]
        else:
            sql = """SELECT e.*, n.name as to_name, n.type as to_type, n.namespace as to_namespace
                     FROM edge e JOIN node n ON e.to_id=n.id
                     WHERE e.from_id=?"""
            params = [node_id]
        return [_hydrate_edge(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_edges_to(self, node_id: int, relation_type: str = None) -> list[dict]:
        """查询指向指定节点的边"""
        if relation_type:
            sql = """SELECT e.*, n.name as from_name, n.type as from_type, n.namespace as from_namespace
                     FROM edge e JOIN node n ON e.from_id=n.id
                     WHERE e.to_id=? AND e.relation_type=?"""
            params = [node_id, relation_type]
        else:
            sql = """SELECT e.*, n.name as from_name, n.type as from_type, n.namespace as from_namespace
                     FROM edge e JOIN node n ON e.from_id=n.id
                     WHERE e.to_id=?"""
            params = [node_id]
        return [_hydrate_edge(row) for row in self.conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Include dependency operations
    # ------------------------------------------------------------------

    def insert_include(self, inc: IncludeDep):
        """插入 include 依赖，已存在则跳过"""
        try:
            self.conn.execute(
                "INSERT INTO include_dep (source_file, included_file, is_system) VALUES (?, ?, ?)",
                (inc.source_file, inc.included_file, 1 if inc.is_system else 0)
            )
        except sqlite3.IntegrityError:
            pass

    def get_includers(self, header_path: str, recursive: bool = False) -> list[str]:
        """查询所有 include 指定头文件的翻译单元

        Args:
            header_path: 头文件路径
            recursive: 是否递归查询间接 include
        """
        # included_file 存 basename，按 basename 精确匹配避免子串误匹配（主题A-3）
        # 'util.h' 不再误匹配 'my_util.h'；兼容偶尔存带路径的 included_file
        header_base = Path(header_path).name
        if not recursive:
            rows = self.conn.execute(
                """SELECT DISTINCT source_file FROM include_dep
                   WHERE included_file = ? OR included_file LIKE '%/' || ?""",
                (header_base, header_base)
            ).fetchall()
            return [row["source_file"] for row in rows]

        # Recursive: BFS through include chain
        result = set()
        queue = [header_base]
        visited = set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            current_base = Path(current).name
            rows = self.conn.execute(
                """SELECT DISTINCT source_file FROM include_dep
                   WHERE included_file = ? OR included_file LIKE '%/' || ?""",
                (current_base, current_base)
            ).fetchall()
            for row in rows:
                result.add(row["source_file"])
                # Check if this source file is also a header that might be included
                queue.append(row["source_file"])
        return list(result)

    # ------------------------------------------------------------------
    # Inheritance helpers (for override resolution)
    # ------------------------------------------------------------------

    def _find_base_classes(self, class_name: str, namespace: str = "") -> list[str]:
        """查找类的直接基类名称列表

        通过 inherits_* 边查找: from=子类, to=基类

        Args:
            class_name: 类名（不含命名空间）
            namespace: 命名空间前缀

        Returns:
            基类名称列表
        """
        # 先找子类节点
        if namespace:
            # namespace 是 '::' 分隔路径，按完整段匹配避免子串误匹配（主题A-1）
            # 'foo' 匹配 'foo' / 'foo::bar' / 'bar::foo'，不匹配 'foobar'
            child_rows = self.conn.execute(
                """SELECT id FROM node
                   WHERE name=? AND type IN ('class', 'struct')
                   AND (namespace = ?
                        OR namespace LIKE ? || '::%'
                        OR namespace LIKE '%::' || ?)
                   LIMIT 5""",
                (class_name, namespace, namespace, namespace)
            ).fetchall()
        else:
            child_rows = self.conn.execute(
                """SELECT id FROM node
                   WHERE name=? AND type IN ('class', 'struct')
                   LIMIT 5""",
                (class_name,)
            ).fetchall()

        base_names = []
        for child_row in child_rows:
            child_id = child_row["id"]
            # 查 inherits 边: from=child(子类) → to=base(基类)
            inherit_rows = self.conn.execute(
                """SELECT n.name FROM edge e
                   JOIN node n ON e.to_id = n.id
                   WHERE e.from_id=? AND e.relation_type LIKE 'inherits%'
                   AND n.type IN ('class', 'struct')""",
                (child_id,)
            ).fetchall()
            for row in inherit_rows:
                if row["name"] not in base_names:
                    base_names.append(row["name"])
        return base_names

    # ------------------------------------------------------------------
    # Parse status operations
    # ------------------------------------------------------------------

    def update_parse_status(self, source_file: str, status: str,
                            error_message: str = "",
                            node_count: int = 0, edge_count: int = 0,
                            file_hash: str = ""):
        """更新翻译单元的解析状态"""
        self.conn.execute(
            """INSERT OR REPLACE INTO parse_status
               (source_file, status, error_message, node_count, edge_count, parsed_at, file_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            (source_file, status, error_message, node_count, edge_count, file_hash)
        )

    def get_parse_status(self, source_file: str) -> dict | None:
        """查询翻译单元的解析状态"""
        row = self.conn.execute(
            "SELECT * FROM parse_status WHERE source_file=?", (source_file,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_parse_status(self) -> list[dict]:
        """查询所有翻译单元的解析状态"""
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM parse_status ORDER BY parsed_at DESC"
        ).fetchall()]

    # ------------------------------------------------------------------
    # Bulk import
    # ------------------------------------------------------------------

    def import_parse_result(self, result: ParseResult) -> dict:
        """将单个翻译单元的解析结果批量入库

        Returns:
            统计信息: {nodes_new, nodes_updated, edges_new, edges_skipped, includes_new}
        """
        stats = {
            "nodes_new": 0,
            "nodes_updated": 0,
            "edges_new": 0,
            "edges_skipped": 0,
            "includes_new": 0,
        }

        # 1. Import nodes
        for node in result.nodes:
            type_val = node.type.value if isinstance(node.type, NodeType) else node.type
            flat = _flatten_node_extra(node.extra_info)
            v3_values = [flat[c] for c in _NODE_V3_COLUMNS]

            # Check if exists
            existing = self.conn.execute(
                "SELECT id FROM node WHERE unique_key=?", (node.unique_key,)
            ).fetchone()

            if existing:
                # 刷新行号/命名空间/文件路径 + v3 新列（v4：不再写 extra_info）
                v3_set = ", ".join(f"{c}=?" for c in _NODE_V3_COLUMNS)
                self.conn.execute(
                    f"""UPDATE node SET start_line=?, end_line=?,
                                       namespace=?, file_path=?, {v3_set}, updated_at=datetime('now')
                       WHERE unique_key=?""",
                    (node.start_line, node.end_line,
                     node.namespace, node.file_path, *v3_values, node.unique_key)
                )
                stats["nodes_updated"] += 1
            else:
                v3_placeholders = ", ".join("?" * len(_NODE_V3_COLUMNS))
                self.conn.execute(
                    f"""INSERT INTO node (type, name, namespace, file_path, start_line, end_line,
                                          unique_key, {_NODE_V3_COLUMNS_JOIN})
                       VALUES (?, ?, ?, ?, ?, ?, ?, {v3_placeholders})""",
                    (type_val, node.name, node.namespace, node.file_path,
                     node.start_line, node.end_line, node.unique_key,
                     *v3_values)
                )
                stats["nodes_new"] += 1

        # 2. Resolve and import edges
        for edge in result.edges:
            rt = edge.relation_type.value if isinstance(edge.relation_type, RelationType) else edge.relation_type

            # Resolve from_key to from_id
            from_row = self.conn.execute(
                "SELECT id FROM node WHERE unique_key=?", (edge.from_unique_key,)
            ).fetchone()
            if not from_row:
                continue  # Skip edges with unresolved source

            from_id = from_row["id"]
            to_id = None

            # Resolve to_key to to_id
            if edge.to_unique_key:
                to_row = self.conn.execute(
                    "SELECT id FROM node WHERE unique_key=?", (edge.to_unique_key,)
                ).fetchone()
                if to_row:
                    to_id = to_row["id"]

            # For unresolved edges, try to find target by context
            if to_id is None and edge.extra_info and edge.extra_info.get("_needs_resolution"):
                resolve_hint = edge.extra_info.get("_resolve_hint", "")

                if resolve_hint == "override":
                    # ── Override 边解析 ──
                    # from=派生类函数 → to=基类虚函数
                    # 策略: 从 from_unique_key 提取派生类名 → 查继承边找基类 → 在基类中找同名虚函数
                    func_name = edge.extra_info.get("function_name", "")
                    derived_class = edge.extra_info.get("derived_class", "")
                    base_ns = edge.extra_info.get("base_namespace", "")

                    if func_name and derived_class:
                        # 查继承边: 找派生类的基类
                        base_classes = self._find_base_classes(derived_class, base_ns)
                        for base_name in base_classes:
                            # 析构函数: 派生类 ~Derived → 基类 ~Base
                            search_name = func_name
                            if func_name.startswith("~"):
                                search_name = f"~{base_name}"

                            # 在基类中找同名函数（主题A-2：Python 端精确匹配 namespace 末段=base_name）
                            # 函数节点 namespace 末段=所属类名，按末段精确匹配，
                            # 避免 LIKE '%base_name%' 子串误匹配（'Base' 命中 'Database'）
                            # 及 '_' 通配符误匹配
                            cand_rows = self.conn.execute(
                                "SELECT id, namespace FROM node WHERE name=? AND type='function'",
                                (search_name,)
                            ).fetchall()
                            for cr in cand_rows:
                                ns = cr["namespace"] or ""
                                ns_tail = ns.rsplit("::", 1)[-1] if ns else ""
                                if ns_tail != base_name:
                                    continue
                                if base_ns and not ns.startswith(base_ns + "::"):
                                    continue
                                to_id = cr["id"]
                                break
                            if to_id is not None:
                                break

                        # Fallback: 主路径未命中时，在基类链中找同名虚函数（P0-1：限定 base_classes）
                        # 加 is_virtual + belongs_to 过滤，末段精确匹配（主题A-2）
                        if to_id is None and func_name and base_classes:
                            from_row = self.conn.execute(
                                "SELECT id FROM node WHERE unique_key=?",
                                (edge.from_unique_key,)
                            ).fetchone()
                            from_node_id = from_row["id"] if from_row else -1

                            for base_name in base_classes:
                                search_name = func_name
                                if func_name.startswith("~"):
                                    search_name = f"~{base_name}"
                                cand_rows = self.conn.execute(
                                    """SELECT n.id, n.namespace FROM node n
                                       JOIN edge e ON e.from_id = n.id
                                       WHERE n.name=? AND n.type='function'
                                       AND e.relation_type='belongs_to'
                                       AND n.is_virtual = 1
                                       AND n.id != ?""",
                                    (search_name, from_node_id)
                                ).fetchall()
                                for cr in cand_rows:
                                    ns = cr["namespace"] or ""
                                    ns_tail = ns.rsplit("::", 1)[-1] if ns else ""
                                    if ns_tail == base_name:
                                        to_id = cr["id"]
                                        break
                                if to_id is not None:
                                    break

                elif resolve_hint == "type_alias":
                    # ── 类型别名边解析 ──
                    # from=别名节点 → to=目标类型节点
                    # target 的 namespace/file_path 未知（多来自外部库），
                    # 按 target_simple_name 在 DB 中查同名 class 节点。
                    target_simple = edge.extra_info.get("target_simple_name", "")
                    if target_simple:
                        to_row = self.conn.execute(
                            """SELECT id FROM node
                               WHERE name=? AND type IN ('class', 'struct')
                               LIMIT 1""",
                            (target_simple,)
                        ).fetchone()
                        if to_row:
                            to_id = to_row["id"]

                elif resolve_hint == "using_decl":
                    # ── using 声明边解析 ──
                    # from=子类::func → to=基类::func
                    # 按 base_class + func_name 查基类中的同名函数。
                    func_name = edge.extra_info.get("function_name", "")
                    base_class = edge.extra_info.get("base_class", "")
                    if func_name and base_class:
                        to_row = self.conn.execute(
                            """SELECT id FROM node
                               WHERE name=? AND type='function'
                               AND namespace LIKE ?
                               LIMIT 1""",
                            (func_name, f"%{base_class}%")
                        ).fetchone()
                        if to_row:
                            to_id = to_row["id"]

                else:
                    # ── 调用边解析 ──
                    callee_name = edge.extra_info.get("callee_name", "")
                    callee_parent = edge.extra_info.get("callee_parent_class", "")
                    callee_ns = edge.extra_info.get("callee_namespace", "")
                    callee_params = edge.extra_info.get("callee_param_types", None)

                    # 第 1 级：name + parent + 参数精确匹配 → 唯一重载
                    # 取回同名 function 候选，Python 端比对 param_types（区分重载）。
                    # 仅当调用点提供了 callee_param_types 时启用（旧数据/无参数信息回退）。
                    if callee_name and callee_params is not None:
                        cand_rows = self.conn.execute(
                            """SELECT id, namespace, param_types FROM node
                               WHERE name=? AND type='function'""",
                            (callee_name,)
                        ).fetchall()
                        # 优先在 parent/namespace 匹配的候选里找参数一致的重载
                        best = None
                        for cr in cand_rows:
                            ns = cr["namespace"] or ""
                            # parent 约束：namespace 末段=parent 或含 callee_ns
                            if callee_parent:
                                ns_tail = ns.rsplit("::", 1)[-1] if ns else ""
                                if ns_tail != callee_parent and callee_parent not in ns:
                                    continue
                            # param_types 列是 TEXT(JSON array)，v4 直接读列 parse
                            try:
                                cand_params = json.loads(cr["param_types"]) if cr["param_types"] else None
                            except (json.JSONDecodeError, TypeError):
                                cand_params = None
                            if cand_params == callee_params:
                                best = cr["id"]
                                break
                        if best is not None:
                            to_id = best

                    # 第 2 级：name + parent class 匹配（回退，参数对不齐时）
                    if to_id is None and callee_name and callee_parent:
                        to_row = self.conn.execute(
                            """SELECT id FROM node
                               WHERE name=? AND type='function'
                               AND (namespace LIKE ? OR namespace LIKE ?)
                               LIMIT 1""",
                            (callee_name, f"%{callee_parent}%", f"%{callee_ns}%")
                        ).fetchone()
                        if to_row:
                            to_id = to_row["id"]

                    # 第 3 级：仅按 name 匹配（最后回退，least precise）
                    if to_id is None and callee_name:
                        to_row = self.conn.execute(
                            """SELECT id FROM node
                               WHERE name=? AND type='function'
                               LIMIT 1""",
                            (callee_name,)
                        ).fetchone()
                        if to_row:
                            to_id = to_row["id"]

            # Insert edge if we have both endpoints
            if to_id is not None:
                # 已解析成功 → 清 _needs_resolution 陈旧标记（防复发，P1-1 逻辑修复）
                # 入库的边 to_id 都有效，语义上不再是 pending。
                if edge.extra_info:
                    edge.extra_info["_needs_resolution"] = False
                # 提取 call_line 用于区分同一函数内多次调用同一目标的多个调用点
                call_line = edge.extra_info.get("call_line", 0) if edge.extra_info else 0
                edge_id = self.insert_edge(from_id, to_id, rt, edge.extra_info, call_line=call_line)
                if edge_id:
                    stats["edges_new"] += 1
                else:
                    stats["edges_skipped"] += 1
            # else: unresolved edge — store as pending for later resolution

        # 3. Import includes
        for inc in result.includes:
            try:
                self.conn.execute(
                    "INSERT INTO include_dep (source_file, included_file, is_system) VALUES (?, ?, ?)",
                    (inc.source_file, inc.included_file, 1 if inc.is_system else 0)
                )
                stats["includes_new"] += 1
            except sqlite3.IntegrityError:
                pass

        # 4. Update parse status
        self.update_parse_status(
            source_file=result.source_path,
            status=result.status,
            error_message=result.error_message,
            node_count=result.node_count,
            edge_count=result.edge_count,
        )

        self._commit()
        return stats

    def import_results(self, results: list[ParseResult]) -> dict:
        """批量导入多个翻译单元的解析结果

        Returns:
            汇总统计
        """
        total_stats = {
            "files_processed": 0,
            "files_failed": 0,
            "nodes_new": 0,
            "nodes_updated": 0,
            "edges_new": 0,
            "edges_skipped": 0,
            "includes_new": 0,
        }

        # 第一轮：导入所有 TU（跨 TU 边可能因 to 节点尚未入库而丢弃）
        for result in results:
            stats = self.import_parse_result(result)
            total_stats["files_processed"] += 1
            if result.status == "failed":
                total_stats["files_failed"] += 1
            total_stats["nodes_new"] += stats["nodes_new"]
            total_stats["nodes_updated"] += stats["nodes_updated"]
            total_stats["edges_new"] += stats["edges_new"]
            total_stats["edges_skipped"] += stats["edges_skipped"]
            total_stats["includes_new"] += stats["includes_new"]

        # P1-B 修复（第4项：未解析边多趟补全）
        # 第一轮所有节点已入库，第二轮重试之前因 to 节点缺失而丢弃的跨 TU 边
        # （如 derived.cpp 先导入时，override 边 to=基类虚函数还没入库；type_alias 跨 TU 等）
        # 已插入的边由 UNIQUE 约束跳过，仅补全之前未解析的
        if any(r.status != "failed" for r in results):
            for result in results:
                if result.status == "failed":
                    continue
                stats = self.import_parse_result(result)
                total_stats["edges_new"] += stats["edges_new"]
                total_stats["edges_skipped"] += stats["edges_skipped"]

        return total_stats

    # ------------------------------------------------------------------
    # Delete operations (for incremental update)
    # ------------------------------------------------------------------

    def delete_by_source_file(self, file_path: str) -> int:
        """删除与指定源文件关联的所有节点和边

        用于增量更新时清除旧数据。

        Returns:
            删除的节点数量
        """
        # Find nodes belonging to this file
        rows = self.conn.execute(
            "SELECT id FROM node WHERE file_path LIKE ?", (f"%{file_path}%",)
        ).fetchall()
        node_ids = [row["id"] for row in rows]

        if not node_ids:
            return 0

        # Delete edges involving these nodes
        placeholders = ",".join("?" * len(node_ids))
        self.conn.execute(
            f"DELETE FROM edge WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
            node_ids + node_ids
        )

        # Delete nodes
        self.conn.execute(
            f"DELETE FROM node WHERE id IN ({placeholders})", node_ids
        )

        # Delete includes
        self.conn.execute(
            "DELETE FROM include_dep WHERE source_file LIKE ?", (f"%{file_path}%",)
        )

        # Delete parse status
        self.conn.execute(
            "DELETE FROM parse_status WHERE source_file LIKE ?", (f"%{file_path}%",)
        )

        self._commit()
        return len(node_ids)

    # ------------------------------------------------------------------
    # 增量更新专用删除方法（精确匹配 file_path，不用 LIKE）
    # ------------------------------------------------------------------

    def delete_edges_from_file(self, file_path: str) -> int:
        """删除指定文件的所有出边（from_id 在该文件的边）

        增量更新核心：只删出边，保留入边，节点用 upsert 更新。
        精确匹配 file_path，不用 LIKE（避免子串误匹配）。

        Args:
            file_path: DB 相对路径（如 'peri_update/soc/soc_update.cpp'）

        Returns:
            删除的边数
        """
        cursor = self.conn.execute(
            """DELETE FROM edge
               WHERE from_id IN (SELECT id FROM node WHERE file_path = ?)""",
            (file_path,)
        )
        self._commit()
        return cursor.rowcount

    def delete_tu_data(self, source_file_rel: str,
                       source_file_abs: str) -> dict:
        """删除翻译单元特有数据：include_dep + parse_status

        Args:
            source_file_rel: DB 相对路径（include_dep.source_file 格式）
            source_file_abs: 绝对路径（parse_status.source_file 格式）

        Returns:
            {includes_deleted, parse_status_deleted}
        """
        inc_cur = self.conn.execute(
            "DELETE FROM include_dep WHERE source_file = ?",
            (source_file_rel,)
        )
        ps_cur = self.conn.execute(
            "DELETE FROM parse_status WHERE source_file = ?",
            (source_file_abs,)
        )
        # 兜底：parse_status 可能存的是相对路径（历史数据）
        if ps_cur.rowcount == 0:
            ps_cur = self.conn.execute(
                "DELETE FROM parse_status WHERE source_file LIKE ?",
                (f"%{source_file_rel}%",)
            )
        self._commit()
        return {
            "includes_deleted": inc_cur.rowcount,
            "parse_status_deleted": ps_cur.rowcount,
        }

    def delete_removed_nodes(self, file_path: str,
                             retained_keys: set[str]) -> int:
        """删除文件中不在 retained_keys 集合里的节点

        用于增量更新后清理已从源码中删除的函数/类。
        CASCADE 自动删除关联边（PRAGMA foreign_keys=ON）。

        Args:
            file_path: DB 相对路径
            retained_keys: 重新解析后该文件仍存在的 unique_key 集合

        Returns:
            删除的节点数
        """
        rows = self.conn.execute(
            "SELECT id, unique_key FROM node WHERE file_path = ?",
            (file_path,)
        ).fetchall()

        to_delete = [r["id"] for r in rows
                     if r["unique_key"] not in retained_keys]

        if not to_delete:
            return 0

        placeholders = ",".join("?" * len(to_delete))
        # P1-B 修复（第3项）：删节点 CASCADE 会删其他文件的入边，记录来源以便追加重解析
        # 完整修复需在增量更新末尾对这些来源文件追加重解析（见审查报告 P1-B 主题）
        incoming = self.conn.execute(
            f"""SELECT DISTINCT n.file_path FROM edge e
                JOIN node n ON e.from_id = n.id
                WHERE e.to_id IN ({placeholders})""", to_delete
        ).fetchall()
        if incoming:
            logger.warning("删除 %s 的 %d 个节点将 CASCADE 删除来自其他文件的入边（建议追加重解析来源）: %s",
                           file_path, len(to_delete), [r["file_path"] for r in incoming])
        self.conn.execute(
            f"DELETE FROM node WHERE id IN ({placeholders})",
            to_delete
        )
        self._commit()
        return len(to_delete)

    def delete_file_completely(self, file_path: str) -> dict:
        """完全删除一个文件的所有数据（用于物理删除的文件）

        删除节点（CASCADE 删边）+ include_dep + parse_status。

        Args:
            file_path: DB 相对路径

        Returns:
            {nodes_deleted, edges_cascaded, includes_deleted, parse_status_deleted}
        """
        node_count = self.conn.execute(
            "SELECT COUNT(*) as c FROM node WHERE file_path = ?",
            (file_path,)
        ).fetchone()["c"]

        edge_count = self.conn.execute(
            """SELECT COUNT(*) as c FROM edge
               WHERE from_id IN (SELECT id FROM node WHERE file_path = ?)
                  OR to_id IN (SELECT id FROM node WHERE file_path = ?)""",
            (file_path, file_path)
        ).fetchone()["c"]

        # 删节点（CASCADE 删边）
        self.conn.execute(
            "DELETE FROM node WHERE file_path = ?",
            (file_path,)
        )
        # 删 include_dep（source 或 included）
        inc_cur = self.conn.execute(
            "DELETE FROM include_dep WHERE source_file = ? OR included_file = ?",
            (file_path, file_path)
        )
        # 删 parse_status
        ps_cur = self.conn.execute(
            "DELETE FROM parse_status WHERE source_file LIKE ?",
            (f"%{file_path}%",)
        )
        self._commit()
        return {
            "nodes_deleted": node_count,
            "edges_cascaded": edge_count,
            "includes_deleted": inc_cur.rowcount,
            "parse_status_deleted": ps_cur.rowcount,
        }

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """获取图谱统计信息"""
        node_count = self.conn.execute("SELECT COUNT(*) as c FROM node").fetchone()["c"]
        edge_count = self.conn.execute("SELECT COUNT(*) as c FROM edge").fetchone()["c"]
        include_count = self.conn.execute("SELECT COUNT(*) as c FROM include_dep").fetchone()["c"]

        # Node type distribution
        type_dist = {}
        for row in self.conn.execute(
            "SELECT type, COUNT(*) as c FROM node GROUP BY type ORDER BY c DESC"
        ).fetchall():
            type_dist[row["type"]] = row["c"]

        # Edge type distribution
        rel_dist = {}
        for row in self.conn.execute(
            "SELECT relation_type, COUNT(*) as c FROM edge GROUP BY relation_type ORDER BY c DESC"
        ).fetchall():
            rel_dist[row["relation_type"]] = row["c"]

        # Parse status
        status_dist = {}
        for row in self.conn.execute(
            "SELECT status, COUNT(*) as c FROM parse_status GROUP BY status"
        ).fetchall():
            status_dist[row["status"]] = row["c"]

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "include_count": include_count,
            "node_type_distribution": type_dist,
            "edge_type_distribution": rel_dist,
            "parse_status": status_dist,
        }
