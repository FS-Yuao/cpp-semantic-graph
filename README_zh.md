# cpp-semantic-graph

> C++ 语义图谱 — 让 AI 精准理解你的 C++ 代码库

[English](README.md)

为 AI 编程助手（Claude Code、Cursor、Windsurf 等）构建 C++ 代码的语义知识图谱，通过 [MCP 协议](https://modelcontextprotocol.io/) 暴露 11 个查询工具，让 AI 能直接搜索类定义、查继承关系、追踪调用链、分析影响面——无需翻文件、无需 grep。

## 本项目为何存在：用 clang，而非 tree-sitter

大部分通用语言的代码图谱工具用 **tree-sitter** 解析源码——它是一个快速的、增量式的**语法**解析器。这个选择对很多语言没问题，但对 **C++ 语义**图谱来说是错的工具。

**核心区别是语法解析 vs 语义解析：**

- **语法解析器**回答的是"这些 token 是否构成合法的 C++ 程序、语法树长什么样"——它**不去解析一个名字到底指代哪个实体**。
- **语义分析**（编译器前端做的事）回答的是"这个符号具体指代哪个实体、它是什么类型、和谁有关系"——这需要**类型解析与名字查找**。

C++ 是一门很多信息**不在文本表面、而在类型系统里**的语言。看这个例子：

```cpp
namespace nv {
struct Base { virtual void PerformUpgrade() = 0; };
template<typename T> struct Middle : virtual Base {     // 虚继承 + 模板
    void PerformUpgrade() override {}
};
struct SoCUpdate : Middle<SoC>, NonCopyable, public Logger {};   // 多继承
}
```

语法解析器（tree-sitter）能看出 `SoCUpdate` 是个 class，后面跟着三个基类说明符的**文本片段**。它**无法**确定：

- `Middle<SoC>` 是特化 `nv::Middle<SoC>`——需要**模板实例化**。
- `Logger` 在哪个命名空间——需要**名字查找**。
- 继承链的根是 `virtual Base`——需要**展开模板并跟随 referenced 声明**。
- `PerformUpgrade() override` 到底重写了哪个基类方法——需要**跨类虚函数匹配**。

这些都需要类型解析，而这正是编译器前端（clang）提供、语法解析器在结构上做不到的。这不是 tree-sitter"差"——它刻意保持无类型、单文件、快速，是为语法高亮和代码折叠设计的。拿它来建 C++ **语义**图谱，是用错了工具。

**本项目改用 clang/libclang 的语义 AST。** 差异直接体现在解析器里：

- `base_spec.referenced`——把基类解析为跨文件、跨命名空间的**真实声明游标**（`parser/ast_visitor.py`）。
- `clang_isVirtualBase`——检测**虚继承**，这是语法树毫无概念的、C++ 特有的菱形继承语义。
- `cursor.is_virtual_method()` / `is_pure_virtual_method()`——虚函数语义。
- `access_specifier`——区分 public/protected/private 继承。
- 跨 TU 的 override 匹配——基类和派生类常在不同文件，clang 的 `referenced` 游标把它们连起来，而按文件的语法解析做不到。
## 为什么需要它？

AI 编程助手理解 C++ 代码的常见痛点：

| 痛点 | cpp-semantic-graph 的解法 |
|------|--------------------------|
| "这个类在哪定义的？" | `cpp_search_class("SocUpdate")` → 命名空间 + 文件位置 |
| "谁调用了这个函数？" | `cpp_get_callers("GetSocBootChain")` → 所有调用方 |
| "改这个头文件会影响什么？" | 增量更新自动递归 include 依赖，只重解析受影响的 TU |
| "虚函数有哪些 override？" | `cpp_get_overrides("PerformUpgrade", "BasePeriUpdate")` → 所有实现 |
| "这个模块的架构是怎样的？" | `cpp_traverse_graph("SocUpdate")` → 多跳遍历关联节点 |

## ✨ 核心特性

- **11 个 MCP 工具**：类搜索、函数搜索、继承关系、调用链（caller/callee）、override、文件符号、多跳遍历、文档搜索、改动爆炸半径、代码→文档反向
- **增量更新**：基于 include 依赖图，改一个 `.cpp` 秒级刷新（16× 快于全量重建）
- **文档融合**：项目文档与代码双向关联，搜索文档时自动定位相关代码
- **开箱即用**：一个 YAML 配置 + `compile_commands.json` 即可启动，MCP Server 自动注册到 AI 工具
- **项目无关**：表结构和工具定义不含任何项目硬编码，可迁移到任何 C++ 项目

## 🚀 快速开始

### 前置条件

- Python 3.10+
- libclang（与你的 LLVM 版本匹配）
- `compile_commands.json`（由 CMake/Bear 生成）

### 1. 安装

```bash
git clone https://github.com/your-org/cpp-semantic-graph.git
cd cpp-semantic-graph

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 准备 compile_commands.json

如果你的项目用 CMake 构建：

```bash
cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ...
```

如果没有 CMake，用 [Bear](https://github.com/rizsotto/Bear) 拦截编译命令：

```bash
bear -- make
```

### 3. 编写配置文件

创建 `cpp_semantic_graph.yaml`（这是唯一需要改的文件）：

```yaml
project:
  name: "your_project"
  compile_commands: "/path/to/your/project/compile_commands.json"

# 源码范围 — 决定哪些代码算"项目代码"
source_paths:
  - "src"
  - "include"

# 生成代码路径（如 ARA COM src-gen、protobuf）
generated_paths:
  - "src-gen"

# 完全忽略的路径
exclude_paths:
  - "thirdparty"
  - "build"
  - "test/mock"

# libclang 路径（按你的系统调整）
libclang_path: "/usr/lib/llvm-18/lib/libclang.so.1"

parse_options:
  skip_function_bodies: false  # false 才能提取调用关系
  max_workers: 4               # 并行解析进程数
```

### 4. 全量解析

> ⚠️ **重要**：全量解析前必须先停掉 MCP Server，否则 MCP Server 持有的 DB 连接会导致写入失败（`disk I/O error`）。
> 停止方法：`pkill -f "run_server.py"` 或在 Claude Code 设置中禁用 MCP Server 后重启。

```bash
# 1. 停掉 MCP Server（必须！）
pkill -f "run_server.py"

# 2. 全量解析
python3 -m cpp_semantic_graph full-parse \
  --config cpp_semantic_graph.yaml \
  --db semantic_graph_full.db

# 3. 重新启动 MCP Server（解析完成后）
```

解析完成后，数据库包含：
- **节点**：类、结构体、函数（含签名、命名空间、文件位置）
- **边**：继承、调用、override、belongs_to、类型别名（type_alias）、using 声明（using_decl）、友元（friend_of）等关系（模板实例化边 `instantiates` 因 libclang AST 形态暂未启用，见 [复杂场景说明](#-复杂场景说明)）
- **include 依赖**：翻译单元间的 include 关系图
- **文档关联**：文档切片 ↔ 代码实体（可选）

### 5. 启动 MCP Server

```bash
# 设置数据库路径
export CPP_GRAPH_DB=/path/to/semantic_graph_full.db

# 启动 MCP Server（stdio 传输）
python3 -m cpp_semantic_graph.mcp_server.server
```

### 6. 注册到 AI 工具

#### Claude Code

编辑 `~/.claude.json`，在 `mcpServers` 中添加：

```json
{
  "mcpServers": {
    "cpp-semantic-graph": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/cpp-semantic-graph/mcp_server/run_server.py"],
      "env": {
        "CPP_GRAPH_DB": "/absolute/path/to/semantic_graph_full.db"
      }
    }
  }
}
```

#### Cursor

编辑 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "cpp-semantic-graph": {
      "command": "python3",
      "args": ["/absolute/path/to/cpp-semantic-graph/mcp_server/run_server.py"],
      "env": {
        "CPP_GRAPH_DB": "/absolute/path/to/semantic_graph_full.db"
      }
    }
  }
}
```

#### Windsurf / 其他 MCP 客户端

配置方式类似，参考各工具的 MCP 文档。核心参数：
- **command**: `python3`
- **args**: `["/path/to/mcp_server/run_server.py"]`
- **env.CPP_GRAPH_DB**: 数据库绝对路径

> 💡 **提示**：`CPP_GRAPH_PROJECT` 环境变量可指定项目名（用于 MCP instructions），不设则自动从 DB 路径推断。

---

## 🛠️ 11 个 MCP 工具

| # | 工具名 | 用途 | 典型场景 |
|---|--------|------|---------|
| 1 | `cpp_search_class` | 按类名搜索类定义 | "SocUpdate 类在哪定义？" |
| 2 | `cpp_search_function` | 按函数名搜索函数定义 | "PerformUpgrade 的签名是什么？" |
| 3 | `cpp_get_inheritance` | 查询类的继承关系 | "BasePeriUpdate 有哪些子类？" |
| 4 | `cpp_get_callers` | 查询谁调用了指定函数 | "谁调用了 GetSocBootChain？" |
| 5 | `cpp_get_callees` | 查询指定函数调用了谁 | "PerformUpgrade 内部调用了什么？" |
| 6 | `cpp_get_overrides` | 查询虚函数的所有重写 | "PerformUpgrade 有哪些 override？" |
| 7 | `cpp_get_file_symbols` | 查询文件内的所有符号 | "soc_update.cpp 里有什么？" |
| 8 | `cpp_traverse_graph` | 多跳遍历图谱 | "修改 SocUpdate 会影响什么？" |
| 9 | `cpp_search_docs` | 搜索项目文档（含关联代码） | "OTA 升级流程的设计文档" |
| 10 | `cpp_blast_radius` | 改动爆炸半径（递归调用方 + 虚函数 override 展开 + 文件聚合 + 按跳数分层） | "我要改 PerformUpgrade，哪些文件必须 review？" |
| 11 | `cpp_get_code_docs` | 反向：代码符号 -> 描述它的文档 | "哪些设计文档讲到了 PerformUpgrade？" |

### 工具详情

#### `cpp_search_class(name, exact=False)`

按类名搜索 C++ 类定义。支持模糊匹配。

```
cpp_search_class("SocUpdate")
→ ## 搜索结果：类 "SocUpdate"（1 个）
  ### update::SocUpdate
  - 文件: soc_update.h:15-120
```

#### `cpp_search_function(name, class_name="")`

按函数名搜索函数定义。可限定所属类名。

```
cpp_search_function("PerformUpgrade", class_name="SocUpdate")
→ ## 搜索结果：函数 "PerformUpgrade"（1 个）
  ### SocUpdate::PerformUpgrade [virtual, override]
  - 签名: void PerformUpgrade() override
  - 文件: soc_update.cpp:45
```

#### `cpp_get_inheritance(class_name, direction="down", depth=1)`

查询类的继承关系。`direction="down"` 查子类，`"up"` 查父类。`depth=-1` 递归全部。

```
cpp_get_inheritance("BasePeriUpdate", direction="down", depth=-1)
→ ## BasePeriUpdate 的子类（4 条）
  - update::SocUpdate --public--> BasePeriUpdate
  - update::McuUpdate --public--> BasePeriUpdate
  - ...
```

#### `cpp_get_callers(function_name, class_name="")`

查询谁调用了指定函数（影响面分析）。

```
cpp_get_callers("GetSocBootChain")
→ ## 调用 "GetSocBootChain" 的代码（3 个）
  ### OtaManager::CheckBootChain
  - 文件: ota_manager.cpp:128
  - 调用类型: calls_direct
```

#### `cpp_get_callees(function_name, class_name="")`

查询指定函数调用了谁（调用链分析）。

```
cpp_get_callees("PerformUpgrade", class_name="SocUpdate")
→ ## "PerformUpgrade" 调用的代码（8 个）
  ...
```

#### `cpp_get_overrides(function_name, class_name)`

查询虚函数的所有重写实现。`class_name` 为声明该虚函数的基类名（必填）。

```
cpp_get_overrides("PerformUpgrade", class_name="BasePeriUpdate")
→ ## "PerformUpgrade" 的重写实现（4 个）
  ### update::SocUpdate::PerformUpgrade
  - 签名: void PerformUpgrade() override
  - 文件: soc_update.cpp:45
  - 重写基类: BasePeriUpdate
```

#### `cpp_get_file_symbols(file_path)`

查询文件内的所有类和函数符号。`file_path` 支持部分匹配。

```
cpp_get_file_symbols("soc_update.h")
→ ## 文件符号：soc_update.h（共 12 个）
  ### 类/结构体（2 个）
    1. ### [class] update::SocUpdate
  ### 函数（10 个）
    ...
```

#### `cpp_traverse_graph(start, relation_types=None, direction="outgoing", depth=3, max_results=50)`

多跳遍历图谱，最灵活的查询。沿指定关系类型遍历关联节点。

常用关系类型：`inherits_public`, `inherits_protected`, `overrides`, `belongs_to`, `calls_direct`, `calls_virtual`, `calls_callback`, `doc_describes_code`, `code_refers_to_doc`

```
cpp_traverse_graph("SocUpdate", depth=2, max_results=30)
→ ## 遍历结果：从 "SocUpdate" 出发（18 个节点）
  深度: 2, 遍历边数: 22
  ### 关联节点
    - [class] update::SocUpdate (soc_update.h)
    - [class] update::BasePeriUpdate (base_peri_update.h)
    - [function] update::SocUpdate::PerformUpgrade (soc_update.cpp)
    ...
```

#### `cpp_search_docs(keyword, tag="", max_results=10, min_confidence=0.7)`

搜索项目文档，返回文档切片 + 关联代码。

```
cpp_search_docs("升级", tag="架构设计")
→ ## 文档搜索："升级"（3 个结果）
  ### OTA 完整升级流程
  - 文件: docs/OTA_flow/OTA_COMPLETE_FLOW.md
  - 字数: 2450
  - 标签: 架构设计, OTA

  关联代码:
    - [class] BasePeriUpdate confidence=0.92
    - [class] SocUpdate confidence=0.88
```

注意：默认 `min_confidence=0.7` 过滤低质量共现关联（confidence=0.6 的占 63%，多为
关键词共现的泛类，如文档讲"刷写"却关联到 Data/Response 等泛化结构）。要查全就传 0.0。

#### `cpp_get_code_docs(symbol, min_confidence=0.0, max_results=10)`

反向查询：给定代码符号，返回描述它的文档切片（设计文档 / HLD / 架构文档）。
比 `cpp_search_docs` 反向：直接给代码符号即可，不用想关键词。

```
cpp_get_code_docs("PerformUpgrade")
→ ## 描述 "PerformUpgrade" 的文档（6 个切片）

  ### 3. 当前流程时序
  - 文档: SOC A/B 分区切换方案
  - 文件: AB_Switch/AB_PARTITION_SWITCH_DESIGN.md:62-81
  - 标签: 架构设计, A/B分区

  ### 4.3 组件 Component
  - 文件: ADC4.0_System_architecture/ADC4.0_OTA_SW_HLD.md:342-578
  - 标签: 系统架构
```

注意：反向关联多为 content_scan（confidence=0.6），与正向不同——代码符号名出现在
文档里通常就是讲它，0.6 多数有效，故默认全返回，靠 max_results 限量。

---

## 📖 CLI 用法

除了 MCP 工具，还提供 CLI 直接查询：

```bash
# 搜索类
python3 -m cpp_semantic_graph search-class "SocUpdate"

# 查继承
python3 -m cpp_semantic_graph inheritance "BasePeriUpdate" --direction down --depth -1

# 搜索函数
python3 -m cpp_semantic_graph search-func "PerformUpgrade"

# 查文件符号
python3 -m cpp_semantic_graph file-symbols "soc_update.cpp"

# 查 include 依赖
python3 -m cpp_semantic_graph include "base_peri_update.h" --mode all

# 数据库统计
python3 -m cpp_semantic_graph stats
```

### 增量更新

代码修改后，增量更新只重解析受影响的翻译单元：

```bash
# 基于 git diff（默认 HEAD~1）
python3 -m cpp_semantic_graph incremental --base HEAD~1

# 手动指定文件
python3 -m cpp_semantic_graph incremental --files soc_update.cpp,base_peri_update.h

# 仅检测不执行
python3 -m cpp_semantic_graph incremental --files soc_update.cpp --dry-run

# 跳过文档关联重建（更快）
python3 -m cpp_semantic_graph incremental --files soc_update.cpp --skip-associations
```

### 惰性增量（MCP 自动）

MCP 工具调用时自动检测新合入 commit，有才增量一次，同一 commit no-op
（[task_4_5](docs/task/cpp_semantic_graph/task_4_5_mcp_lazy_incremental.md)）。
无需手动跑 `incremental`，代码合入后图谱自动跟上。

- **基准是 commit**（合入），不碰工作区未提交改动
- **rev-parse 节流**：查询时 `git rev-parse HEAD`（<1ms）比较 `last_incremented_ref`，
  相同 no-op，不同才增量
- **阈值降级**：变更文件数超 `lazy_increment.threshold`（默认 20）跳过同步增量 + warning，
  查询先用旧图谱，提示手动跑
- **连接刷新**：增量后查询连接自动重建

配置（`cpp_semantic_graph.yaml`）：

```yaml
lazy_increment:
  enabled: true     # false=仅手动 CLI incremental
  threshold: 20     # 变更文件数超此跳过同步增量
```

关闭后回退到手动 CLI（见上节）。

---

## 📐 架构

```
┌─────────────────────────────────────────────────┐
│                  AI 工具层                       │
│  Claude Code / Cursor / Windsurf / 其他 MCP 客户端 │
└────────────────────┬────────────────────────────┘
                     │ MCP 协议 (stdio)
┌────────────────────▼────────────────────────────┐
│              MCP Server (9 工具)                 │
│  FastMCP + Lazy Init DB 连接 + Markdown 格式化    │
└────────────────────┬────────────────────────────┘
                     │ Python API
┌────────────────────▼────────────────────────────┐
│                查询层 (query/)                    │
│  GraphQuery │ CallQuery │ PolymorphismQuery      │
│  TraverseQuery │ DocQuery │ IncludeQuery          │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│             数据层 (db/)                         │
│  SQLite + 9 索引 + CASCADE 约束                  │
│  node │ edge │ include_dep │ parse_status        │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│              解析层 (parser/)                     │
│  AST Visitor (libclang) │ CompileDB │ Config     │
│  ChangeDetector │ ImpactAnalyzer                 │
└─────────────────────────────────────────────────┘
```

### 核心数据模型

**节点（node 表）**：类、结构体、函数，含命名空间、文件位置、签名等

**边（edge 表）**：节点间关系，方向约定：
- `inherits`: from=子类 → to=父类
- `calls`: from=调用方 → to=被调用方
- `belongs_to`: from=函数 → to=所属类
- `overrides`: from=派生类函数 → to=基类函数

**include_dep 表**：翻译单元的 include 依赖，增量更新的核心依据

---

## 🧩 复杂场景说明（模板/别名/友元）

| 特性 | 关系边 | 状态 | 说明 |
|------|--------|------|------|
| 类型别名 `using Alias = T` | `type_alias` | ✅ 已启用 | 别名节点入库 + 边关联目标；目标来自外部库时边可能悬空丢弃，别名节点仍保留 `target_type` 元信息 |
| using 声明 `using B::func` | `using_decl` | ✅ 已启用 | 子类函数 → 基类函数；本项目源码无常规 using 声明，仅 1 处 literal operator 未提取 |
| 友元 `friend class F` | `friend_of` | ✅ 已启用 | friend → 宿主类；本项目源码无 friend 声明，0 条边符合预期 |
| 模板实例化 | `instantiates` | ⏸️ 暂未启用 | libclang 不为模板特化产生独立 CLASS_DECL 节点（特化名仅出现在 CONSTRUCTOR/TYPE_REF 的 spelling 中），`walk_preorder` 找不到含 `<` 的类声明，提取无产出。提取器代码保留，待改用 LibTooling 或基于 TYPE_REF 重建时启用 |

> 这部分曾存在"提取器已编写但未集成进 pipeline"的问题（死代码），现已将 AliasExtractor/FriendExtractor 集成进 `SemanticExtractor.parse()`，并用 clangd 实测验证产出。

---

## 🔄 增量更新机制

```
1. ChangeDetector  ──→ 检测文件变更（git diff / 手动指定）
2. ImpactAnalyzer   ──→ 分析影响范围（.h → 递归 includer）
3. 删除旧数据        ──→ 删出边（不删共享节点）+ include_dep + parse_status
4. 重新解析          ──→ SemanticExtractor.parse() × 受影响 TU
5. upsert 新数据     ──→ 节点更新/边去重
6. 清理残留节点      ──→ 删除文件中已消失的函数/类
7. 重建文档关联      ──→ content_scan（可选 embedding）
```

**删除策略核心**：只删出边（from_id 在该文件的边），保留入边；节点用 upsert 更新。这保证了头文件中共享的类/函数节点不会被误删。

| 场景 | 受影响 TU | 更新耗时 |
|------|----------|---------|
| 单个 .cpp 变更 | 1 个 | ~11s |
| 单个 .h 变更 | 递归 includer | 取决于 TU 数（7 TU ~76s） |
| 幂等性（二次执行） | 不变 | 边数稳定不变 ✅ |

> **注意**：git diff 自动检测模式（`--base HEAD~1`）在 Android repo tool 管理的仓库下可能不工作（`_ensure_repo_root` 会找到 repo 顶层 .git 而非子仓库）。推荐使用 `--files` 手动指定变更文件，更可控。

---

## 📚 文档融合

将项目文档（Markdown）与代码实体双向关联，让 AI 搜索文档时自动定位相关代码。

**本项目已配置文档融合**：58 个文档 → 546 个切片 → 1,756 条关联边（doc_describes_code + code_refers_to_doc）。`cpp_search_docs` 可直接使用。

### 配置

创建 `config/doc_config.yaml`：

```yaml
doc_dirs:
  - "docs/"

exclude_patterns:
  - "*.html"
  - "*/build/*"

# 按目录自动打标签
tag_rules:
  - path_pattern: "architecture/**"
    tags: ["架构设计"]
  - path_pattern: "api/**"
    tags: ["接口规约"]

section_split:
  min_level: 2          # 按 ## 切片
  min_word_count: 20    # 少于 20 字的切片合并

# 手动精准关联（不侵入文档原文）
manual_links:
  - doc: "architecture/OTA_FLOW.md"
    heading: "升级流程"
    code:
      - "BasePeriUpdate"
      - "SocUpdate"
      - "PerformUpgrade"
```

### Embedding 关联（可选）

安装 `sentence-transformers` 后，可基于语义相似度自动关联文档与代码：

```bash
pip install sentence-transformers
```

默认使用 `all-MiniLM-L6-v2` 模型。中文项目建议换 `bge-small-zh-v1.5` 或 `multilingual-e5-small`。

---

## 🧪 验证

全量解析后可运行正确性验证（与 clangd 交叉比对）：

```bash
python3 -m cpp_semantic_graph full-parse \
  --config cpp_semantic_graph.yaml \
  --validate \
  --baseline validation/clangd_baseline.json
```

---

## 📋 依赖

### 核心（必装）

| 包 | 版本 | 用途 |
|----|------|------|
| `clang` | ≥18 | libclang Python 绑定，AST 解析 |
| `PyYAML` | ≥6.0 | 配置文件解析 |
| `mcp` | ≥1.0 | MCP 协议实现（FastMCP） |

### 文档融合（可选）

| 包 | 版本 | 用途 |
|----|------|------|
| `sentence-transformers` | ≥2.0 | 文档-代码语义关联 |
| `torch` | ≥2.0 | sentence-transformers 依赖 |

### 安装

```bash
# 核心依赖
pip install clang PyYAML mcp

# 文档融合（可选）
pip install sentence-transformers
```

或使用 requirements.txt：

```bash
pip install -r requirements.txt        # 核心
pip install -r requirements-docs.txt   # 文档融合（可选）
```

---

## 🗂️ 项目结构

```
cpp_semantic_graph/
├── __init__.py                  # 包声明
├── __main__.py                  # python -m 入口
├── cli.py                       # CLI 工具（search/inheritance/incremental/...）
├── pipeline.py                  # 全量解析流水线
├── incremental_updater.py       # 增量更新编排器
│
├── parser/                      # 解析层
│   ├── ast_visitor.py           #   AST 提取器（libclang）
│   ├── config.py                #   项目配置加载
│   ├── compile_db.py            #   compile_commands.json 解析
│   ├── change_detector.py       #   文件变更检测（git diff）
│   ├── impact_analyzer.py       #   影响范围分析
│   ├── doc_parser.py            #   文档解析器
│   ├── doc_association.py       #   文档-代码关联
│   ├── association_ingester.py  #   关联边入库
│   └── models.py                #   数据模型
│
├── query/                       # 查询层
│   ├── graph_query.py           #   类/函数/文件符号查询
│   ├── call_query.py            #   调用关系查询
│   ├── polymorphism_query.py    #   多态体系查询
│   ├── traverse.py              #   多跳遍历查询
│   ├── doc_query.py             #   文档融合查询
│   ├── include_query.py         #   include 依赖查询
│   ├── architecture_query.py    #   架构概览查询
│   └── fusion_query.py          #   融合查询
│
├── db/                          # 数据层
│   ├── graph_db.py              #   SQLite 操作
│   ├── importer.py              #   JSON→SQLite 导入
│   ├── schema.sql               #   建表+索引
│   └── relation_types.py        #   关系类型枚举
│
├── mcp_server/                  # MCP Server
│   ├── server.py                #   FastMCP Server + 9 工具
│   └── run_server.py            #   启动脚本
│
├── validation/                  # 正确性验证
│   ├── accuracy_validator.py    #   精度验证器
│   └── clangd_baseline.py       #   clangd 基线
│
├── config/                      # 配置模板
│   ├── doc_config.yaml          #   文档配置示例
│   └── template_whitelist.yaml  #   模板白名单
│
└── cpp_semantic_graph.yaml      # 项目配置（用户编写）
```

---

## ❓ FAQ

### Q: 需要什么版本的 LLVM/libclang？

需要与编译项目时使用的 LLVM 版本匹配。检查方法：

```bash
# 查看 clang 版本
clang --version

# 对应的 libclang 路径
ls /usr/lib/llvm-*/lib/libclang.so*
```

在配置文件中指定 `libclang_path`。

### Q: compile_commands.json 怎么生成？

- **CMake 项目**：`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ...`
- **Make 项目**：`bear -- make`
- **Ninja 项目**：`bear -- ninja`
- **Bazel 项目**：使用 [bazel-compile-commands](https://github.com/kiron1/bazel-compile-commands)

### Q: 增量更新安全吗？

是的。删除策略只删出边（from_id 在该文件的边），不删节点。共享头文件中的类/函数节点用 upsert 更新，不会被误删。极端情况（如重命名类）建议全量重建。

### Q: 支持哪些 AI 工具？

所有支持 MCP 协议的 AI 工具：Claude Code、Cursor、Windsurf、Continue 等。MCP Server 使用 stdio 传输，这是最广泛支持的传输方式。

### Q: 数据库有多大？

典型 C++ 项目（~100 个翻译单元）：约 1500 节点 / 5000 边 / 20000 include 关系，SQLite 文件约 5-10 MB。含文档融合时节点数和边数翻倍，DB 约 5-15 MB。

### Q: 和 clangd 有什么区别？

| | cpp-semantic-graph | clangd |
|---|---|---|
| 数据存储 | 离线 SQLite 图谱 | 实时 AST |
| 查询范围 | 跨文件、跨模块 | 单文件 + 索引 |
| 调用链 | 完整调用图 + 多跳遍历 | 单跳引用 |
| 文档关联 | 支持 | 不支持 |
| 增量更新 | 基于 include 依赖图 | 实时 |
| 适合场景 | 架构理解、影响面分析 | 实时编辑、签名查看 |

**两者互补**：日常编辑用 clangd，架构理解和影响面分析用 cpp-semantic-graph。

### Q: 为什么有些调用边缺失？（条件编译盲区）

cpp-semantic-graph 的 AST 来自**单一编译配置的预处理翻译单元**——即 `compile_commands.json` 记录的那个配置。C 预处理器在 libclang 见到代码**之前**，就会把当前 `-D` 宏未选中的 `#if` / `#ifdef` / `#else` 分支整段删除，因此落在未选中分支里的函数调用对图谱不可见。`get_callers` / `get_callees` 对这类调用会返回空，哪怕它在源码里明明存在。

这是 libclang 单配置方法的**固有局限，不是 bug**——clangd 同样有此盲区，因为它也只解析一个配置。

**典型形态。** 若函数*签名*位于 `#if` 之外，节点仍然会生成，但落在未选中 `#else` 函数体内的调用会缺失。于是节点存在，却对这些调用没有入边/出边：

```c
bool Foo::CompareVersion(const char* path) {   // 签名 -> 节点存在
#if SKIP_CHECK              // 当前配置: SKIP_CHECK == 1, 此分支保留
  LogWarning("skipped");    // -> 调用边被提取 (Logger::Warning)
#else                        // 死分支, 预处理器删除
  ExtractVersion(path);     // -> 无调用边 (libclang 看不到)
  CompareVersions(...);     // -> 无调用边
#endif
}
```

**如何区分盲区与真实遗漏。** 当 `get_callers` 对某个源码中可见被调用的函数返回空时，先看调用点是否落在 `#if`/`#else` 块内、以及控制宏的当前取值是否选中了该分支。图谱只反映被编译的那个配置，对当前配置而言它是正确的。

**缓解。** 要分析另一个配置，用对应的 `-D` 标志重新生成 `compile_commands.json` 并重建图谱。单一预处理 AST 无法同时呈现所有配置，无法靠一次解析取并集。

---

## 📄 License

MIT License

---

## 📋 测试报告

| 报告 | 说明 |
|------|------|
| [TEST_REPORT.md](tests/TEST_REPORT.md) | 综合测试报告（功能/准确性/效率/Bug修复） |
| [TEST_CASES.md](tests/TEST_CASES.md) | 具体测试用例表（62 条，98.4% 通过） |
| [TEST_THREE_LAYERS.md](tests/TEST_THREE_LAYERS.md) | 三层测试（问题→工具调用→代码验证，25 条真实问题） |
| [TEST_DOC_FUSION.md](tests/TEST_DOC_FUSION.md) | 文档融合专项测试（27 条，89% 通过） |
| [PROJECT_EVALUATION.md](tests/PROJECT_EVALUATION.md) | 项目整体评估（六维评分+对比+结论） |
