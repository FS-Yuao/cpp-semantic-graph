# 阶段 1-1：libclang AST Visitor 开发（类/继承/函数/include 提取）

## 目标

编写 libclang Python AST visitor，从 C++ 源码中提取类、继承、函数、include 依赖四类核心语义，输出标准化中间 JSON。

## 现状问题

- 阶段 0 已验证 libclang 对核心语义的提取能力，现在需要将验证脚本升级为可复用的生产级解析器
- 当前没有结构化的代码语义数据，AI 查代码只能靠 grep 或 clangd 实时查询
- 需要一次性解析产出持久化数据，支持后续图谱入库和遍历查询

## 依赖

- 阶段 0-1：`compile_commands.json` 已生成
- 阶段 0-2：交叉编译环境已验证可运行
- 阶段 0-3：兼容性矩阵已确认覆盖率 ≥ 80%
- 阶段 0-5：模板白名单已定义

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `parser/config.py` | 新建，**项目配置加载** — 从 YAML 加载，提供统一的路径判断接口 |
| `parser/compile_db.py` | 新建，**编译数据库** — 加载 compile_commands.json，参数清洗 |
| `parser/models.py` | 新建，**数据模型** — NodeInfo/EdgeInfo/IncludeDep/ParseResult |
| `parser/ast_visitor.py` | 新建，**AST visitor 主逻辑** — 基于 ProjectConfig 过滤，不硬编码项目名 |
| `cpp_semantic_graph.yaml` | 新建，**项目配置文件** — 换项目只改此文件 |

> **设计变更**：原计划拆为 class_extractor / function_extractor / include_extractor / template_handler 四个独立模块，实际实现中合并为单文件 `ast_visitor.py`，因为 libclang 的 `walk_preorder` 天然只遍历一次 AST，拆成多模块需要多次遍历反而增加开销。

## 设计方案

### 核心设计：项目无关，配置驱动

**所有过滤逻辑基于 `ProjectConfig`，不硬编码任何项目名称。**

```python
# 用法：加载配置 → 创建提取器 → 解析
config = ProjectConfig.from_yaml("cpp_semantic_graph.yaml")
extractor = SemanticExtractor(config)
result = extractor.parse(entry)
```

过滤逻辑对照表：

| 过滤场景 | 配置方法 | 说明 |
|---------|---------|------|
| 是否提取此文件的类/函数 | `config.should_extract_node(file)` | 在 `source_paths` + `generated_paths` 内 |
| 是否提取指向此文件的调用 | `config.should_extract_call(file)` | callee 在项目范围内 |
| 是否属于生成代码 | `config.is_generated(file)` | 决定入库策略 |
| 路径转相对路径 | `config.make_relative_path(file)` | 统一存储格式 |

### 模块架构

```
cpp_semantic_graph.yaml     # 项目配置（换项目只改这个）
parser/
├── config.py                # ProjectConfig — 配置加载 + 路径判断接口
├── compile_db.py            # CompileDB — 编译数据库加载 + 参数清洗
├── models.py                # 数据模型 — NodeInfo/EdgeInfo/IncludeDep/ParseResult
└── ast_visitor.py           # SemanticExtractor — 核心语义提取
    ├── _extract_classes()    # 类定义、继承、抽象类检测
    ├── _extract_functions()  # 函数签名、虚函数、override
    ├── _extract_inheritance()# 继承关系（含权限）
    ├── _extract_calls()      # 调用关系（CALL_EXPR + MEMBER_REF_EXPR）
    ├── _extract_includes()   # #include 依赖
    └── _deduplicate()        # 翻译单元内去重
```

### 提取字段规范

**类节点**：
```json
{
  "type": "class",
  "name": "SocUpdate",
  "namespace": "hq_ota",
  "file_path": "include/soc_update.h",
  "start_line": 9,
  "end_line": 45,
  "extra_info": {
    "is_abstract": false,
    "access": "public",
    "template_params": null,
    "template_specialization_of": null
  }
}
```

**继承边**：
```json
{
  "relation_type": "inherits_public",
  "from_unique_key": "class|hq_ota|SocUpdate|include/soc_update.h",
  "to_unique_key": "class|hq_ota|BasePeriUpdate|include/base_peri_update.h",
  "extra_info": {
    "is_virtual": false
  }
}
```

**函数节点**：
```json
{
  "type": "function",
  "name": "PerformUpgrade",
  "namespace": "hq_ota::SocUpdate",
  "file_path": "src/peri_update/soc/soc_update.cpp",
  "start_line": 157,
  "end_line": 180,
  "extra_info": {
    "signature": "ErrorCode PerformUpgrade() override",
    "is_virtual": false,
    "is_pure_virtual": false,
    "is_override": true,
    "is_static": false,
    "is_const": false,
    "access": "public",
    "overrides_base": "class|hq_ota|BasePeriUpdate|include/base_peri_update.h::PerformUpgrade"
  }
}
```

**include 依赖**：
```json
{
  "source_file": "src/peri_update/soc/soc_update.cpp",
  "included_file": "include/soc_update.h",
  "is_system": false
}
```

### 去重策略

- `unique_key` = `type|namespace|name|file_path`
- 同一翻译单元多次解析时覆盖旧数据
- 同一实体在多个翻译单元中出现时只创建一次节点（按 unique_key 去重），但记录所有出现位置
- **未解析调用边的去重**：`to_unique_key` 为空时，用 `callee_name` 参与去重 key，避免同一调用者的不同调用被错误合并

### Phase 0 发现的关键实现技巧

1. **Proxy 方法调用**：`CALL_EXPR.referenced` 对 ARA COM Proxy 方法返回 None，**必须用 `MEMBER_REF_EXPR.referenced`**
2. **Enclosing Function 查找**：`MEMBER_REF_EXPR` 的 `semantic_parent` 链可能为空（Proxy 方法调用时），需**预建行号范围映射表**做 fallback
3. **调用关系过滤**：基于 `ProjectConfig.should_extract_call()` 过滤，`source_paths` + `generated_paths` 内的 callee 保留
4. **compile_commands.json 参数清洗**：`-isystem` + path 需合并、`-o`/`-c`/`-W*` 需删除
5. **is_system 判定**：`INCLUSION_DIRECTIVE.included_file` 在本 libclang 版本恒为 None、无 `is_system`/`is_angled` 属性，`displayname` 已去尖括号/引号无法区分。改用 **token 读原始 `#include` 写法**：扫描 token 找第一个以 `<`（系统头）或 `"`（项目头）开头者。token 实测拆为 `['#', 'include', '"header"' 或 '<' ...]`，故不能写死索引，需扫描。详见 `_is_system_include`

## 验收标准

- [ ] 对 `soc_update.cpp` 的解析结果与 clangd 查询结果一致（类、继承、函数签名）
- [ ] 输出标准化 JSON，字段符合上述规范
- [ ] 命名空间隔离的同名类不会混淆（unique_key 包含命名空间）
- [ ] include 依赖提取完整（直接 include + 间接 include）
- [ ] 模板白名单中的特化能正确导出为独立节点
- [ ] 解析失败的单个翻译单元不影响整体流程，记录错误日志

## 风险点

1. **libclang Python 绑定的线程安全**：多翻译单元并行解析时需确认是否需要进程级并行
2. **大文件内存占用**：单个翻译单元的 AST 可能很大，需测试内存峰值
3. **宏生成的代码**：ARA COM 宏生成的代码可能缺少源码位置信息

## 实施步骤

1. 搭建解析器项目结构（包、配置文件）
2. 实现 config.py：项目配置加载 + 路径判断接口
3. 实现 compile_db.py：编译数据库加载 + 参数清洗
4. 实现 models.py：数据模型定义
5. 实现 ast_visitor.py：核心语义提取（类/函数/继承/调用/include）
6. 创建 cpp_semantic_graph.yaml：项目配置文件
7. 对核心模块做端到端验证，对比 clangd 结果
8. 验证 Proxy 方法调用提取（BootChainChanged/GetSocBootChain/EnterUpgrade）

## 实际结果

- 项目无关设计已实现：所有过滤逻辑基于 `ProjectConfig`，换项目只改 YAML
- 类/继承/虚函数/override 提取正确：soc_update.cpp → 10 个类，1 条继承，14 个 override
- Proxy 方法调用提取成功：ota_service.cpp → BootChainChanged/GetSocBootChain/EnterUpgrade 全部识别
- 调用关系 175 条（soc_update.cpp）+ 52 条（ota_service.cpp），无标准库噪声
- include 依赖 1502 条（含系统头文件）
- 关键实现：MEMBER_REF_EXPR + 行号映射 fallback + 未解析边 callee_name 去重

### is_system 修复（task 1-5 验收时回溯发现）

- **问题**：`include_dep` 表 2730 行 `is_system` 全为 0，系统头（`cstdint`/`bits/c++config.h`）被误判为项目头
- **根因**：原判断 `included_file.startswith("/usr/") or "SDK" in included_file`，但 `INCLUSION_DIRECTIVE.included_file` 恒为 None，fallback 到 `displayname`（已去括号），判断失效
- **修复**：新增 `_is_system_include`，用 token 读原始 `#include` 写法，`<` 开头为系统头、`"` 开头为项目头
- **验证**：全量解析 hq_ota_service（25 TU）后，系统头 6454 / 项目头 2121，区分正确，`IncludeQuery` 的 `skip_system` 功能可用

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | AST Visitor 完成，项目无关设计，Proxy 方法调用提取成功 | 通过，进入 Phase 1-2 |
| 2026-06-23 | is_system 判定修复（token 判尖括号），include_dep 系统头标记正确 | 通过，task 1-5 include 查询可用 |
