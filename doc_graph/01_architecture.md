# 文档知识图谱：架构设计

> 本文档定义文档图谱的完整技术架构：数据模型、解析管道、查询层、符号桥接、MCP 接口。

---

## 1. 系统定位

### 1.1 与 cppsg 的关系

```
cppsg (代码图谱)                doc_graph (文档图谱)
┌──────────────────┐            ┌──────────────────┐
│ clang AST 解析    │            │ markdown 规则解析  │
│ semantic_graph_   │            │ doc_graph.db      │
│   full.db         │            │                   │
│                   │            │                   │
│  class/function   │  符号桥接   │  document/know    │
│  callers/callees  │◄──────────►│  symbol/ref       │
│  inheritance      │  (唯一桥梁) │  BFS 遍历         │
└──────────────────┘            └──────────────────┘
```

**分离原则**：
- 两个系统独立运行，互不依赖
- 唯一耦合点：符号桥接（文档符号名 → cppsg 代码节点）
- cppsg 不做任何改动

### 1.2 为什么不增强 cppsg 的文档能力

| 维度 | cppsg 现状 | 增强成本 | 独立文档图谱 |
|------|-----------|---------|-------------|
| 搜索 | `LIKE %keyword%`（"AB分区方案"=0结果） | 需改 schema + 查询层 | 新建，无包袱 |
| 数据模型 | 以代码节点为中心 | 文档是二等公民 | 文档为一等公民 |
| 解析方式 | clang AST 副产物 | 需独立 markdown 解析器 | 独立解析器，专注文档 |
| 查询模式 | 单跳 `doc_describes_code` | 需重构为多跳 BFS | 原生 BFS |

## 2. 数据模型

### 2.1 节点（Node）

```python
@dataclass
class Node:
    # ── 基础 ──
    id: str           # 前缀:内容  →  doc:xxx / know:xxx / symbol:xxx
    type: str         # document | knowledge | symbol
    doc_type: str     # task | diary | review | design | link | requirement | report
    title: str
    path: str         # 相对 docs 根的路径
    line: int         # 在源文件中的行号（知识点用）

    # ── 知识点专属 ──
    ktype: str        # diary: 根因分析/架构发现/决策记录/审查结论/约束规则/潜在缺陷
                      # review: P0/P1/P2（从 ### [P0] 标题提取）
                      # task: 目标/现状问题/设计方案/验收标准/...
    conclusion: str   # diary "结论" 字段 / review 章节摘要
    session: str      # diary "会话" 字段（Claude session ID）

    # ── 元数据 ──
    status: str       # 待评审/进行中/已完成/已归档（task/review）
                      # 通过/不通过（review）
                      # 定稿/草稿（design/link）
    date: str         # YYYY-MM-DD
    tags: list        # 自由标签

    # ── 置信度 ──
    manual: bool      # True  = 来自 frontmatter（人工确认，高置信）
                      # False = 来自正文自动提取（中置信）

    # ── 兼容 ──
    legacy: bool      # True = 无 frontmatter 的旧文档（兼容模式解析）
```

**节点 ID 规则**：
| 前缀 | 格式 | 示例 |
|------|------|------|
| `doc:` | `doc:` + kebab-case 路径 | `doc:diary-2026-07-13` |
| `know:` | `know:` + 文档ID + `s01` + 标题 | `know:diary-2026-07-13-s01-switch-version-compare` |
| `symbol:` | `symbol:` + 原始符号名（保留大小写） | `symbol:GnssUpdate::TryActivate` |

### 2.2 边（Edge）

```python
@dataclass
class Edge:
    src: str          # 源节点 ID
    dst: str          # 目标节点 ID
    rel: str          # 关系类型（见下表）
    manual: bool      # True = frontmatter 声明（高置信）
                      # False = 正文自动提取（中置信）
```

**边类型**：

| 关系 | 含义 | 来源 | 方向 |
|------|------|------|------|
| `has_knowledge` | 文档包含知识点 | `##` 标题 | document → knowledge |
| `relates_to` | 文档间关联 | frontmatter + markdown 链接 | document → document |
| `mentions_symbol` | 提到代码符号 | frontmatter + 反引号 | document/knowledge → symbol |

**去重规则**：同 `src + dst + rel` 只保留一条边。若 manual 和 auto 冲突，manual 优先。

### 2.3 SQLite Schema

```sql
-- 节点表
CREATE TABLE node (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,       -- document | knowledge | symbol
    doc_type    TEXT,
    title       TEXT,
    path        TEXT,
    line        INTEGER DEFAULT 0,
    ktype       TEXT,
    conclusion  TEXT,
    session     TEXT,
    status      TEXT,
    date        TEXT,
    tags        TEXT,                -- JSON array
    manual      INTEGER DEFAULT 0,
    legacy      INTEGER DEFAULT 0
);

-- 边表
CREATE TABLE edge (
    src     TEXT NOT NULL,
    dst     TEXT NOT NULL,
    rel     TEXT NOT NULL,
    manual  INTEGER DEFAULT 0,
    PRIMARY KEY (src, dst, rel)      -- 去重
);
CREATE INDEX idx_edge_src ON edge(src);
CREATE INDEX idx_edge_dst ON edge(dst);
CREATE INDEX idx_edge_rel ON edge(rel);

-- FTS5 全文索引（仅索引文档和知识点）
CREATE VIRTUAL TABLE doc_fts USING fts5(
    doc_id,
    title,
    content_preview,
    tags,
    tokenize='unicode61'
);
```

## 3. 解析管道

### 3.1 管道流程

```
输入：单个 md 文件
   │
   ├─ 1. 解析 frontmatter
   │      ├─ 提取 doc_id / type / status / date / tags
   │      ├─ 提取 relates_to → 建 manual=True relates_to 边
   │      └─ 提取 code_symbols → 建 manual=True mentions_symbol 边
   │
   ├─ 2. 创建 document 节点
   │      └─ 若无 frontmatter → 标 legacy=True，用兼容模式
   │
   ├─ 3. 扫描 ## 章节 → 创建 knowledge 节点 + has_knowledge 边
   │      └─ ### 及以下归入所属 ## 切片
   │
   ├─ 4. diary 增强：扫描每个 ## 后的 - **类型/结论/关联/会话** 字段
   │      ├─ 填充 knowledge 节点属性（ktype / conclusion / session）
   │      └─ "关联" 字段按反引号区分：反引号=符号，无反引号=文本
   │
   ├─ 5. 扫描 markdown 链接 [text](path) → 建 auto relates_to 边（去重）
   │      └─ 路径规范化：去掉 ../ 前缀、用 doc_id 而非路径
   │
   └─ 6. 扫描反引号代码符号 → 建 auto mentions_symbol 边（去重）
          └─ 黑名单过滤：全大写缩写（OTA/UDS/HTTP...）、文件名、枚举值（kXxx）
```

### 3.2 关键算法决策

| 决策 | 做法 | 解决的问题 |
|------|------|-----------|
| 边去重 | 同 src+dst+rel 只建一条 | P1 符号重复爆炸（30 条重复 → 1 条） |
| 链接路径规范化 | 去掉 `../` 前缀、用 doc_id | P2 `..-` 残留导致 ID 碰撞 |
| 符号置信度 | frontmatter 标 manual，正文标 auto | 查询时手动优先 |
| 反引号区分 | `` `symbol` `` = 代码符号，`plain` = 文本 | P0 小写函数名误判 |
| 未识别类型 | 不报错，按"doc"类型建文档节点 | 兼容旧文档/未来新类型 |

### 3.3 符号提取规则

```
高置信度（ClassName::Method）：
  正则: `([A-Z][A-Za-z0-9_]*(?:::[A-Za-z0-9_~]+)+)`
  示例: `GnssUpdate::TryActivate` → symbol:GnssUpdate::TryActivate

中置信度（单独 ClassName）：
  正则: `([A-Z][A-Za-z0-9_]{2,})`
  过滤: 黑名单（OTA/UDS/HTTP...）+ 全大写缩写 + kXxx 枚举值
  示例: `OtaManager` → symbol:OtaManager

低置信度（snake_case 函数名）：
  条件: 仅在 frontmatter code_symbols 中出现时才提取
  示例: frontmatter 里 `code_symbols: [parse_customer_data]` → 提取
        正文里 `parse_customer_data`（无反引号）→ 不提取
```

### 3.4 旧文档兼容模式

```
检测: 文件头无 "---" frontmatter
行为:
  ├─ 标 legacy=True
  ├─ 自动检测 ## 标题建知识点
  ├─ 自动提取 markdown 链接建 relates_to 边
  ├─ 自动提取反引号符号建 mentions_symbol 边
  ├─ diary "关联"字段：反引号=符号，无反引号=文本（不误判）
  └─ 纯文本引用（task/xxx.md 无 markdown 链接格式）→ 不保证提取，记 warning

输出: 节点标 legacy=True，定期生成"待补 frontmatter 文档清单"
```

## 4. 查询层设计

文档图谱的核心价值不是解析，是**查询**。用户问一个问题，图谱要能：
1. 定位起始节点（FTS5 全文搜索）
2. BFS 遍历关联（沿边走到关联文档/知识点/代码符号）
3. 桥接到代码图谱（文档符号 → cppsg 代码节点）
4. 返回结构化结果（不是扁平列表，是图结构）

### 4.1 起始节点定位（FTS5）

```sql
-- "AB分区方案" → 分词为 "AB" "分区" "方案"
-- "分区" 命中 partition-switch-design 的标题，即使文档写的是"分区切换"
SELECT doc_id, bm25(doc_fts) as score
FROM doc_fts
WHERE doc_fts MATCH 'AB OR 分区 OR 方案'
ORDER BY score LIMIT 5;
```

**兜底**：FTS5 未命中时降级为 `WHERE title LIKE '%关键词%' OR tags LIKE '%关键词%'`。91 文档全表扫描 <10ms。

### 4.2 BFS 图遍历

```python
def query_doc_graph(start_doc_id: str, depth: int = 2,
                    edge_filter: list[str] = None) -> dict:
    """从文档节点出发，BFS 遍历关联

    Args:
        start_doc_id: 起始文档 ID（FTS5 定位的结果）
        depth: 遍历深度（默认 2 跳）
        edge_filter: 限定边类型（如只查 relates_to）

    Returns:
        {
            "docs": [文档节点 + hop(跳数)],
            "knowledge": [知识点 + parent_doc + hop],
            "symbols": [符号名 + source + confidence + hop]
        }
    """
    visited = set()
    results = {"docs": [], "knowledge": [], "symbols": []}
    queue = [(start_doc_id, 0)]

    while queue:
        node_id, hop = queue.pop(0)
        if node_id in visited or hop > depth:
            continue
        visited.add(node_id)

        node = get_node(node_id)
        if not node:
            continue

        # 分类收集
        if node["type"] == "document":
            results["docs"].append({**node, "hop": hop})
        elif node["type"] == "knowledge":
            results["knowledge"].append({**node, "hop": hop,
                                         "parent_doc": node["parent_doc"]})

        # 遍历出边
        for edge in get_edges_from(node_id):
            if edge_filter and edge["rel"] not in edge_filter:
                continue

            if edge["rel"] == "mentions_symbol":
                # 符号不继续 BFS，但记录来源
                results["symbols"].append({
                    "name": edge["dst"],
                    "source": node_id,
                    "confidence": "manual" if edge["manual"] else "auto",
                    "hop": hop
                })
            else:
                queue.append((edge["dst"], hop + 1))

    return results
```

**查询示例**：用户问「上次 AB 分区方案是什么」

```
Step 1: FTS5 搜 "分区" → 命中 doc:partition-switch-design
Step 2: BFS depth=2
  Level 0: doc:partition-switch-design
  Level 1:
    ├─ has_knowledge → know:s1/当前方案
    │   └─ mentions_symbol → symbol:GetSocBootChain (manual)
    ├─ has_knowledge → know:s2/分区切换机制
    ├─ relates_to → doc:partition-switch-link (auto)
    ├─ relates_to → doc:ab-partition-rollback-analysis (auto)
    └─ mentions_symbol → symbol:setSocDefaultBootChain (auto)
  Level 2:
    └─ doc:partition-switch-link
        ├─ has_knowledge → know:协议层, know:调用链
        └─ relates_to → doc:mcu-bootloader-and-partition (auto)
Step 3: 符号桥接 → GetSocBootChain → cppsg 查 class/function + callers
```

### 4.3 符号桥接（文档图谱 → 代码图谱）

```python
def bridge_symbol_to_cppsg(symbol_name: str, cppsg_db_path: str) -> list[dict]:
    """文档图谱符号名 → cppsg 代码节点查找（三级降级）"""

    # Level 1: 精确匹配 name
    nodes = cppsg_db.execute(
        "SELECT * FROM node WHERE name=? AND type IN ('class','struct','function')",
        [symbol_name]
    )
    if nodes:
        return enrich_with_callgraph(nodes, cppsg_db)

    # Level 2: ClassName::MethodName 拆分
    if "::" in symbol_name:
        cls_name, method_name = symbol_name.rsplit("::", 1)
        nodes = cppsg_db.execute(
            "SELECT * FROM node WHERE name=? AND parent_class=? AND type='function'",
            [method_name, cls_name]
        )
        if nodes:
            return enrich_with_callgraph(nodes, cppsg_db)

    # Level 3: 模糊匹配（兜底）
    nodes = cppsg_db.execute(
        "SELECT * FROM node WHERE name LIKE ? AND type IN ('class','function') LIMIT 5",
        [f"%{symbol_name.split('::')[-1]}%"]
    )
    return nodes
```

**桥接效果**：用户问「TryActivate 相关的文档和代码」，一次查询返回：

```
文档侧：
  diary-2026-07-15: "TryActivate 有激活 bug"（根因分析）
  task/gnss-version-check-fix: "修复 TryActivate 阶段版本校验缺陷"（已完成）

代码侧（通过符号桥接从 cppsg 查到）：
  function: update::PeriAdapter::TryActivate
    定义: peri_manger/peri_adapter.cpp
    调用方: OtaManager::StartUpdate
    被调用: GnssUpdate::CompareVersion
```

### 4.4 MCP 工具接口

```python
def doc_graph_search(keyword: str, depth: int = 2,
                     edge_filter: list[str] = None,
                     bridge_to_code: bool = True) -> dict:
    """搜索文档图谱，返回结构化关联结果

    流程：
    1. FTS5 搜索 keyword → 定位起始文档（≤10ms）
    2. BFS 遍历 → 收集关联文档/知识点/符号（≤50ms）
    3. 符号桥接 → 查 cppsg 代码节点 + callers/callees（≤100ms）
    4. 按 manual 优先 + auto 兜底排序

    总延迟目标：≤200ms
    """
```

**CLAUDE.md 搜索决策树**：

```
我要找什么？
├─ C++ 类/函数/调用链        → cpp-semantic-graph（已有）
├─ 文档关联/任务历史/决策追溯 → doc_graph_search（新增）
├─ 代码+文档一起查           → doc_graph_search + bridge_to_code=true
└─ 读已知文件内容             → Read
```

## 5. 端到端流程

### 5.1 写文档时

```
编写者按规则写文档（frontmatter + ## 章节 + 链接 + 反引号符号）
       │
       ▼
解析器自动提取节点/边 → 入 SQLite + FTS5 索引
       │
       ▼
文档进入图谱，可被 BFS 查询到达
```

### 5.2 查询时

```
用户提问
    │
    ├─ FTS5 搜关键词 → 定位起始文档（≤10ms）
    ├─ BFS 遍历 → 收集关联文档/知识点/符号（≤50ms）
    ├─ 符号桥接 → 查 cppsg 代码节点 + callers/callees（≤100ms）
    └─ 返回结构化结果（文档链路 + 知识点 + 代码上下文）
       总延迟目标：≤200ms
```

## 6. 验收标准

### 6.1 解析侧

| 指标 | 当前 | 目标 | 验证方法 |
|------|------|------|----------|
| 解析成功率 | 91/91 (100%) | ≥95% | 全量跑无报错 |
| 文档有关联率 | 86/91 (94.5%) | ≥95% | 孤立文档 ≤5 |
| 边去重 | 未去重 | 同 src+dst+rel 唯一 | 全量统计 |
| PoC P0 问题 | 存在 | 0 | 5 类代表性样本比对 |
| PoC P1/P2 问题 | 存在 | 0 | 全量统计 |
| 旧文档兼容 | N/A | 全 91 文档可解析不报错 | 兼容模式全量跑 |
| 增量更新 | N/A | 新加 1 个 diary <5s | 性能测试 |

### 6.2 查询侧

| 指标 | 当前 | 目标 | 验证方法 |
|------|------|------|----------|
| FTS5 起始节点命中率 | 0%（LIKE 失败） | ≥70% | 10 个改写表述的问题 |
| BFS 遍历召回率 | N/A | ≥80% | 10 个典型问题 2 跳召回 |
| 符号桥接命中率 | N/A | ≥60% | 10 个符号桥接 cppsg |
| 查询延迟 | N/A | ≤200ms | FTS5 + BFS + 桥接端到端 |
| 典型问题召回率 | 未测 | ≥80% | 10 个典型问题用户自评 |
