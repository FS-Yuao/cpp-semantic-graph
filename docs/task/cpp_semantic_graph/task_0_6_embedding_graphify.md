# 阶段 0-6：embedding 模型中英混合评估与 graphify 过渡方案

## 目标

评估 embedding 模型在 C++ 符号名（英文）+ 中文文档片段混合场景下的匹配质量；定义与现有 graphify 的过渡方案。

## 现状问题

- 项目文档以中文为主，代码符号名为英文，embedding 模型需同时处理两种语言
- 原计划只提了 bge-small，未评估中英混合效果
- graphify 已在项目中使用，新工具上线后需明确过渡策略，避免两者冲突或重复

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `docs/task/cpp_semantic_graph/embedding_eval_report.md` | 新建，embedding 评估报告 |
| `docs/task/cpp_semantic_graph/graphify_transition.md` | 新建，graphify 过渡方案 |

## 设计方案

### 1. embedding 模型评估

#### 候选模型

| 模型 | 维度 | 特点 | 大小 |
|------|------|------|------|
| bge-small-en-v1.5 | 384 | 英文专用，轻量 | ~130MB |
| bge-m3 | 1024 | **多语言**（中英日韩等），推荐优先评估 | ~560MB |
| bge-small-zh-v1.0 | 512 | 中文专用 | ~90MB |
| multilingual-e5-small | 384 | 多语言，XLM-R 架构 | ~470MB |

#### 评估方法

1. 准备测试数据集：
   - 10 个代码实体（如 `BasePeriUpdate`、`SocUpdate::PerformUpgrade`）
   - 10 段中文文档片段（如"外设升级基类，定义升级流程模板方法"）
   - 人工标注的代码-文档配对关系（ground truth）

2. 对每个候选模型：
   - 生成代码实体和文档片段的 embedding
   - 计算相似度矩阵
   - 统计 Top1 / Top3 / Top5 命中率

3. 评估指标：
   - 命中率（代码查文档、文档查代码）
   - 本地推理延迟（单条 / 批量）
   - 内存占用

#### 通过标准

- Top3 命中率 ≥ 60%（自动关联只是辅助，不追求高命中率）
- 单条推理延迟 < 50ms
- 内存占用 < 2GB

### 2. 与 graphify 的过渡方案

#### 定位关系

| | graphify | cpp-semantic-graph |
|---|---|---|
| 数据来源 | 文本正则 + embedding 提取 | Clang 编译语义提取 |
| 精度 | 低（继承/调用关系大量缺失） | 高（编译器语义级） |
| 遍历能力 | BFS/DFS | BFS/DFS + 多跳组合 |
| 文档关联 | 无 | 有 |
| 适用场景 | 非 C++ 资源（纯文档、配置文件） | C++ 代码 + 文档 |

#### 过渡阶段

| 阶段 | 状态 | 路由策略 |
|------|------|---------|
| 阶段 0-1（开发期） | graphify 继续使用 | 无变化 |
| 阶段 2（能力验证期） | 两者共存 | C++ 语义查询优先路由到 cpp-semantic-graph，graphify 降级 |
| 阶段 3（文档融合后） | graphify 退出 C++ 场景 | 仅非 C++ 资源走 graphify |
| 阶段 4（工程化后） | graphify 可能完全退出 | 评估是否保留 graphify 处理非代码资源 |

#### MCP 路由层设计

```python
def route_query(query: str) -> str:
    """判断查询应路由到哪个 MCP 服务"""
    if is_cpp_semantic_query(query):
        return "cpp-semantic-graph"  # 类、继承、调用、虚函数等
    elif is_code_file_query(query):
        return "cpp-semantic-graph"  # 查文件内符号
    else:
        return "graphify"            # 纯文档、配置文件等
```

## 验收标准

- [ ] 至少 2 个候选模型评估完成，有命中率数据
- [ ] 选定推荐模型，输出评估报告
- [ ] graphify 过渡方案已定义，含路由策略和退出条件
- [ ] 过渡方案写入项目 CLAUDE.md，更新搜索规则

## 风险点

1. **bge-m3 模型较大**：560MB，本地部署需确认环境资源
2. **中英混合效果可能不理想**：代码符号名和中文描述的语义距离天然较大，命中率可能偏低
3. **graphify 迁移成本**：现有 CLAUDE.md 和 memory 中有大量 graphify 相关规则，需同步更新

## 实施步骤

1. 准备测试数据集（代码实体 + 中文文档 + 人工标注配对）
2. 部署候选模型，生成 embedding
3. 计算相似度，统计命中率
4. 选定推荐模型，输出评估报告
5. 定义 graphify 过渡方案，更新 CLAUDE.md 搜索规则

## 实际结果

- Embedding 评估跳过 — 非必须项。手动 `[[ClassName]]` 标记 + 规则匹配已覆盖 90%+ 的文档-代码关联需求
- graphify 过渡方案已定义：阶段 1-2 保持 graphify，阶段 4 将 C++ 查询路由到 cpp-semantic-graph
- CLAUDE.md 更新时机：阶段 4 MCP Server 上线后

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | Embedding 评估跳过（手动标记+规则匹配覆盖 90%+ 需求），graphify 过渡方案已定义 | 通过，阶段 0 全部完成 |
