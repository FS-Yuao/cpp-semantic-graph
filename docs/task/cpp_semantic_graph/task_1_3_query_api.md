# 阶段 1-3：核心检索 API 开发

## 目标

封装 4 个高频核心查询接口，实现"替代全局 grep"的基本价值：按类名、函数名、文件路径快速定位代码实体和继承关系。

## 现状问题

- 当前 AI 查代码靠 grep 全扫项目，token 消耗大、定位慢
- 图谱数据已入库，但还没有可用的查询接口
- 需要 MVP 级查询能力，让 AI 能毫秒级返回"类在哪、继承谁、函数签名是什么"

## 依赖

- 阶段 1-2：SQLite 图谱库已建好，数据已入库

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/query/__init__.py` | 新建，查询包 |
| `tools/cpp_semantic_graph/query/graph_query.py` | 新建，核心查询逻辑 |
| `tools/cpp_semantic_graph/query/query_models.py` | 新建，查询结果数据模型 |
| `tools/cpp_semantic_graph/cli.py` | 新建，CLI 命令行工具 |
| `tools/cpp_semantic_graph/validation/test_query_api.py` | 新建，端到端验证脚本（解析→导入→查询→断言） |

## 设计方案

### 4 个核心查询接口

```python
class GraphQuery:
    def __init__(self, db_path: str): ...

    def search_class(self, name: str, exact: bool = False) -> list[ClassInfo]:
        """按类名搜索，返回类信息与文件位置
        - exact=True: 精确匹配
        - exact=False: 模糊匹配（LIKE %name%）
        """

    def get_inheritance(self, class_name: str, direction: str = "down",
                        depth: int = 1) -> list[InheritanceInfo]:
        """查询类的继承关系
        - direction="up": 查父类
        - direction="down": 查子类
        - depth: 递归深度（1=直接，-1=全部）
        - 返回结果含继承权限（public/protected/private）
        """

    def search_function(self, name: str, class_name: str = None) -> list[FunctionInfo]:
        """按函数名搜索，返回签名、所属类、文件位置
        - class_name: 可选，限定所属类
        - 返回结果含是否虚函数、是否 override
        """

    def get_file_symbols(self, file_path: str) -> list[SymbolInfo]:
        """按文件路径查询文件内所有类与函数"""
```

### 查询结果数据模型

```python
@dataclass
class ClassInfo:
    name: str
    namespace: str
    file_path: str
    start_line: int
    end_line: int
    is_abstract: bool
    template_params: list[str] | None

@dataclass
class InheritanceInfo:
    parent: ClassInfo
    child: ClassInfo
    access: str          # public / protected / private
    is_virtual: bool

@dataclass
class FunctionInfo:
    name: str
    signature: str
    namespace: str
    class_name: str | None
    file_path: str
    start_line: int
    is_virtual: bool
    is_override: bool
    is_pure_virtual: bool
    is_static: bool

@dataclass
class SymbolInfo:
    node_type: str       # class / function
    name: str
    namespace: str
    start_line: int
    end_line: int
    extra: dict
```

### CLI 工具

```bash
# 按类名搜索
python -m cpp_semantic_graph search-class "SocUpdate"

# 查继承关系（向下 2 级）
python -m cpp_semantic_graph inheritance "BasePeriUpdate" --direction down --depth 2

# 按函数名搜索
python -m cpp_semantic_graph search-func "PerformUpgrade"

# 查文件内符号
python -m cpp_semantic_graph file-symbols "soc_update.cpp"
```

### 性能目标

- 单表查询（search_class / search_function）：< 5ms
- 1 跳关联查询（get_inheritance depth=1）：< 10ms
- 文件内符号查询：< 10ms

## 验收标准

- [x] 4 个查询接口全部实现，返回结构化结果
- [x] CLI 工具可用，支持 4 种查询命令（另含 import / stats 共 6 子命令）
- [x] `search_class("BasePeriUpdate")` 返回正确，含命名空间（`update::`）和文件路径
- [x] `get_inheritance("BasePeriUpdate", "down")` 返回 4 个子类（SocUpdate, GnssUpdate, SwitchUpdate, McuUpdate）
- [x] `search_function("PerformUpgrade")` 返回函数签名、所属类、是否 override（9 条，基类纯虚 / 子类 override）
- [x] `get_file_symbols("soc_update.cpp")` 返回文件内所有类和函数（29 符号）
- [x] 单次查询耗时 < 10ms（实测 0.1–0.6ms）

## 风险点

1. **继承关系递归查询性能**：深度递归时可能产生大量 JOIN，需测试性能
2. **模糊搜索结果过多**：LIKE %name% 可能返回大量结果，需支持分页或限制返回数量
3. **命名空间匹配**：用户搜索时可能不带命名空间，需同时匹配带命名空间和不带的

## 实施步骤

1. 定义查询结果数据模型（query_models.py）
2. 实现核心查询逻辑（graph_query.py）
3. 实现 CLI 工具（cli.py）
4. 用核心模块数据做端到端验证
5. 性能测试与优化

## 实际结果

- **4 个文件全部完成**：query/\_\_init\_\_.py / query/graph_query.py / query/query_models.py / cli.py
- **额外新增**：validation/test_query_api.py — 端到端验证脚本（解析→导入→查询→断言）
- **数据模型**：4 个 dataclass（ClassInfo / InheritanceInfo / FunctionInfo / SymbolInfo），均含 to_dict 序列化
- **GraphQuery 接口**：4 个核心（search_class / get_inheritance / search_function / get_file_symbols）+ 2 个辅助（get_class_by_key / get_function_by_key）
- **CLI**：6 个子命令（search-class / inheritance / search-func / file-symbols + import / stats）
- **端到端验证通过**（5 个核心模块 cpp，0 fatal error）：
  - 入库：378 nodes（class 24 / function 348 / struct 6）、574 edges（inherits_public 4 / calls_* 328 / belongs_to 242）、2047 includes
  - 继承边正好 4 条，与设计预期一致
- **查询验证**（CLI + API 双跑）：
  - `search_class("BasePeriUpdate")` → 1 个，`update::BasePeriUpdate` (abstract)，base_peri_update.h:13-80
  - `get_inheritance("BasePeriUpdate", "down")` → 4 个子类，全 `--public-->`
  - `search_function("PerformUpgrade")` → 9 个，基类 `pure=True/override=False`、子类 `override=True` 正确区分
  - `get_file_symbols("soc_update.cpp")` → 29 符号，含 SocUpdate 全部成员函数
- **性能**（目标 10ms）：search_class 0.1ms / get_inheritance 0.3ms / search_function 0.2ms / get_file_symbols 0.5ms。继承查询走 BFS + 单表索引，未用递归 JOIN
- **代码修复**（graph_query.py）：`get_inheritance` 读取 `is_virtual` 时，`e["extra_info"]` 从 DB 取出为 JSON 字符串，原 `isinstance(..., dict)` 判断恒 False，会导致未来提取虚继承信息后静默丢失。新增 `_edge_extra` 辅助函数统一解析 edge.extra_info。当前数据无虚继承，结果未变；为 task 2-1 多态分析铺路
- **产物保留**：output/*.json（5 文件，318K–612K）、semantic_graph.db（960K），可供后续 task 复用

## 遗留问题（P2，不影响本阶段验收）

1. **namespace 显示冗余冒号**：`get_file_symbols` 输出自由函数显示 `update::::ElevatedPrefix`（4 冒号）。根因是 ast_visitor 提取自由函数 namespace 时带尾部 `::`，属 task 1-1 范畴，query 层仅透传。不影响查询功能
2. **search_function 返回声明+定义**：PerformUpgrade 返回 9 条（.h 声明 + .cpp 定义各一）。这是 task 1-2 的设计——unique_key 含 file_path 区分声明与定义。是否符合预期待 task 1-4 准确性验证确认
3. **is_virtual 当前恒 False**：ast_visitor 未提取虚继承（phase 0 已知限制，`is_virtual_base()` 不可用），EdgeInfo.extra_info 为空。task 2-1 补充提取后配合本次 `_edge_extra` 修复可正确读取

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | 4 查询接口 + CLI 全部实现，端到端验证 5/5 断言通过，性能 < 1ms，修复 is_virtual 读取隐患 | 通过，进入 Phase 1-4 |
