# 2026-07-27 综合修复：数据完整性 + 可靠性 + 性能 + 测试

## 概述

对 cpp_semantic_graph 项目进行全面审查后，分 4 个优先级修复了 10 项缺陷，
新增 48 项自动化回归测试。所有修复均通过验证，0 条未解析边，4087 条调用边正常解析。

---

## 第一优先：数据完整性

### 1. 消灭残留 LIKE 子串匹配（6 处）

**风险**：`LIKE '%ota.cpp%'` 会误匹配 `my_ota.cpp`，删除/解析时产生数据损坏。

| 位置 | 修改前 | 修改后 |
|------|--------|--------|
| `delete_by_source_file` (3 处) | `LIKE %path%` | `= ?` 精确匹配 |
| `delete_tu_data` 兜底 | `LIKE %rel%` | `= ?` 精确匹配 |
| `delete_file_completely` parse_status | `LIKE %path%` | `= ?` + 安全兜底 `LIKE %/path` |
| using_decl 边解析 | `namespace LIKE %base_class%` | Python `ns_tail == base_class` |
| 调用边 Level2 回退 | `namespace LIKE %parent%` | Python `ns_tail == callee_parent` |

### 2. 接入文档关联死代码

**问题**：`ingest_manual_associations()` 和 `ingest_rule_associations()` 从未被调用，
设计文档的 5 层关联策略只实现了 2 层。

**修复**：在 `pipeline.py` 和 `incremental_updater.py` 中追加调用。
现在 4/5 层已接入：manual → config → rule → content_scan（仅缺 embedding 实验性）。

### 3. 修正 README embedding 描述

**问题**：embedding 关联从未端到端验证，但 README 宣称可用。

**修复**：`README.md` + `README_zh.md` 标注为"实验性 — 未经验证"，说明默认不启用。

---

## 第二优先：运行可靠性

### 4. MCP 连接 close

**问题**：惰性增量后直接 `_gq = ... = None`，旧 SQLite 连接未 close → 连接泄漏。

**修复**：置 None 前先遍历 close 所有查询对象。

### 5. 并发构建锁

**问题**：MCP 惰性增量 + CLI 手动增量同时跑时 `database is locked`。

**修复**：
- `graph_db.py` 新增 `BuildLock` 类（fcntl.flock 排他锁）
- `IncrementalUpdater.run()` / `FullParsePipeline.run()` 改为 wrapper + BuildLock
- 并发时第二个调用方收到 RuntimeError，MCP 层捕获降级为 warning

### 6. 长事务拆分

**问题**：增量更新事务内调用 `_reparse_tus`（libclang 解析耗时数秒~数十秒），期间 SQLite 写锁不释放。

**修复**：将重解析移出事务，事务内只保留快速 SQL 操作（删旧 → 导入 → 清理）。
事务持锁时间从"数秒~数十秒"降到"毫秒级"。

---

## 第三优先：性能优化

### 7. N+1 查询消除（call_query / graph_query / traverse）

**问题**：边循环内逐个 `get_node_by_id`，N 条边产生 N×2~4 次 SQL 查询。

**修复**：
- `graph_db.py`：`get_edges_from`/`get_edges_to` JOIN 增加 `file_path` 列；新增 `get_nodes_by_ids` 批量查询
- `call_query.py`：`get_callers`/`get_callees`/`_expand_virtual_callers` 用 JOINed 边数据替代 `get_node_by_id`；`_get_owning_class` 简化为取 JOIN `to_name`；新增缓存版本
- `graph_query.py`：`get_inheritance` 引入节点缓存
- `traverse.py`：BFS/DFS 引入节点缓存

### 8. UPSERT + executemany

**问题**：全部逐行 INSERT + SELECT 检查存在性，N 个节点产生 2N~4N 条 SQL。

**修复**：
- `upsert_node`：INSERT+catch+UPDATE+SELECT → `INSERT ... ON CONFLICT DO UPDATE ... RETURNING id`（1 SQL）
- `insert_edge`：同上
- `import_parse_result` 节点：逐行 SELECT → 批量 SELECT 预查 + upsert_node
- `import_parse_result` includes：逐行 INSERT → `executemany INSERT OR IGNORE`（1 SQL）

### 9. DB 备份文件清理

删除 7 个 `.bak`/`.v4copy` 文件（约 98MB）。.gitignore 已有排除规则。

### 10. 自动化回归测试

新增 `tests/test_regression.py`：10 组测试 36 个断言。

---

## 第四优先：深度优化

### 11. polymorphism_query.py N+1 查询消除

**问题**：与 call_query.py 同模式但未修复，8 处 N+1 查询。

**修复**：
- `_get_owning_class`：`get_edges_from` + `get_node_by_id` → 取 JOIN `to_name`（0 额外 SQL）
- `_collect_virtual_functions`：逐行 `get_node_by_id` → `from_type` 过滤 + `get_nodes_by_ids` 批量查询
- `_collect_overrides_recursive`：`get_node_by_id(base_func_id)` 每条边查 2 次 → 移出循环查 1 次；owning_class 缓存
- `_find_overrides_of_func`：用 JOINed 数据减少 `get_node_by_id`
- `_find_function_node`：逐行 `get_node_by_id` → 用 JOIN `from_name`（完全消除循环内查询）
- `_get_ancestor_classes`：逐行 `get_node_by_id` → 用 JOIN `to_name`
- `_get_all_descendants`：逐行 `get_node_by_id` → 用 JOIN `from_name`/`from_namespace`/`from_file_path`
- `_is_abstract_class`：N 次 `get_node_by_id` + Python 过滤 → 单条 JOIN SQL

### 12. 回归测试补充

多态查询测试组：`get_virtual_functions` / `get_all_overrides` / `get_all_implementations`，
验证 N+1 修复后字段完整性。测试总数从 36 → 48。

---

## 文件变更清单

| 文件 | 修改内容 |
|------|----------|
| `db/graph_db.py` | LIKE→精确匹配(6处); BuildLock类; edge JOIN+file_path; get_nodes_by_ids; upsert_node/insert_edge UPSERT; import_parse_result 批量预查+executemany |
| `mcp_server/server.py` | 连接 close 后再置 None |
| `incremental_updater.py` | BuildLock wrapper; 事务拆分; 接入 manual+rule 关联 |
| `pipeline.py` | BuildLock wrapper; 接入 manual+rule 关联 |
| `query/call_query.py` | N+1 消除: JOINed数据+缓存+owning_class简化 |
| `query/graph_query.py` | N+1 消除: get_inheritance 节点缓存 |
| `query/traverse.py` | N+1 消除: BFS/DFS 节点缓存 |
| `query/polymorphism_query.py` | N+1 消除: 8处优化(JOIN+缓存+批量+单SQL) |
| `README.md` / `README_zh.md` | embedding 标注为实验性 |
| `tests/test_regression.py` | 新增: 48 项回归测试 |
| `changelogs/fix-2026-07-27-comprehensive.md` | 本文件 |

## 验证结果

- 48/48 回归测试通过
- 所有模块导入正常
- 0 条未解析边
- 4087 条调用边正常解析
- BuildLock 并发阻塞测试通过
- UPSERT 重复导入不创建重复行
- 删除方法精确匹配无误删
