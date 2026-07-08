# 阶段 2-4：高级查询接口（架构分析）

## 目标

实现 3 个架构分析专用查询接口：查接口的所有实现、查虚函数的所有重写、查类的虚函数清单。

## 依赖

- 阶段 2-1：继承链与多态体系已实现
- 阶段 2-2：调用关系已入库

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/query/architecture_query.py` | 新建，架构分析查询 |

## 设计方案

### 3 个架构分析接口

```python
class ArchitectureQuery:
    def get_interface_implementations(self, interface_name: str) -> list[ClassInfo]:
        """查询接口类的所有实现子类（递归所有派生）
        - 只返回非抽象的叶子类
        - 返回每个实现子类的：类名、文件路径、命名空间
        - 示例：get_interface_implementations("BasePeriUpdate")
          → [SocUpdate, GnssUpdate, SwitchUpdate, McuUpdate]
        """

    def get_virtual_function_overrides(self, func_name: str,
                                       class_name: str) -> list[OverrideInfo]:
        """查询指定虚函数的所有重写实现（跨所有子类）
        - 沿继承链向下递归，收集所有 override
        - 返回每个重写的：子类名、文件路径、行号、签名
        - 示例：get_virtual_function_overrides("DoUpdate", "BasePeriUpdate")
          → [SocUpdate::DoUpdate, GnssUpdate::DoUpdate, ...]
        """

    def get_class_virtual_table(self, class_name: str,
                                include_inherited: bool = True) -> list[VirtualFuncInfo]:
        """查询指定类的所有虚函数清单
        - include_inherited=True: 包含从基类继承的虚函数
        - 返回每个虚函数的：函数名、签名、首次声明类、是否纯虚、子类是否 override
        - 可用于虚表分析和 override 完整性检查
        """
```

### 返回结果结构

```python
@dataclass
class OverrideInfo:
    function_name: str
    class_name: str
    namespace: str
    file_path: str
    line_number: int
    signature: str
    base_class: str              # 被重写的基类
    base_function_signature: str # 基类虚函数签名

@dataclass
class VirtualFuncInfo:
    function_name: str
    signature: str
    declaring_class: str         # 首次声明的类
    is_pure_virtual: bool
    is_overridden: bool          # 是否有子类 override
    override_count: int          # override 的数量
    overrides: list[str]         # override 的子类列表
```

## 验收标准

- [ ] `get_interface_implementations("BasePeriUpdate")` 返回 4 个非抽象子类
- [ ] `get_virtual_function_overrides("DoUpdate", "BasePeriUpdate")` 返回所有子类重写，无遗漏
- [ ] `get_class_virtual_table("SocUpdate", include_inherited=True)` 返回含继承来的虚函数
- [ ] 纯虚函数正确标记，抽象类只作为中间节点不作为"实现"
- [ ] 查询结果与 clangd `find_implementations` 对比一致

## 风险点

1. **抽象类判定**：含纯虚函数的类是抽象类，但某些类可能只含 protected 构造函数来模拟抽象，需确认判定规则
2. **override 计数**：虚函数在多层继承中可能被 override 多次，计数需准确

## 实施步骤

1. 编写 architecture_query.py，实现 3 个接口
2. 用 BasePeriUpdate 及其子类做端到端验证
3. 对比 clangd 查询结果，确认准确率

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
