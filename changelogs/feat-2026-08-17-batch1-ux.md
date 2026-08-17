# feat-2026-08-17-batch1-ux：参数统一 / namespace 过滤 / formatter 测试 / 失败 TU 自愈

> 设计文档：docs/plan-2026-08-17-batch1-ux.md

## 改动内容（4 项）

### 1. MCP 工具参数名统一（向后兼容）
`cpp_get_callers` / `cpp_get_callees` / `cpp_get_overrides` 主参数改为 `name`
（与其余 6 个工具一致），旧参数 `function_name` 保留为别名（`name or function_name`）。

### 2. 调用/重写查询补 namespace 参数
- `query/call_query.py`：`_find_function_ids` / `get_callers` / `get_callees`
  增加 `namespace`（精确匹配 node.namespace，消歧同名重载，如自由函数版
  `FileExists` 不会再混入 `update::FileHandler` 版的结果）
- `query/polymorphism_query.py`：`get_all_overrides` / `_find_function_node`
  增加 `namespace`（过滤基类节点，消歧同名类）
- `mcp_server/server.py`：3 个工具均暴露 `namespace: str = ""`

### 3. formatter 层纳入 full_test.py
`tests/full_test.py` 新增 0.5 节：8 个纯函数断言
（`_qualified` 5 例 + `_fmt_call_info` caller/callee + `_fmt_override`），
含 2026-08-17 双写 bug 回归项。无 fastmcp 环境优雅跳过。
新增 `--formatter` 参数可单独跑。

### 4. 增量更新失败 TU 自愈
`IncrementalUpdater.run()` / `_run_impl()` 新增 `retry_failed: bool = False`：
查询 `parse_status` 中 `status='failed'` 的 TU，并入变更集（标记 M）走常规
"删旧+重解析+upsert"事务流程；报告新增 `tus_retried` 字段。
背景：diag_key_store.cpp 残留 2026-07-16 失败状态一个月形成假盲区。

## 验证

1. `full_test.py`：formatter 8/8 + 冒烟 11/11 全过
2. retry_failed 验收：人工注入 failed → `run(files=[], retry_failed=True)` →
   翻绿（23 节点/24 边），全库 failed=0
3. 端到端（kill MCP 重启后）：
   - `cpp_get_callers(name="FileExists", namespace="update::FileHandler")` →
     19 条全部正确、自由函数重载被排除 ✅
   - `cpp_get_callers(function_name="ExecuteDriveUpdate")` → 旧参数可用 ✅
   - `cpp_get_overrides(name="PerformUpdate", class_name="DeviceAdapter")` →
     4 个重写、无双写 ✅

## 过程发现（已修复）

- **连带回归**：retry 验收时传 `rebuild_associations=False`，重解析 16 个 TU 的
  文档关联边被删未重建（`code_refers_to_doc` 5927→3873）。用
  `run(doc_only=True, rebuild_associations=True)` 重建后恢复至 5979/6040
  （超基线，含新符号关联）。
- **防御性改进（已实施）**：`_run_impl` 中当 `tus_reparsed > 0` 且
  `rebuild_associations=False` 时强制覆盖为 True 并打 WARNING——"删旧"会连带
  删文档关联边，不重建即丢。验证：复现原场景（单 TU 重解析 + False），
  `associations_rebuilt=True`、关联边 5979 前后无丢失。
