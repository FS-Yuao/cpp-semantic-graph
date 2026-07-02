# 修复日志：调用边去重丢失多调用点信息

**日期**：2026-07-01  
**修复者**：华勤 OTA 底层团队  
**影响版本**：2026-06 全量索引及之前所有版本

---

## 问题描述

`cpp_get_callers` 和 `cpp_get_callees` 返回结果不完整——同一函数内多次调用同一目标时，只返回 1 条调用关系，丢失其余调用点信息。

**典型场景**：`OtaManager::UpdateThread` 内有 7 处调用 `HandleError()`（L241/251/568/602/637/689/761），但 `cpp_get_callers("HandleError")` 只返回 1 条 `UpdateThread → HandleError`，无法看到具体在哪些行号调用。

**根因**：两层去重逻辑均按 `(caller, callee)` 去重，不含调用行号：

1. **`parser/ast_visitor.py` `_deduplicate()`**：翻译单元内去重时，key 不含 `call_line`，同一 `(from_unique_key, callee)` 的多条边只保留第 1 条
2. **`db/schema.sql` edge 表 `UNIQUE(from_id, to_id, relation_type)`**：入库时同一对 caller→callee 只允许 1 条边

## 修复内容

### 1. `parser/ast_visitor.py` — `_deduplicate` 去重 key 加入 call_line

```python
# 修改前
key = (edge.from_unique_key,
       f"__unresolved__{callee_ns}::{callee_parent}::{callee}", rt)

# 修改后
call_line = edge.extra_info.get("call_line", 0)
key = (edge.from_unique_key,
       f"__unresolved__{callee_ns}::{callee_parent}::{callee}@{call_line}", rt)
```

已解析边（`to_unique_key` 非空）同样加入 `call_line`：
```python
# 修改前
key = (edge.from_unique_key, edge.to_unique_key, rt)
# 修改后
key = (edge.from_unique_key, edge.to_unique_key, rt, call_line)
```

### 2. `db/schema.sql` — edge 表新增 call_line 列

```sql
-- 修改前
UNIQUE(from_id, to_id, relation_type)

-- 修改后
call_line INTEGER DEFAULT 0,  -- 调用行号（0=非调用边或行号未知）
UNIQUE(from_id, to_id, relation_type, call_line)
```

新增索引：`CREATE INDEX IF NOT EXISTS idx_edge_call_line ON edge(call_line);`

### 3. `db/graph_db.py` — insert_edge 支持 call_line

- `insert_edge()` 新增 `call_line: int = 0` 参数，INSERT/UPDATE SQL 均含 `call_line`
- `import_parse_result()` 从 `edge.extra_info` 提取 `call_line` 传入 `insert_edge()`
- 内联建表 fallback（`_create_tables_inline`）同步更新 schema

### 4. `query/call_query.py` — get_callees 返回 caller_line

```python
# 修改前
caller_line=0,
# 修改后
caller_line=extra.get("call_line", 0) or edge.get("call_line", 0),
```

## 验证结果

### 整体指标

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| calls_direct 边 | 1,901 | 2,919 | +1,018 |
| calls_virtual 边 | 61 | 112 | +51 |
| **调用边总计** | **1,962** | **3,031** | **+1,069 (+54%)** |
| 节点数 | 5,362 | 4,718 | -644（正常波动，解析版本差异） |

### HandleError 验证（核心测试用例）

**修复前**：4 条边
```
ErrorHandler::HandleError @L17
OtaManager::Init          @L211
OtaManager::UpdateThread  @L533
OtaManager::HandleError   @L1172
```

**修复后**：9 条边
```
ErrorHandler::HandleError @L17
OtaManager::HandleError  @L1219
OtaManager::Init         @L241
OtaManager::Init         @L251
OtaManager::UpdateThread @L568
OtaManager::UpdateThread @L602
OtaManager::UpdateThread @L637
OtaManager::UpdateThread @L689
OtaManager::UpdateThread @L761
```

### 多调用点统计（修复前全部被合并为 1 条）

| 调用对 | 调用次数 |
|--------|---------|
| TryPrepare → GetInstance | 35 |
| Read → Instance | 33 |
| Read → ToHexString | 32 |
| Read → ParseReadResponseArray | 30 |
| operator<< → Append | 30 |

## 向后兼容性

- **DB schema 变更**：edge 表新增 `call_line` 列，UNIQUE 约束变更。**旧 DB 不兼容，需全量重建**
- **API 兼容**：`insert_edge()` 新增 `call_line` 参数有默认值 0，不影响现有调用
- **查询层兼容**：`CallInfo.caller_line` 原本就有此字段，只是之前 `get_callees` 写死 0，现在正确取值

## 重建索引

```bash
cd hq_ota_service/_tools
source cpp_semantic_graph_env/bin/activate
rm semantic_graph_full.db  # 删旧 DB
python3 -c "
import sys; sys.path.insert(0, '.')
from cpp_semantic_graph.pipeline import FullParsePipeline
p = FullParsePipeline('cpp_semantic_graph/cpp_semantic_graph.yaml')
p.run('cpp_semantic_graph/semantic_graph_full.db')
"
```

> **注意**：重建前需停掉 MCP server（持有 DB 连接会导致 `disk I/O error`）
