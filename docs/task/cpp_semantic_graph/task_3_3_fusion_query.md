# 阶段 3-3：融合查询能力

## 目标

在代码查询和文档查询之间建立双向联动：查代码时带出设计说明，查文档时定位到代码实现，支持按标签过滤。

## 现状问题

- 代码和文档已分别入库并建立关联，但查询接口还没有融合
- AI 查 `SocUpdate` 时应该同时返回"升级流程设计"的文档片段
- AI 查"升级流程设计"文档时应该同时返回 SocUpdate 类和关键函数
- 多跳遍历需要支持文档节点，实现"文档 → 代码 → 继承/调用"的跨域遍历

## 依赖

- 阶段 3-1：文档切片已入库
- 阶段 3-2：代码-文档关联已建立
- 阶段 2-5：多跳遍历查询已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/query/fusion_query.py` | 新建，融合查询 |
| `tools/cpp_semantic_graph/query/doc_query.py` | 新建，文档查询 |
| `tools/cpp_semantic_graph/query/graph_query.py` | 修改，代码查询扩展返回文档片段 |

## 设计方案

### 1. 代码查询扩展（自动带出文档）

```python
def search_class_with_docs(self, name: str) -> ClassWithDocs:
    """查类时自动带出关联文档
    返回：
    - 类的基本信息（名称、命名空间、文件路径）
    - 继承关系
    - 关联的文档切片列表（按 confidence 排序）
    """
```

### 2. 文档查询接口

```python
def search_documentation(self, keyword: str, tag: str = None) -> list[DocWithCode]:
    """按关键词搜文档，返回文档+关联代码
    - keyword: 搜索文档标题和内容预览
    - tag: 按标签过滤（架构设计/开发任务/接口规约等）
    返回：
    - 文档切片信息（标题、文档路径、内容预览）
    - 关联的代码实体列表（类、函数，按 confidence 排序）
    """

def get_docs_for_class(self, class_name: str) -> list[DocSection]:
    """获取指定类关联的所有文档切片"""

def get_docs_for_function(self, func_name: str) -> list[DocSection]:
    """获取指定函数关联的所有文档切片"""
```

### 3. 多跳遍历融合

`traverse_graph` 支持文档节点参与遍历：

```python
# 示例：从文档出发，找到关联的代码，再沿继承链展开
traverse_graph(
    start="SocUpdate 升级流程设计",          # 文档节点
    relation_types=["doc_describes_code",     # 文档 → 代码
                    "inherits_public",         # 代码 → 子类
                    "calls_direct"],           # 代码 → 调用
    direction="outgoing",
    depth=3
)

# 示例：从代码出发，找到关联的文档，再找文档关联的其他代码
traverse_graph(
    start="BasePeriUpdate",
    relation_types=["code_refers_to_doc",      # 代码 → 文档
                    "doc_describes_code"],      # 文档 → 其他代码
    direction="outgoing",
    depth=2
)
```

### 4. 融合查询结果格式

```json
{
  "class": {
    "name": "SocUpdate",
    "namespace": "hq_ota",
    "file_path": "include/soc_update.h",
    "inheritance": {"parent": "BasePeriUpdate", "children": []}
  },
  "related_docs": [
    {
      "title": "SocUpdate 升级流程设计",
      "doc_path": "docs/OTA_flow/soc_upgrade_design.md",
      "content_preview": "SocUpdate 继承 BasePeriUpdate...",
      "confidence": 1.0,
      "method": "manual"
    }
  ]
}
```

## 验收标准

- [ ] `search_class_with_docs("SocUpdate")` 返回类信息 + 关联的架构设计文档片段
- [ ] `search_documentation("升级流程")` 返回文档切片 + 关联的代码实体
- [ ] `get_docs_for_class("BasePeriUpdate")` 返回所有关联文档
- [ ] 文档查询支持按标签过滤（tag 参数）
- [ ] 多跳遍历支持文档节点参与（`doc_describes_code` / `code_refers_to_doc` 关系类型）
- [ ] 融合查询结果格式清晰，代码信息和文档信息分区展示

## 风险点

1. **文档关联数量不均**：某些类可能关联大量文档，需限制返回数量并按 confidence 排序
2. **关键词搜索精度**：中文关键词搜索可能匹配到不相关的文档切片
3. **多跳遍历的文档路径爆炸**：文档节点的出度可能很大（一篇文档关联多个代码实体），需控制遍历深度

## 实施步骤

1. 编写 fusion_query.py，实现代码-文档融合查询
2. 编写 doc_query.py，实现文档独立查询
3. 修改 graph_query.py，代码查询扩展返回文档片段
4. 更新 traverse_graph 支持文档节点
5. 端到端验证：查代码带出文档、查文档定位代码

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
