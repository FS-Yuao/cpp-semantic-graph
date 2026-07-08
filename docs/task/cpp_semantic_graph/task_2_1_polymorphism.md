# 阶段 2-1：完整继承链与多态体系提取

## 目标

实现完整的多级继承链查询和虚函数体系精准映射，支持递归查询所有祖先/子类、虚函数重写关系、抽象类/接口识别。

## 现状问题

- 阶段 1 只支持直接继承查询（depth=1），不支持多级递归
- 虚函数 override 关系需要与继承链配合，才能实现"查接口的所有实现"
- 多重继承、钻石继承、虚继承等复杂场景未处理

## 依赖

- 阶段 1-3：查询接口已实现
- 阶段 1-4：正确性验证已建立

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/override_resolver.py` | 新建，override 关系精准解析 |
| `tools/cpp_semantic_graph/query/inheritance_query.py` | 新建，多级继承链递归查询 |
| `tools/cpp_semantic_graph/query/polymorphism_query.py` | 新建，多态体系查询 |

## 设计方案

### 1. 递归继承链查询

```python
def get_full_inheritance_chain(self, class_name: str, direction: str = "down") -> list[InheritanceNode]:
    """递归查询完整继承链
    - direction="up": 所有祖先类
    - direction="down": 所有子类
    - 返回树形结构，每层包含继承权限信息
    """

def get_diamond_inheritance(self, class_name: str) -> DiamondInfo | None:
    """检测钻石继承结构
    - 如果某子类通过多条路径继承同一基类，返回钻石信息
    """
```

### 2. 虚函数体系精准映射

```python
def get_virtual_functions(self, class_name: str, include_inherited: bool = True) -> list[VirtualFuncInfo]:
    """查询类的所有虚函数
    - include_inherited=True: 包含从基类继承的虚函数
    - 返回每个虚函数的：基类声明、子类重写（如有）、是否纯虚
    """

def get_all_overrides(self, func_name: str, class_name: str) -> list[OverrideInfo]:
    """查询虚函数的所有重写实现（递归所有子类）
    - 返回每个重写的：子类名、文件路径、签名
    """

def get_all_implementations(self, interface_class: str) -> list[ClassInfo]:
    """查询接口类的所有实现子类（递归所有派生）
    - 只返回非抽象的叶子类
    """
```

### 3. override 关系精准解析

- 基于 Clang 语义校验 override 合法性（签名完全匹配）
- 建立"子类重写函数 → 基类虚函数"的 `overrides` 边
- 区分 override（签名匹配）vs hides（同名但签名不同）
- 自动识别抽象类（含纯虚函数）和接口类（仅含纯虚函数）

### 4. 复杂继承场景

| 场景 | 处理方式 |
|------|---------|
| 多重继承 | 一条 inherits 边对应一个基类，查询时按边分别遍历 |
| 钻石继承 | 检测同一基类出现多次，标注 `is_diamond` |
| 虚继承 | `extra_info.is_virtual = true`，查询时优先走虚继承路径 |

## 验收标准

- [ ] `get_full_inheritance_chain("BasePeriUpdate", "down")` 返回完整子类树，无遗漏
- [ ] `get_virtual_functions("BasePeriUpdate")` 返回所有虚函数，含继承来的
- [ ] `get_all_overrides("DoUpdate", "BasePeriUpdate")` 返回所有子类的重写实现
- [ ] `get_all_implementations("BasePeriUpdate")` 只返回非抽象的叶子子类
- [ ] override 关系与 clangd `find_implementations` 对比，准确率 100%
- [ ] 多重继承、虚继承场景下继承链不混乱

## 风险点

1. **override 签名匹配复杂度**：const 修饰、引用/指针、默认参数等都会影响签名匹配
2. **多重继承下的歧义**：同名函数从不同基类继承时，需要明确是哪个基类的重写
3. **虚继承的遍历路径**：虚继承改变了继承图的遍历语义，需特殊处理

## 实施步骤

1. 编写 override_resolver.py，从 AST 中提取 override 关系
2. 编写 inheritance_query.py，实现递归继承链查询
3. 编写 polymorphism_query.py，实现虚函数体系查询
4. 处理多重继承、钻石继承、虚继承场景
5. 与 clangd 对比验证，确保准确率 100%

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
