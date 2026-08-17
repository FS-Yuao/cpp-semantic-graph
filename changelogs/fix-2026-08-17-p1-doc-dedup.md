# fix-2026-08-17-p1-doc-dedup：doc_section 增量重复入库（P1）

> 设计文档：docs/fix-2026-08-17-p1-doc-dedup.md
> 来源：第三批全量重建暴露（doc_section ~700 重复、关联边 5927/5943 不对称）

## 根因（实锤）

`parser/doc_ingester.py` 两个缺陷叠加：
1. `unique_key = doc_section|{rel_path}|{start_line}` 含**行号**——文档编辑后
   行号漂移，所有 section 换新 key
2. ingest 只 create/update，**无 per-file stale 清理**——旧 key 节点永久残留，
   连带关联边成对膨胀

## 修复

复用现成 `GraphDB.delete_removed_nodes(file_path, retained_keys)`：
`ingest` 每个文件入库后，删除该 file_path 下 unique_key 不在本次解析集合中的
doc_section（CASCADE 连带删重复关联边）。stats 新增 `sections_deleted`。
不改 unique_key 构成（行号键+清理已闭环，改 hash 键仍需清理且引入内容微调
即换键问题）。空解析（not sections）提前 skip，天然防御误删。

## 验证（反馈环：改文档 → ingest → 数量守恒）

| 实验 | 操作 | 结果 |
|------|------|------|
| A 行漂移 | seclog 文件第 8 行插注释（全部 section 行号+1） | 该文件 14→**14**（旧行为 28）；sections_deleted=15（含当日过期 section） |
| B 反向恢复 | 还原文件（再次全量换 key） | created 13 / deleted 13 对称，仍 **14**，幂等 ✅ |
| 关联对称 | ingest 后重建关联 | code_refers_to_doc / doc_describes_code = **3253/3253** ✅ |
| 系统 | full_test | 冒烟 11/11 + formatter 10/10 ✅ |

## 未定性项（记录，不阻塞）

- 函数节点 1975（旧）vs 1862（新）差 113：key 稳定理论不应漂移，旧库已
  reset 无法 diff；**推测**与重构期文件移动有关，未经证实。下次增量周期观察。
- overrides 71→45 多出的 26 条疑为 decl/def 双挂，同无旧库可 diff；当前库
  45 条经 TryPrepare 抽查全部真实。
- 已知关联主题 P1-B：删 doc 节点 CASCADE 会删其他文件出边，ingest 后需
  `_rebuild_associations` 补回（incremental doc_only 路径已自动跑，实验中
  手动跑过；直接调 DocIngester 的脚本需注意）。
