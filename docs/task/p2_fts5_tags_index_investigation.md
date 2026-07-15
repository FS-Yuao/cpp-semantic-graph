# P2 优化调研结论：FTS5 全文索引 + tags 反向索引（暂缓）

> 版本：调研阶段 | 日期：2026-07-07 | 前置：[extra_info 列化拆分](extra_info_columnar_design.md)、[P1 收尾](p1_needs_resolution_drop_extrainfo_design.md)
> **结论：两项均暂缓，不实施。** 本文档固化实测依据，避免将来重复踩坑。

## 1. 背景

extra_info 列化优化路线图（见 [extra_info_columnar_design.md](extra_info_columnar_design.md)）在 P1 之后规划了 P2：

| 优先级 | 优化项 | 路线图预期收益 | 复杂度 |
|---|---|---|---|
| P2-a | `doc_title` FTS5 全文索引 | LIKE 模糊 → 全文搜索，加速 10×+ | 中 |
| P2-b | `tags` 反向索引表 | `json_each` → JOIN，tag 过滤加速 5×+ | 中 |

P1 落地后，`doc_title` / `tags` / `content_preview` 已是 node 表独立列，前置条件满足。本次按开发流程进入 P2 的"现状问题"调研阶段，实测后发现**收益前提在本项目场景下不成立**，故暂缓。

## 2. 环境能力（✅ 满足，非阻塞）

| 能力 | 检测结果 |
|---|---|
| SQLite 版本 | 3.45.1 |
| FTS5（`CREATE VIRTUAL TABLE ... USING fts5`） | 可用 ✓ |
| json_each（tags 反向表回填用） | 可用 ✓ |
| trigram / porter / unicode61 分词器 | 均可用 ✓ |

环境不构成阻塞。暂缓原因是收益，不是能力。

## 3. 现状问题（❌ 收益前提不成立）

### 3.1 数据规模小，当前查询已非瓶颈

| 指标 | 实测值（主库 semantic_graph_full.db） |
|---|---|
| doc_section 节点总数 | **657 条** |
| 带 tags 节点 | 600 条 |
| 全部节点 | 1280 条 |
| 当前 `LIKE 三列`（name/doc_title/content_preview） | 341ms / 100 次 = **3.4ms/次** |
| 当前 `json_each(tags)` 过滤 | 81ms / 100 次 = **0.8ms/次** |

路线图"10×+ / 5×+"的预期建立在**大规模语料**假设上。本项目仅 657 条文档切片，
全表 LIKE 单次 3.4ms、tags 过滤 0.8ms，均非瓶颈。对 3.4ms 做 10× 优化的绝对收益 < 3ms，
不足以抵偿虚表 + 触发器的复杂度与长期维护成本。

### 3.2 FTS5 对中文有语义硬伤（P2-a 致命）

本项目文档标题、tag、正文均为中文（"升级""刷写""架构设计"），查询关键词常为 **2 个汉字**。
实测三种分词器对 2 字中文词 `升级` 的 `MATCH` 行为：

| 分词器 | 2 字词"升级" MATCH | 原因 |
|---|---|---|
| `unicode61`（FTS5 默认） | ❌ 不命中 | 中文不按词切分，整段视为一个 token |
| `trigram` | ❌ 不命中 | trigram 要求 query **≥ 3 字符**，2 字词无法生成三元组 |
| `porter` | ❌ 不命中 | 英文词干算法，不适用中文 |

补充实测（trigram 表）：

| query | `MATCH` 结果 | `LIKE '%q%'` 结果 |
|---|---|---|
| `升级`（2字） | 0（失效） | 1（命中） |
| `固件升级`（4字） | 1 | 1 |
| `OTA` | 1 | 1 |
| `刷写流程`（4字） | 1 | 1 |

**结论**：上 FTS5 `MATCH` 后，"升级""刷写"等高频 2 字查询会从"能搜到"退化为"搜不到"，
这是**语义倒退**而非加速。唯一兜底是 trigram 分词器下继续用 `LIKE`（trigram 能让 LIKE 走索引，
见 `EXPLAIN QUERY PLAN` 显示 `SCAN VIRTUAL TABLE`），但那样 FTS5 的 `MATCH` 全文能力完全没用上，
等于白建虚表。

### 3.3 tags 反向索引（P2-b）语义无损但收益微弱

P2-b 本身语义无损（`json_each` → `JOIN node_tag`），无中文风险。但 0.8ms/次的现状同样非瓶颈，
收益是"从很快到更快"。若实施，唯一技术要点见 §5。

## 4. 决策

| 项 | 决策 | 理由 |
|---|---|---|
| P2-a（FTS5） | **暂缓（架构建议不做）** | 收益不成立 + 中文 2 字词 MATCH 语义倒退 |
| P2-b（tags 反向索引） | **暂缓（可做可不做）** | 语义无损但 0.8ms 现状非瓶颈，ROI 低 |

于先生已确认：**两项均暂缓。**

## 5. 若将来重启 P2 的技术备忘

- **触发条件重估**：仅当 doc_section 规模增长到**数万条以上**、或 LIKE 单次耗时进入**几十 ms 量级**时，P2 才值得重启。届时先重跑 §3.1 基准。
- **P2-a 中文方案**：若必须上全文检索，不能用内置分词器。可选：(a) 引入外部中文分词（jieba）在写入侧预切词后喂给 unicode61；(b) 用 trigram + 强制 query ≥3 字 + 2 字查询回退 LIKE。两者都显著抬高复杂度。
- **P2-b 同步机制（关键陷阱）**：增量重解析存在多条 `DELETE FROM node` 路径（graph_db.py 有 4+ 处删除点），反向索引表**必须用 SQLite 触发器**（`AFTER INSERT/UPDATE/DELETE ON node`）自动同步，**禁止应用层手动维护**——否则任一删除点漏改就导致索引与主表不一致。
- **三处 schema 源仍须同步**：`db/schema.sql`、`graph_db.py:_create_tables_inline()`、`_NODE_V3_COLUMNS` 列表（新增表/触发器同理）。

## 6. 实测复现命令

```bash
cd /mnt/code1/cpp_semantic_graph
source /mnt/code1/cpp_semantic_graph_env/bin/activate

# 规模 & 基准
python3 -c "import sqlite3,time; c=sqlite3.connect('semantic_graph_full.db'); \
kw='%ota%'; t=time.perf_counter(); \
[c.execute(\"SELECT * FROM node WHERE type='doc_section' AND (name LIKE ? OR doc_title LIKE ? OR content_preview LIKE ?)\",(kw,kw,kw)).fetchall() for _ in range(100)]; \
print('LIKE x100:', round((time.perf_counter()-t)*1000,1),'ms')"

# FTS5 中文分词器验证
python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); \
c.execute(\"CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')\"); \
c.execute(\"INSERT INTO t VALUES('SoC 固件升级与 OTA 刷写流程')\"); \
print('MATCH 升级:', c.execute('SELECT count(*) FROM t WHERE t MATCH ?',('升级',)).fetchone()[0]); \
print('LIKE 升级:', c.execute('SELECT count(*) FROM t WHERE x LIKE ?',('%升级%',)).fetchone()[0])"
```
