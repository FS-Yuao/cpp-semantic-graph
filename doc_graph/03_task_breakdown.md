# 文档知识图谱：任务拆分

> 本文档将设计方案拆分为 8 个独立任务，每个任务有明确的输入/输出/验收标准。
> 任务间有依赖关系，但每个任务可独立验证和交付。

---

## 任务依赖关系

```
Task 1: 规则定型 ──────────────────────────────────────┐
  │                                                    │
  ├─► Task 2: 写 diary 模板                             │
  │                                                    │
  ├─► Task 3: 改造 task 模板                            │
  │                                                    │
  ▼                                                    ▼
Task 4: 重写解析器 ◄────────── Task 2 + Task 3 完成 ────┘
  │
  ▼
Task 5: 全量验证
  │
  ├─► Task 6: 实现查询层
  │     │
  │     ▼
  │   Task 7: MCP 工具接入
  │
  └─► Task 8: 旧文档迁移（可并行，Task 6 完成后触发）
```

---

## Task 1：规则定型

| 项 | 内容 |
|----|------|
| **目标** | 完成文档结构化规则的评审和定稿 |
| **前置依赖** | 无 |
| **产出物** | `doc_graph/` 目录全套文档（README + 01_architecture + 02_doc_rules + 03_task_breakdown + templates） |
| **验收标准** | 1. 7 类文档规则明确<br>2. frontmatter 字段定义完整<br>3. 4 个模板可用<br>4. 迁移工作量估算完成<br>5. 评审通过 |
| **预估工时** | 4 小时（已完成） |
| **状态** | ✅ 本次完成 |

---

## Task 2：写 diary 模板

| 项 | 内容 |
|----|------|
| **目标** | 创建 diary 标准模板，新写 diary 直接用模板 |
| **前置依赖** | Task 1 |
| **产出物** | `templates/diary_template.md` |
| **验收标准** | 1. 含 frontmatter（doc_id / type / date / tags）<br>2. 含标准字段示例（类型/影响/结论/关联/会话）<br>3. "关联"字段反引号规则有注释说明<br>4. 新人按模板写一遍能直接进解析器 |
| **预估工时** | 30 分钟 |
| **状态** | 🔲 待开始 |

---

## Task 3：改造 task 模板

| 项 | 内容 |
|----|------|
| **目标** | 改造现有 `task/_template.md`，顶部加 frontmatter 段 |
| **前置依赖** | Task 1 |
| **产出物** | `templates/task_template.md`（新模板）<br>`task/_template.md`（同步更新） |
| **验收标准** | 1. frontmatter 含 doc_id / type / status / date / tags / relates_to / code_symbols<br>2. 保留现有 ## 章节结构<br>3. 用模板新建一个 task，解析器完整提取 frontmatter + 章节 + 符号 |
| **预估工时** | 30 分钟 |
| **状态** | 🔲 待开始 |

---

## Task 4：重写解析器

| 项 | 内容 |
|----|------|
| **目标** | 按 `01_architecture.md` §3 解析管道重写 `poc_doc_graph.py`，解决 5 类 P0/P1/P2 问题 |
| **前置依赖** | Task 2 + Task 3（需要模板验证解析器） |
| **产出物** | `doc_graph/parser.py`（重写后的解析器） |
| **关键改动** | 1. frontmatter 解析（YAML 格式）<br>2. 边去重：同 src+dst+rel 只建一条<br>3. 链接路径规范化：去掉 `../` 残留<br>4. 符号置信度标记：manual / auto<br>5. diary "关联"字段：反引号区分符号/文本（解决 P0）<br>6. 纯文本引用兼容：记 warning 不报错<br>7. legacy 标记：无 frontmatter 标 `legacy=True` |
| **验收标准** | 1. 5 类代表性样本（diary/task/review/design/link）比对，§2.2 表 5 个问题**全部解决**<br>2. 边去重：同 src+dst+rel 唯一<br>3. 路径规范化：无 `..-` 残留<br>4. 符号提取：小写函数名加反引号才识别（解决 P0）<br>5. 纯文本链接：记 warning，不误抓 |
| **预估工时** | 4 小时 |
| **状态** | 🔲 待开始 |

**P0/P1/P2 问题清单（必须全部解决）**：

| 级别 | 问题 | 验证样本 | 解决方案 |
|------|------|---------|---------|
| **P0** | diary"关联"字段函数名误判为文档引用 | diary/2026-07-13 | 反引号 = 符号，无反引号 = 文本 |
| **P1** | 符号边重复爆炸 | task/gnss_activate_version_check_fix | 同 src+dst+rel 去重 |
| **P1** | 纯文本文档引用漏抓 | review/2026-06-01_sm_integration | 记 warning，建议改 markdown 链接 |
| **P2** | 链接路径 `..-` 残留 | AB_Switch/PARTITION_SWITCH_DESIGN | 路径规范化清理 `../` |
| **P2** | 链接边重复 | 同上 | 同 P1 去重逻辑 |

---

## Task 5：全量验证

| 项 | 内容 |
|----|------|
| **目标** | 全量跑 91 文档，对比改造前后指标 |
| **前置依赖** | Task 4 |
| **产出物** | `doc_graph/validation_report.md`（验证报告）<br>含：节点/边统计、legacy 文档清单、孤立文档清单、P0-P2 问题验证结果 |
| **验收标准** | 1. 解析成功率 ≥95%（91 文档全量跑无报错）<br>2. 文档有关联率 ≥95%（孤立文档 ≤5）<br>3. 边去重生效（同改造前边数下降）<br>4. 5 类 P0/P1/P2 问题全部解决（抽样 5 文档人工核对）<br>5. 旧文档兼容：全 91 文档可解析不报错<br>6. 增量更新：新加 1 个 diary <5s |
| **预估工时** | 1 小时 |
| **状态** | 🔲 待开始 |

---

## Task 6：实现查询层

| 项 | 内容 |
|----|------|
| **目标** | 实现 FTS5 搜索 + BFS 图遍历 + 符号桥接，落库到 SQLite |
| **前置依赖** | Task 5 |
| **产出物** | `doc_graph/doc_graph.db`（SQLite 数据库）<br>`doc_graph/query.py`（查询层实现） |
| **关键实现** | 1. SQLite schema 建表（node + edge + FTS5 虚拟表）<br>2. 解析结果落库（从 Task 4 的 parser 输出导入）<br>3. FTS5 全文搜索（BM25 排序）<br>4. BFS 图遍历（depth 可配，edge_filter 可配）<br>5. 符号桥接（三级降级：精确→拆分→模糊）<br>6. 兜底：FTS5 未命中降级 LIKE 搜索 |
| **验收标准** | 1. FTS5 起始节点命中率 ≥70%（10 个改写表述的问题）<br>2. BFS 遍历召回率 ≥80%（10 个典型问题 2 跳召回）<br>3. 符号桥接命中率 ≥60%（10 个符号桥接 cppsg）<br>4. 查询延迟 ≤200ms（FTS5 + BFS + 桥接端到端）<br>5. 用「AB分区方案」测试：从 0 结果 → BFS 返回结构化关联 |
| **预估工时** | 6 小时 |
| **状态** | 🔲 待开始 |

**测试用例（10 个典型问题）**：

| # | 问题 | 期望命中 | 验证点 |
|---|------|---------|--------|
| 1 | AB 分区方案是什么 | partition-switch-design + link + analysis | FTS5 "分区" 命中 |
| 2 | ip 怎么配置 | 含 IP 配置的 diary/task | FTS5 + tag 过滤 |
| 3 | 做过什么任务 | type=task 的文档列表 | type + status 过滤 |
| 4 | 某次审查的 P0 问题 | review + P0 知识点 | severity 过滤 |
| 5 | TryActivate 相关的文档和代码 | diary + task + cppsg 节点 | 符号桥接 |
| 6 | GNSS 版本校验怎么做的 | gnss task + diary | FTS5 + BFS |
| 7 | bootchain 初始化流程 | bootchain task + design | BFS 2 跳 |
| 8 | SM 集成的审查结论 | sm review + task | relates_to 边 |
| 9 | 并行更新的风险 | parallel_update task + issues | BFS 关联 |
| 10 | 日志工具怎么用 | driveupdate_log task | FTS5 + tag |

---

## Task 7：MCP 工具接入

| 项 | 内容 |
|----|------|
| **目标** | 新建 `doc_graph_search` MCP 工具，更新 CLAUDE.md 搜索决策树 |
| **前置依赖** | Task 6 |
| **产出物** | MCP 工具配置（`/home/ubuntu24/.codebuddy/mcp.json` 新增条目）<br>`doc_graph/mcp_server.py`（MCP 服务端）<br>CLAUDE.md 搜索决策树更新 |
| **关键实现** | 1. MCP 工具接口：`doc_graph_search(keyword, depth, edge_filter, bridge_to_code)`<br>2. 输入：关键词 + 遍历深度 + 边类型过滤 + 是否桥接代码<br>3. 输出：结构化 JSON（docs + knowledge + symbols + cppsg_nodes）<br>4. CLAUDE.md 新增搜索决策树 |
| **验收标准** | 1. Agent 能通过 MCP 查文档图谱<br>2. 返回文档 + 知识点 + 符号 + 代码上下文<br>3. 用「AB分区方案」测试，Agent 能直接拿到关联文档和代码<br>4. 延迟 ≤200ms |
| **预估工时** | 3 小时 |
| **状态** | 🔲 待开始 |

---

## Task 8：旧文档分批迁移

| 项 | 内容 |
|----|------|
| **目标** | 91 个旧文档分 4 批补 frontmatter + 规范化 |
| **前置依赖** | Task 6 完成（迁移后需跑全量验证确认图谱效果提升） |
| **产出物** | 迁移后的 91 个 md 文件（frontmatter + 规范化链接/符号） |
| **批次** | **第 1 批**：diary（16 个，~50 分钟）—— 加 frontmatter + "关联"加反引号<br>**第 2 批**：task（25 个，~2 小时）—— 加 frontmatter + 列 code_symbols<br>**第 3 批**：review（6 个，~30 分钟）—— 加 frontmatter + 改纯文本链接<br>**第 4 批**：design + link（~20 个，~40 分钟）—— 仅加 frontmatter<br>**第 5 批**：跳过（requirement + report，P3 不强制） |
| **验收标准** | 每批补完后：<br>1. 跑全量解析，该批 `legacy=True` 数量降为 0<br>2. 该批文档有关联边（不再孤立）<br>3. 无新增解析错误<br>4. 全量完成后：91 文档 legacy ≤24（仅 P3 跳过的） |
| **预估工时** | ~4 小时（分 4 批） |
| **状态** | 🔲 待开始（Task 6 完成后触发） |

---

## 总览

| Task | 名称 | 依赖 | 工时 | 优先级 |
|------|------|------|------|--------|
| 1 | 规则定型 | 无 | 4h | ✅ 完成 |
| 2 | diary 模板 | T1 | 0.5h | ✅ 完成 |
| 3 | task 模板 | T1 | 0.5h | ✅ 完成 |
| 4 | 重写解析器 | T2+T3 | 4h | ✅ 完成 |
| 5 | 全量验证 | T4 | 1h | ✅ 完成 |
| 6 | 实现查询层 | T5 | 6h | ✅ 完成 |
| 7 | MCP 工具接入 | T6 | 3h | ✅ 完成 |
| 8 | 旧文档迁移 | T6 | 4h | ✅ 完成 |
| | **合计** | | **~23h** | **全部完成** |

**关键路径**：T1 → T2+T3 → T4 → T5 → T6 → T7 → T8 ✅ 全部完成

**并行机会**：
- T2 和 T3 可并行
- T8 可在 T6 完成后与 T7 并行
