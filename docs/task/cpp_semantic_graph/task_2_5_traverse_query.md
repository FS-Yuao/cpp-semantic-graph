# 阶段 2-5：多跳遍历查询接口

## 目标

实现多跳遍历查询能力，支持从指定节点出发沿多种关系类型遍历 N 跳，支持路径过滤和组合查询。这是 AI 真正需要的能力——不只是单步查询，而是"从 A 出发沿继承+调用+文档遍历到 Z"。

## 现状问题

- 阶段 1 的查询都是单步的（查子类、查调用者），AI 需要多步查询时只能手动拼
- 典型需求："GetSocBootChain 被哪些子类的 DoUpdate 调用了？"需要先查调用者再过滤继承关系
- 典型需求："修改 BasePeriUpdate 会影响哪些模块？"需要沿继承+调用+文档多跳展开
- graphify 有 BFS/DFS 遍历但边不准，新工具需要精准遍历

## 依赖

- 阶段 2-1：继承链查询已实现
- 阶段 2-2：调用关系查询已实现
- 阶段 2-3：复杂场景已适配

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/query/traverse.py` | 新建，多跳遍历核心逻辑 |
| `tools/cpp_semantic_graph/query/path_filter.py` | 新建，路径过滤器 |

## 设计方案

### 核心接口

```python
def traverse_graph(
    start: str | list[str],          # 起始节点（类名/函数名/unique_key）
    relation_types: list[str],        # 遍历的关系类型
    direction: str = "outgoing",      # outgoing: 从 from 到 to / incoming: 反向
    depth: int = 3,                   # 最大遍历深度
    filters: dict = None,             # 过滤条件
    max_results: int = 100,           # 最大返回节点数
) -> TraverseResult:
    """多跳遍历查询

    示例 1：GetSocBootChain 被哪些子类的 DoUpdate 调用了？
    traverse_graph(
        start="GetSocBootChain",
        relation_types=["calls_direct", "calls_virtual"],
        direction="incoming",     # 反向：谁调用了 GetSocBootChain
        depth=1,
        filters={"function_name": "DoUpdate"}  # 只保留 DoUpdate
    )

    示例 2：修改 BasePeriUpdate 会影响哪些模块？
    traverse_graph(
        start="BasePeriUpdate",
        relation_types=["inherits_public", "inherits_protected",
                        "calls_direct", "calls_virtual",
                        "doc_describes_code"],
        direction="outgoing",
        depth=3
    )
    """
```

### 返回结构

```python
@dataclass
class TraverseResult:
    nodes: list[NodeInfo]         # 遍历到的所有节点
    edges: list[EdgeInfo]         # 遍历经过的所有边
    paths: list[Path]             # 从起点到每个节点的路径
    stats: TraverseStats          # 统计信息

@dataclass
class Path:
    start_key: str                # 起点 unique_key
    end_key: str                  # 终点 unique_key
    hop_count: int                # 跳数
    edges: list[EdgeInfo]         # 路径上的边序列

@dataclass
class TraverseStats:
    total_nodes_visited: int
    total_edges_traversed: int
    max_depth_reached: int
    truncated: bool               # 是否因 max_results 截断
```

### 路径过滤

```python
class PathFilter:
    """路径过滤条件"""

    def __init__(self,
                 node_types: list[str] = None,      # 只保留指定类型的节点
                 relation_types: list[str] = None,    # 只沿指定关系类型遍历
                 namespaces: list[str] = None,        # 只保留指定命名空间下的节点
                 name_pattern: str = None,            # 按名称模式过滤
                 function_name: str = None,           # 只保留指定函数名
                 class_name: str = None):             # 只保留指定类名
```

### 遍历算法

- **BFS**（默认）：逐层展开，适合"影响面分析"
- **DFS**：沿单条路径深入，适合"调用链追踪"
- **环路检测**：记录已访问节点，避免死循环
- **截断**：达到 `max_results` 或 `depth` 上限时停止

## 验收标准

- [ ] `traverse_graph` 支持 BFS/DFS 两种遍历模式
- [ ] 支持指定关系类型列表进行遍历（如只沿 inherits + calls 遍历）
- [ ] 支持路径过滤（按节点类型、命名空间、名称模式等）
- [ ] 支持反向遍历（direction="incoming"）
- [ ] 环路检测正确，不会死循环
- [ ] 3 跳以内遍历耗时 < 50ms
- [ ] 返回结构包含完整路径信息（节点序列 + 边序列）
- [ ] 示例查询结果正确："GetSocBootChain 的调用者中哪些是 BasePeriUpdate 子类的 DoUpdate"

## 风险点

1. **遍历爆炸**：某些节点（如基类）的出度可能很大，3 跳遍历可能产生数千个结果，需 max_results 截断
2. **环路检测开销**：记录已访问节点的 Set 操作在大规模遍历时有性能开销
3. **路径数量**：两个节点间可能有多条路径，需决定是返回所有路径还是只返回最短路径

## 实施步骤

1. 编写 traverse.py，实现 BFS/DFS 遍历核心逻辑
2. 编写 path_filter.py，实现路径过滤
3. 实现环路检测和截断逻辑
4. 用核心场景验证（影响面分析、调用链追踪）
5. 性能测试与优化

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
