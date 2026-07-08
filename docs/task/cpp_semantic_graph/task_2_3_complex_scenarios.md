# 阶段 2-3：复杂场景适配（模板/别名/友元）

## 目标

适配 C++ 的复杂特性：模板实例化区分、类型别名解析、using 声明、友元关系，确保这些场景下继承和调用关系不混乱。

## 现状问题

- 项目大量使用模板（ARA COM Proxy/Skeleton），不同模板特化必须作为独立节点
- `using Alias = Base<T>` 等类型别名如果不解析，查 Base 找不到 Alias
- `using Base::func;` 继承转发的函数如果不关联，调用链会断裂
- 友元类如果不关联，跨类访问关系会遗漏

## 依赖

- 阶段 0-5：模板白名单已定义
- 阶段 2-1：继承链查询已实现
- 阶段 2-2：调用关系已入库

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/template_extractor.py` | 新建，模板实例化提取 |
| `tools/cpp_semantic_graph/parser/alias_extractor.py` | 新建，类型别名 / using 声明提取 |
| `tools/cpp_semantic_graph/parser/friend_extractor.py` | 新建，友元关系提取 |
| `tools/cpp_semantic_graph/query/alias_query.py` | 新建，别名解析查询 |

## 设计方案

### 1. 模板实例化区分

```python
class TemplateExtractor:
    def extract_template_specializations(self, tu_cursor) -> list[NodeInfo]:
        """提取白名单中的模板特化
        - 每个特化作为独立节点，unique_key 包含模板参数
        - 通过 instantiates 边关联模板定义
        """
```

去重策略：
- `Base<int>` 和 `Base<float>` 的 unique_key 不同（含模板参数），作为独立节点
- 同一特化在多个翻译单元中出现时，按 unique_key 合并，记录所有出现位置

### 2. 类型别名解析

```python
class AliasExtractor:
    def extract_type_aliases(self, tu_cursor) -> list[EdgeInfo]:
        """提取类型别名关系
        - using Alias = Base<T>  → type_alias 边（Alias → Base<T>）
        - typedef Base<T> Alias  → type_alias 边（Alias → Base<T>）
        """

    def extract_using_declarations(self, tu_cursor) -> list[EdgeInfo]:
        """提取 using 声明
        - using Base::func  → using_decl 边（子类::func → Base::func）
        """
```

查询时的别名解析：
- 查询 `Base` 时，同时返回所有 Alias（沿 type_alias 边反向查找）
- 查询 `Alias` 时，自动追溯到 `Base`（沿 type_alias 边正向查找）

### 3. 友元关系提取

```python
class FriendExtractor:
    def extract_friends(self, tu_cursor) -> list[EdgeInfo]:
        """提取友元关系
        - friend class Foo  → friend_of 边（Foo → 本类）
        - friend void func()  → friend_of 边（func → 本类）
        """
```

## 验收标准

- [ ] 白名单中的模板特化作为独立节点入库，与模板定义通过 `instantiates` 边关联
- [ ] `Base<int>` 和 `Base<float>` 不混淆，unique_key 不同
- [x] 类型别名入库：`using Alias = Target` 生成 `type_alias` 边（AliasExtractor 已集成到 pipeline）
- [ ] 查询 `Proxy` 时同时返回 `OtaServiceProxy` 等别名（alias_query 待实现）
- [x] using 声明入库：`using Base::func` 生成 `using_decl` 边（AliasExtractor 已处理）
- [x] 友元关系入库：`friend class Foo` 生成 `friend_of` 边（FriendExtractor 已集成到 pipeline）
- [ ] 模板实例化、类型别名场景下继承关系不混乱

> 注：模板实例化（TemplateExtractor）实测在当前 libclang AST 形态下产不出数据
> —— ARA COM 的 `ThreadDrivenProxy<...>` 特化不产生独立 CLASS_DECL 节点，
> 特化名仅出现在 CONSTRUCTOR/TYPE_REF 的 spelling 中。提取器代码保留但默认不调用，
> 待改用 LibTooling 或基于 TYPE_REF 重建时再启用。

## 风险点

1. **模板参数的规范化**：`Proxy<Svc, int>` vs `Proxy<Svc,int>` 空格差异可能导致去重失败
2. **嵌套模板**：`Container<Proxy<Svc>>` 的模板参数本身是模板，解析复杂度高
3. **别名链**：A = B, B = C，需要递归解析到最终原始类型
4. **target 悬空**（实测）：alias 的 target 类型多来自外部库（std/ara::com），
   其 namespace/file_path 无法获取，target_key 指向不存在的节点 → type_alias 边被丢弃。
   别名节点本身入库（含 target_type 元信息），查询时可通过 extra_info 回溯。

## 实施步骤

1. 编写 template_extractor.py，实现模板实例化提取
2. 编写 alias_extractor.py，实现类型别名和 using 声明提取
3. 编写 friend_extractor.py，实现友元关系提取
4. 编写 alias_query.py，实现别名解析查询
5. 用 ARA COM Proxy/Skeleton 场景验证

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-25 | 集成审查 | 发现三个提取器为死代码（grep 引用零命中），未集成进 pipeline，但 README/本任务文档此前按"已完成"表述。根因：验收标准未勾选、accuracy 验证 baseline 仅覆盖类/继承/签名/调用 4 维，未覆盖模板/别名/友元，导致"未实现"无法被自动发现。本次：AliasExtractor/FriendExtractor 已重写（统一 file_path 走 config.make_relative_path、修正 friend 节点缺失）并集成进 ast_visitor.parse()；TemplateExtractor 因 AST 形态产不出数据，保留代码默认不调用。alias_query 与 instantiates 暂未实现。 |
