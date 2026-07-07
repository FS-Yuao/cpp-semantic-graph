# cpp_semantic_graph 功能与通用性审查 + 修复报告

**日期**: 2026-06-25
**审查对象**: `drive-vendor/ap/ap-aa/app/hq_ota_service/_tools/cpp_semantic_graph`
**审查重点**: 功能正确性、通用性（项目无关），以及"声称完成但未实现"的流程问题

---

## 一、根因分析：为什么会"报告完成但未实现"

审查发现三个提取器（template/alias/friend）是**死代码**——`grep` 证实从未被 import 或实例化，pipeline 只调 `SemanticExtractor.parse`。但 README 与任务文档此前按"已完成"表述。

根因有两层：

### 1. 验收标准未执行
`task_2_3_complex_scenarios.md` 的验收标准原本全部是 `[ ]` 未勾选，审查记录表为空。文件写出来后被当作"完成了"，但"勾选验收、跑验证"这两步根本没做。

### 2. 验证机制对这批功能是盲区
`accuracy_validator` 的 clangd baseline 只覆盖**类/继承/签名/调用** 4 个维度，**未覆盖模板实例化/类型别名/友元/override/traverse**。因此即使三提取器声称完成，也没有任何自动验证能发现"没集成"——验证维度本身没覆盖到。

> 这正是 CLAUDE.md "不靠肉眼看，用 ground truth 量化"想防的，但这里验证维度本身没覆盖到，导致量化机制失效。

### 流程教训
- **文件存在 ≠ 功能实现**：提取器文件写出来了，但没接入 pipeline，等于没有。
- **验收标准必须勾选并实测**：不能写完代码就把验收项当自动达成。
- **验证维度要覆盖所有声称的功能**：新增功能必须同步加入 accuracy baseline，否则验证是盲的。

---

## 二、本次修复内容（A 类：声称与实现不符）

### A1: 三提取器死代码 → 集成进 pipeline
- **实测 AST 形态**：ARA COM 的 `ThreadDrivenProxy<...>` 特化**不产生独立 CLASS_DECL 节点**（特化名只出现在 CONSTRUCTOR/TYPE_REF 的 spelling 中），`walk_preorder` 找不到含 `<` 的类 → TemplateExtractor 产不出数据。
- **AliasExtractor / FriendExtractor**：重写（统一 file_path 走 `config.make_relative_path`、修正 friend 节点缺失、修正 target_key 悬空问题），集成进 `SemanticExtractor.parse()` 的 `_extract_complex_scenarios`。
- **实测产出**：3 个 TU 提取出 51 个类型别名节点 + 51 条 type_alias 边（如 `StringViewType`→`ara::core::StringView`、`BootChainChanged`→`MethodParameters<...>`）。
- **TemplateExtractor**：保留代码但默认不调用（AST 形态不支持），代码注释说明原因。

### A2: "从 DB 路径推断项目名"不生效 → 修复
- **原问题**：`mcp=FastMCP(instructions=...)` 在模块导入期固化 instructions，此时 `_PROJECT_NAME` 只从环境变量读；`_infer_project_name` 在 `main()` 才跑，但 instructions 已无法更新。
- **修复**：`main()` 推断完 `_PROJECT_NAME` 后，`mcp.instructions = _build_instructions()` 覆写刷新。

### A3: CLI 缺 5 个查询命令 → 补齐
- 补齐 `callers / callees / overrides / traverse / search-docs`，与 MCP 9 工具对齐。实测全部可用（callers 找到虚调用、overrides 找到 4 个子类重写、traverse 找到关联文档节点）。
- 文件头注释从"4 个核心查询命令"改为"9 个查询命令"。

### A4: MCP 工具 docstring 项目硬编码 → 去除
- 9 个工具的 docstring 示例从 `SocUpdate/BasePeriUpdate/PerformUpgrade/GetSocBootChain` 改为通用占位 `MyClass/doWork/getValue` 等。

---

## 三、文档同步修订

- `task_2_3_complex_scenarios.md`：验收标准如实勾选（别名/using/友元 ✅，模板实例化/alias_query 待实现），审查记录补本次结论。
- `README.md`：边类型描述改为准确（type_alias/using_decl/friend_of 已启用，instantiates 暂未启用），新增"复杂场景说明"小节如实说明各特性状态。

---

## 四、未修复的遗留项（P1，本次范围外）

以下为审查发现但本次未修（属 B 类功能 bug，建议后续处理）：

| # | 位置 | 问题 |
|---|---|---|
| B1 | incremental_updater.py:98-182 | 增量更新事务边界缺失，中途异常数据不一致 |
| B2 | graph_db.py:135-139 | upsert 不更新行号 |
| B3 | graph_db.py:186-203 | 边 upsert 冲突只跳过不更新 extra_info |
| B4 | change_detector.py:168 | 重命名(R)当 modified，旧路径残留 |
| B5 | ast_visitor.py:135,157 | 构造/析构函数体内调用丢失 |
| B6 | ast_visitor.py:465 | operator 过滤误伤合法函数名 |
| B9 | compile_db.py:27 | 硬编码 "src-gen" 判断生成代码（通用性） |
| B10 | compile_db.py:57 | 不处理 arguments 格式（通用性） |
| B11 | call_query.py:333 | PRAGMA database_list 反推 DB 路径重开连接 |

详见首次审查报告全文（本次会话产出）。
