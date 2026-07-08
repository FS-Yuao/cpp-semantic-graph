# extra_info 列化拆分 — 设计文档

> 日期: 2026-07-07 | SCHEMA_VERSION: 2 → 3

## 目标

将 `node.extra_info` 和 `edge.extra_info` 的 JSON blob 拆分为独立列，实现：
1. **存储压缩**：消除 JSON key 重复 + 引号 + 括号开销（实测节省 ~72%）
2. **查询加速**：`json_extract()` → 直接列访问，可建索引，无需逐行解析 JSON
3. **类型安全**：布尔/整数不再存为 JSON 字符串，SQLite 原生类型约束

## 现状问题

| 指标 | 值 |
|---|---|
| DB 总大小 | 11.9 MB |
| extra_info 合计 | 2.2 MB（占 DB 18.3%） |
| Function node JSON→列式 | 节省 73.2% |
| Calls edge JSON→列式 | 节省 71.7% |
| `json_extract(doc_title LIKE)` | 218.8ms/100次（全表扫描+逐行解析） |
| `json_extract(is_virtual = 1)` | 8.2ms/100次（仍需逐行解析） |
| 所有 calls/overrides/type_alias 边 | 100% 标记 `_needs_resolution=true`（解析后应清除，但 JSON blob 难以局部更新） |

**核心痛点**：
- `json_extract()` 无法利用索引，每次查询全表扫描+逐行 parse JSON
- 查询层每个文件都有 `_parse_extra()` / `json.loads()` 样板代码（P2-3 只统一了 query 层，db/graph_db.py 仍有大量 json.loads）
- 局部更新某个字段（如清除 `_needs_resolution`）需读→改→写整个 JSON blob
- 存储冗余：每个 function 节点重复存 `"is_virtual": false, "is_pure_virtual": false, ...` 等 key 名

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `db/schema.sql` | 新增列 + 索引，extra_info 保留为 fallback |
| `db/graph_db.py` | 读写层适配：upsert/insert 写入新列，查询返回合并 dict |
| `db/migrate_v3.py` | **新增**：v2→v3 迁移脚本 |
| `parser/models.py` | NodeInfo/EdgeInfo 不变（extra_info dict 接口不变） |
| `parser/ast_visitor.py` | 无改动（仍写 extra_info dict，由 graph_db 层拆列） |
| `parser/doc_association.py` | 无改动 |
| `parser/association_ingester.py` | 读 extra_info 改为读列（或通过 graph_db 封装） |
| `query/doc_query.py` | `json_extract` → 直接列引用 |
| `query/polymorphism_query.py` | `is_virtual` → 直接列 |
| `query/query_utils.py` | `parse_extra` 简化或废弃 |
| `query/graph_query.py` | 行→dict 转换适配 |
| `incremental_updater.py` | 适配新列 |
| `tests/` | 迁移测试 + 查询正确性测试 |

## 设计方案

### 1. Schema 变更（SCHEMA_VERSION = 3）

**node 表新增列**（按 node_type 分组，NULL 表示该节点类型无此字段）：

```sql
-- class/struct 专用
ALTER TABLE node ADD COLUMN is_abstract INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_template_spec INTEGER DEFAULT 0;  -- is_template_specialization
ALTER TABLE node ADD COLUMN is_type_alias INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_typedef INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN template_params TEXT;                -- JSON array（低频，保留 JSON）
ALTER TABLE node ADD COLUMN target_type TEXT;                    -- alias 目标类型

-- function 专用
ALTER TABLE node ADD COLUMN is_virtual INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_pure_virtual INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_override INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_static INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN is_const INTEGER DEFAULT 0;
ALTER TABLE node ADD COLUMN access TEXT;                        -- public/protected/private
ALTER TABLE node ADD COLUMN parent_class TEXT;
ALTER TABLE node ADD COLUMN signature TEXT;
ALTER TABLE node ADD COLUMN result_type TEXT;
ALTER TABLE node ADD COLUMN param_types TEXT;                   -- JSON array（保留，重载区分用）
ALTER TABLE node ADD COLUMN is_project INTEGER;                 -- 是否项目代码（vs SDK/BSW）

-- doc_section 专用
ALTER TABLE node ADD COLUMN doc_title TEXT;
ALTER TABLE node ADD COLUMN heading TEXT;
ALTER TABLE node ADD COLUMN section_level INTEGER;
ALTER TABLE node ADD COLUMN content_preview TEXT;
ALTER TABLE node ADD COLUMN content_hash TEXT;
ALTER TABLE node ADD COLUMN word_count INTEGER;
ALTER TABLE node ADD COLUMN tags TEXT;                          -- JSON array（json_each 查询用）
```

**edge 表新增列**：

```sql
-- calls_direct/calls_virtual 专用
ALTER TABLE edge ADD COLUMN callee_name TEXT;
ALTER TABLE edge ADD COLUMN callee_namespace TEXT;
ALTER TABLE edge ADD COLUMN callee_parent_class TEXT;
ALTER TABLE edge ADD COLUMN callee_file TEXT;
ALTER TABLE edge ADD COLUMN callee_param_types TEXT;            -- JSON array
ALTER TABLE edge ADD COLUMN callee_is_const INTEGER DEFAULT 0;
ALTER TABLE edge ADD COLUMN call_type TEXT;                     -- direct/virtual/callback

-- overrides 专用
ALTER TABLE edge ADD COLUMN function_name TEXT;                 -- 被重写的虚函数名
ALTER TABLE edge ADD COLUMN derived_class TEXT;
ALTER TABLE edge ADD COLUMN base_namespace TEXT;

-- 解析状态（原 _needs_resolution / _resolve_hint）
ALTER TABLE edge ADD COLUMN needs_resolution INTEGER DEFAULT 0;
ALTER TABLE edge ADD COLUMN resolve_hint TEXT;

-- doc 关系专用
ALTER TABLE edge ADD COLUMN confidence REAL;
ALTER TABLE edge ADD COLUMN match_method TEXT;                  -- 原 method
ALTER TABLE edge ADD COLUMN matched_name TEXT;
ALTER TABLE edge ADD COLUMN code_type TEXT;
ALTER TABLE edge ADD COLUMN link_text TEXT;
```

**新增索引**：

```sql
CREATE INDEX IF NOT EXISTS idx_node_is_virtual ON node(is_virtual) WHERE is_virtual = 1;
CREATE INDEX IF NOT EXISTS idx_node_doc_title ON node(doc_title);
CREATE INDEX IF NOT EXISTS idx_node_parent_class ON node(parent_class);
CREATE INDEX IF NOT EXISTS idx_node_is_project ON node(is_project) WHERE is_project IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edge_callee_name ON edge(callee_name);
CREATE INDEX IF NOT EXISTS idx_edge_needs_resolution ON edge(needs_resolution) WHERE needs_resolution = 1;
```

### 2. extra_info 保留策略

- **写入时**：新列 + extra_info 都写（双写，过渡期兼容）
- **读取时**：优先读列，列值为 NULL 时 fallback 到 extra_info JSON
- **迁移后**：extra_info 列保留但不再写入新数据（未来版本可 DROP）
- **理由**：避免一次性破坏性变更，允许回退；parser 层零改动

### 3. graph_db.py 读写层适配

**写入（upsert_node / insert_edge / import_parse_result）**：
- 新增 `_flatten_node_extra(node) -> dict`：从 `node.extra_info` 提取已知 key → 返回列名→值映射
- 新增 `_flatten_edge_extra(edge) -> dict`：同上
- INSERT/UPDATE 语句加入新列
- `extra_info` 仍写入（双写）

**读取（get_node_by_id / find_node_by_name / get_edges_from 等）**：
- 返回 dict 时，将列值合并回 `extra_info` dict（对调用方透明）
- 新增 `_hydrate_node(row) -> dict`：列值 → extra_info dict（反向映射）
- 新增 `_hydrate_edge(row) -> dict`：同上

**关键：查询层和 MCP server 零改动**——它们仍通过 `row["extra_info"]` dict 取字段，只是这个 dict 现在由列值组装而非 JSON 解析。

### 4. 查询层优化

| 文件 | 原方式 | 新方式 |
|---|---|---|
| `doc_query.py` | `json_extract(extra_info, '$.doc_title') LIKE ?` | `doc_title LIKE ?` |
| `doc_query.py` | `json_each(json_extract(extra_info, '$.tags'))` | `json_each(tags)`（tags 仍为 JSON array，但无嵌套 extract） |
| `graph_db.py` override 回退 | `json_extract(n.extra_info, '$.is_virtual') = 1` | `n.is_virtual = 1` |
| `polymorphism_query.py` | `parse_extra(func_node["extra_info"])` | 直接读列或仍走 hydrate（透明） |

### 5. 迁移脚本 `db/migrate_v3.py`

```python
def migrate_v2_to_v3(conn):
    """v2 → v3: 将 extra_info JSON 拆入独立列"""
    # 1. ALTER TABLE ADD COLUMN（所有新列）
    # 2. UPDATE node SET is_virtual = json_extract(extra_info, '$.is_virtual'), ...
    #    WHERE type = 'function' AND extra_info IS NOT NULL
    # 3. UPDATE edge SET callee_name = json_extract(extra_info, '$.callee_name'), ...
    #    WHERE relation_type IN ('calls_direct', 'calls_virtual') AND extra_info IS NOT NULL
    # 4. CREATE INDEX（新索引）
    # 5. PRAGMA user_version = 3
```

- 幂等：重复执行不报错（ALTER TABLE IF NOT EXISTS 等效检查）
- 可回退：extra_info 仍保留完整数据，新列清零即可

### 6. _needs_resolution 清理优化

当前 100% 的 calls/overrides/type_alias 边标记 `_needs_resolution=true`，但已解析的边不应保留此标记。列化后：

```sql
-- 解析成功后直接 UPDATE
UPDATE edge SET needs_resolution = 0 WHERE id = ?
-- 比 JSON blob 读→改→写快得多，且可索引批量查询未解析边
```

## 验收标准

1. **存储**：迁移后 DB 大小减少 ≥10%（当前 11.9MB → 预期 ≤10.7MB）
2. **查询正确性**：全量 MCP 查询结果与迁移前完全一致（逐字段对比）
3. **查询性能**：`doc_title LIKE` 查询加速 ≥2x（当前 2.2ms/次 → 预期 ≤1ms/次）
4. **json_extract 消除**：query/ 和 db/ 中 `json_extract` 调用从 5 处降至 1 处（tags 的 json_each）
5. **迁移幂等**：重复执行 migrate_v3 不报错、不丢数据
6. **parser 零改动**：ast_visitor.py / doc_association.py 不变

## 风险点

| 风险 | 缓解 |
|---|---|
| SQLite ALTER TABLE 不支持 DROP COLUMN（3.35.0 前） | extra_info 保留不删，无破坏性 |
| 新列 NULL 语义：class 节点的 is_virtual 恒为 NULL | 查询时 `WHERE is_virtual = 1` 自动跳过 NULL，无需额外过滤 |
| 双写过渡期数据不一致 | 迁移脚本一次性回填；新写入双写保证一致 |
| param_types / tags 仍为 JSON array | 低频字段，保留 JSON 合理；json_each 仍可用但无嵌套 extract |
| association_ingester.py 直接读 extra_info | 改为通过 graph_db 封装读取，或迁移后 extra_info 仍有完整数据 |

## 实施步骤

1. **写迁移脚本** `db/migrate_v3.py`：ALTER + UPDATE 回填 + 索引
2. **改 schema.sql**：新增列定义 + 索引
3. **改 graph_db.py**：
   - `_flatten_node_extra()` / `_flatten_edge_extra()` 写入适配
   - `_hydrate_node()` / `_hydrate_edge()` 读取适配
   - `upsert_node` / `insert_edge` / `import_parse_result` 加入新列
   - SCHEMA_VERSION = 3
4. **改查询层**：`json_extract` → 直接列引用
5. **改 association_ingester.py**：extra_info 读取适配
6. **改 incremental_updater.py**：适配新列
7. **跑迁移**：对现有 DB 执行 migrate_v3
8. **全量验证**：MCP 查询结果对比 + DB 大小 + 查询性能基准
