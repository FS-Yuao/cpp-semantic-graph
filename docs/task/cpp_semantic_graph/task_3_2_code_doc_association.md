# 阶段 3-2：代码 ↔ 文档双向关联

## 目标

建立代码实体与文档切片的双向关联：手动标记（精准、100% 准确）为主，embedding 自动匹配（辅助、候选补全）为辅。

## 现状问题

- 代码和文档分别入库了，但两者没有关联
- AI 查代码时拿不到设计说明，查文档时定位不到代码实现
- 需要两种关联方式：手动精准关联（保证准确率）+ 自动语义关联（扩大覆盖面）

## 依赖

- 阶段 3-1：文档切片已入库
- 阶段 0-6：embedding 模型已选定
- 阶段 1-3：代码查询接口已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/doc_association.py` | 新建，手动标记解析 |
| `tools/cpp_semantic_graph/parser/embedding_association.py` | 新建，自动语义关联 |
| `tools/cpp_semantic_graph/parser/association_ingester.py` | 新建，关联边入库 |

## 设计方案

### 1. 手动精准关联（主）

在文档中用 `[[ClassName]]` 或 `[[FuncName]]` 标记，解析脚本自动建立关联边。

**标记语法**：

```markdown
## SocUpdate 升级流程设计

SocUpdate 继承 [[BasePeriUpdate]]，实现 SoC 固件升级。
升级流程调用 [[PerformUpgrade]] 方法，内部使用 [[ExecuteDriveUpdate]] 执行 driveupdate 工具。
```

**解析规则**：

```python
class DocAssociationParser:
    def parse_manual_links(self, doc_section: DocSection) -> list[Association]:
        """解析文档中的 [[...]] 标记
        - [[ClassName]] → 查找 node 表中匹配的类节点
        - [[FuncName]] → 查找 node 表中匹配的函数节点
        - 支持限定命名空间：[[hq_ota::SocUpdate]]
        - 未找到匹配节点时记录 warning，不创建关联边
        """
```

**关联边属性**：

```json
{
  "relation_type": "doc_describes_code",
  "from_id": "<doc_section节点ID>",
  "to_id": "<代码节点ID>",
  "extra_info": {
    "confidence": 1.0,
    "method": "manual",
    "link_text": "SocUpdate"
  }
}
```

反向边 `code_refers_to_doc` 自动创建（同一条关联的双向表达）。

### 2. 自动语义关联（辅）

用阶段 0-6 选定的 embedding 模型，为文档切片和代码实体生成向量，按相似度生成弱关联边。

```python
class EmbeddingAssociation:
    def compute_associations(self, doc_sections: list, code_nodes: list,
                             top_k: int = 3, threshold: float = 0.5) -> list[Association]:
        """计算文档-代码自动关联
        - 为每个文档切片找 Top-K 最相似的代码实体
        - 过滤掉相似度低于 threshold 的候选
        - 返回关联候选列表（需人工确认或规则二次确认）
        """
```

**关联边属性**：

```json
{
  "relation_type": "doc_describes_code",
  "from_id": "<doc_section节点ID>",
  "to_id": "<代码节点ID>",
  "extra_info": {
    "confidence": 0.72,
    "method": "embedding",
    "model": "bge-m3",
    "similarity_score": 0.72
  }
}
```

### 3. 关联确认流程

```
自动关联候选 → 规则二次确认 → 入库
                  ↓ 未确认
              输出候选列表供人工审核
```

规则二次确认逻辑：
- 代码实体名出现在文档内容中 → 自动确认（升级为高置信度）
- 文档标签与代码实体命名空间匹配 → 自动确认
- 其余 → 标记为 "pending_review"，输出候选列表

## 验收标准

- [ ] 手动标记 `[[ClassName]]` 正确解析，生成 `doc_describes_code` / `code_refers_to_doc` 边
- [ ] 手动关联准确率 100%（未找到匹配节点时记录 warning，不创建错误边）
- [ ] 自动关联 Top3 命中率 ≥ 70%（对比人工标注 ground truth）
- [ ] 自动关联的 confidence 字段正确，手动=1.0，自动=相似度分数
- [ ] 反向边 `code_refers_to_doc` 自动创建
- [ ] 关联确认流程可用：规则二次确认 + pending_review 候选输出

## 风险点

1. **标记语法歧义**：`[[Update]]` 可能匹配多个类（SocUpdate、GnssUpdate...），需支持命名空间限定
2. **embedding 模型中英混合效果**：C++ 符号名（英文）和中文文档的语义距离天然较大，命中率可能偏低
3. **自动关联的噪声**：低置信度关联可能误导 AI，需严格控制入库阈值

## 实施步骤

1. 编写 doc_association.py，实现 `[[...]]` 标记解析
2. 对核心文档（OTA_flow/、架构设计文档）添加手动标记
3. 编写 embedding_association.py，实现自动语义关联
4. 编写 association_ingester.py，实现关联边入库
5. 评估自动关联命中率，调整阈值
6. 实现关联确认流程

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
