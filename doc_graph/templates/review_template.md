---
doc_id: review-<主题>-<YYYY-MM-DD>              # 如 review-sm-integration-2026-06-01
type: review
status: 通过                                     # 通过 / 有条件通过 / 不通过
date: YYYY-MM-DD
tags: [审查, <模块名>]
relates_to:                                     # 必填：指向被审查的 task
  - <被审查的-task-doc_id>
code_symbols:                                   # 审查涉及的关键符号（3-5个）
  - ClassName::MethodName
---

# 审查报告：<标题>

## 审查范围
- 任务文档：[任务名](task/xxx.md)              ← 必须用 markdown 链接，不要用纯文本
- 变更文件：`ota_manager.cpp`, `sm_update_session_client.cpp`
- 审查日期：YYYY-MM-DD

## 发现问题

### [P0] <问题标题>
- **位置**：`ota_manager.cpp:402`
- **依据**：...
- **影响**：...
- **建议**：...

### [P1] <问题标题>
- **位置**：`sm_update_session_client.cpp:128`
- **依据**：...
- **影响**：...
- **建议**：...

### [P2] <问题标题>
- **位置**：...
- **依据**：...
- **影响**：...
- **建议**：...

## 审查结论
- **结论**：通过（P0 已修复，P1 全部关闭）
- **遗留**：P2 共 N 条，后续跟进

<!--
编写规则：
1. doc_id 格式：review-<主题>-<日期>
2. relates_to：**必须**指向被审查的 task 文档（这是 review → task 关联的核心边）
3. "审查范围"里的任务文档引用**必须用 markdown 链接** [文本](path.md)
   - 不要写纯文本 task/xxx.md（解析器不保证提取）
4. ### [P0] / ### [P1] / ### [P2] 标题格式：
   - 解析器提取 P0/P1/P2 作为知识点的 severity 属性
   - 查询「某次审查的 P0 问题」= type=review + 知识点 severity=P0
5. status：通过 / 有条件通过 / 不通过
   - 查询「审查结论」= type=review + status=通过/不通过
6. code_symbols：审查涉及的 3-5 个核心符号
-->
