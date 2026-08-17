# feat-2026-08-17-batch2-context：调用点源码行文本 + 查询遥测

> 设计文档：docs/plan-2026-08-17-batch2-context.md

## 改动内容（2 项）

### 1. 调用点源码行文本（省掉查询后的 Read 往返）
- `mcp_server/server.py` 新增：
  - `_build_rel_abs_map` / `_resolve_src_path`：rel→abs 精确反查表，
    从 compile_commands.json 对每个 TU 套用与解析器相同的
    `make_relative_path`（子串截断不可简单逆向，同口径建表才精确）；
    source_paths 候选根（含 `app/` 变体）兜底
  - `_read_source_line`：读单行（截断 200 字符），mtime 缓存上限 64 文件
  - `_annotate_call_lines`：给 CallInfo 动态挂 `call_line_text`
- `_fmt_call_info` caller/callee 双分支展示 `调用点: ...`；
  无该属性（stub/解析失败）优雅降级不显示
- callers/callees 工具在格式化前调用 `_annotate_call_lines`

### 2. 查询遥测（空结果/耗时分析数据基础）
- `@_telemetry("tool_name")` 装饰全部 11 个 MCP 工具
  （`functools.wraps` 保留签名，FastMCP schema 不受影响）
- 记录 `ts/tool/args/n_results/duration_ms`，append 至 DB 同目录
  `query_telemetry.jsonl`（O_APPEND 单行原子写，多进程安全，
  不入图谱 DB 避免 SQLite 写锁竞争）
- n_results 取返回文本 `###` 标题数（零侵入近似计数），异常记 -1；
  遥测自身异常绝不影响查询

## 验证

1. `full_test.py`：formatter 10/10（新增 2 断言：带 call_line_text 显示、
   无属性 stub 不崩且无调用点行）+ 冒烟 11/11
2. 端到端（kill MCP 重启后）：
   - `cpp_get_callers("ExecuteDriveUpdate")` 输出
     `调用点: \`const bool du_ok = ExecuteDriveUpdate();\`` ✅
   - 空结果查询正常返回；`query_telemetry.jsonl` 两条记录字段齐全
     （n_results=1 / n_results=0 均正确）✅
   - 装饰器回归：`cpp_search_class`（含 exact 参数）/`cpp_get_overrides`
     schema 与输出正常 ✅

## 实施中发现

- **初版路径解析 bug**：source_paths 相对的是 `workspace/app` 而非
  `workspace`（compile_commands 所在目录），直接拼接全 miss。
  根因是解析器 `make_relative_path` 为子串截断（模式可在绝对路径任意位置
  匹配），改为 compile_commands 反查表（同口径）后精确命中。
