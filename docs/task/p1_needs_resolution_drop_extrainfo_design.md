# P1 优化设计文档：needs_resolution 清理 + DROP extra_info

> 版本：v3 → v3.1 | 日期：2026-07-07 | 前置：[extra_info 列化拆分](extra_info_columnar_design.md)

## 1. 目标

在 v3 列化拆分基础上完成两项 P1 收尾优化：

- **P1-1**：批量清除已解析边的陈旧 `needs_resolution=1` 标记，使 partial index 恢复高选择度
- **P1-2**：DROP `extra_info` 列，消除双写冗余，DB 体积 14MB → ~8MB

## 2. 现状问题

### 2.1 P1-1：needs_resolution 全是陈旧标记

用 MCP 数据验证（semantic_graph_full.db）：

| relation_type | needs_resolution=1 | to_id 有效（已解析） |
|---|---|---|
| calls_direct | 2224 | 2224（100%） |
| calls_virtual | 103 | 103（100%） |
| overrides | 57 | 57（100%） |
| type_alias | 3 | 3（100%） |
| **合计** | **2387** | **2387（100%）** |

**根因**：`import_parse_result()`（graph_db.py:951）中，**未解析的边根本不入库**（"unresolved edge — store as pending"）。凡是入库的边，to_id 都已指向有效节点。但解析成功后 `extra_info._needs_resolution` 从未被清回 0，v3 回填时列继承了这个陈旧的 1。

**影响**：`idx_edge_needs_resolution` partial index 覆盖了 48.5% 的边（2387/4921），选择度极低，`needs_resolution=1` 查询仅 1.3× 加速。清除后 index 应覆盖 0 行，查询走空索引近乎瞬时。

### 2.2 P1-2：extra_info 双写冗余

- 当前双写：v3 列 + 完整 extra_info JSON blob
- extra_info 占用：node 1036.8KB + edge 833.1KB = 1869.9KB
- 已验证 **node extra_info 无任何未拆列的孤儿 key**；edge 仅剩 `call_line`（本就是独立列，JSON 中冗余）
- DROP 零数据丢失，DB 预计 14MB → ~8MB（-43%）

## 3. 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `db/migrate_v3.py` | 修改 | 新增 `migrate_v3_to_v31()`：清 needs_resolution + DROP extra_info |
| `db/graph_db.py` | 修改 | SCHEMA_VERSION 3→4；hydrate 纯列重建；停写 extra_info；resolve 逻辑改读列；upsert/insert 时清 _needs_resolution |
| `db/schema.sql` | 修改 | 移除 extra_info 列定义 |
| `query/graph_query.py` | 修改 | `_row_to_*` 不再依赖 row["extra_info"]，改读列（hydrate 已重建则透明） |
| `docs/result_reports/` | 新增 | P1 优化结果报告 |

## 4. 设计方案

### 4.1 P1-1：清除 needs_resolution（数据 + 逻辑双修）

**数据修复**（迁移脚本）：库中所有入库边都已解析，直接批量清零：

```sql
UPDATE edge SET needs_resolution = 0 WHERE needs_resolution = 1;
```

**逻辑修复**（防复发）：`import_parse_result()` 在成功解析 to_id 后（graph_db.py:942 `if to_id is not None`），插入前将 extra_info 的 `_needs_resolution` 置 False：

```python
if to_id is not None:
    if edge.extra_info:
        edge.extra_info["_needs_resolution"] = False  # 已解析，清标记
    ...
    edge_id = self.insert_edge(...)
```

这样 flatten 层写入 `needs_resolution=0`，未来重建库不再产生陈旧标记。

### 4.2 P1-2：DROP extra_info（hydrate 纯列重建）

**核心变更**：hydrate 从"列值合并到 JSON 上"改为"纯从列重建 dict"。

当前逻辑（v3）：
```
raw_extra = row["extra_info"]  →  parse JSON  →  列值覆盖同名 key
```

DROP 后（v3.1）：
```
extra = {}  →  遍历列，非 NULL 则按 col→key 映射写入 extra
（无 JSON 可读，完全由列驱动）
```

因已验证无孤儿 key，纯列重建与原 JSON 语义等价。

**停止双写**：`upsert_node()`/`insert_edge()` 移除 extra_info 列的 INSERT/UPDATE，仅写 v3 列。

**resolve 逻辑改列**：graph_db.py:895 `SELECT id, namespace, extra_info FROM node` 改为 `SELECT id, namespace, param_types FROM node`，直接读列比对重载参数（消除最后一处运行时 json.loads）。

**query 层**：`_row_to_class_info` 等仍读 `row["extra_info"]`——这些行来自 GraphDB 方法（已 hydrate），extra_info 字段由列重建，透明兼容。需验证是否有绕过 hydrate 直接读原始行的路径。

### 4.3 迁移安全

- **DROP 前备份**：迁移脚本执行 `cp semantic_graph_full.db semantic_graph_full.db.v3bak`
- **幂等**：检查 `PRAGMA user_version`，已是 4 则跳过
- SQLite 3.45.1 ✓ 支持 `ALTER TABLE ... DROP COLUMN`

## 5. 验收标准

| # | 标准 | 目标 |
|---|---|---|
| 1 | needs_resolution=1 边数 | 0 |
| 2 | extra_info 列存在性 | 已 DROP |
| 3 | DB 大小 | ≤9 MB |
| 4 | MCP 查询结果 | 与 v3 完全一致（抽样对比 class/function/doc/call/inheritance） |
| 5 | 运行时 json.loads 调用 | 仅剩 hydrate 中不再有（列重建无需 parse） |
| 6 | 全量重建库 | 新库 needs_resolution=1 边数为 0（逻辑修复生效） |
| 7 | 备份文件 | .db.v3bak 存在且可打开 |

## 6. 风险点

| 风险 | 缓解 |
|---|---|
| DROP 不可逆丢数据 | 已验证无孤儿 key；DROP 前 cp 备份 |
| query 层绕过 hydrate 直读原始行 | 审查所有 `row["extra_info"]` 来源，确认经 hydrate |
| hydrate 重建遗漏字段 | 抽样对比 v3bak 与新库的 hydrate 输出 dict |
| 迁移中断未提交 | 单事务 + 末尾 commit；失败回滚不影响 v3bak |
| 增量更新写入陈旧标记 | 逻辑修复在 import_parse_result 统一处理 |

## 7. 实施步骤

1. **备份**：cp semantic_graph_full.db → .db.v3bak
2. **改代码**：graph_db.py（hydrate 纯列重建 + 停写 extra_info + resolve 改列 + 清标记逻辑 + SCHEMA_VERSION=4）
3. **改迁移**：migrate_v3.py 新增 migrate_v3_to_v31()
4. **改 schema**：schema.sql 移除 extra_info
5. **改 query**：graph_query.py 审查 row["extra_info"] 来源
6. **执行迁移**：清 needs_resolution + DROP extra_info + user_version=4
7. **验证**：验收标准 1-7 逐条测；MCP 抽样对比 v3bak
8. **全量重建验证**：跑一次 full-parse，确认新库 needs_resolution=0
9. **Review**：MCP 工具验证影响面
10. **报告**：生成结果报告 + HTML
