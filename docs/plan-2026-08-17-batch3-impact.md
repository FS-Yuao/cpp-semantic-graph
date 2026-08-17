# 设计文档：第三批改进（include 影响面工具 + 遥测分析 + 空结果降级提示 + 全量重建）

> 日期：2026-08-17
> 来源：插件评估第三批；含第二批遗留第 7 项（rebuild_report 口径对齐）

## 1. 目标

1. **`cpp_get_include_impact` MCP 工具**：暴露 include_dep（68k 条）——
   "改这个头文件会影响哪些 TU"，review/影响面场景刚需
2. **遥测分析脚本**：消费 `query_telemetry.jsonl`，空结果 top / 工具使用分布
   /耗时分布，闭环第二批埋点
3. **空结果降级提示**：查询落空时提示"SDK/BSW 符号不在覆盖范围，建议 clangd"——
   把项目 CLAUDE.md 的搜索决策树固化进工具输出，减少 AI 误判"符号不存在"
4. **全量重建**（后台）：对齐 rebuild_report 口径、刷新 CLAUDE.md 覆盖数

## 2. 范围决策（重要）

**砍掉"MCP 进程单例化"**：MCP 是 stdio 协议，每个 IDE 会话必须各起一个进程
（进程间不能共享 stdio 连接），多进程是协议要求而非泄漏；且 server 进程只查
SQLite 不加载 libclang，单实例内存很轻。强行单例化会破坏多会话可用性。

## 3. 设计方案

### 3.1 include 影响面工具（薄封装，零新查询逻辑）

复用现成 `query/include_query.py IncludeQuery`：
- `get_all_includers(header_path)`：BFS 反向追溯（A include B、B include C →
  改 C 影响 A、B），部分匹配，已带 max_depth 防环
- MCP 层新增 `cpp_get_include_impact(header_path)`：懒初始化 `_iq`（与其他
  query 一致），输出受影响 TU 列表 + 计数；格式化按"直接/传递"不区分
  （IncludeQuery 已合并），提示"这些 TU 在下次增量更新时会重解析"

### 3.2 遥测分析脚本

`scripts/telemetry_stats.py [--file PATH] [--top N]`：
- 总量、时间范围；按工具聚合：调用数、空结果数、空结果率、平均/P95 耗时
- 空结果 top N 查询参数（识别覆盖缺口/高频误查）
- 纯 stdlib（json/collections），无依赖

### 3.3 空结果降级提示

5 个符号类工具（search_class/search_function/callers/callees/overrides）的
空结果返回追加固定提示行：
`提示: 本图谱只覆盖 <source_paths> 范围。SDK/BSW/foundation 符号请改用 clangd MCP 查询。`
不额外查 DB 判断（单凭名字无法判断是否 SDK 符号），纯静态文案，零成本。

### 3.4 全量重建（后台执行）

`FullParsePipeline.run(reset_db=True)` 重建 `semantic_graph_full.db`，
max_workers=8（yaml）。预期 ~6 分钟（167 TU）。完成后：
- 核对 rebuild_report 口径（edges_new vs db_edge_count 差异是否复现）
- 重建文档关联（pipeline 自带 associations_rebuilt）
- 更新项目 CLAUDE.md 覆盖数（TU 数/模块数）
- 重跑 full_test 冒烟
注意：重建期间 MCP 查询会读到部分数据（reset 后渐进写入），BuildLock 只挡
并发构建不挡查询——选择空闲时段、重建完成前避免依赖查询结论。

## 4. 改动文件清单

| 文件 | 改动 |
|------|------|
| `mcp_server/server.py` | `cpp_get_include_impact` 工具（+`_iq` 懒初始化、+`@_telemetry`）；5 个工具空结果加提示行 |
| `scripts/telemetry_stats.py` | 新建 |
| `workspace/CLAUDE.md` | 重建后更新覆盖数 |

## 5. 验收标准

1. `cpp_get_include_impact("base_device_update.h")` 返回全部 device_update 子类 TU；
   与已知继承体系一致（4 个子类模块的 TU 必须在内）
2. 遥测脚本对现有 jsonl 输出统计（含空结果 top）
3. 空结果查询输出含降级提示
4. 全量重建：tu_failed=0、冒烟全过、rebuild_report 与 DB 口径一致、
   CLAUDE.md 覆盖数更新
5. 系统级：full_test 全过

## 6. 风险点

| 风险 | 评估 | 缓解 |
|------|------|------|
| 重建期间查询读到部分数据 | 中 | 重建窗口短（~6min），完成后验证；提前告知 |
| include_dep 不含 header→header 边时 BFS 退化 | 低 | IncludeQuery 为增量更新生产使用，已验证 | 
| telemetry jsonl 尚无足够数据 | 无 | 脚本对空文件优雅输出 |

## 7. 实施步骤

1. server.py：include 工具 + 空结果提示
2. telemetry_stats.py
3. 本地测试 + kill MCP 端到端
4. 后台启动全量重建
5. 重建完成后：口径核对 + CLAUDE.md 更新 + 冒烟
6. changelog + 记忆
