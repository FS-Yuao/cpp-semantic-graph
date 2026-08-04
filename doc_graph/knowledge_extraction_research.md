# 知识点提取方案调研与对比

## 一、我们的方案：结构化规则解析

### 核心思路

基于 Markdown 文档的**结构化标记**（frontmatter + 标题层级）做规则提取，不依赖 LLM 或 NLP。

### 提取流程

```
文档输入 → frontmatter 解析 → doc_id 确定 → ## 标题扫描 → 知识点节点 → 符号提取 → 边构建
                ↓                ↓              ↓              ↓            ↓
            YAML 键值对      doc:xxx      know:xxx-sNN-title    反引号标识符    has_knowledge
            (doc_id/type     或路径推导      (保留中英文)         + 黑名单过滤    mentions_symbol
             /status/tags)                                          + diary/review 增强
```

### 具体技术

| 环节 | 方法 | 代码位置 |
|------|------|---------|
| **文档 ID** | frontmatter `doc_id` 字段优先，降级到文件路径转换 | `path_to_doc_id()` |
| **知识点切分** | 正则匹配 `## ` 二级标题，每个标题 = 一个知识点 | parser.py L422 |
| **知识点 ID** | `know:{doc_id}-s{NN}-{safe_title}`，标题去标点保留中英文 | parser.py L426 |
| **符号提取** | 反引号 `` `symbol` `` + 黑名单过滤（排除 OTA/UDP 等缩写） | `extract_symbols_from_backticks()` |
| **diary 增强** | 扫描 `## ` 后的 `- **类型**/`- **结论**/`- **关联**` 字段 | parser.py L436-461 |
| **review 增强** | 扫描 `### [P0]`/`### [P1]` 严重级别标记 | parser.py L464-471 |
| **文档间关联** | frontmatter `relates_to` + 正文 `[text](path.md)` 链接 | parser.py L398-407 |
| **全文搜索** | SQLite FTS5 虚拟表，`unicode61` 分词器支持中文 | `doc_fts` 表 |
| **代码桥接** | 符号名 → 代码图谱数据库查询 callers/callees/file_path | query.py `bridge_to_code()` |

### 优势

1. **零依赖**：不需要 LLM API、不需要 GPU、不需要 embedding 模型
2. **确定性**：同样的输入永远产出同样的图谱，可复现可调试
3. **毫秒级**：95 个文档解析 < 1 秒
4. **领域定制**：diary/review 类型有专门的字段提取逻辑

### 局限

1. **依赖文档规范**：作者必须用 `##` 划分章节、用 frontmatter 声明元数据
2. **符号提取粗糙**：反引号 + 正则，无法理解上下文语义
3. **无实体消歧**：`PerformUpdate` 在不同文档中是同一个符号还是不同符号，无法自动判断
4. **无语义关系**：只知道"A 提到 B"，不知道"A 支持B"还是"A 反对B"

---

## 二、市场方案调研

### 方案 A：Neo4j LLM Graph Builder（2025.03）

**代表产品**：Neo4j + LangChain LLMGraphTransformer

```
文档 → token 分块 → embedding 生成 → LLM 实体提取 → 后处理优化
                                          ↓
                                    动态推断 schema
                                    (不预定义节点/边类型)
```

**分块策略**：
- 基于 token 数量切分（可配置 chunk_size）
- 每个 chunk 是一个节点，用 `PART_OF` 关联到文档，用 `NEXT_CHUNK` 串联
- chunk 大小影响 embedding 质量——太大语义模糊，太小丢失上下文

**实体提取**：
- 使用 LLM（GPT-4o / Gemini）从每个 chunk 中提取实体和关系
- LLMGraphTransformer 动态推断 schema，无需预定义节点/边类型
- 支持配置 `allowed_nodes` / `allowed_relationships` 过滤噪声
- 用户可附加指令（如"关注医疗术语"）

**后处理**：
- Leiden 社区检测算法 → 层次化社区摘要
- KNN 向量相似度 → chunk 间语义连接
- 实体合并去重
- 混合搜索（向量 + 全文）

**对比我们的**：

| 维度 | Neo4j 方案 | 我们的方案 |
|------|-----------|-----------|
| 分块依据 | token 数量 | Markdown 结构（## 标题） |
| 实体提取 | LLM 自动推断 | 规则匹配（反引号+黑名单） |
| schema | 动态推断 | 预定义（document/knowledge/symbol） |
| 计算成本 | 每文档一次 LLM 调用 | 零 LLM 调用 |
| 语义理解 | LLM 理解上下文 | 纯结构匹配 |
| 可控性 | 低（LLM 黑盒） | 高（规则透明可调） |

### 方案 B：Microsoft GraphRAG（2024-2025）

**代表产品**：微软开源 GraphRAG 框架

```
文档 → 分块 → LLM 实体/关系提取 → 图谱构建 → 社区聚类 → 层次化摘要
                                      ↓
                              局部搜索 + 全局搜索
```

**核心创新**：

1. **层次化社区摘要**：
   - 用 Leiden 算法对图谱做社区检测
   - 每个社区用 LLM 生成摘要
   - 形成 0→1→2 三级层次结构
   - 支持全局性问题（"整个项目的架构是什么？"）—— 传统 RAG 无法回答

2. **两种检索模式**：
   - **局部搜索**：针对具体问题，从相关实体出发遍历邻域
   - **全局搜索**：针对总结性问题，汇总所有社区摘要

3. **Claim 提取**：除了实体和关系，还提取每个实体的声明（属性/状态）

**对比我们的**：

| 维度 | GraphRAG | 我们的方案 |
|------|---------|-----------|
| 目标 | 通用文档问答 | 代码工程文档导航 |
| 社区摘要 | ✅ Leiden + LLM 摘要 | ❌ 无 |
| 全局问题 | ✅ 社区摘要聚合 | ❌ 不支持 |
| 查询模式 | 局部+全局 | 仅 BFS 遍历 |
| 代码桥接 | ❌ 不涉及 | ✅ 符号→代码图谱 |

### 方案 C：LlamaIndex Semantic Chunking（2024-2025）

**代表产品**：LlamaIndex NodeParser / SemanticChunker

```
文档 → 句子分割 → embedding 逐句 → 语义相似度 → 相似度断点切分 → chunk 节点
```

**分块策略**：
- **固定大小分块**：按 token 数切分（最简单，但可能切断语义）
- **语义分块**：逐句计算 embedding，当相邻句子相似度低于阈值时切分
- **结构化分块**：按 Markdown 标题/段落/列表结构切分（最接近我们的方法）

**对比我们的**：
- LlamaIndex 的 `MarkdownNodeParser` 也按 `##` 切分，和我们的方法类似
- 但 LlamaIndex 不做符号提取和代码桥接
- LlamaIndex 的语义分块需要 embedding 模型，我们不需要

### 方案 D：LangChain LLMGraphTransformer

```
文本 chunk → LLM prompt → {entities: [...], relationships: [...]} → 图谱
```

- 通过精心设计的 prompt 让 LLM 输出结构化的实体和关系
- 支持约束节点/关系类型
- 被 Neo4j Graph Builder 采用作为核心提取引擎

**对比我们的**：本质上是用 LLM 替代我们的正则规则，代价是 API 成本和不确定性。

---

## 三、横向对比总结

| 维度 | 我们 | Neo4j | GraphRAG | LlamaIndex |
|------|------|-------|---------|-----------|
| **分块方法** | Markdown ## 标题 | token 数量 | token 数量 | 标题/语义/embedding |
| **实体提取** | 正则+反引号 | LLM 推断 | LLM+prompt | 不提取实体 |
| **需要 LLM** | ❌ | ✅ GPT-4o | ✅ GPT-4 | embedding 可选 |
| **需要 GPU** | ❌ | ❌ (API) | ❌ (API) | 可选 (本地) |
| **解析速度** | <1s/95文档 | ~2s/文档 | ~5s/文档 | <1s |
| **确定性** | ✅ 完全确定 | ❌ LLM 输出不确定 | ❌ LLM 输出不确定 | ✅ 确定(结构化) |
| **中文支持** | ✅ FTS5+正则 | 需配置 | 需配置 | 需配置 |
| **代码桥接** | ✅ 符号→代码图谱 | ❌ | ❌ | ❌ |
| **社区摘要** | ❌ | ✅ Leiden | ✅ 层次化 | ❌ |
| **全局问题** | ❌ | ✅ | ✅ | ❌ |
| **部署成本** | 零 | API 费用 | API 费用 | 可选 |

---

## 四、我们的方案定位

我们的方案本质上是 **LlamaIndex MarkdownNodeParser 的工程化特化版本**：

1. **相同的分块策略**：都按 Markdown 标题结构切分
2. **我们的增强**：
   - 领域定制：diary 的类型/结论/会话字段、review 的 P0/P1/P2 级别
   - 代码桥接：符号提取 → 代码图谱查询（市场上没有现成方案）
   - frontmatter 元数据：doc_id/status/tags 人工声明
   - 文档间关联：relates_to 边
3. **我们的取舍**：
   - 放弃了 LLM 实体提取 → 换取零成本、确定性、毫秒级
   - 放弃了社区摘要 → 换取简单可维护
   - 保留了代码桥接 → 这是我们的核心差异化

### 适用场景

| 场景 | 适合我们 | 适合 LLM 方案 |
|------|---------|-------------|
| 工程文档导航（有规范） | ✅ | 过重 |
| 代码↔文档关联查询 | ✅ 独有 | ❌ |
| 非结构化文本知识提取 | ❌ | ✅ |
| 全局性问题（"整体架构？"） | ❌ | ✅ GraphRAG |
| 实体消歧（同名不同义） | ❌ | ✅ LLM |

---

## 五、可能的演进方向

如果未来需要增强，可以考虑的路径：

### Level 1：embedding 增强（低成本）
- 为每个 knowledge 节点生成 embedding
- 支持语义搜索（"和分区切换相关的章节"）而非仅关键词匹配
- 不需要 LLM，只需 embedding 模型

### Level 2：LLM 辅助符号消歧（中成本）
- 对同名符号（如 `Update` 出现在多个类中）用 LLM 判断上下文指向哪个
- 仅在符号提取阶段调用 LLM，不在分块阶段
- 保持规则分块的确定性

### Level 3：社区检测 + 摘要（高成本，对标 GraphRAG）
- 对文档图谱做 Leiden 社区检测
- 用 LLM 生成社区摘要
- 支持全局性问题
- 需要持续的 LLM API 调用

**当前阶段**：Level 0 已经满足工程需求，代码↔文档桥接是核心价值，暂不需要 LLM。
