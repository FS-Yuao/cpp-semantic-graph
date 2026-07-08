# 阶段 1-2：SQLite 图谱库设计与入库脚本

## 目标

设计 SQLite 图谱库表结构，编写入库脚本，将 AST visitor 输出的标准化 JSON 批量写入数据库，建立可查询的持久化图谱。

## 现状问题

- AST visitor 输出的 JSON 是无结构的文件，无法直接查询
- 需要一个持久化的图谱存储，支持按类名、函数名、文件路径等条件快速检索
- 需要支持节点和边的关联查询（如"查类的继承关系"）

## 依赖

- 阶段 1-1：AST visitor 已输出标准化中间 JSON

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `db/schema.sql` | 新建，建表语句（node / edge / include_dep / parse_status + 索引） |
| `db/__init__.py` | 新建，数据库包，导出 GraphDB / Importer / RelationType |
| `db/graph_db.py` | 新建，数据库操作封装（CRUD + 批量导入 + 增量删除 + 统计） |
| `db/importer.py` | 新建，JSON → SQLite 入库脚本（CLI 入口 + 批量导入 + 进度输出） |
| `db/relation_types.py` | 新建，关系类型枚举独立模块（从 models.py 重导出 + 分类/查询工具方法） |

## 设计方案

### 表结构

```sql
-- 节点表：类、函数、文件、文档切片
CREATE TABLE node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,           -- class / function / file / doc_section
  name TEXT NOT NULL,
  namespace TEXT,
  file_path TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  extra_info JSON,              -- 模板参数、访问权限、是否抽象、签名等
  unique_key TEXT UNIQUE,       -- type|namespace|name|file_path 去重
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 边表：节点间的关系
CREATE TABLE edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id INTEGER NOT NULL,
  to_id INTEGER NOT NULL,
  relation_type TEXT NOT NULL,   -- 见 relation_types.py 枚举
  extra_info JSON,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(from_id) REFERENCES node(id),
  FOREIGN KEY(to_id) REFERENCES node(id),
  UNIQUE(from_id, to_id, relation_type)  -- 同一对节点同类型边不重复
);

-- include 依赖表
CREATE TABLE include_dep (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL,
  included_file TEXT NOT NULL,
  is_system BOOLEAN DEFAULT 0,
  UNIQUE(source_file, included_file)
);

-- 索引
CREATE INDEX idx_node_name ON node(name);
CREATE INDEX idx_node_type ON node(type);
CREATE INDEX idx_node_file_path ON node(file_path);
CREATE INDEX idx_node_unique_key ON node(unique_key);
CREATE INDEX idx_edge_from_id ON edge(from_id);
CREATE INDEX idx_edge_to_id ON edge(to_id);
CREATE INDEX idx_edge_relation_type ON edge(relation_type);
CREATE INDEX idx_include_source ON include_dep(source_file);
CREATE INDEX idx_include_included ON include_dep(included_file);
```

### 关系类型枚举

```python
class RelationType:
    # 继承关系（区分权限）
    INHERITS_PUBLIC = "inherits_public"
    INHERITS_PROTECTED = "inherits_protected"
    INHERITS_PRIVATE = "inherits_private"

    # 函数关系
    OVERRIDES = "overrides"           # 虚函数重写
    HIDES = "hides"                   # 同名函数隐藏（非 override）
    BELONGS_TO = "belongs_to"         # 函数/成员属于某个类

    # 调用关系
    CALLS_DIRECT = "calls_direct"     # 直接函数调用
    CALLS_VIRTUAL = "calls_virtual"   # 虚函数调用（通过指针/引用）
    CALLS_CALLBACK = "calls_callback" # 回调/函数对象调用

    # 模板关系
    INSTANTIATES = "instantiates"     # 模板实例化
    TYPE_ALIAS = "type_alias"         # using / typedef
    USING_DECL = "using_decl"         # using 声明

    # 其他
    FRIEND_OF = "friend_of"           # 友元

    # 文档关系（阶段 3）
    DOC_DESCRIBES_CODE = "doc_describes_code"
    CODE_REFERS_TO_DOC = "code_refers_to_doc"
```

### 入库流程

1. 读取 AST visitor 输出的 JSON 文件
2. 解析节点，按 `unique_key` 去重：
   - 已存在 → 更新 `extra_info` 和 `updated_at`
   - 不存在 → 插入新节点
3. 解析边，按 `(from_id, to_id, relation_type)` 去重：
   - 已存在 → 更新 `extra_info`
   - 不存在 → 插入新边
4. 解析 include 依赖，写入 `include_dep` 表
5. 输出导入统计：新增/更新/跳过的节点和边数量

### 去重与冲突处理

- 同一实体在多个翻译单元中出现：按 `unique_key` 合并，`extra_info` 中记录所有出现位置
- 同一条边可能出现多次（不同翻译单元提取到同一关系）：按 `UNIQUE(from_id, to_id, relation_type)` 去重
- 声明（头文件）与定义（源文件）的同一函数：通过 `unique_key`（含 file_path）区分，但用 `BELONGS_TO` 边关联

## 验收标准

- [x] 表结构可正常创建，索引完整
- [x] 入库脚本可批量导入 AST visitor 输出的 JSON
- [x] 去重逻辑正确：同一实体不会创建重复节点，同一关系不会创建重复边
- [x] 导入统计输出正确：新增/更新/跳过数量
- [ ] 10 万级节点查询耗时 < 10ms（有索引）（待大规模数据验证）
- [ ] 数据库文件大小合理（百万行项目 < 500MB）（待大规模数据验证）

## 风险点

1. **JSON extra_info 字段膨胀**：如果 extra_info 存储过多字段，查询性能可能下降
2. **批量导入性能**：单条插入 vs 批量插入，需做性能测试
3. **数据库文件锁**：多进程同时写入时需处理并发

## 实施步骤

1. 编写 schema.sql，创建表和索引
2. 编写 relation_types.py，定义关系类型枚举
3. 编写 graph_db.py，封装数据库操作
4. 编写 importer.py，实现 JSON → SQLite 入库
5. 用核心模块的 JSON 输出做端到端测试
6. 性能测试：10 万节点查询耗时

## 实际结果

- **5 个文件全部完成**：schema.sql / \_\_init\_\_.py / graph_db.py / importer.py / relation_types.py
- **表结构**：4 张表（node / edge / include_dep / parse_status）+ 12 个索引，含 parse_status 增量更新支持
- **GraphDB 封装**：节点 CRUD、边 CRUD、include 依赖查询、批量导入、增量删除、统计信息
- **Importer CLI**：
  - `python -m cpp_semantic_graph.db.importer <json_dir> -o <db_path>` — 批量导入
  - `python -m cpp_semantic_graph.db.importer --stats -o <db_path>` — 查看统计
  - 支持 `-v` 逐文件进度输出
- **去重验证通过**：
  - 首次导入：4 nodes_new, 3 edges_new, 3 includes_new
  - 重复导入：4 nodes_updated, 0 edges_new, 3 edges_skipped, 0 includes_new
- **查询验证通过**：SocUpdate → 继承边 → BasePeriUpdate，结果正确
- **设计变更**：增加了 `parse_status` 表（任务文档中未提及），用于增量更新时追踪翻译单元状态
- **RelationType 独立模块**：从 models.py 提取到 relation_types.py，新增分类查询方法（inherits_types / call_types / doc_types）和关系类型→类别映射表

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | SQLite 图谱库实现完成，5 文件全部通过端到端测试 | 通过，进入 Phase 1-3 |
