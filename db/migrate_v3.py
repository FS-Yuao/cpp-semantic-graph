"""
Schema 迁移脚本

v2 → v3：将 extra_info JSON blob 拆入独立列（双写过渡，extra_info 保留）
    设计文档：docs/extra_info_columnar_design.md
v3 → v4：清 needs_resolution 陈旧标记 + DROP extra_info 列（列成唯一数据源）
    设计文档：docs/task/p1_needs_resolution_drop_extrainfo_design.md

迁移策略：
- ALTER TABLE ADD COLUMN 添加所有新列
- UPDATE ... SET col = json_extract(extra_info, '$.key') 回填数据
- CREATE INDEX 添加新索引
- 幂等：以 PRAGMA user_version 判断，重复执行不报错、不丢数据
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

SCHEMA_VERSION_FROM = 2
SCHEMA_VERSION_TO = 3


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """检查列是否已存在（幂等 ALTER 的前提）"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _add_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]):
    """安全添加列（已存在则跳过）

    Args:
        table: 表名
        columns: [(column_name, column_def), ...]  e.g. [("is_virtual", "INTEGER DEFAULT 0")]
    """
    for col_name, col_def in columns:
        if _column_exists(conn, table, col_name):
            logger.debug("列 %s.%s 已存在，跳过", table, col_name)
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
        logger.info("添加列 %s.%s %s", table, col_name, col_def)


def _backfill_node_columns(conn: sqlite3.Connection):
    """回填 node 表新列：从 extra_info JSON 提取值写入对应列"""
    # class/struct 字段
    conn.execute("""
        UPDATE node SET
            is_abstract = CASE WHEN json_extract(extra_info, '$.is_abstract') IS NOT NULL
                              THEN json_extract(extra_info, '$.is_abstract') ELSE 0 END,
            is_template_spec = CASE WHEN json_extract(extra_info, '$.is_template_specialization') IS NOT NULL
                                    THEN json_extract(extra_info, '$.is_template_specialization') ELSE 0 END,
            is_type_alias = CASE WHEN json_extract(extra_info, '$.is_type_alias') IS NOT NULL
                                 THEN json_extract(extra_info, '$.is_type_alias') ELSE 0 END,
            is_typedef = CASE WHEN json_extract(extra_info, '$.is_typedef') IS NOT NULL
                              THEN json_extract(extra_info, '$.is_typedef') ELSE 0 END,
            template_params = json_extract(extra_info, '$.template_params'),
            target_type = json_extract(extra_info, '$.target_type'),
            access = json_extract(extra_info, '$.access'),
            is_project = json_extract(extra_info, '$.is_project')
        WHERE type IN ('class', 'struct') AND extra_info IS NOT NULL
    """)

    # function 字段
    conn.execute("""
        UPDATE node SET
            is_virtual = CASE WHEN json_extract(extra_info, '$.is_virtual') IS NOT NULL
                              THEN json_extract(extra_info, '$.is_virtual') ELSE 0 END,
            is_pure_virtual = CASE WHEN json_extract(extra_info, '$.is_pure_virtual') IS NOT NULL
                                   THEN json_extract(extra_info, '$.is_pure_virtual') ELSE 0 END,
            is_override = CASE WHEN json_extract(extra_info, '$.is_override') IS NOT NULL
                               THEN json_extract(extra_info, '$.is_override') ELSE 0 END,
            is_static = CASE WHEN json_extract(extra_info, '$.is_static') IS NOT NULL
                             THEN json_extract(extra_info, '$.is_static') ELSE 0 END,
            is_const = CASE WHEN json_extract(extra_info, '$.is_const') IS NOT NULL
                            THEN json_extract(extra_info, '$.is_const') ELSE 0 END,
            access = json_extract(extra_info, '$.access'),
            parent_class = json_extract(extra_info, '$.parent_class'),
            signature = json_extract(extra_info, '$.signature'),
            result_type = json_extract(extra_info, '$.result_type'),
            param_types = json_extract(extra_info, '$.param_types'),
            is_project = json_extract(extra_info, '$.is_project')
        WHERE type = 'function' AND extra_info IS NOT NULL
    """)

    # doc_section 字段
    conn.execute("""
        UPDATE node SET
            doc_title = json_extract(extra_info, '$.doc_title'),
            heading = json_extract(extra_info, '$.heading'),
            section_level = json_extract(extra_info, '$.section_level'),
            content_preview = json_extract(extra_info, '$.content_preview'),
            content_hash = json_extract(extra_info, '$.content_hash'),
            word_count = json_extract(extra_info, '$.word_count'),
            tags = json_extract(extra_info, '$.tags')
        WHERE type = 'doc_section' AND extra_info IS NOT NULL
    """)

    logger.info("node 表回填完成")


def _backfill_edge_columns(conn: sqlite3.Connection):
    """回填 edge 表新列：从 extra_info JSON 提取值写入对应列"""
    # calls_direct / calls_virtual 字段
    conn.execute("""
        UPDATE edge SET
            callee_name = json_extract(extra_info, '$.callee_name'),
            callee_namespace = json_extract(extra_info, '$.callee_namespace'),
            callee_parent_class = json_extract(extra_info, '$.callee_parent_class'),
            callee_file = json_extract(extra_info, '$.callee_file'),
            callee_param_types = json_extract(extra_info, '$.callee_param_types'),
            callee_is_const = CASE WHEN json_extract(extra_info, '$.callee_is_const') IS NOT NULL
                                   THEN json_extract(extra_info, '$.callee_is_const') ELSE 0 END,
            call_type = json_extract(extra_info, '$.call_type'),
            needs_resolution = CASE WHEN json_extract(extra_info, '$._needs_resolution') IS NOT NULL
                                    THEN json_extract(extra_info, '$._needs_resolution') ELSE 0 END,
            resolve_hint = json_extract(extra_info, '$._resolve_hint')
        WHERE relation_type IN ('calls_direct', 'calls_virtual') AND extra_info IS NOT NULL
    """)

    # overrides 字段
    conn.execute("""
        UPDATE edge SET
            function_name = json_extract(extra_info, '$.function_name'),
            derived_class = json_extract(extra_info, '$.derived_class'),
            base_namespace = json_extract(extra_info, '$.base_namespace'),
            needs_resolution = CASE WHEN json_extract(extra_info, '$._needs_resolution') IS NOT NULL
                                    THEN json_extract(extra_info, '$._needs_resolution') ELSE 0 END,
            resolve_hint = json_extract(extra_info, '$._resolve_hint')
        WHERE relation_type = 'overrides' AND extra_info IS NOT NULL
    """)

    # type_alias 字段
    conn.execute("""
        UPDATE edge SET
            alias_name = json_extract(extra_info, '$.alias_name'),
            target_type = json_extract(extra_info, '$.target_type'),
            target_simple_name = json_extract(extra_info, '$.target_simple_name'),
            needs_resolution = CASE WHEN json_extract(extra_info, '$._needs_resolution') IS NOT NULL
                                    THEN json_extract(extra_info, '$._needs_resolution') ELSE 0 END,
            resolve_hint = json_extract(extra_info, '$._resolve_hint')
        WHERE relation_type = 'type_alias' AND extra_info IS NOT NULL
    """)

    # doc 关系字段
    conn.execute("""
        UPDATE edge SET
            confidence = json_extract(extra_info, '$.confidence'),
            match_method = json_extract(extra_info, '$.method'),
            matched_name = json_extract(extra_info, '$.matched_name'),
            code_type = json_extract(extra_info, '$.code_type'),
            link_text = json_extract(extra_info, '$.link_text')
        WHERE relation_type IN ('doc_describes_code', 'code_refers_to_doc')
              AND extra_info IS NOT NULL
    """)

    # belongs_to 字段（access）
    conn.execute("""
        UPDATE edge SET
            access = json_extract(extra_info, '$.access')
        WHERE relation_type = 'belongs_to' AND extra_info IS NOT NULL
    """)

    logger.info("edge 表回填完成")


def _create_indexes(conn: sqlite3.Connection):
    """创建新索引（IF NOT EXISTS 保证幂等）"""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_node_is_virtual ON node(is_virtual) WHERE is_virtual = 1",
        "CREATE INDEX IF NOT EXISTS idx_node_doc_title ON node(doc_title)",
        "CREATE INDEX IF NOT EXISTS idx_node_parent_class ON node(parent_class)",
        "CREATE INDEX IF NOT EXISTS idx_node_is_project ON node(is_project) WHERE is_project IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_edge_callee_name ON edge(callee_name)",
        "CREATE INDEX IF NOT EXISTS idx_edge_needs_resolution ON edge(needs_resolution) WHERE needs_resolution = 1",
    ]
    for idx_sql in indexes:
        conn.execute(idx_sql)
    logger.info("新索引创建完成")


# ── 列定义（与 schema.sql 保持同步） ──

_NODE_COLUMNS = [
    # class/struct 专用
    ("is_abstract",       "INTEGER DEFAULT 0"),
    ("is_template_spec",  "INTEGER DEFAULT 0"),
    ("is_type_alias",     "INTEGER DEFAULT 0"),
    ("is_typedef",        "INTEGER DEFAULT 0"),
    ("template_params",   "TEXT"),
    ("target_type",       "TEXT"),
    # function 专用
    ("is_virtual",        "INTEGER DEFAULT 0"),
    ("is_pure_virtual",   "INTEGER DEFAULT 0"),
    ("is_override",       "INTEGER DEFAULT 0"),
    ("is_static",         "INTEGER DEFAULT 0"),
    ("is_const",          "INTEGER DEFAULT 0"),
    ("access",            "TEXT"),
    ("parent_class",      "TEXT"),
    ("signature",         "TEXT"),
    ("result_type",       "TEXT"),
    ("param_types",       "TEXT"),
    ("is_project",        "INTEGER"),
    # doc_section 专用
    ("doc_title",         "TEXT"),
    ("heading",           "TEXT"),
    ("section_level",     "INTEGER"),
    ("content_preview",   "TEXT"),
    ("content_hash",      "TEXT"),
    ("word_count",        "INTEGER"),
    ("tags",              "TEXT"),
]

_EDGE_COLUMNS = [
    # calls 专用
    ("callee_name",           "TEXT"),
    ("callee_namespace",      "TEXT"),
    ("callee_parent_class",   "TEXT"),
    ("callee_file",           "TEXT"),
    ("callee_param_types",    "TEXT"),
    ("callee_is_const",       "INTEGER DEFAULT 0"),
    ("call_type",             "TEXT"),
    # type_alias 专用
    ("alias_name",            "TEXT"),
    ("target_simple_name",    "TEXT"),
    ("target_type",           "TEXT"),
    # overrides 专用
    ("function_name",         "TEXT"),
    ("derived_class",         "TEXT"),
    ("base_namespace",        "TEXT"),
    # 解析状态
    ("needs_resolution",      "INTEGER DEFAULT 0"),
    ("resolve_hint",          "TEXT"),
    # doc 关系
    ("confidence",            "REAL"),
    ("match_method",          "TEXT"),
    ("matched_name",          "TEXT"),
    ("code_type",             "TEXT"),
    ("link_text",             "TEXT"),
    # belongs_to
    ("access",                "TEXT"),
]


def migrate_v2_to_v3(conn: sqlite3.Connection):
    """v2 → v3 迁移：将 extra_info JSON 拆入独立列

    Args:
        conn: SQLite 连接（调用方负责 commit/rollback）
    """
    cur_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur_ver >= SCHEMA_VERSION_TO:
        logger.info("DB schema 版本 %d >= %d，跳过迁移", cur_ver, SCHEMA_VERSION_TO)
        return

    if cur_ver < SCHEMA_VERSION_FROM:
        logger.warning("DB schema 版本 %d < %d，迁移可能不完整", cur_ver, SCHEMA_VERSION_FROM)

    logger.info("开始 v2 → v3 迁移...")

    # 1. 添加新列
    _add_columns(conn, "node", _NODE_COLUMNS)
    _add_columns(conn, "edge", _EDGE_COLUMNS)

    # 2. 回填数据
    _backfill_node_columns(conn)
    _backfill_edge_columns(conn)

    # 3. 创建新索引
    _create_indexes(conn)

    # 4. 更新 schema 版本
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION_TO}")

    logger.info("v2 → v3 迁移完成")


SCHEMA_VERSION_V4 = 4


def migrate_v3_to_v31(conn: sqlite3.Connection):
    """v3 → v4 迁移：清 needs_resolution 陈旧标记 + DROP extra_info 列

    P1-1：所有已解析成功的边（to_id 有效）仍残留 needs_resolution=1 陈旧标记，
          批量清零（逻辑修复已在 graph_db.import_parse_result 落地，此处清历史数据）。
    P1-2：双写过渡结束，列成为唯一数据源，DROP node/edge 的 extra_info 列。
          需 SQLite ≥ 3.35.0（DROP COLUMN 支持）。

    Args:
        conn: SQLite 连接（调用方负责 commit/rollback）
    """
    cur_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur_ver >= SCHEMA_VERSION_V4:
        logger.info("DB schema 版本 %d >= %d，跳过 v3→v4 迁移", cur_ver, SCHEMA_VERSION_V4)
        return

    if cur_ver < SCHEMA_VERSION_TO:
        logger.warning("DB schema 版本 %d < %d，应先执行 v2→v3 迁移", cur_ver, SCHEMA_VERSION_TO)

    logger.info("开始 v3 → v4 迁移...")

    # 1. P1-1：批量清零已解析边的陈旧 needs_resolution 标记
    cur = conn.execute("UPDATE edge SET needs_resolution = 0 WHERE needs_resolution = 1")
    logger.info("清理 needs_resolution 陈旧标记：%d 条边", cur.rowcount)

    # 2. P1-2：DROP extra_info 列（列已成唯一数据源）
    if _column_exists(conn, "node", "extra_info"):
        conn.execute("ALTER TABLE node DROP COLUMN extra_info")
        logger.info("已 DROP node.extra_info")
    if _column_exists(conn, "edge", "extra_info"):
        conn.execute("ALTER TABLE edge DROP COLUMN extra_info")
        logger.info("已 DROP edge.extra_info")

    # 3. 更新 schema 版本
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION_V4}")

    logger.info("v3 → v4 迁移完成")


def migrate(db_path: str):
    """便捷入口：打开 DB、执行全部迁移、提交关闭"""
    conn = sqlite3.connect(db_path)
    try:
        migrate_v2_to_v3(conn)
        migrate_v3_to_v31(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
