# 文档知识图谱 + 记忆层（doc_graph）

> 状态：**已上线运行**（2026-08-21 起）
> 关联设计：`01_architecture.md`（架构）/ `02_doc_rules.md`（文档规则）/ `03_task_breakdown.md`（任务拆分）

---

## 项目目标

把项目散落的 markdown 文档结构化成语义关联网络，**并让会话中得出的经验结论（finding）可沉淀、可检索、可随代码演进自动失效检测**——Agent 提问时直接从图谱拿到答案，不再依赖 `grep` + `find` + `Read` 全局扫，也不再重复推导前人已得出的结论。

**核心理念**：与代码图谱（cppsg）一致——节点 + 边 + BFS 遍历，确定性、可解释、可追溯。默认纯词法检索（FTS5），不依赖 GPU 和云 API。

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                     Agent (用户提问)                              │
│                        │                                          │
│                        ▼                                          │
│         ┌──────────────────────────┐                              │
│         │  doc-graph MCP (HTTP 单例) │  6 个工具（streamable-http） │
│         │  systemd 常驻 · 多客户端共享│                              │
│         └───────┬──────────────────┘                              │
│                 │                                                  │
│     ┌───────────┼───────────────┬─────────────────┐               │
│     ▼           ▼               ▼                 ▼               │
│ ┌────────┐ ┌─────────┐ ┌───────────────┐ ┌──────────────┐        │
│ │ 三路RRF │ │  BFS    │ │ 符号桥接→cppsg │ │ findings 记忆 │        │
│ │ 起始定位│ │ 图遍历   │ │ +同义表扩展    │ │ 结论召回/沉淀  │        │
│ └───┬────┘ └────┬────┘ └───────┬───────┘ └──────┬───────┘        │
│     └───────────┴──────┬───────┴────────────────┘                │
│                        ▼                                          │
│         ┌──────────────────────────────┐                          │
│         │  SQLite (doc_graph.db)       │                          │
│         │  node/edge/doc_fts（文档图谱） │                          │
│         │  finding 三表（经验记忆）      │                          │
│         └──────────────────────────────┘                          │
│                        ▲                                          │
│         ┌──────────────┴──────────────┐                           │
│         │  parser.py 全量重建          │                           │
│         │  （finding 表导出→回写防丢失）│                           │
│         └─────────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

## 两个图谱层

| 层 | 存什么 | 数据来源 |
|----|--------|---------|
| **文档图谱** | document / knowledge / symbol 节点 + has_knowledge / relates_to / mentions_symbol 边 | parser.py 解析 markdown（frontmatter + 章节 + 链接 + 反引号符号） |
| **经验记忆（findings）** | 会话产出的可复用结论：fact / constraint / decision / lesson / risk，带符号锚定与生命周期 | `record_finding_tool` 在线写入（MCP 首个写工具），独立于 parser 重建管道 |

## MCP 工具（6 个）

| 工具 | 类型 | 用途 |
|------|------|------|
| `doc_graph_search_tool` | 读 | 关键词 → 三路 RRF 融合定位 → BFS → 符号桥接 cppsg → findings 自动附带 |
| `record_finding_tool` | **写** | 沉淀经验结论（同 title 幂等合并；symbols 符号锚定） |
| `search_findings_tool` | 读 | 纯经验结论直查（词法 + 同义表扩展 + 向量排序增强） |
| `check_finding_freshness_tool` | 写 | 锚定符号 vs cppsg 存在性比对 → active↔stale 自动迁移 |
| `list_documents` / `get_doc_stats` | 读 | 文档清单 / 图谱统计 |

## 检索设计（三层语义，全部零 API）

```
第一层：调用侧 AI 改写 —— MCP 调用方是 LLM，工具描述引导传同义关键词
第二层：服务端同义表 —— 中英技术词对照（崩溃↔crash↔挂↔abort…）自动扩展
第三层：向量排序增强 —— bge-small（fastembed 本地 CPU）仅对词法命中集重排
```

实测边界（详见 `tasks/` 内部实验记录，结论已验证）：
- bi-encoder（bge-small 33M / e5-large 560M）对"短中文查询 vs 短技术结论"**无召回判别力**——所有相似度挤在密集区，阈值不可分
- cross-encoder（bge-reranker-base）强否定可靠、弱肯定不可靠——留作备用闸门
- 同义表 + 调用侧改写实测命中改述查询（"程序挂了"→"崩溃"结论）

## 部署（HTTP 单例）

```bash
# systemd user unit（多客户端共享单进程，替代 stdio 每连接一进程）
systemctl --user start doc-graph-mcp
# mcp.json: {"url": "http://127.0.0.1:8930/mcp"}
```

改代码后 `systemctl --user restart doc-graph-mcp` 生效；全量重建 `python3 parser.py <docs_root>`（finding 表自动保留）。

## 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 检索技术 | FTS5 + BM25 + RRF 融合 | 文档量级不需要 GPU；确定性可解释 |
| 经验记忆 | 独立 finding 三表 | 生命周期与 parser 重建管道分离，重建不丢 |
| 语义理解 | 调用侧改写 + 同义表 | MCP 调用方本身就是 LLM，无需再买云 API |
| 向量 | 仅排序增强 | 实测判别力边界明确，小模型即可满足重排 |
| 服务形态 | streamable-http 单例 | 多客户端共享、无孤儿进程、systemd 守护 |

## 文件导航

| 文件 | 内容 |
|------|------|
| `parser.py` | 文档解析管道（frontmatter/章节/链接/符号 → SQLite + FTS5） |
| `query.py` | 查询层（三路 RRF / BFS / 符号桥接 / findings 召回） |
| `finding_store.py` | 经验记忆存储层（CRUD/幂等合并/同义表/向量/stale 检测） |
| `mcp_server.py` | MCP 服务端（stdio 与 streamable-http 双传输） |
| `test_integration.py` | 集成测试（69 用例） |
| `01_architecture.md` 等 | 设计文档（Phase 1 架构；记忆层设计见 git log） |
| `templates/` | diary / task / review / design 四类文档模板 |
