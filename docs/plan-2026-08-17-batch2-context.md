# 设计文档：第二批改进（调用点源码行文本 + 查询遥测）

> 日期：2026-08-17
> 来源：插件评估第二批（原第 5、6 项）；第 7 项 rebuild_report 口径对齐需 30min 级全量重建，单独执行不并入本批

## 1. 目标

1. **调用点带源码行文本**：callers/callees 结果直接给出调用行代码，省掉 AI 每次查询后的 Read 往返
2. **查询遥测**：记录每次 MCP 查询的工具/参数/结果数/耗时（JSONL），定期分析空结果 → 指导覆盖范围与工具改进（借鉴 Sourcegraph Cody 查询遥测思路）

## 2. 现状问题

| # | 问题 | 证据 |
|---|------|------|
| 1 | callers 返回 `file:line` 后必须再 Read 一次才能看到调用上下文，高频查询 × 每次 1 次 Read 是最大使用摩擦 | 日常使用固定动作 |
| 2 | 无法知道哪些查询落空（符号不在图谱/参数写错/覆盖缺口），改进无数据依据 | 评估时发现，无采集机制 |

## 3. 设计方案

### 3.1 源码行文本（惰性读取，不改 schema、不全量重建）

- **路径解析**：DB 中 `file_path` 为相对路径（相对 source_paths 各根）。
  候选根 = `dirname(compile_commands)` + 每个 `source_path`（yaml 中即此约定）。
  绝对路径直接用。全部 miss 则优雅降级（不显示文本）。
- **读取缓存**：`{abs_path: (mtime, lines)}`，容量上限 64 条（LRU 简化为 FIFO 驱逐）；
  单次查询通常命中 <10 个文件，毫秒级。
- **注入方式**：查询结果返回前 `_annotate_call_lines(results)` 给 `CallInfo`
  动态 setattr `call_line_text`（dataclass 无 slots，可行）；`_fmt_call_info`
  若有该属性则追加 `调用点: <stripped 代码行>`（截断 200 字符）。
  formatter 纯函数性保持——无该属性/stub 对象时行为不变（已有单测兼容）。

### 3.2 查询遥测（JSONL append，独立于图谱 DB）

- **存储**：`<DB 同目录>/query_telemetry.jsonl`，每行一条 JSON。
  不入图谱 DB——MCP 多进程并发，避免 SQLite 写锁竞争；O_APPEND 单行 <4KB 原子写。
- **采集**：`@_telemetry` 装饰器（functools.wraps 保留签名/docstring，FastMCP
  schema 不受影响），记录 `ts/tool/args/n_results/duration_ms`。
  覆盖全部 11 个查询工具。装饰器内部 try/except 全包，遥测故障绝不影响查询。
- **n_results 语义**：工具各自定义（列表长度/结果计数），失败/参数错误记 -1。
- **消费**：暂只采集。分析脚本后续按需加（`jq` 即可起步）。

## 4. 改动文件清单

| 文件 | 改动 |
|------|------|
| `mcp_server/server.py` | `_src_roots`/`_read_source_line`/`_annotate_call_lines` 三个辅助 + `_fmt_call_info` 展示行文本；`_telemetry` 装饰器 + 11 个工具加装饰；`_fmt_call_info` 补 n_results 传递方式（装饰器读返回值标记） |
| `tests/full_test.py` | formatter 单测补 2 断言：带 call_line_text 显示、stub 无属性不崩 |

遥测 n_results 实现：装饰器约定工具函数通过返回字符串解析条数不可靠——改为
装饰器记录返回值长度 + 工具在成功路径 `globals` 不动，直接在装饰器里数
返回文本中的 `###` 标题数（近似结果数，够用且零侵入）。

## 5. 验收标准

1. `full_test.py` 全过（formatter 新断言含）；
2. 端到端：`cpp_get_callers("ExecuteDriveUpdate")` 输出含调用点源码行文本；
   `cpp_get_callers` 空结果查询（不存在的符号）正常返回不报错；
3. `query_telemetry.jsonl` 出现本次查询记录，字段齐全，MCP 多工具 schema 正常
   （端到端调用即验证）；
4. 系统级：冒烟 11 项全过。

## 6. 风险点

| 风险 | 评估 | 缓解 |
|------|------|------|
| 装饰器破坏 FastMCP schema | 低：wraps 拷贝签名 | 端到端全工具回归 |
| 惰性读文件慢查询 | 低：缓存 + 单行读 | 首查询计时验证 |
| JSONL 无限增长 | 低：文本行级，量小 | 后续按需 rotate |
| file_path 根解析 miss | 中：依赖 yaml 约定 | 优雅降级 + 端到端验证主流场景 |

## 7. 实施步骤

1. server.py：源码行文本三辅助 + formatter 展示
2. server.py：`_telemetry` 装饰器 + 11 工具覆盖
3. full_test.py 补断言
4. 全量测试 + kill MCP 端到端验收
5. changelog + 记忆
