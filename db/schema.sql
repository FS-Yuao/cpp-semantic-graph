-- SQLite 图谱库建表语句
-- 项目无关设计：表结构不绑定任何特定项目
-- schema 版本由 graph_db.py SCHEMA_VERSION 管理，写入 PRAGMA user_version（P2-6）
-- v3: extra_info JSON blob 拆入独立列（docs/extra_info_columnar_design.md）
-- v4: DROP extra_info 列，独立列成为唯一数据源（docs/task/p1_needs_resolution_drop_extrainfo_design.md）

-- 节点表：类、函数、文件、文档切片
CREATE TABLE IF NOT EXISTS node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,           -- class / struct / function / file / doc_section
  name TEXT NOT NULL,
  namespace TEXT DEFAULT '',
  file_path TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  unique_key TEXT NOT NULL UNIQUE,  -- type|namespace|name|file_path[|params[|c]]（function 含参数签名区分重载, E-3）
  -- v3: class/struct 专用列
  is_abstract INTEGER DEFAULT 0,
  is_template_spec INTEGER DEFAULT 0,    -- is_template_specialization
  is_type_alias INTEGER DEFAULT 0,
  is_typedef INTEGER DEFAULT 0,
  template_params TEXT,                  -- JSON array（低频，保留 JSON）
  target_type TEXT,                      -- alias 目标类型
  -- v3: function 专用列
  is_virtual INTEGER DEFAULT 0,
  is_pure_virtual INTEGER DEFAULT 0,
  is_override INTEGER DEFAULT 0,
  is_static INTEGER DEFAULT 0,
  is_const INTEGER DEFAULT 0,
  access TEXT,                           -- public/protected/private/invalid
  parent_class TEXT,
  signature TEXT,
  result_type TEXT,
  param_types TEXT,                      -- JSON array（重载区分用）
  is_project INTEGER,                    -- 是否项目代码（vs SDK/BSW），NULL=未知
  -- v3: doc_section 专用列
  doc_title TEXT,
  heading TEXT,
  section_level INTEGER,
  content_preview TEXT,
  content_hash TEXT,
  word_count INTEGER,
  tags TEXT,                             -- JSON array（json_each 查询用）
  -- 时间戳
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

-- 边表：节点间的关系
-- from_id = 关系起点, to_id = 关系终点
-- 方向约定: inherits → from=子类 to=父类, calls → from=调用方 to=被调用方
-- call_line: 调用行号，用于区分同一函数内多次调用同一目标的多个调用点
CREATE TABLE IF NOT EXISTS edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id INTEGER NOT NULL,
  to_id INTEGER NOT NULL,
  relation_type TEXT NOT NULL,   -- 见 models.RelationType 枚举
  call_line INTEGER DEFAULT 0,  -- 调用行号（0=非调用边或行号未知）
  -- v3: calls_direct/calls_virtual 专用列
  callee_name TEXT,
  callee_namespace TEXT,
  callee_parent_class TEXT,
  callee_file TEXT,
  callee_param_types TEXT,              -- JSON array
  callee_is_const INTEGER DEFAULT 0,
  call_type TEXT,                       -- direct/virtual/callback
  -- v3: type_alias 专用列
  alias_name TEXT,
  target_simple_name TEXT,
  target_type TEXT,                    -- alias 目标类型（完整限定名）
  -- v3: overrides 专用列
  function_name TEXT,                   -- 被重写的虚函数名
  derived_class TEXT,
  base_namespace TEXT,
  -- v3: 解析状态（原 _needs_resolution / _resolve_hint）
  needs_resolution INTEGER DEFAULT 0,
  resolve_hint TEXT,
  -- v3: doc 关系专用列
  confidence REAL,
  match_method TEXT,                    -- 原 method
  matched_name TEXT,
  code_type TEXT,
  link_text TEXT,
  -- v3: belongs_to / 其他
  access TEXT,                          -- 访问权限（belongs_to 边）
  -- 时间戳
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(from_id) REFERENCES node(id) ON DELETE CASCADE,
  FOREIGN KEY(to_id) REFERENCES node(id) ON DELETE CASCADE,
  UNIQUE(from_id, to_id, relation_type, call_line)
);

-- include 依赖表：支持增量更新时的翻译单元影响范围分析
CREATE TABLE IF NOT EXISTS include_dep (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL,     -- 翻译单元（.cpp）
  included_file TEXT NOT NULL,   -- 被 include 的文件（.h/.hpp）
  is_system INTEGER DEFAULT 0,  -- 是否系统头文件
  UNIQUE(source_file, included_file)
);

-- 解析状态表：记录每个翻译单元的解析状态，支持增量更新
CREATE TABLE IF NOT EXISTS parse_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending / success / partial / failed
  error_message TEXT,
  node_count INTEGER DEFAULT 0,
  edge_count INTEGER DEFAULT 0,
  parsed_at TEXT DEFAULT (datetime('now')),
  file_hash TEXT                -- 文件内容 hash，用于增量更新检测
);

-- 增量状态表：记录惰性增量进度（last_incremented_ref = 上次增量到的 commit）
-- task_4_5: MCP 惰性增量，rev-parse HEAD 比较 last_ref，有新 commit 才增量
CREATE TABLE IF NOT EXISTS incremental_state (
  key TEXT PRIMARY KEY,          -- 状态键（如 'last_incremented_ref'）
  value TEXT,                     -- 状态值（commit hash 等）
  updated_at TEXT DEFAULT (datetime('now'))
);

-- 索引
-- 原有索引
--   idx_node_unique_key    ← node.unique_key UNIQUE 自动建索引
--   idx_edge_from_id       ← edge UNIQUE(from_id,to_id,relation_type,call_line) 最左前缀覆盖
--   idx_include_source     ← include_dep UNIQUE(source_file,included_file) 最左前缀覆盖
--   idx_parse_status_file  ← parse_status.source_file UNIQUE 覆盖
-- 注：idx_edge_from_type(from_id, relation_type) 保留——UNIQUE 最左前缀是
--     (from_id, to_id, ...)，跳过 to_id 不匹配 (from_id, relation_type)，
--     查询 WHERE from_id=? AND relation_type=? 需要它
CREATE INDEX IF NOT EXISTS idx_node_name ON node(name);
CREATE INDEX IF NOT EXISTS idx_node_type ON node(type);
CREATE INDEX IF NOT EXISTS idx_node_file_path ON node(file_path);
CREATE INDEX IF NOT EXISTS idx_node_namespace ON node(namespace);
CREATE INDEX IF NOT EXISTS idx_edge_to_id ON edge(to_id);
CREATE INDEX IF NOT EXISTS idx_edge_relation_type ON edge(relation_type);
CREATE INDEX IF NOT EXISTS idx_edge_from_type ON edge(from_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edge_to_type ON edge(to_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edge_call_line ON edge(call_line);
CREATE INDEX IF NOT EXISTS idx_include_included ON include_dep(included_file);

-- v3: 新增索引（替代 json_extract 全表扫描）
CREATE INDEX IF NOT EXISTS idx_node_is_virtual ON node(is_virtual) WHERE is_virtual = 1;
CREATE INDEX IF NOT EXISTS idx_node_doc_title ON node(doc_title);
CREATE INDEX IF NOT EXISTS idx_node_parent_class ON node(parent_class);
CREATE INDEX IF NOT EXISTS idx_node_is_project ON node(is_project) WHERE is_project IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edge_callee_name ON edge(callee_name);
CREATE INDEX IF NOT EXISTS idx_edge_needs_resolution ON edge(needs_resolution) WHERE needs_resolution = 1;
