# cppsg MCP 工具场景化打包设计（10→3，原子工具 deprecated 过渡）

- 日期：2026-09-03
- 状态：实施中
- 背景：search-stats 数据（原子工具 12 次/91% 成功）证明查询质量可用，但 agent 每次"10 选 1"是认知税；市场趋势为 MCP 工具精简（工具数 = agent 决策点数）。

## 目标

agent 的真实意图是**场景级**的。将 10 个原子工具打包为 3 个场景工具，每个决策点对应一种真实意图；原子工具保留（docstring 标 deprecated）一个遥测周期，usage 归零后删除。

## 场景工具设计

| 新工具 | 打包的原子工具 | 场景 | 输出 |
|---|---|---|---|
| `cpp_symbol_lookup(name, class_name="")` | search_class + search_function | "这个符号是什么、在哪" | 类/函数命中合并（含签名/文件/行号） |
| `cpp_relationship(name, direction="callers", class_name="", depth=1)` | get_callers + get_callees + get_overrides | "它和谁有关系" | 按 direction 分发（callers/callees/overrides），组合信息一次返回 |
| `cpp_impact_analysis(symbols, max_depth=2)` | blast_radius + traverse_graph + include_impact | "改它影响谁" | 影响面三视角合并（反向调用闭包/可达集/头文件波及） |

不做自然语言兜底工具（v1）：NL 解析歧义风险 > 收益；agent 表达意图用 3 个场景工具足够，长尾场景由 deprecated 原子工具兜底。

## 实现要点

1. 场景函数**直接调用现有原子 python 函数**（@mcp.tool 装饰后仍可本地调用），零查询逻辑重写；
2. 全部挂 `_telemetry` 装饰器，遥测名独立（`cpp_symbol_lookup` 等），与原子工具分开统计——迁移期数据对比的依据；
3. 原子工具 docstring 头部加 `[deprecated → cpp_xxx]`；不删除（兼容 + 兜底）；
4. 迁移判据：search-stats 连续 2 周原子工具 usage≈0 → 物理删除。

## 风险与回退

- 组合输出可能过长（blast_radius + traverse 双视图）→ 各子结果做条数截断（复用原子函数现有截断）；
- 回退：删 3 个场景函数即可，原子工具未动。
