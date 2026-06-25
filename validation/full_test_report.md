# C++ 语义图谱全量测试报告

生成时间: 2026-06-25 16:52:47
数据库: semantic_graph_full.db (7.54 MB)
节点: 1182 | 边: 1362 | includes: 36749

## 数据分布

| 类型 | 数量 |
|------|------|
| node: function | 922 |
| node: class | 212 |
| node: struct | 48 |
| edge: belongs_to | 611 |
| edge: calls_direct | 514 |
| edge: overrides | 151 |
| edge: calls_virtual | 48 |
| edge: type_alias | 31 |
| edge: inherits_public | 7 |

## 一、功能正确性验证（9 个 MCP 工具）

| 工具 | 查询 | 结果数 | 耗时 | 结果 |
|------|------|--------|------|------|
| search_class | BasePeriUpdate (模糊) | 1 | 10.2ms | ❌ |
| search_function | PerformUpgrade | 11 | 20.4ms | ✅ |
| get_inheritance | BasePeriUpdate down depth=1 | 4 | 1.6ms | ✅ |
| get_callers | PerformUpgrade | 1 | 4.6ms | ❌ |
| get_callees | PerformUpgrade (SocUpdate) | 13 | 4.7ms | ✅ |
| get_overrides | PerformUpgrade (BasePeriUpdate) | 4 | 1.5ms | ✅ |
| get_file_symbols | soc_update.cpp | 29 | 1.1ms | ❌ |
| traverse_graph | SocUpdate depth=2 | 42 | 4.4ms | ✅ |
| search_docs | OTA | 0 | 0.1ms | ✅ |

**结论**: 存在失败 ❌

## 二、准确性验证（与 clangd ground truth 对比）

| 维度 | TP | FP | FN | Precision | Recall | 门限 | 结果 |
|------|----|----|----|-----------|--------|----- |------|
| 类定义 | 2 | 0 | 0 | 100.0% | 100.0% | P≥98%/R≥95% | ✅ |
| 继承关系 | 5 | 0 | 0 | 100.0% | 100.0% | P≥95%/R≥90% | ✅ |
| 函数签名 | 6 | 0 | 0 | 100.0% | 100.0% | P≥95%/R≥90% | ✅ |
| 调用关系 | 1 | 0 | 2 | 100.0% | 33.3% | P≥85%/R≥80% | ❌ |
| type_alias边 | 31 | 0 | 0 | 100.0% | 100.0% | — | ✅ |
| friend_of边 | 0 | 0 | 0 | 100.0% | 100.0% | — | ✅ |

**结论**: 存在不达标 ❌

## 三、性能对比：图谱查询 vs find+grep

每个场景重复 10 次取中位数耗时。

| 场景 | 图谱耗时 | find+grep 耗时 | 图谱结果数 | grep 结果数 | 加速比 |
|------|---------|---------------|-----------|-----------|--------|
| S1:查类定义 | 0.2ms | 4.7ms | 1 | 1 | **28.5x** |
| S2:查继承关系 | 0.5ms | 4.9ms | 4 | 4 | **9.5x** |
| S3:查调用方 | 1.6ms | 6.0ms | 1 | 6 | **3.7x** |
| S4:查override | 1.2ms | 9.0ms | 4 | 4 | **7.5x** |
| S5:查文件符号 | 0.8ms | 4.7ms | 29 | 29 | **6.0x** |
| S6:多跳遍历 | 3.8ms | 6.0ms | 42 | 7 | **1.6x** |

### 补充说明

- **S3:查调用方**: grep 返回含 PerformUpgrade 的文件（含声明/定义/调用，无法区分）
- **S5:查文件符号**: grep 为粗略正则匹配，无法区分类/函数/变量
- **S6:多跳遍历**: grep 仅完成第1轮，完整多跳需 3-5 轮串联，耗时成倍增长

**平均加速比**: 9.4x

### 关键优势

1. **O(1) vs O(N)**: 图谱查询命中 SQLite 索引，无需扫描文件系统
2. **语义精确**: grep 只能做文本匹配，无法区分声明/定义/调用/override
3. **多跳遍历**: 图谱一次查询完成多跳关联分析，grep 需多轮串联
4. **增量更新**: 仅重解析受影响 TU，无需全量扫描

## 四、增量更新验证

错误: compile_commands.json not found: /mnt/code1/adc4.0/drive-vendor/ap/ap-aa/compile_commands.json
