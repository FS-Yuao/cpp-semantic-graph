---
doc_id: <kebab-case-任务名>                    # 全局唯一，如 gnss-version-check-fix
type: task
status: 待评审                                   # 待评审 / 进行中 / 已完成 / 已归档
date: YYYY-MM-DD
tags: [<主题标签>]                              # 如 [GNSS, 版本校验]
relates_to:                                     # 关联文档 doc_id（可选）
  - <前置任务-doc_id>
  - <关联链路-doc_id>
code_symbols:                                   # 本任务改动的核心符号（5-10个）
  - ClassName::MethodName
  - MACRO_NAME
---

# Task: <任务标题>

## 目标
<!-- 一句话说清楚要做什么 -->

## 现状问题
<!-- 为什么要做？当前有什么问题？ -->

## 改动文件清单
| 文件 | 改动内容 |
|------|----------|
| `src/xxx.cpp` | 修改 `FunctionName`，增加参数校验 |

## 设计方案
<!-- 具体怎么实现。代码符号必须反引号：`ClassName::Method` -->

## 验收标准
- [ ] 标准1
- [ ] 标准2
- [ ] 标准3

## 风险点
<!-- 注意事项 -->

## 实现步骤
1. ...
2. ...

## 审查记录
<!-- 审查者填写 -->

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |

<!--
编写规则：
1. doc_id：全局唯一，kebab-case（如 gnss-version-check-fix）
2. code_symbols：本任务**改动**的核心符号（不是文档里提到的所有符号）
   - 正文里反引号包裹的符号由解析器自动提取（标 auto）
   - frontmatter 里的符号是人工确认的（标 manual，查询优先）
3. relates_to：前置任务、依赖的链路文档
   - 用 doc_id 不用路径——文档移动不影响关联
4. status 驱动查询：「做过什么任务」= type=task + status=已完成
5. 正文代码符号必须反引号：`ClassName::Method` 或 `function_name`
6. 文档间引用必须用 markdown 链接：[文本](path.md)
-->
