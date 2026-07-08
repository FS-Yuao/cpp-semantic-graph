# 阶段 0-3：libclang 核心语义提取能力验证

## 目标

验证 libclang Python 绑定对 C++ 核心语义的提取能力，与 clangd 查询结果逐项对比，产出兼容性矩阵。

## 现状问题

- 本项目核心价值是精准语义，但 libclang Python 绑定的 API 覆盖度未经验证
- 不同语义点的提取难度差异大：类定义容易，虚函数 override 对应关系难
- 如果不先验证，阶段 1 写了大量代码后发现关键语义提不出来，将导致大面积返工
- 需要量化"能做 / 不能做 / 需 LibTooling 补完"，给出覆盖率数据

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/scripts/validate_semantics.py` | 新建，语义提取验证脚本 |
| `docs/task/cpp_semantic_graph/compatibility_matrix.md` | 新建，兼容性矩阵 |

## 设计方案

### 验证目标与验收标准

| 语义点 | 验证方法 | 与 clangd 对比方式 | 通过标准 |
|--------|---------|-------------------|---------|
| 类定义与命名空间 | 提取 BasePeriUpdate 及其 4 个子类 | `get_type_hierarchy` | 结果一致 |
| 继承关系（含权限） | 提取 SocUpdate → BasePeriUpdate 的 public 继承 | `get_type_hierarchy` | 权限、虚继承标记正确 |
| 虚函数与 override | 提取子类重写函数与基类虚函数的对应关系 | `find_implementations` | 一一对应正确 |
| 函数签名 | 提取函数名、参数类型、返回类型、是否 const | `get_type_info` | 签名完全匹配 |
| 类成员变量 | 提取成员变量名、类型、访问权限 | `get_type_info` | 名称和类型匹配 |
| 访问权限（public/protected/private） | 提取类成员的访问属性 | `get_type_info` | 权限标记正确 |
| 命名空间嵌套 | 提取嵌套命名空间中的类 | `find_symbol` | 命名空间路径正确 |
| 宏展开 | 提取宏定义生成的类/函数 | 手动对比源码 | 展开后结果与编译器一致 |
| 跨文件声明-定义 | 头文件声明与源文件定义的关联 | `get_definition` | 配对正确 |

### 验证脚本结构

```python
class SemanticValidator:
    def __init__(self, compile_commands_path: str):
        self.index = clang.cindex.Index.create()
        # 加载编译数据库

    def validate_class_hierarchy(self):
        """验证类与继承关系"""
        # 提取 BasePeriUpdate 的子类，对比 clangd 结果

    def validate_virtual_functions(self):
        """验证虚函数与 override"""

    def validate_function_signatures(self):
        """验证函数签名"""

    def run_all(self):
        """运行所有验证，输出兼容性矩阵"""
```

### 兼容性矩阵输出格式

| 语义点 | libclang Python | 需 LibTooling 补完 | LibTooling 工作量估算 | 备注 |
|--------|----------------|-------------------|---------------------|------|
| 类定义 | ✅ | - | - | |
| 继承关系 | ✅ | - | - | |
| 虚函数 override | ? | ? | ? 人天 | |
| 调用关系 | ? | ? | ? 人天 | 阶段 0-4 专项验证 |
| ... | | | | |

## 验收标准

- [ ] 9 个语义点逐项验证完成，每个有 pass/fail/partial 结论
- [ ] 兼容性矩阵已填写，含 LibTooling 补完工作量估算（人天）
- [ ] 整体覆盖率 ≥ 80%（否则不进入阶段 1，先补完解析能力）
- [ ] 验证脚本可复用，后续阶段可持续运行

## 风险点

1. **libclang Python 绑定 API 缺失**：某些 C++ 语义在 Python 绑定中没有暴露，需查文档或测试确认
2. **虚函数 override 对应关系**：这是最难提取的语义点之一，libclang 可能不直接提供 override → base 的映射
3. **宏展开的边界**：项目使用大量 ARA 宏（如 `ARA_COM_[...]`），展开后的代码结构可能与源码差异很大

## 实现步骤

1. 编写验证脚本框架（加载 compile_commands.json、解析核心文件）
2. 逐项实现 9 个语义点的提取逻辑
3. 逐项与 clangd 查询结果对比
4. 填写兼容性矩阵，量化 LibTooling 补完工作量
5. 判定覆盖率，给出 go/no-go 建议

## 实际结果

- 所有核心语义均可提取：类定义、继承关系（含访问权限）、纯虚函数、override 检测
- API 兼容性限制：`is_virtual_base()` 不可用，`lexical_children` 不可用
- CXX_OVERRIDE_ATTR 可通过 `cursor.get_children()` 检测
- 整体覆盖率约 90%，虚继承（virtual inheritance）不可检测

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | 核心语义均可提取，覆盖率约 90%，虚继承不可检测为已知限制 | 通过，进入阶段 0-4 |
