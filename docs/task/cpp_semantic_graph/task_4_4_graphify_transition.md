# 阶段 4-4：graphify 过渡实施与项目集成

## 目标

实施与 graphify 的过渡方案，将新工具集成到项目 AI 工作流中，更新 CLAUDE.md 搜索规则，确保 graphify 在 C++ 场景下安全退出。

## 现状问题

- graphify 仍在使用，但 C++ 语义查询精度不够
- 新工具已具备完整查询能力，但还未接入项目 AI 工作流
- CLAUDE.md 和 memory 中有大量 graphify 相关规则，需同步更新
- 需要验证新工具在 C++ 场景下全面优于 graphify，才能安全切换

## 依赖

- 阶段 4-2：MCP Server 已封装
- 阶段 4-3：性能已优化
- 阶段 0-6：graphify 过渡方案已定义

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `CLAUDE.md` | 修改，更新搜索规则，优先使用 cpp-semantic-graph |
| `memory/mcp_search_examples.md` | 修改，更新 MCP 工具选择示例 |
| `memory/project_map.md` | 修改，更新项目地图 |
| `tools/cpp_semantic_graph/mcp/router.py` | 新建，MCP 路由层 |
| `docs/cpp_semantic_graph/graphify_migration_checklist.md` | 新建，迁移检查清单 |

## 设计方案

### 1. MCP 路由层

```python
class QueryRouter:
    """判断查询应路由到哪个 MCP 服务"""

    CPP_KEYWORDS = ["class", "inherit", "override", "virtual",
                     "function", "call", "template", "namespace"]

    def route(self, query: str, context: dict = None) -> str:
        """路由判断逻辑：
        1. 查询涉及 C++ 语义（类/继承/调用/虚函数）→ cpp-semantic-graph
        2. 查询涉及代码文件路径 → cpp-semantic-graph
        3. 查询涉及文档（设计说明/任务文档）→ cpp-semantic-graph
        4. 查询涉及非代码资源（配置文件/部署文档）→ graphify
        5. cpp-semantic-graph 无结果时 → 降级到 graphify
        """
```

### 2. CLAUDE.md 搜索规则更新

```markdown
## 搜索规则（必须遵守）

### 搜索决策树（按顺序判断，命中即停）
```
我要找什么？
├─ C++ 类/继承/虚函数   → cpp-semantic-graph: cpp_search_class / cpp_get_inheritance
├─ C++ 函数/调用关系    → cpp-semantic-graph: cpp_search_function / cpp_get_callers
├─ 代码文件内符号       → cpp-semantic-graph: cpp_get_file_symbols
├─ 影响面分析          → cpp-semantic-graph: cpp_traverse_graph
├─ 设计文档/任务文档    → cpp-semantic-graph: cpp_search_docs
├─ 函数签名/类型信息    → clangd get_type_info
├─ 非代码资源（配置等）  → graphify query_graph
├─ SDK/BSW 里的东西     → grep（MCP 覆盖不到）
└─ 读已知文件内容       → Read
```

### 降级规则
- cpp-semantic-graph 查询无结果 → 尝试 clangd → 尝试 graphify → grep
```

### 3. 迁移验证检查清单

| 检查项 | 验证方法 | 通过标准 |
|--------|---------|---------|
| C++ 类搜索 | `cpp_search_class("SocUpdate")` | 返回正确，含命名空间和文件路径 |
| 继承关系 | `cpp_get_inheritance("BasePeriUpdate", "down")` | 返回 4 个子类 |
| 调用关系 | `cpp_get_callers("GetSocBootChain")` | 返回调用方 |
| 文档关联 | `cpp_search_class("SocUpdate", with_docs=true)` | 返回关联文档 |
| 多跳遍历 | `cpp_traverse_graph("BasePeriUpdate", [...], depth=3)` | 返回完整影响面 |
| 查询性能 | P95 延迟 | < 50ms |
| 增量更新 | 修改文件后更新 | .cpp < 1s, .h < 5s |
| graphify 降级 | 搜索非 C++ 资源 | graphify 仍可用 |

### 4. memory 文件更新

- `mcp_search_examples.md`：更新工具选择示例，加入 cpp-semantic-graph 工具
- `project_map.md`：更新项目地图，加入图谱工具说明
- 删除或归档不再需要的 graphify 相关记忆

## 验收标准

- [ ] MCP 路由层可用：C++ 查询路由到 cpp-semantic-graph，非 C++ 走 graphify
- [ ] CLAUDE.md 搜索规则已更新，优先使用 cpp-semantic-graph
- [ ] 迁移验证检查清单全部通过
- [ ] graphify 在 C++ 场景下可安全降级
- [ ] memory 文件已同步更新
- [ ] 连续运行 7 天无崩溃，数据一致性正常

## 风险点

1. **切换期间的查询空档**：旧规则已删除、新规则未生效时，AI 可能不知查哪个工具
2. **graphify 依赖项**：某些 memory / CLAUDE.md 中的规则可能隐式依赖 graphify，需全面排查
3. **用户习惯切换**：团队成员可能习惯了 graphify 的查询方式，需同步培训

## 实施步骤

1. 编写 router.py，实现 MCP 路由层
2. 更新 CLAUDE.md 搜索规则
3. 更新 memory 文件（mcp_search_examples.md、project_map.md）
4. 执行迁移验证检查清单
5. 试运行 3 天，观察 AI 查询行为
6. 确认 graphify 可安全降级，完成迁移

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
