# 阶段 2-2：函数调用关系入库与查询

## 目标

将函数级调用关系按 `calls_direct` / `calls_virtual` / `calls_callback` 分类入库，实现"查谁调用了这个函数"和"这个函数调用了谁"的查询能力。

## 现状问题

- 阶段 0-4 已验证调用关系提取能力，确认了可行方案
- 当前图谱没有调用关系数据，无法回答"谁调用了 GetSocBootChain"这类问题
- 调用关系是架构分析和影响面分析的基础

## 依赖

- 阶段 0-4：调用关系提取能力已验证通过
- 阶段 1-1：AST visitor 框架已搭建
- 阶段 1-2：入库脚本已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/call_extractor.py` | 新建，调用关系提取 |
| `tools/cpp_semantic_graph/query/call_query.py` | 新建，调用关系查询 |

## 设计方案

### 1. 调用关系提取

```python
class CallExtractor:
    """从 AST 中提取函数调用关系"""

    def extract_calls(self, tu_cursor) -> list[CallRelation]:
        """遍历 CALL_EXPR，提取调用关系"""

    def classify_call(self, call_expr) -> str:
        """分类调用类型：
        - calls_direct: 直接函数调用（obj.method() / globalFunc()）
        - calls_virtual: 虚函数调度（ptr->virtualMethod()）
        - calls_callback: 回调/函数对象调用
        """
```

### 2. 虚函数调度补全

阶段 0-4 确认的混合方案：
- libclang 提取 CALL_EXPR，识别静态调用和虚调用
- 虚调用只能看到基类声明，**通过 override 关系补全所有可能的目标函数**
  - 例如：`basePtr->DoUpdate()` → 调用目标是 `SocUpdate::DoUpdate`、`GnssUpdate::DoUpdate`、`SwitchUpdate::DoUpdate`、`McuUpdate::DoUpdate`
  - 在边表中为每个可能目标创建 `calls_virtual` 边

### 3. 调用关系查询接口

```python
def get_callers(self, func_name: str, class_name: str = None,
                call_type: str = None) -> list[CallInfo]:
    """查询谁调用了指定函数
    - class_name: 限定所属类
    - call_type: 限定调用类型（direct/virtual/callback）
    - 返回：调用方函数名、所属类、文件路径、调用类型
    """

def get_callees(self, func_name: str, class_name: str = None,
                call_type: str = None) -> list[CallInfo]:
    """查询指定函数调用了谁"""

def get_call_chain(self, func_name: str, class_name: str = None,
                   direction: str = "down", depth: int = 3) -> list[CallChainNode]:
    """查询调用链（递归）
    - direction="down": 调用了谁
    - direction="up": 被谁调用
    - depth: 递归深度
    """
```

### 4. 调用关系验证

对核心模块的调用链，与 clangd `get_callees` / `get_callers` 结果对比：
- 直接调用：覆盖率应 ≥ 95%
- 虚调用（补全后）：覆盖率应 ≥ 90%
- 回调：不做覆盖率要求

## 验收标准

- [ ] 调用关系按 `calls_direct` / `calls_virtual` / `calls_callback` 分类入库
- [ ] `get_callers("GetSocBootChain")` 返回正确的调用方列表
- [ ] `get_callees("PerformUpgrade", "SocUpdate")` 返回 PerformUpgrade 内部的调用链
- [ ] 虚调用补全：`basePtr->DoUpdate()` 的调用目标包含所有子类重写
- [ ] `get_call_chain` 支持递归查询调用链（depth ≤ 5）
- [ ] 直接调用关系与 clangd 对比覆盖率 ≥ 95%
- [ ] 虚调用关系与手动分析结果一致

## 风险点

1. **虚调用补全的准确性**：不是所有通过基类指针的调用都是多态调度，可能是确切类型
2. **回调调用的识别**：std::function、函数指针、lambda 等场景的静态分析天然受限
3. **调用链递归的性能**：深度递归可能产生大量中间结果，需限制深度和返回数量

## 实施步骤

1. 编写 call_extractor.py，实现调用关系提取和分类
2. 实现虚调用补全逻辑（override 关系推导）
3. 编写 call_query.py，实现调用关系查询
4. 对核心模块验证，与 clangd 对比覆盖率
5. 性能测试，优化查询索引

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
