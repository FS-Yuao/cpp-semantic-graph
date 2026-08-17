# 设计文档：第一批易用性改进（参数统一 / namespace 过滤 / formatter 测试 / 失败 TU 自愈）

> 日期：2026-08-17
> 来源：插件评估（市场调研 + 使用实证）第一批 4 项，均低风险小改动

## 1. 目标

消除 AI 调用方使用摩擦与数据自愈盲区，全部改动保持向后兼容：
1. MCP 工具参数名统一（`function_name` → `name`，旧名保留为别名）
2. 调用/重写查询补 `namespace` 可选参数（消歧同名重载）
3. MCP 格式化层纳入 `full_test.py`（补测试盲区——2026-08-17 双写 bug 即此盲区漏网）
4. 增量更新支持重试历史失败 TU（`retry_failed`，消除假盲区——diag_key_store 教训）

## 2. 现状问题（均有实证）

| # | 问题 | 证据 |
|---|------|------|
| 1 | `cpp_get_callers/callees/overrides` 用 `function_name`，其余 6 个工具用 `name`，AI 首次调用常踩参数校验错 | 2026-08-17 会话实测报错 |
| 2 | 同名重载聚合返回：`FileExists` 查询混入自由函数版（16 vs 15）；测试脚本有 ns 过滤但 MCP 层没有该参数 | validation/full_validation_report.md 3.3 节注记 |
| 3 | `full_test.py` 直读 SQLite，MCP 格式化层零覆盖 | 2026-08-17 双写 bug（已修，但同类问题仍测不出） |
| 4 | `parse_status='failed'` 残留不自愈，形成假盲区 | diag_key_store.cpp 残留 2026-07-16 失败状态至 2026-08-17 |

## 3. 改动文件清单

| 文件 | 改动 |
|------|------|
| `query/call_query.py` | `_find_function_ids`/`get_callers`/`get_callees` 增加 `namespace` 参数（精确匹配 node.namespace） |
| `query/polymorphism_query.py` | `get_all_overrides`/`_find_function_node` 增加 `namespace` 参数（过滤基类节点） |
| `mcp_server/server.py` | 3 个工具签名：主参数 `name`，`function_name` 保留为别名（`name or function_name`）；3 个工具均加 `namespace: str = ""` |
| `incremental_updater.py` | `run()/_run_impl` 加 `retry_failed: bool = False`：查 `parse_status` failed 列表 → 并入 `detect_from_files`；report 加 `tus_retried` |
| `tests/full_test.py` | 新增第 0.5 节：formatter 层单测（`_qualified`/`_fmt_call_info`/`_fmt_override`，stub 对象断言输出） |

不改动：DB schema、解析器、其余 6 个工具签名、增量事务边界。

## 4. 设计方案要点

- **参数别名而非改名**：FastMCP 按 schema 校验，直接改名会破坏既有调用方；`name`/`function_name` 双可选参数 + `or` 合并，两者等价。
- **namespace 过滤语义**：精确匹配 `node.namespace`（成员函数形如 `update::FileHandler`），不做模糊，与测试脚本 `graph_callers(ns=)` 口径一致。
- **retry_failed 实现路径**：`GraphDB.get_all_parse_status()` 过滤 `status=='failed'` → 文件并入变更检测（标记 M）→ 走既有"删旧+重解析+upsert"事务流程，不新开代码路径（删除策略/事务边界复用）。
- **formatter 测试隔离**：`import cpp_semantic_graph.mcp_server.server` 需要 fastmcp，用 try/except ImportError 优雅跳过（无 fastmcp 环境仍可跑其余测试）。

## 5. 验收标准

1. `python3 tests/full_test.py` 全过，且新增 formatter 节 ≥6 断言全绿；
2. 端到端（kill MCP 重启后）：
   - `cpp_get_callers(name="FileExists", namespace="update::FileHandler")` 返回 15 条（不含自由函数版）；
   - `cpp_get_callers(function_name="ExecuteDriveUpdate")` 旧参数仍可用；
   - qualified name 无双写（回归昨修 bug）；
3. `retry_failed` 验证：人工将一个 TU 的 parse_status 置 failed → `run(files=[], retry_failed=True)`（或含变更的常规增量）→ 该 TU 翻绿入库；
4. 系统级指标：冒烟 11 项全过，节点/边总数无异常下降。

## 6. 风险点

| 风险 | 评估 | 缓解 |
|------|------|------|
| 工具签名变化导致 MCP schema 不兼容 | 低：全部新增可选参数，原调用方式不变 | 端到端回归旧参数 |
| retry_failed 把配置性排除（exclude_paths）的 TU 反复重试失败 | 低：detect_from_files 经 `_convert_abs_path`，路径不匹配配置会被过滤 | 略 |
| formatter 测试导入 server.py 的副作用（FastMCP 实例化） | 低：导入期不连 DB（惰性加载） | ImportError 时跳过 |

## 7. 实施步骤

1. call_query.py / polymorphism_query.py 加 namespace 过滤
2. server.py 三工具签名改造（name + function_name 别名 + namespace）
3. incremental_updater.py 加 retry_failed
4. tests/full_test.py 加 formatter 单测
5. 全量测试 + kill MCP 端到端验收
6. changelog + 记忆更新
