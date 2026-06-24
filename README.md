# cpp-semantic-graph

> C++ 语义图谱 — 让 AI 精准理解你的 C++ 代码库

为 AI 编程助手（Claude Code、Cursor、Windsurf 等）构建 C++ 代码的语义知识图谱，通过 [MCP 协议](https://modelcontextprotocol.io/) 暴露 9 个查询工具，让 AI 能直接搜索类定义、查继承关系、追踪调用链、分析影响面——无需翻文件、无需 grep。

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

- **9 个 MCP 工具**：类搜索、函数搜索、继承关系、调用链（caller/callee）、override、文件符号、多跳遍历、文档搜索
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

```bash
python3 -m cpp_semantic_graph full-parse \
  --config cpp_semantic_graph.yaml \
  --db semantic_graph_full.db
```

解析完成后，数据库包含：
- **节点**：类、结构体、函数（含签名、命名空间、文件位置）
- **边**：继承、调用、override、belongs_to、模板实例化等关系
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

## 🛠️ 9 个 MCP 工具

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
| 9 | `cpp_search_docs` | 搜索项目文档 | "OTA 升级流程的设计文档" |

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

#### `cpp_search_docs(keyword, tag="", max_results=10, min_confidence=0.0)`

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
| 单个 .cpp 变更 | 1 个 | ~7.5s |
| 单个 .h 变更 | 递归 includer | 取决于 TU 数 |
| 幂等性（二次执行） | 1 个 | 边数稳定不变 |

---

## 📚 文档融合（可选）

将项目文档（Markdown）与代码实体双向关联，让 AI 搜索文档时自动定位相关代码。

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

典型 C++ 项目（~100 个翻译单元）：约 1500 节点 / 5000 边 / 20000 include 关系，SQLite 文件约 5-10 MB。

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

---

## 📄 License

MIT License
