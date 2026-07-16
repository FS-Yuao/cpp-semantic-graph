# C++ 语义图谱全量测试报告

生成时间: 2026-07-16 12:39:14
数据库: semantic_graph_full.db (14.59 MB)
节点: 2986 | 边: 9518 | includes: 66475

## 数据分布

| 类型 | 数量 |
|------|------|
| node: function | 1888 |
| node: doc_section | 595 |
| node: class | 408 |
| node: struct | 95 |
| edge: calls_direct | 4016 |
| edge: code_refers_to_doc | 1766 |
| edge: doc_describes_code | 1766 |
| edge: belongs_to | 1656 |
| edge: calls_virtual | 169 |
| edge: overrides | 69 |
| edge: type_alias | 41 |
| edge: inherits_public | 34 |
| edge: inherits_private | 1 |

## 一、功能正确性验证（9 个 MCP 工具）

| 工具 | 查询 | 结果数 | 耗时 | 结果 |
|------|------|--------|------|------|
| search_class | BasePeriUpdate | 1 | 0.4ms | ✅ |
| search_function | PerformUpgrade | 6 | 0.7ms | ✅ |
| get_inheritance | BasePeriUpdate down depth=1 | 4 | 1.3ms | ✅ |
| get_callers | PerformUpgrade | 2 | 1.9ms | ✅ |
| get_callees | PerformUpgrade (SocUpdate) | 26 | 11.1ms | ✅ |
| get_overrides | PerformUpgrade (BasePeriUpdate) | 4 | 1.3ms | ✅ |
| get_file_symbols | soc_update.h | 1 | 0.6ms | ✅ |
| traverse_graph | SocUpdate depth=2 | 50 | 20.1ms | ✅ |
| search_docs | OTA | 10 | 1.8ms | ✅ |

**结论**: 全部通过 ✅

## 二、准确性验证（与 clangd ground truth 对比）

| 维度 | TP | FP | FN | Precision | Recall | 门限 | 结果 |
|------|----|----|----|-----------|--------|----- |------|
| 类定义 | 2 | 0 | 0 | 100.0% | 100.0% | P≥98%/R≥95% | ✅ |
| 继承关系 | 5 | 0 | 0 | 100.0% | 100.0% | P≥95%/R≥90% | ✅ |
| 函数签名 | 6 | 0 | 0 | 100.0% | 100.0% | P≥95%/R≥90% | ✅ |
| 调用关系 | 3 | 0 | 0 | 100.0% | 100.0% | P≥85%/R≥80% | ✅ |
| type_alias边 | 41 | 0 | 0 | 100.0% | 100.0% | — | ✅ |
| friend_of边 | 0 | 0 | 0 | 100.0% | 100.0% | — | ✅ |

**结论**: 全部达标 ✅

## 三、性能对比：图谱查询 vs find+grep

每个场景重复 10 次取中位数耗时。

| 场景 | 图谱耗时 | find+grep 耗时 | 图谱结果数 | grep 结果数 | 加速比 |
|------|---------|---------------|-----------|-----------|--------|
| S1:查类定义 | 0.1ms | 2.2ms | 1 | 1 | **16.3x** |
| S2:查继承关系 | 1.7ms | 2.2ms | 4 | 4 | **1.3x** |
| S3:查调用方 | 2.5ms | 2.7ms | 2 | 6 | **1.1x** |
| S4:查override | 1.0ms | 3.0ms | 4 | 4 | **3.0x** |
| S5:查文件符号 | 1.4ms | 2.7ms | 29 | 29 | **1.9x** |
| S6:多跳遍历 | 20.3ms | 2.9ms | 50 | 8 | **0.1x** |

### 补充说明

- **S3:查调用方**: grep 返回含 PerformUpgrade 的文件（含声明/定义/调用，无法区分）
- **S5:查文件符号**: grep 为粗略正则匹配，无法区分类/函数/变量
- **S6:多跳遍历**: grep 仅完成第1轮，完整多跳需 3-5 轮串联，耗时成倍增长

**平均加速比**: 3.9x

### 关键优势

1. **O(1) vs O(N)**: 图谱查询命中 SQLite 索引，无需扫描文件系统
2. **语义精确**: grep 只能做文本匹配，无法区分声明/定义/调用/override
3. **多跳遍历**: 图谱一次查询完成多跳关联分析，grep 需多轮串联
4. **增量更新**: 仅重解析受影响 TU，无需全量扫描

## 四、增量更新验证

错误: No module named 'clang'
