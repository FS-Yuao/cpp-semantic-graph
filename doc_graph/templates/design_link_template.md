---
doc_id: <kebab-case-文档名>                     # 如 partition-switch-design
type: design                                    # design | link
status: 定稿                                     # 定稿 / 草稿 / 已归档
date: YYYY-MM-DD
tags: [架构设计, <主题>]                        # 如 [架构设计, A/B分区]
relates_to:                                     # 关联文档（补充 > 引用块里没有的）
  - <关联文档-doc_id>
---

# <文档标题>

> **文档定位**：<一句话说明本文讲什么>
>
> **关联文档**：
> - [链路文档](xxx_LINK.md) = 协议链路级
> - [分析文档](xxx_ANALYSIS.md) = 异常处理级
> - [任务文档](task/xxx_task.md) = 实现任务

## 1. 目标与核心结论
<!-- 本文档要解决什么问题，核心结论是什么 -->

## 2. 详细设计
<!-- 具体设计内容。代码符号用反引号：`ClassName::Method` -->

## 3. 关联分析
<!-- 与其他文档/模块的关系 -->

## 4. 风险与约束
<!-- 注意事项 -->

<!--
编写规则：
1. doc_id：全局唯一，kebab-case
2. type：design（讲"为什么这么设计"）或 link（讲"具体怎么做"）
3. > 引用块里的 markdown 链接 = relates_to 边（解析器自动提取，标 auto）
4. frontmatter relates_to 只补充 > 引用块里**没有**的关联（标 manual）
5. ## 编号. 标题 格式保留——解析器提取标题作为知识点
6. 正文代码符号用反引号：`ClassName::Method`
7. 这类文档结构已经很好，迁移时**几乎零改动**——主要就是加 frontmatter
-->
