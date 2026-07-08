# 阶段 4-2：MCP 服务封装

## 目标

按 MCP 协议封装标准服务，暴露核心查询能力，设计完整的工具 Schema，让 AI 能准确判断何时调用哪个工具。

## 现状问题

- 查询接口已实现，但只能通过 CLI 或 Python API 调用
- AI 工具（Claude Code / Cursor）需要通过 MCP 协议访问图谱
- MCP 工具的 name/description/inputSchema 设计直接影响 AI 的调用准确率
- 需要定义 AI 调用策略：何时查图谱、何时降级为文件扫描

## 依赖

- 阶段 2-5：多跳遍历查询已实现
- 阶段 3-3：融合查询已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/mcp/__init__.py` | 新建，MCP 服务包 |
| `tools/cpp_semantic_graph/mcp/server.py` | 新建，MCP Server 主逻辑 |
| `tools/cpp_semantic_graph/mcp/tools.py` | 新建，MCP 工具 Schema 定义 |
| `tools/cpp_semantic_graph/mcp/config.yaml` | 新建，MCP 服务配置 |
| `docs/cpp_semantic_graph/mcp_integration.md` | 新建，MCP 接入文档 |

## 设计方案

### 1. MCP 工具 Schema 定义

| 工具名 | 描述 | 输入 Schema |
|--------|------|------------|
| `cpp_search_class` | 按类名搜索 C++ 类，返回类信息与文件位置。用于：找类在哪定义、类的基本信息。 | `{name: string, exact?: bool, with_docs?: bool}` |
| `cpp_search_function` | 按函数名搜索 C++ 函数，返回签名、所属类、文件位置。用于：找函数定义、查看签名。 | `{name: string, class?: string, with_docs?: bool}` |
| `cpp_get_inheritance` | 查询类的继承关系（含多级）。用于：查父类/子类、理解类层次。 | `{class_name: string, direction: "up"\|"down", depth?: int}` |
| `cpp_get_overrides` | 查询虚函数的所有重写实现。用于：查接口的所有实现、理解多态结构。 | `{function_name: string, class_name: string}` |
| `cpp_get_callers` | 查询谁调用了指定函数。用于：影响面分析、理解函数被谁依赖。 | `{function_name: string, class?: string}` |
| `cpp_get_callees` | 查询指定函数调用了谁。用于：理解函数内部逻辑、追踪调用链。 | `{function_name: string, class?: string}` |
| `cpp_get_file_symbols` | 查询文件内所有类与函数。用于：快速了解文件内容。 | `{file_path: string}` |
| `cpp_traverse_graph` | 多跳遍历查询，沿指定关系类型遍历图谱。用于：影响面分析、跨模块关联查询。 | `{start: string, relation_types: string[], direction: "outgoing"\|"incoming", depth?: int, filters?: object, max_results?: int}` |
| `cpp_search_docs` | 搜索文档，返回文档+关联代码。用于：查设计说明、找任务文档。 | `{keyword: string, tag?: string}` |

### 2. MCP Server 实现

```python
class CppSemanticGraphMCPServer:
    """MCP Server：封装 cpp-semantic-graph 的查询能力"""

    def __init__(self, db_path: str, config_path: str):
        self.graph_query = GraphQuery(db_path)
        self.fusion_query = FusionQuery(db_path)
        self.traverse = TraverseQuery(db_path)

    async def handle_tool_call(self, tool_name: str, params: dict) -> dict:
        """处理 MCP 工具调用"""
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        return await handler(params)
```

### 3. AI 调用策略

在工具的 `description` 中嵌入调用引导：

- **代码结构类问题**（"X 类在哪"、"X 的子类有哪些"）→ 优先 `cpp_search_class` / `cpp_get_inheritance`
- **调用链分析**（"谁调用了 X"）→ `cpp_get_callers` / `cpp_get_callees`
- **影响面分析**（"修改 X 会影响什么"）→ `cpp_traverse_graph`
- **查设计说明**（"X 的架构设计是什么"）→ `cpp_search_docs`
- **图谱无结果时**→ 降级为文件扫描

### 4. 适配主流 AI 工具

- **Claude Code**：配置 `.claude/settings.json` 中的 MCP server
- **Cursor**：配置 `.cursor/mcp.json`
- **其他**：输出标准 MCP 配置模板

## 验收标准

- [ ] MCP Server 可启动，9 个工具全部注册
- [ ] 每个工具有完整的 name / description / inputSchema
- [ ] Claude Code 通过 MCP 调用 `cpp_search_class("SocUpdate")` 返回正确结果
- [ ] 工具 description 清晰，AI 能准确判断何时调用哪个工具
- [ ] MCP 接入文档完整，包含配置方法和使用示例
- [ ] 错误处理：查询无结果时返回友好提示，不抛异常

## 风险点

1. **MCP 协议版本兼容**：不同 AI 工具可能使用不同版本的 MCP 协议，需确认兼容性
2. **工具数量过多**：9 个工具可能让 AI 难以选择，需在 description 中明确区分使用场景
3. **查询结果过大**：某些查询可能返回大量结果，需限制返回数量并支持分页

## 实施步骤

1. 编写 tools.py，定义 9 个 MCP 工具的 Schema
2. 编写 server.py，实现 MCP Server 主逻辑
3. 编写 config.yaml，配置服务参数
4. 集成到 Claude Code 测试
5. 编写 MCP 接入文档
6. 优化工具 description，测试 AI 调用准确率

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-24 | MCP Server 实现完成 | FastMCP + 9 个工具，已注册到 ~/.claude.json，Claude Code 自动加载可用；官方 MCP 客户端验证全部工具调用正确 |
