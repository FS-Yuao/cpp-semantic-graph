# 文档结构化规则

> 本文档定义 7 类文档的编写规则、frontmatter 规范、旧文档迁移指南。
> **所有写文档的人必须遵守本规则。**

---

## 1. 文档分类

基于 `docs/` 下 91 个文档的实际内容，分为 7 类：

| 类型 | 目录 | 数量 | 现有结构特征 | 图谱价值 | 迁移优先级 |
|------|------|------|------------|---------|-----------|
| **diary** | `diary/` | 16 | `## 标题` + `- **类型/结论/关联/会话**` | ⭐⭐⭐ 含决策/根因/结论 | P0 |
| **task** | `task/` | 25 | `## 目标/现状问题/设计方案/验收标准...` | ⭐⭐⭐ 含改动和符号 | P0 |
| **review** | `review_reports/` | 6 | `## 审查范围/发现问题[P0/P1]/审查结论` | ⭐⭐⭐ 含问题分级 | P1 |
| **design** | `ADC4.0_System_architecture/`, `AB_Switch/` | ~8 | `> 引用块` + `## 编号. 标题` | ⭐⭐ 含关联文档链接 | P1 |
| **link** | `Doip_Uds/`, `OTA_flow/`, `du_mcc/`, `nvidia_switch_upgrade/` | ~12 | `> 引用块` + `## 编号. 标题` | ⭐⭐ 链路文档互相引用 | P2 |
| **requirement** | `origin_requier/` | 8 | 外部文档，格式不一 | ⭐ 不是我们写的 | P3（不强制） |
| **report/guide** | `Completion status/`, `Claude_Code_Graph/`, 散落 | ~16 | 各种格式 | ⭐ 参考性质 | P3（不强制） |

**优先级说明**：
- **P0**：图谱价值最高，必须尽快迁移。diary 含排障结论和决策记录，task 含改动和符号。
- **P1**：图谱价值高，第二批迁移。review 含问题分级，design 含架构关联。
- **P2**：图谱价值中等，第三批迁移。link 文档结构已好，主要加 frontmatter。
- **P3**：不强制迁移。解析器兼容模式处理，标 `legacy=True`。

---

## 2. 通用 frontmatter 规则

### 2.1 基本格式

**所有 P0/P1 类型文档必须带 frontmatter**（P2/P3 可选，不写则解析器兼容模式处理）：

```markdown
---
doc_id: gnss-version-check-fix          # 全局唯一，kebab-case（必填）
type: task                               # task | review | diary | design | link | requirement | report（必填）
status: 已完成                           # 待评审 / 进行中 / 已完成 / 已归档（task/review 必填，其他可选）
date: 2026-07-29                         # YYYY-MM-DD（必填，diary 可从文件名推断）
tags: [GNSS, 版本校验, 激活]             # 自由标签（可选，用于过滤）
relates_to:                              # 关联文档 doc_id 列表（可选，手动声明的高置信关联）
  - bootchain-init-task
  - mcu-upgrade-link
code_symbols:                            # 本文核心代码符号（可选，仅 task/review/diary 适用）
  - GnssUpdate::TryActivate
  - HQ_OTA_ENABLE_GNSS_VERSION_CHECK
---
```

### 2.2 字段说明

| 字段 | 必填 | 适用类型 | 说明 |
|------|------|---------|------|
| `doc_id` | ✅ | 全部 | 全局唯一标识（不写则按路径自动生成，但**强烈建议手写**） |
| `type` | ✅ | 全部 | 7 种之一 |
| `status` | task/review ✅ | 其他可选 | task: 待评审/进行中/已完成/已归档；review: 通过/不通过；design: 定稿/草稿 |
| `date` | ✅ | 全部 | YYYY-MM-DD，diary 可从文件名推断 |
| `tags` | 可选 | 全部 | 自由标签，用于查询过滤 |
| `relates_to` | 可选 | 全部 | 用 `doc_id` 不用路径——文档移动不影响关联 |
| `code_symbols` | 可选 | task/review/diary | 人确认的核心符号（标 `manual=True`），仅这三种适用 |

### 2.3 置信度区分

- `frontmatter` 的 `relates_to` / `code_symbols` → **manual=True**（人工确认，高置信）
- 正文 markdown 链接 / 反引号符号 → **auto=False**（自动提取，中置信）
- 查询时 manual 优先

---

## 3. 各类型专属规则

### 3.1 diary（开发日志）

**图谱角色**：每条 `## 标题` 是一个知识点节点，携带类型/影响/结论/会话属性。

**新文档模板**（见 `templates/diary_template.md`）：

```markdown
---
doc_id: diary-2026-08-04
type: diary
date: 2026-08-04
tags: [Switch, 版本比较]
---

# 2026-08-04 会话日记

## Switch 版本比较 customer data 段静默丢弃
- **类型**: 根因分析
- **影响**: Switch 升级时 customer data 被丢弃，可能导致配置丢失
- **结论**: `CompareVersion` 解析 customer data 时直接 skip 了 0x11 段，需加长度校验
- **关联**: `SwitchUpdate::CompareVersion`, `parseCustomerData`
- **会话**: a3b4c5d6
```

**关键约束**：
- "关联"字段：**反引号 = 代码符号**（建 `mentions_symbol` 边），**无反引号 = 工具/组件名**（不建边，仅文本）
- "类型"字段值：根因分析 / 架构发现 / 决策记录 / 审查结论 / 约束规则 / 潜在缺陷
- 每条 `## 标题` 是独立知识点，携带自己的"关联"符号
- 工具名（dutii, dumaster, ota_packer）**不加**反引号

**旧文档迁移**（16 个 diary，P0 优先）：
1. 文件头加 3 行 frontmatter（`doc_id` / `type` / `date`）
2. "关联"字段：给代码符号加反引号（`parseIvcRxHeader` → `` `parseIvcRxHeader` ``）
3. 工具名不加反引号
4. 不需要改 `## 标题` 和其他字段

迁移示例（diary/2026-07-13.md）：
```markdown
<!-- 改前 -->
- **关联**: dutii, dumaster, libnvdusclient, parseIvcRxHeader, duscExecute

<!-- 改后 -->
- **关联**: dutii, dumaster, libnvdusclient, `parseIvcRxHeader`, `duscExecute`
```

### 3.2 task（任务文档）

**图谱角色**：文档节点携带 status；`## 章节` 是知识点；frontmatter `code_symbols` 是核心符号。

**新文档模板**（见 `templates/task_template.md`，改造自 `task/_template.md`）：

```markdown
---
doc_id: <kebab-case-任务名>
type: task
status: 待评审
date: 2026-08-04
tags: [<主题标签>]
relates_to:
  - <关联任务 doc_id>
code_symbols:
  - ClassName::MethodName
---

# Task: <任务标题>

## 目标
## 现状问题
## 改动文件清单
## 设计方案
## 验收标准
## 风险点
## 实现步骤
## 审查记录
```

**关键约束**：
- `code_symbols`：本任务**改动**的核心符号（不是文档里提到的所有符号，5-10 个足够）
- 正文里反引号包裹的符号由解析器自动提取为 `auto` 边
- `relates_to`：前置任务、依赖的链路文档
- `status` 字段驱动查询：「做过什么任务」= `type=task + status=已完成`

**旧文档迁移**（25 个 task，P0 优先）：
1. 文件头加 frontmatter（从 `# Task: 标题` 和 `> 引用块` 提取 date/status）
2. `code_symbols`：通读一遍，列出改动的核心符号（5-10 个）
3. 正文代码符号检查反引号（大部分 task 已在用）
4. 不需要改 `## 章节` 结构

### 3.3 review（审查报告）

**图谱角色**：文档节点携带审查结论；`## 发现问题/[P0]` 子章节是带严重级别的知识点。

**新文档模板**（见 `templates/review_template.md`）：

```markdown
---
doc_id: review-<主题>-<日期>
type: review
status: 通过
date: 2026-08-04
tags: [审查, <模块名>]
relates_to:
  - <被审查的 task doc_id>
code_symbols:
  - ClassName::MethodName
---

# 审查报告：<标题>

## 审查范围
- 任务文档：[任务名](task/xxx.md)       ← 必须用 markdown 链接
- 变更文件：`ota_manager.cpp`
- 审查日期：2026-08-04

## 发现问题

### [P0] <问题标题>
- 位置：`ota_manager.cpp:402`
- 依据：...
- 影响：...
- 建议：...

### [P1] <问题标题>
...

## 审查结论
- **通过**（P0 已修复，P1 全部关闭）
```

**关键约束**：
- `relates_to`：**必须**指向被审查的 task 文档
- "审查范围"里的任务文档引用**必须用 markdown 链接** `[text](path.md)`
- `### [P0]` / `### [P1]` 标题：解析器提取 `P0`/`P1`/`P2` 作为 `severity` 属性
- `status`：通过/不通过

**旧文档迁移**（6 个 review，P1）：
1. 加 frontmatter
2. "审查范围"里的 `task/xxx.md` 纯文本 → 改为 `[任务名](task/xxx.md)` markdown 链接
3. `code_symbols`：列出审查涉及的 3-5 个核心符号

迁移示例（review_reports/2026-06-01_sm_integration.md）：
```markdown
<!-- 改前 -->
- 任务文档：task/sm_integration_task.md

<!-- 改后 -->
- 任务文档：[SM 集成任务](task/sm_integration_task.md)
```

### 3.4 design / link（设计文档 / 链路文档）

**图谱角色**：`> 引用块` 里的 markdown 链接 = 文档间关联边；`## 编号. 标题` = 知识点。

**新文档模板**（见 `templates/design_link_template.md`）：

```markdown
---
doc_id: <kebab-case-文档名>
type: design                            # design | link
status: 定稿
date: 2026-07-20
tags: [架构设计, A/B分区]
relates_to:
  - <关联文档 doc_id>
---

# <文档标题>

> **文档定位**：<一句话说明本文讲什么>
> **关联文档**：
> - [链路文档](xxx_LINK.md) = 协议链路级
> - [分析文档](xxx_ANALYSIS.md) = 异常处理级

## 1. 目标与核心结论
## 2. 详细设计
```

**关键约束**：
- `> 引用块` 里的 markdown 链接 = `relates_to` 边（标 `auto`）
- frontmatter `relates_to` 只补充 `>` 引用块里**没有**的关联（标 `manual`）
- `## 编号. 标题` 格式保留
- design 和 link 的区别：design 讲"为什么这么设计"，link 讲"具体怎么做"

**旧文档迁移**（~20 个，P1/P2）：
1. 加 frontmatter（`doc_id` / `type` / `date` / `tags`）
2. `> 引用块` 里的 markdown 链接**不用改**
3. `## 编号. 标题` **不用改**
4. **几乎零改动**——这类文档结构已经很好，主要就是加 frontmatter

### 3.5 requirement / report / guide（低优先级）

**图谱角色**：仅文档节点 + `## 章节` 知识点。不做 frontmatter 强制要求。

**规则**：
- 解析器兼容模式处理：自动检测 `##` 标题建知识点，自动提取 markdown 链接和反引号符号
- 标 `legacy=True`，提示"待补 frontmatter"
- 不阻塞——这类文档变更少，靠全文搜索 + 章节切分够用

**旧文档迁移**：不强制。如果某个需求文档被频繁引用，再单独补 frontmatter。

---

## 4. 通用规则（跨类型）

### 规则 A：文档间引用统一用 markdown 链接

```markdown
<!-- 对的 -->
关联任务见 [GNSS 版本校验修复](task/gnss_activate_version_check_fix.md)

<!-- 错的：纯文本，解析器不保证抓取 -->
关联任务：task/gnss_activate_version_check_fix.md
```

解析器只认 `[text](path)` 格式。纯文本引用要么改为 markdown 链接，要么补到 frontmatter `relates_to` 字段。

### 规则 B：代码符号引用统一用反引号

```markdown
<!-- 对的 -->
修复 `GnssUpdate::TryActivate` 方法，调用 `ConfirmVersion` 刷新版本。

<!-- 错的：无反引号，解析器不识别 -->
修复 GnssUpdate::TryActivate 方法，调用 ConfirmVersion 刷新版本。
```

- `ClassName::Method` 形式 = 高置信度
- 单独 `ClassName`（大写开头 + ≥3 字符 + 不在黑名单）= 中置信度
- `snake_case_function` = 仅在 frontmatter `code_symbols` 中才提取

### 规则 C：章节标题保留 `##` 格式

| 类型 | 标题格式 |
|------|---------|
| task | `## 目标` `## 现状问题` `## 设计方案` ... |
| diary | `## 标题`（每条一个知识点） |
| review | `## 审查范围` `## 发现问题` `## 审查结论`；子章节 `### [P0] 标题` |
| design/link | `## 1. 标题` `## 2. 标题`（编号格式保留） |

解析器把每个 `##` 作为知识点节点，`###` 及以下归入所属 `##` 切片。

---

## 5. 旧文档迁移工作量估算

| 批次 | 类型 | 数量 | 单个操作 | 单个工作量 | 批次总时 | 优先级 |
|------|------|------|---------|-----------|---------|--------|
| 第 1 批 | diary | 16 | 加 frontmatter + "关联"加反引号 | ~3 分钟 | ~50 分钟 | P0 |
| 第 2 批 | task | 25 | 加 frontmatter + 列 code_symbols | ~5 分钟 | ~2 小时 | P0 |
| 第 3 批 | review | 6 | 加 frontmatter + 改纯文本链接 | ~5 分钟 | ~30 分钟 | P1 |
| 第 4 批 | design + link | ~20 | 仅加 frontmatter | ~2 分钟 | ~40 分钟 | P1/P2 |
| 第 5 批 | requirement + report | ~24 | 不强制 | 0 | 0 | P3 跳过 |
| **合计** | | **91** | | | **~4 小时** | |

**第 1 批最优先**：diary 包含排障结论和决策记录，是图谱价值最高的文档类型，且迁移成本最低。

**迁移后验证**：每批补完后跑全量解析，确认：
- 该批文档 `legacy=True` 数量下降
- 该批文档有关联边（不再孤立）
- 无新增解析错误
