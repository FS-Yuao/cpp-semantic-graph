# 修复日志：全量解析不索引文档（cpp_search_docs 失效）

**日期**：2026-07-02
**修复者**：华勤 OTA 底层团队
**影响版本**：所有版本（从文档融合功能引入起）

---

## 问题描述

`cpp_search_docs` 工具完全失效——全量解析（`full-parse`）后数据库中 `doc_section` 节点数为 0，文档-代码关联边数为 0，任何关键词搜索都返回"未找到"。

**README 声称**：58 个文档 → 546 个切片 → 1,756 条关联边
**实际（全量解析后）**：0 个切片，0 条关联边

**错误统计证据**：`cpp_search_docs` 成功率仅 50%（实际应能到 90%+），失败案例包括 `seclog`、`OTA升级架构`、`MCU升级`、`bootchain` 等明显应存在的关键词。

## 根因

`pipeline.py` 的 `FullParsePipeline.run()` 方法只做 3 步：
1. 加载 compile_commands，筛选翻译单元
2. 并行解析翻译单元（C++ AST）
3. 入库（node/edge/include）
4. 可选验证

**没有文档解析步骤**。文档融合模块（`doc_ingester.py`、`association_ingester.py`）代码完整，但只在 `incremental_updater.py` 的 `_detect_and_ingest_doc_changes()` 中被调用。全量解析路径完全跳过了文档。

这意味着：**必须先跑 `full-parse` 再跑 `incremental` 才会有文档索引**，单独跑 `full-parse` 文档索引为空。

## 修复内容

### `pipeline.py`

#### 1. ParseReport 新增文档统计字段

```python
# 文档融合
doc_sections_new: int = 0
doc_associations_new: int = 0
associations_rebuilt: bool = False
```

`to_dict()` 同步新增这三个字段。

#### 2. run() 方法入库后新增步骤 3.5：文档融合

```python
# 3.5 文档融合：解析文档切片 + 重建文档-代码关联边
# 全量解析必须包含文档，否则 cpp_search_docs 完全失效
try:
    from .parser.doc_ingester import DocIngester
    from .parser.association_ingester import AssociationIngester

    doc_ingester = DocIngester(
        db_path, config_path=None,
        project_config_path=self.config_path,
    )
    doc_stats = doc_ingester.ingest_from_config(verbose=False)
    doc_ingester.close()
    report.doc_sections_new = doc_stats.get("sections_created", 0)

    assoc_ingester = AssociationIngester(db_path, self.config)
    assoc_stats = assoc_ingester.ingest_content_scan_associations()
    assoc_ingester.close()
    report.doc_associations_new = assoc_stats.get("edges_created", 0)
    report.associations_rebuilt = True
except Exception as e:
    logger.warning("文档融合失败（非致命，cpp_search_docs 将不可用）: %s", e)
    report.associations_rebuilt = False
```

文档融合失败不阻断全量解析（非致命），只记录警告。

## 验证结果

修复后对当前数据库补跑文档解析（无需全量重建）：

| 指标 | 修复前 | 修复后 | README 声称 |
|------|--------|--------|------------|
| doc_section 节点 | 0 | 603 | 546 |
| 文档-代码关联边 | 0 | 2290 | 1756 |
| 处理文档文件数 | 0 | 70 | 58 |

**cpp_search_docs 测试**（修复前全部返回 0 结果）：

| 关键词 | 修复前 | 修复后 |
|--------|--------|--------|
| `seclog` | 0 | **20** |
| `OTA升级架构` | 0 | 有结果 |
| `MCU升级` | 0 | 有结果 |
| `指纹` | 0 | 有结果 |
| `A/B分区` | 0 | 有结果 |

切片数和关联边数超过 README 声称值，是因为文档数量自 README 编写后有所增长（58→70 个文件）。

## 向后兼容性

- **API 兼容**：`run()` 方法签名不变，文档融合默认启用
- **报告兼容**：ParseReport 新增字段有默认值，不影响现有代码
- **降级兼容**：文档融合失败不阻断全量解析，仍能完成 C++ 索引

## 相关文件

- `pipeline.py` — 主修复（新增步骤 3.5）
- `parser/doc_ingester.py` — 文档切片入库（已存在，本次接入）
- `parser/association_ingester.py` — 文档-代码关联（已存在，本次接入）
- `incremental_updater.py` — 增量更新的文档解析逻辑（参考实现）
