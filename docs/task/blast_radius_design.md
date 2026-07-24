# cpp_blast_radius MCP 工具设计文档

## 目标

新增一个 MCP 工具 `cpp_blast_radius`，输入"改动符号/文件"，输出"受影响文件清单 + 分层调用链"，
让于先生在改动前一键评估影响面，替代当前手工组合 `cpp_get_callers` + `cpp_get_overrides` +
`cpp_get_inheritance` + `cpp_traverse_graph` 的工作流。

直接服务 CLAUDE.md「改动前评估影响面」硬性规则，并将影响面从"节点列表"提升为"去重文件集 +
按跳数分层"的产品形态（对标 code-review-graph 的 blast radius）。

## 现状问题

### 1. 递归调用链能力已实现但未暴露 MCP
`CallQuery.get_call_chain(direction, depth)`（call_query.py:211）支持递归 BFS 遍历调用链，
direction="up" 查被谁调用、带 depth 分层。但 MCP 只暴露了一跳的 `cpp_get_callers`/`cpp_get_callees`，
递归版被埋没。查"改这个函数影响谁"只能看到一层，追第二层要手动对每个 caller 再查一遍。

### 2. 缺文件维度聚合
现有工具返回的是**节点列表**（函数/类），不是去重后的**文件清单**。blast radius 的核心价值是
"这 5 个文件要 review"，不是"23 个函数受影响"。节点都带 file_path，但没做文件级去重聚合。

### 3. 缺融合入口
改一个虚函数的真实影响面 = 递归 callers + 所有 overrides + 子类 + 关联文档。当前要手动组合
4 个工具。`cpp_traverse_graph` 支持多 relation_types 但输出是平铺节点，不按"直接/间接"分层。

### 4. 缺 diff 驱动入口
现有工具从符号名起步。缺"给改动文件列表 → 输出影响面"的入口（零件齐全：`cpp_get_file_symbols`
+ 递归 chain，但未组合）。

## 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `query/blast_radius_query.py` | **新增** | BlastRadiusQuery 类：编排递归调用链 + 虚分派展开 + 文件聚合 + 分层 |
| `mcp_server/server.py` | 修改 | 注册 `cpp_blast_radius` 工具 + 懒加载 `_bq` |
| `tests/test_blast_radius.py` | **新增** | 单测 + 系统级断言（边覆盖率/孤儿数回归） |
| `docs/task/blast_radius_design.md` | **新增** | 本文档 |

**不改动**：parser/、db/、incremental_updater.py、现有 query 模块。blast_radius 是纯查询层
编排，复用已有能力，零解析层改动、零 DB schema 变更。

## 设计方案

### 工具签名

```python
@mcp.tool()
def cpp_blast_radius(
    symbols: list[str] | None = None,
    files: list[str] | None = None,
    depth: int = 3,
    include_overrides: bool = True,
    include_subclasses: bool = True,
    direction: str = "up",   # "up"=谁受影响(默认), "down"=依赖什么
) -> str:
```

### 输入解析

- `symbols`: 符号名列表（函数名/类名），直接作为遍历起点
- `files`: 改动文件路径列表（部分匹配），先用 `GraphQuery.get_file_symbols` 展开成符号集
- 两者至少一个；同时给则取并集

### 编排逻辑（BlastRadiusQuery）

```
1. 起点 = symbols ∪ files 展开的符号
2. 对每个起点符号:
   a. 若 include_overrides 且是虚函数 → 加所有 overrides 进起点集（影响多态调度方）
   b. 若 include_subclasses 且是类 → 加直接子类进起点集
3. 对起点集每个函数跑 CallQuery.get_call_chain(direction=direction, depth=depth)
   （direction="up" 查被谁调用 = 受影响方；已含 expand_virtual 虚分派展开）
4. 收集所有 CallChainNode（带 file_path + depth）
5. 聚合到文件维度去重，按 depth 分层
6. 格式化输出
```

### 输出格式

```
## 爆炸半径（共 N 个文件需 review，M 个符号受影响）

### 起点符号
- MyClass::doUpdate  (虚函数，展开 3 个 override)
- OtherClass::process (普通函数)

### 直接受影响（1 跳）
- path/a.cpp  ← CallerA::func1 (calls_direct)
- path/b.cpp  ← CallerB::func2 (calls_virtual)

### 间接受影响（2 跳）
- path/c.cpp  ← 经由 CallerA::func1 → CallerC::entry
- path/d.cpp  ← 经由 CallerB::func2 → ...

### 间接受影响（3 跳）
- path/e.cpp  ← ...

### 汇总
- 受影响文件: N（去重）
- 受影响符号: M
- 最大跳数: 3
- 建议: 需 review 的文件清单可直接用于 PR/审查范围
```

### 与现有工具的边界

| 场景 | 用哪个 |
|---|---|
| 改一个函数前查影响面 | `cpp_blast_radius`（递归+聚合+分层） |
| 只看直接调用方 | `cpp_get_callers`（一跳，快） |
| 通用多关系遍历（继承/文档/包含） | `cpp_traverse_graph`（灵活但平铺） |
| 查虚函数所有重写 | `cpp_get_overrides` |

blast_radius 不替代通用遍历，专注"改动影响面"这一高频场景。

## 验收标准

### 功能验收
1. 输入函数名 → 返回递归调用链（depth>1），按跳数分层
2. 输入虚函数 → 自动展开 overrides，受影响文件含多态调度方
3. 输入文件路径 → 自动展开为符号集再遍历
4. 文件维度去重：同一文件多个受影响符号只列一次（但保留符号明细）
5. direction="up" 默认查受影响方；direction="down" 查依赖方

### 系统级验收（回归照妖镜，遵循 CLAUDE.md 测试经验）
6. **边覆盖率不降**：blast_radius 是纯查询，不改 DB；新增工具前后 `edge` 表行数、
   `node` 表行数完全不变（断言相等）
7. **现有查询工具行为不变**：新增前后对同一符号跑 `cpp_get_callers`/`cpp_traverse_graph`
   结果一致
8. **虚分派展开与 cpp_get_overrides 一致**：blast_radius 展开的 override 集合 ⊇
   `cpp_get_overrides` 返回集合
9. 增量更新后（57 文件变更已同步）blast_radius 在新图谱上跑通，无节点缺失

### 证伪测试（遵循"无论对错都通过=没测"）
10. 对一个**确认无调用方**的叶子函数跑 blast_radius → 应返回"无受影响文件"，
    而非返回空表或全部节点
11. depth=1 时不应出现 2 跳节点（防分层错误）

## 风险点

### R1: 虚分派展开的递归爆炸
虚函数 overrides 多 + depth 大 → 指数膨胀。
**对策**：复用 `get_call_chain` 内部 visited 去重（已有）；MCP 层 depth 上限 [1,5]
（比 traverse 的 6 略紧），max_results 软上限 500，超限截断并提示。

### R2: 起点符号歧义
`get_file_symbols` 可能匹配多个同名符号（不同 namespace/class）。
**对策**：起点集取并集全部遍历，不挑；输出标注"匹配 N 个同名符号"让用户判断。

### R3: 文件路径聚合的归一化
同一文件可能以不同相对路径出现（如带/不带前缀）。
**对策**：复用 db_rel_path 统一表示；聚合时按 basename + 去重，必要时对照 parse_status 表。

### R4: 惰性增量与新工具的交互
MCP 惰性增量超阈值（>20 文件）会降级，blast_radius 可能查到旧图谱。
**对策**：不在工具内强制增量（保持查询轻量）；文档注明"超阈值需先手动 `incremental`"。
本次 57 文件变更已手动增量，图谱为最新。

### R5: direction 语义
"up"在 get_call_chain 里是"被谁调用"。但"爆炸半径"通常指"改动向后传播"——改了 X，
谁受影响 = 谁调用了 X = up。需在 docstring 明确，避免用户混淆。

## 实施步骤

1. **本文档评审** — 于先生确认范围、签名、验收标准
2. 实现 `query/blast_radius_query.py`（BlastRadiusQuery 类）
3. 在 `mcp_server/server.py` 注册工具 + 懒加载
4. 写 `tests/test_blast_radius.py`（含系统级回归断言）
5. 跑测试：blast_radius 单测 + 全量测试套件（暴露连带回归）
6. Review（P0/P1/P2 分级，MCP 工具验证影响面）
7. 更新 README/文档索引 + 生成 HTML

## 参考对标

- code-review-graph (github.com/tirth8205/code-review-graph)：blast radius 返回最小文件集 +
  调用链，token 降 82×。我们借鉴其"文件集 + 分层"产品形态，解析精度保持 clang AST 语义层优势。
