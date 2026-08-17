# 设计文档：P1 修复 — doc_section 重复入库（增量累积主根因）

> 日期：2026-08-17
> 来源：第三批全量重建暴露的累积重复（doc_section ~700、doc 关联边成对膨胀）

## 1. 根因（已实锤）

`parser/doc_ingester.py`：
1. **unique_key 含行号**：`doc_section|{rel_path}|{start_line}`。
   文档编辑后行号漂移（哪怕顶部插一行）→ 所有 section 换新 key。
2. **无 per-file stale 清理**：ingest 只做 create/update（L186-197），
   旧 key 节点永久残留 → 重复累积；重复节点又各自带出
   code_refers_to_doc/doc_describes_code 关联边成对膨胀
   （旧库 5927/5943 vs 重建 3227/3227 的差距即由此而来）。

## 2. 未定性项（记录，不阻塞）

- 函数节点 1975（旧）vs 1862（新）差 113：函数 unique_key 稳定
  （type|ns|name|file_path，不含行号），理论不应漂移；旧库已被 reset
  覆盖无法 diff。**推测**与重构期间文件移动/namespace 变化或 header-only
  归一差异有关，未经证实。下次增量周期用巡检指标观察。
- overrides 71 vs 45：多出的 26 条疑为 decl（.h）/def（.cpp）双挂，
  同样无旧库可 diff。当前库 45 条经 TryPrepare 抽查全部真实。

## 3. 修复方案

`GraphDB` 新增 `delete_stale_doc_sections(file_path, keep_keys) -> int`：
删除该 file_path 下 unique_key 不在 keep_keys 的 doc_section 节点
（CASCADE 连带删关联边，正是期望行为）。

`doc_ingester.ingest_file` 入库完新 sections 后调用上述清理，
"该文件此刻源里的 sections" = 权威集合。

不改 unique_key 构成（行号键 + 清理已闭环；改 content_hash 键
仍需清理逻辑且会引入内容微调即换键的问题，收益为零）。

## 4. 改动文件

| 文件 | 改动 |
|------|------|
| `db/graph_db.py` | +delete_stale_doc_sections |
| `parser/doc_ingester.py` | ingest_file 末尾 stale 清理 + stats 记录 sections_deleted |

## 5. 验收标准（反馈环：改文档 → ingest → 数量守恒）

1. **行漂移实验**：任选 docs 下 md，顶部插一行注释 → doc 增量 →
   该文件 doc_section 数量不变（旧行为：翻倍）
2. **删 section 实验**：删除一个 section → ingest → 恰好减少 1
3. 系统：full_test 冒烟全过；doc 关联边保持 1:1 对称
4. 重复实验跑 2 次，数量仍守恒（幂等）

## 6. 风险

| 风险 | 评估 | 缓解 |
|------|------|------|
| keep_keys 为空时误删全文件 sections | 中 | 文件解析出 0 sections 时跳过清理（可能是解析故障） |
| CASCADE 删边影响关联计数 | 低 | 正是期望：重复边应随之消失 | 
