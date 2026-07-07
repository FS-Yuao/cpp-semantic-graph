-- SQLite 图谱库建表语句
-- 项目无关设计：表结构不绑定任何特定项目
-- schema 版本由 graph_db.py SCHEMA_VERSION 管理，写入 PRAGMA user_version（P2-6）

-- 节点表：类、函数、文件、文档切片
CREATE TABLE IF NOT EXISTS node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,           -- class / struct / function / file / doc_section
  name TEXT NOT NULL,
  namespace TEXT DEFAULT '',
  file_path TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  extra_info TEXT,              -- JSON: 模板参数、访问权限、签名等
  unique_key TEXT NOT NULL UNIQUE,  -- type|namespace|name|file_path[|params[|c]]（function 含参数签名区分重载, E-3）
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
  extra_info TEXT,               -- JSON
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

-- 索引
-- 索引（主题C：删除 4 个被 UNIQUE 约束自动索引覆盖的冗余索引）
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
