# 阶段 4-3：性能与稳定性优化

## 目标

优化核心查询性能，确保万级节点查询 < 10ms；优化全量解析内存，避免 OOM；完善异常处理，解析失败的翻译单元不影响整体。

## 现状问题

- 大项目全量解析时内存可能飙升，存在 OOM 风险
- 多跳遍历查询在大图谱上可能很慢
- 单个翻译单元解析失败可能导致整个流程中断
- 缺少性能基线数据

## 依赖

- 阶段 1-5：全量解析端到端流程已跑通
- 阶段 2-5：多跳遍历查询已实现
- 阶段 4-1：增量更新已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/ast_visitor.py` | 修改，内存优化 |
| `tools/cpp_semantic_graph/query/traverse.py` | 修改，遍历性能优化 |
| `tools/cpp_semantic_graph/pipeline.py` | 修改，异常处理与容错 |
| `tools/cpp_semantic_graph/db/graph_db.py` | 修改，查询性能优化 |

## 设计方案

### 1. 查询性能优化

| 查询类型 | 当前（估） | 目标 | 优化手段 |
|---------|-----------|------|---------|
| 单表查询（search_class） | ~5ms | < 5ms | 已有索引，保持 |
| 1 跳关联（get_inheritance） | ~10ms | < 10ms | 已有索引，保持 |
| 多跳遍历（3 跳） | ~100ms? | < 50ms | SQL 优化 + 结果缓存 |
| 文档查询 | ~20ms? | < 20ms | 全文索引 |

多跳遍历优化：
- 限制最大遍历深度（默认 5 跳）
- 限制单次返回节点数（默认 100）
- 用 CTE（Common Table Expression）优化递归 SQL
- 遍历路径缓存（相同起点+关系类型的遍历结果缓存 30s）

### 2. 全量解析内存优化

```python
class MemoryOptimizedPipeline:
    """分翻译单元逐个解析，控制内存峰值"""

    def full_parse(self, compile_commands: list, max_workers: int = 1):
        """逐个翻译单元解析
        - max_workers=1: 串行解析，内存最低
        - 每个翻译单元解析完成后，立即写入中间 JSON，释放 AST 内存
        - 批量入库时使用事务批量提交
        """
```

策略：
- 默认串行解析（max_workers=1），内存峰值 = 单个翻译单元的 AST 大小
- 用户可手动调高并发数（max_workers=2~4），需配合内存监控
- 每个翻译单元解析完后立即 `del tu` 释放内存
- 入库时使用 SQLite 事务批量提交（每 1000 条一次）

### 3. 异常处理与容错

```python
class ParseResult:
    source_path: str
    status: str          # "success" / "partial" / "failed"
    error_message: str | None
    nodes_count: int
    edges_count: int

class RobustPipeline:
    def parse_with_fallback(self, tu_path: str) -> ParseResult:
        """容错解析
        - 解析成功 → 返回完整结果
        - 部分解析失败 → 返回部分结果 + 标记 partial
        - 完全失败 → 返回 failed + 错误日志，不影响其他翻译单元
        """

    def generate_parse_report(self, results: list[ParseResult]) -> str:
        """生成解析报告
        - 总翻译单元数 / 成功 / 部分成功 / 失败
        - 失败原因分类（缺少头文件、语法错误、内存不足等）
        - 成功解析的覆盖率
        """
```

### 4. 性能基线测试

```bash
# 性能基线测试脚本
python -m cpp_semantic_graph benchmark \
  --db /path/to/graph.db \
  --iterations 100
```

测试项：

| 测试 | 说明 | 目标 |
|------|------|------|
| search_class | 100 次类名查询 | P95 < 5ms |
| get_inheritance | 100 次继承查询（depth=3） | P95 < 20ms |
| traverse_graph | 100 次多跳遍历（3 跳） | P95 < 50ms |
| 全量解析 | 整个项目 | 内存 < 4GB |
| 增量更新（单文件） | 1 个 .cpp | < 1s |
| 增量更新（头文件） | 1 个 .h | < 5s |

## 验收标准

- [ ] 万级节点单表查询 P95 < 5ms
- [ ] 3 跳遍历查询 P95 < 50ms
- [ ] 全量解析内存峰值 < 4GB（百万行项目）
- [ ] 解析失败的翻译单元不影响整体流程
- [ ] 解析报告自动生成，包含成功率统计
- [ ] 性能基线测试脚本可复用

## 风险点

1. **SQLite 并发写入**：多进程同时写入时可能出现锁竞争
2. **大翻译单元**：某些头文件（如 ARA COM 生成的头文件）可能包含数千行代码，单个 AST 很大
3. **遍历路径缓存一致性**：增量更新后缓存可能过期，需设计缓存失效机制

## 实施步骤

1. 优化多跳遍历查询性能（SQL CTE + 结果缓存）
2. 优化全量解析内存（串行解析 + 及时释放）
3. 完善异常处理（容错解析 + 错误报告）
4. 编写性能基线测试脚本
5. 跑性能基线，记录数据
6. 对不达标的项做针对性优化

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
