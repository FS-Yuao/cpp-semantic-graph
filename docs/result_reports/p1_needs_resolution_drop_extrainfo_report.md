# P1 优化 — needs_resolution 清理 + DROP extra_info 结果报告

> 迁移版本：v3 → v4 | 执行日期：2026-07-07 | DB：semantic_graph_full.db
> 设计文档：[p1_needs_resolution_drop_extrainfo_design.md](../task/p1_needs_resolution_drop_extrainfo_design.md)
> 前置报告：[extra_info 列化拆分 v2→v3](extra_info_columnar_migration_report.md)

## 1. 执行摘要

P1 收尾双写过渡：**列成为唯一数据源**。两项改动：

- **P1-1**：修复 `needs_resolution` 陈旧标记 bug——所有已成功解析（`to_id` 有效）的边仍残留 `needs_resolution=1`，标记从未在解析成功后清除。分两处修：`import_parse_result` 前向修复（防复发）+ 迁移批量回填（清历史）。
- **P1-2**：DROP `node.extra_info` / `edge.extra_info` 列，结束 v3 双写过渡，JSON blob 不再是数据源。

**核心成果**：

| 维度 | 改善 |
|---|---|
| needs_resolution 陈旧标记 | **2387 → 0**（迁移清理），全量重建 **0**（逻辑修复生效） |
| extra_info JSON blob | **1869.9 KB → 0**（node/edge 列彻底移除 JSON 冗余） |
| node+edge 表存储 | **2.95 MB → 2.21 MB（-24.9%）** |
| DB 总大小 | 11.89 MB（v2）→ 10.45 MB（v4，VACUUM 后） |
| 运行时 json.loads | 仅 hydrate 层从列重建，查询层不再解析 JSON 字符串 |
| 下游兼容 | hydrate 层透明——查询/MCP/parser 层零感知 |

---

## 2. needs_resolution 修复（P1-1）

### 2.1 根因

`import_parse_result` 解析调用边时，成功匹配到目标（`to_id` 有效）后**未清除** `_needs_resolution` 标记。历史库累积 **2387 条**「实际已解析、标记却仍为 pending」的假 pending 边，污染增量解析的待解析队列。

### 2.2 双重修复

| 位置 | 修复 | 作用 |
|---|---|---|
| `db/graph_db.py:962-966` | `to_id` 有效时置 `edge.extra_info["_needs_resolution"]=False` 再入库 | **前向修复**：新解析的边不再产生假 pending |
| `db/migrate_v3.py:305` | `UPDATE edge SET needs_resolution=0 WHERE needs_resolution=1` | **历史清理**：批量清 2387 条陈旧标记 |

### 2.3 系统级验证（验收标准 6）

全量重建（full-parse，306s 解析 + 40s 入库，2.4% TU 失败率）——**不是**只测迁移后的库，而是从零解析整个项目：

```
edges total=4151  needs_resolution=1: 0  (to_id NOT NULL: 4151)
user_version=4  node.extra_info=False  edge.extra_info=False
```

**结论**：全新库 4151 条边全部解析成功且 `needs_resolution=0`，证明前向修复在真实全量数据上生效、防复发——不依赖迁移回填。

---

## 3. DROP extra_info（P1-2）

### 3.1 移除的 JSON blob

| 表 | extra_info JSON 字节（v2） | DROP 后 |
|---|---|---|
| node | 1036.8 KB | 0 |
| edge | 833.1 KB | 0 |
| **合计** | **1869.9 KB** | **0** |

v3 双写期这 1.87 MB 与列数据并存（库一度膨胀到 14 MB）。v4 DROP 后列成为唯一数据源。

### 3.2 存储对比

| 表 | v2（bak_before_v3） | v4（current） | 压缩 |
|---|---|---|---|
| node | 1.84 MB | 1.62 MB | 12.1% |
| edge | 1.11 MB | 0.59 MB | **46.3%** |
| **node+edge** | **2.95 MB** | **2.21 MB** | **24.9%** |

edge 压缩显著（46%）：调用边的 `callee_name`/`callee_namespace`/`callee_param_types` 等字符串在 JSON 里有大量 key 名 + 引号开销，列化后仅存值。

### 3.3 关于 DB 总大小（验收标准 3 说明）

设计文档目标 ≤9 MB **未达成**，但根因与本次优化无关：

| 组件 | v4 占用 | 说明 |
|---|---|---|
| include_dep 表 + 索引 | **~7.2 MB** | 63097 条 include 依赖边（`include_dep` 2.94 + autoindex 2.86 + idx 1.36），与 extra_info 优化正交 |
| node + edge 表 | 2.21 MB | **本次优化目标，已 -24.9%** |
| 其他索引/元数据 | ~1.0 MB | — |

`include_dep` 是 v2→v3 之后新增的 include 依赖图数据（63097 边），当时 ≤9 MB 目标未预估此表。**本次优化范围内的 node+edge 表已达成 25% 压缩**，总库目标应重新校准为 node+edge 维度。

---

## 4. 查询性能（v4 列直读）

v4 列直读，无 `json_extract` 全表扫描（每查询 200 次取平均）：

| 查询 | 耗时 | 说明 |
|---|---|---|
| `needs_resolution=1`（部分索引） | 2.2 µs | 修复后命中空集，索引高效 |
| `callee_name=?`（索引） | 3.5 µs | 调用边解析热路径 |
| `is_virtual=1` | 24.2 µs | 全表布尔过滤 |
| `access=?`（node） | 186.9 µs | 全表字符串过滤（无索引，符合预期） |

---

## 5. 兼容性 & hydrate 层

v4 `extra_info` 列已 DROP，`_hydrate_node`/`_hydrate_edge` 从列纯重建 extra_info dict，下游查询层仍用 `d["extra_info"]["is_virtual"]` 取值，**零改动**。

**下游改动清单**（因列成为唯一源而修正的读取路径）：

| 文件 | 改动 | 类型 |
|---|---|---|
| `query/call_query.py` | 6 处 `call_line` 从 extra 改列直读 | **功能修复**（DROP 后 extra 无此值） |
| `query/graph_query.py` | get_file_symbols 走 `_hydrate_node` | 兼容 |
| `query/doc_query.py` | search_documentation 走 hydrate | 兼容 |
| `parser/doc_association.py` | 读 `content_preview` 列替代 JSON | 简化 |
| `parser/doc_ingester.py` | existing 已 hydrate，去 JSON 解析 | 简化 |

---

## 6. 验收标准核对

| # | 标准 | 目标 | 实测 | 结果 |
|---|---|---|---|---|
| 1 | needs_resolution=1 边数 | 0 | 2387→0 | ✅ |
| 2 | extra_info 列存在性 | 已 DROP | node/edge 均无 | ✅ |
| 3 | DB 大小 | ≤9 MB | 总库 10.45 MB / **node+edge 2.21 MB(-25%)** | ⚠️ 见 §3.3（include_dep 7.2 MB 正交） |
| 4 | MCP 查询与 v3 一致 | 完全一致 | callers/inheritance/overrides/traverse/docs 抽样 0 回归 | ✅ |
| 5 | 运行时 json.loads | 仅 hydrate | 查询层 `_parse_extra` 作用于已 hydrate dict | ✅ |
| 6 | 全量重建 needs_resolution | 0 | 全新库 4151 边全 0 | ✅ |
| 7 | 备份文件 | 存在可打开 | `bak_before_v3`（v2，extra_info 完整） | ✅ |

---

## 7. 备份说明

| 文件 | 版本 | extra_info | 用途 |
|---|---|---|---|
| `semantic_graph_full.db.bak_before_v3` | v2 | 完整 | **推荐回滚点**（原始数据完整，可重跑迁移） |
| `semantic_graph_full.db.bak_before_p0fix` | v0 | 完整 | 更早的 P0 修复前备份 |
| `semantic_graph_full.db.v4copy_20h15` | v4 | 无 | DROP 后副本（原误名 v3bak，已更正） |

> 「保留一版备份」由 `bak_before_v3` 满足——它保留全部原始 extra_info，优于 v3 中间态。

---

## 8. Review 结论（P0/P1/P2）

用静态分析 + grep 影响面交叉验证（cpp-semantic-graph 覆盖本项目 C++，但本次改动为 Python 工具链代码，用 grep/Read 核对）：

- **P0（阻断）**：无。
- **P1（应修）**：无遗留。`target_type` 数据丢失风险（DROP 前 3 条 type_alias 边的 target_type 在 extra_info 但缺列表）已在迁移前修复——补入 `_EDGE_V3_COLUMNS` + schema.sql + inline DDL + migrate_v3；同时发现 inline edge DDL 漏了 `alias_name`/`target_simple_name`，一并补齐。修复后 0 orphan key。
- **P2（次要）**：
  1. `migrate()` 便捷入口 DROP COLUMN 后未自动 VACUUM（主库已手动 VACUUM，14.3→10.4 MB）。auto-migrate 路径同样依赖调用方 VACUUM——建议文档注明。
  2. hydrate 省略 None 值列（v3 存显式 null），纯 cosmetic；交叉验证 node 56 + edge 2327 处差异全为此类，0 功能影响。

---

## 9. 三处 schema 一致性

fresh-build 路径的三个 schema 源已核对一致：

| 源 | node extra_info | edge extra_info | target_type |
|---|---|---|---|
| `db/schema.sql` | ✅ 无 | ✅ 无 | ✅ 有 |
| `_create_tables_inline()` | ✅ 无（33 列） | ✅ 无（27 列，含 alias_name/target_simple_name） | ✅ 有 |
| `_*_V3_COLUMNS` 列表 | ✅ 与 DB 全匹配 | ✅ 与 DB 全匹配 | ✅ 有 |

全量重建产出 `user_version=4`、无 extra_info、target_type 存在——三源一致性经真实构建验证。
