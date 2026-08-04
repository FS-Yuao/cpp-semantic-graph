---
doc_id: diary-YYYY-MM-DD
type: diary
date: YYYY-MM-DD
summary: <一句话概括当天主要发现/决策>             # 必填！精炼总结，10-100字
tags: [主题1, 主题2]                         # 当天主要主题
---

# YYYY-MM-DD 会话日记

## <知识点标题，一句话概括发现/决策>

- **类型**: 根因分析                              # 根因分析 / 架构发现 / 决策记录 / 审查结论 / 约束规则 / 潜在缺陷
- **影响**: <这个问题影响了什么>
- **结论**: `<CodeSymbol>` 的什么逻辑导致了什么问题，需要怎么改   ← 代码符号必须反引号
- **关联**: `ClassName::Method`, `function_name`   ← 反引号=代码符号(建边)；无反引号=工具名(不建边)
- **会话**: <Claude session ID>

## <另一个知识点标题>

- **类型**: 架构发现
- **影响**: ...
- **结论**: ...
- **关联**: `SomeClass::DoSomething`
- **会话**: ...

<!--
编写规则：
1. doc_id 格式：diary-YYYY-MM-DD（与文件名一致）
2. 每条 ## 是独立知识点，携带自己的"关联"符号
3. "关联"字段：
   - 反引号包裹的 = 代码符号（解析器建 mentions_symbol 边）
   - 无反引号的 = 工具名/组件名（不建边，仅文本）
   - 示例：dutii, dumaster, ota_packer → 工具名，不加反引号
   - 示例：`parseIvcRxHeader`, `UpdateManager::StartUpdate` → 代码符号，必须反引号
4. "类型"字段值用于查询过滤：根因分析 / 架构发现 / 决策记录 / 审查结论 / 约束规则 / 潜在缺陷
-->
