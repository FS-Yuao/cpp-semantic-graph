# cpp_semantic_graph 代码质量审查报告

**日期**: 2026-07-06
**审查对象**: `/mnt/code1/cpp_semantic_graph`（MCP 实际使用版本，唯一保留副本）
**审查方法**: 5 个 agent 并行深读 Parser/DB/Query/增量+MCP/文档关联 五大模块 + 实际版本逐项复核
**审查重点**: 正确性、数据一致性、性能、可用性、完成度

---

## 〇、版本统一说明（本次清理记录）

审查前发现存在三份副本，版本不一，导致首轮分析基于旧开发版得出错误结论（见 P0-5 误报）。本次已统一：

| 副本 | 状态 | 处理 |
|------|------|------|
| `/mnt/code1/cpp_semantic_graph` | 最新（git 提交 7-03，含 call_line/虚继承/full-parse 文档索引等修复）+ 最全（有完整 validation/、tests/、changelogs/） | **保留**（MCP 实际使用，DB 在此） |
| `/home/ubuntu24/cpp-semantic-graph` | 旧开发版（6-30 提交，无 call_line 修复），子集 | **已删除** |
| `_tools/cpp_semantic_graph` | 中间拷贝（无 .git），子集 | **已删除** |
| `_tools/cpp_semantic_graph_env` | 5.1G 虚拟环境（含 clang/mcp/sentence_transformers） | **已迁移** 到 `/mnt/code1/cpp_semantic_graph_env`（修复 shebang，验证 import 通过） |

- MCP 查询用系统 `python3`（用户级 mcp 包），不依赖 _env；解析（full-parse/增量）用 _env 的 python（需 clang）。
- 已更新 5 个引用旧 `_tools` 路径的历史文档（phase1-4、project_review）。
- **迁移引入回归**：`_infer_project_name` 失效（见 P2-1）。

> **教训**：memory 提示"4 days old，需验证当前代码"——首轮 agent 读的是未同步的旧开发版，导致 P0-5 误报。版本核对不可省。

---

## 一、设计亮点

| 亮点 | 位置 | 价值 |
|---|---|---|
| config 驱动过滤，零硬编码 | `config.py:94-135` | 换项目只改 YAML，`source_paths/generated_paths/exclude_paths` 三层正交 |
| 双策略 enclosing function 定位 | `ast_visitor.py:164-194` | `semantic_parent` 链 → 行号区间 fallback，解决 Proxy 方法父链断裂 |
| 延迟边解析（跨 TU 边） | `ast_visitor.py` + `graph_db.py:446-564` | override/calls/type_alias 提取时 `to_id=""`，入库按 hint 分策略解析 |
| unique_key 自然键去重 | `schema.sql:14` | 跨 TU 同符号只生成一个 node |
| 增量策略：只删出边不删节点 | `incremental_updater.py:237-271` | 节点 upsert，出边重解析重建 |
| 事务原子性 | `incremental_updater.py:162-204` | 删旧+重解析+导入+清理包单事务，异常 rollback |
| call_line 区分多调用点 | `schema.sql:33` + `ast_visitor.py:735` | 同一函数内多次调用同一目标各保留一条边（7-01 已修） |
| 虚分派展开（双向） | `call_query.py:310-419` | 基类虚函数 → 所有子类 override |
| 继承边方向翻转 | `traverse.py:266-345` | 正确处理 from=子类/to=基类的反直觉约定 |
| 文档关联 5 层策略 | `association_ingester.py` | manual → config → rule → content_scan → embedding，思路正确 |

---

## 二、P0 严重问题（静默错误，必须优先修）

### P0-1 override 二次解析 fallback 连到无关虚函数 ⚠️核心功能受损

**位置**: `db/graph_db.py:481-502`

主路径（461-479）已用 `_find_base_classes` 校验基类链，但 fallback（483-502）在主路径找不到时，**只按 `func_name + is_virtual + belongs_to` 全局搜，不校验找到的函数是否在派生类的基类链上**，`LIMIT 1` 无 `ORDER BY` 返回任意一个。`extra_info LIKE '%is_virtual%'`（496）还会误匹配 `"is_virtual":false`。

**失败场景**: `Derived::init` 重写 `Base::init`，但 DB 里还有 `OtherClass::init`（也 virtual）。fallback 不校验基类链，可能连到 `OtherClass::init`，无任何报错。而 override 关系是这个工具的核心卖点。

**修复方案**: fallback 限定在 `base_classes` 集合内（主路径已查出的基类），用 `json_extract` 精确判断 is_virtual；`base_classes` 为空则不连边（to_id 保持 None，安全）。

### P0-2 解析失败导致节点误删 ⚠️数据丢失

**位置**: `incremental_updater.py:345-372`

`_import_results` 只导入成功结果（`successful = [r for r in results if r.status != "failed"]`，line 338），但 `_cleanup_removed_nodes` 遍历**全部** results（line 360，含 failed）。失败的 ParseResult `nodes=[]`，不贡献 unique_key → 该文件 `retained=set()` → `delete_removed_nodes` 删除该文件**全部**节点 → CASCADE 删所有指向它的边。事务已 commit（195），不可恢复。

**失败场景**: 头文件 H 被唯一 TU T 包含，T 解析失败（编译参数错误）→ H 的全部节点被删 → 别人指向 H 的调用边/继承边消失。

**修复方案**: `_cleanup_removed_nodes` 跳过 failed 结果，`if result.status == "failed": continue`。

### P0-3 钻石继承检测对 2+ 跳静默失效

**位置**: `query/inheritance_query.py:248-250`

```python
if current in path[1:]:   # path 末尾就是 current，恒为 True（depth>0 时）
    return
```

防环检查写错：`dfs` 调用时 `path + [child_name]` 已把 current 放在 path 末尾，`path[1:]` 仍含 current，`current in path[1:]` 恒为 True。搜索永远不超过 1 跳。

**失败场景**: `A→B→D` 和 `A→C→D` 这种 2 跳钻石，`_find_inheritance_paths` 返回空，`get_diamond_inheritance` 不生成 DiamondInfo。

**修复方案**: 删除该行，改为递归前检查 `child_node["name"] not in path`。

### P0-4 文档关联 manual/config/rule 三策略是死代码

**位置**: `parser/association_ingester.py:164,240,492` + 调用方 `pipeline.py:198`、`incremental_updater.py:387-390`

全项目 grep 确认 `ingest_config_associations` / `ingest_manual_associations` / `ingest_rule_associations` **零调用点**。full-parse（pipeline.py:198）和 incremental（387-390）只调 `ingest_content_scan_associations` + 可选 `ingest_embedding_associations`。但 `config/doc_config.yaml:42` 配了 `manual_links:`——**配置了却永不生效**。

注：7-02 的 commit `fix: full-parse now indexes docs` 已让 full-parse 调 content_scan（比首轮分析的旧版新），但 manual/config 仍是死代码。

**失败场景**: 用户在 doc_config.yaml 配置 `manual_links`（如 `GetSocBootChain` → 某设计文档），运行 full-parse 后 DB 中无 `method=manual_config` 的关联边，`cpp_search_docs` 查不到该关联。

**修复方案**: full-parse 和 incremental 的 `_rebuild_associations` 末尾追加 `ingest_config_associations(doc_config_path)` 调用。

### P0-5 call_line 去重修复回归 —— 误报，撤销 ⚠️

**首轮结论**（基于 `/home/ubuntu24/cpp-semantic-graph` 旧开发版）：call_line 去重修复回归丢失。

**复核结论**（基于 `/mnt/code1/cpp_semantic_graph` 实际版本）：**误报**。实际版本 `schema.sql:33` `UNIQUE(from_id, to_id, relation_type, call_line)` 和 `ast_visitor.py:735` 去重 key 含 call_line 均完好。changelogs/fix-call-line-dedup.md 记录的修复在 7-01 已应用。

**原因**: 首轮 agent 读的是未同步的旧开发版（6-30 提交，早于 7-01 修复）。三份副本版本不一致导致误判。已通过版本统一消除。

### P0-6 MEMBER_REF_EXPR 未过滤 ref.kind，字段访问产生虚假调用边

**位置**: `parser/ast_visitor.py:584-595`

```python
ref = cursor.referenced
...
parent = ref.semantic_parent
if not parent or parent.kind != CursorKind.CLASS_DECL:
    return
```

只检查 `parent` 是 CLASS_DECL，但 `ref` 本身可能是 FIELD_DECL（字段访问）。`obj.field` 的 ref 是 FIELD_DECL，其 semantic_parent 也是 CLASS_DECL，通过过滤后以 `callee_name=字段名` 创建 CALLS_DIRECT 边。入库时 name-only fallback（graph_db.py:556-564）会匹配到任意同名字段/函数。

**失败场景**: `obj.size`（字段访问）产生 callee_name="size" 的调用边，污染 `cpp_get_callers`/`cpp_get_callees`。

**修复方案**: line 586 后加 `if ref.kind not in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL): return`。

---

## 三、P1 重要问题（按主题聚合）

### 主题 A：LIKE 子串匹配（系统性，遍布 3 模块）

| 位置 | 问题 |
|---|---|
| `graph_db.py:323-325` | `_find_base_classes` 用 `namespace LIKE %ns%`，`foo::Bar` 匹配 `foobar::Bar` |
| `graph_db.py:471-475` | override 主路径 `namespace LIKE %base_name%` + `unique_key LIKE`，误匹配 |
| `graph_db.py:273-296` | `get_includers` 用 `included_file LIKE %header%`，`util.h` 匹配 `my_util.h` |
| `include_query.py:108` | BFS 用 basename `Path(current).name` 做 LIKE，不同目录同名头文件互匹配 |
| `call_query.py:437-438` | `class_name in namespace` 子串，`Update` 匹配 `SocUpdate` |
| `doc_query.py:85-88` | JSON `extra_info LIKE`，搜 "ota" 匹配 "data" |

**影响**: 增量影响分析算不准、调用链误连、include 查询膨胀。这是全项目最普遍的缺陷模式。

### 主题 B：数据一致性

- **import_parse_result 不刷新行号/命名空间** `graph_db.py:409-414`: 已存在节点 UPDATE 只更新 extra_info，函数移行后 DB 存旧行号。`upsert_node` 会更新，但这里没用——两条路径行为不一致。
- **未解析边静默丢弃** `graph_db.py:567`: `to_id is None` 时边不插入，注释写 "pending" 实际什么都没存。先导 derived.cpp 后导 base.cpp，override 边永久丢失，无拓扑排序保证。
- **CASCADE 删入边违反设计声明** `graph_db.py:725-756`: 删节点 CASCADE 删所有边（含入边），与 incremental_updater.py:15 "保留入边" 声明冲突。fileB 的调用边在 fileA 删节点时消失，fileB 不重解析 → 永久丢失。
- **finally 块在 rollback 后仍跑文档入库** `incremental_updater.py:206-228`: 主事务异常 rollback 后，finally 无条件执行 `_detect_and_ingest_doc_changes`（211）和 `_rebuild_associations`（218），文档数据经独立连接写入，关联基于回滚后的旧 C++ 数据 → 不一致。

### 主题 C：性能

- **5 个冗余索引** `schema.sql:61,63,66,69,71`: `idx_node_unique_key`（unique_key 已 UNIQUE）、`idx_edge_from_id`/`idx_edge_from_type`（UNIQUE(from,to,call_line) 最左前缀已覆盖）、`idx_include_source`（UNIQUE(source,included) 覆盖）、`idx_parse_status_file`（source_file UNIQUE）。拖慢写入。
- **缺 SQLite 调优** `graph_db.py:34-35`: 只设 WAL+foreign_keys，缺 `synchronous=NORMAL/temp_store=MEMORY/cache_size/mmap_size/journal_size_limit`。
- **N+1 查询**: `call_query.py:121,189` 边循环内重复 `get_node_by_id`；`inheritance_query.py:107-124` 每个类多次查询；`traverse.py:287` 取全部边后 Python 过滤未用 relation_type 索引。
- **逐行 INSERT 未用 executemany** `graph_db.py:401-422`。
- **长事务跨重解析** `incremental_updater.py:174`: 事务内调 libclang 解析（慢），写锁不释放。

### 主题 D：MCP 可用性

- **工具只 catch FileNotFoundError** `server.py:226,249,274,299,322,345,367,420,466`: `database is locked` 等异常传播到框架层。
- **连接无重连**: full-parse 重建 DB 后缓存连接指向旧文件句柄。
- **5 个查询类各开一个连接**，且从不 close。
- **输入无边界校验**: `depth` 无上限，`direction` 不校验枚举。
- **无并发构建保护**: 两个增量同时跑，第二个 5 秒后 `database is locked`。
- **_worker_parse 丢失 directory** `pipeline.py:105-108`: 并行模式下 `directory=""`，相对 `-I` 路径解析失败 → 图谱不完整。

### 主题 E：解析正确性

- **unique_key 不含签名** `models.py:60-62`: 重载函数/构造函数坍缩为单节点，ARA COM 大量重载无法区分。**【已修 E-3，见第七节：加参数签名后 operator<< 1→16 节点】**
- **STRUCT_DECL 判断缺失** `ast_visitor.py:208,285,321`: 三处只认 CLASS_DECL，struct 方法丢 BELONGS_TO 边。
- **函数提取无 is_definition() 过滤** `ast_visitor.py:300-389`: .h 声明和 .cpp 定义产生两个节点。

### 主题 F：查询正确性

- **expand_virtual_dispatch 用 PRAGMA database_list hack** `call_query.py:333,365`: `:memory:` 库直接失效，每次新建连接。
- **无 class_name 时虚分派静默失效** `call_query.py:366`: `get_callers("foo")` 不带 class_name，override 展开不执行。
- **_walk_inheritance visited 逻辑允许重复遍历** `inheritance_query.py:100-104`: `max_depth=-1` 无安全上限，密集继承图指数级遍历。
- **override 递归无深度限制** `polymorphism_query.py:195-241`: 深层链可能栈溢出。
- **多起始节点 path 的 start_key 错误** `traverse.py:164,237`: 永远取第一个起始节点。

---

## 四、P2 次要问题 + 迁移引入回归

- **P2-1（迁移回归）`_infer_project_name` 失效** `server.py:480-492`: 正则 `/(app|src)/<project>/` 从 DB 路径提取项目名，但 DB 现在在 `/mnt/code1/cpp_semantic_graph/`（路径无 `/app/`），返回空。MCP 工具描述不显示项目名。建议改为读 `cpp_semantic_graph.yaml` 配置或环境变量 `CPP_PROJECT_NAME`。
- **P2-2** RelationType 双重定义（`models.py` vs `db/relation_types.py`），靠人工同步。**【已修，见第七节】**
- **P2-3** `_get_namespace` / `_parse_extra` 在 4-6 个文件重复。**【已修，见第七节】**
- **P2-4** `server.py:484-485` 注释仍写 `_tools` 例子（已过时）。**【P2-1 修复时连带清理，注释已更新为 `/app/<project>/`，`_tools` 全项目 grep 无残留】**
- **P2-5** TemplateExtractor 死代码（libclang 不为模板特化产生独立 CLASS_DECL）。**【已修，删除死文件，见第七节】**
- **P2-6** 无 schema 版本管理（`PRAGMA user_version`），不利演进。**【已修，见第七节】**
- **P2-7** `_autocommit` 私有属性被 incremental_updater 外部操控。**【已修，见第七节】**

---

## 五、验证报告可信度

`/mnt/code1/cpp_semantic_graph/validation/` 下三份报告存在矛盾：

| 报告 | 调用关系 Recall | 可信度 |
|---|---|---|
| `full_test_report.md` | **33.3%** ❌ | 中低——自动生成，TP=1/FN=2 |
| `accuracy_report.md` | **100%** ✅ | 中——15 个样本太少，调用关系仅 2 样本 |
| `full_validation_report.md` | 100% | 中高——逐用例对比 clangd，但只 5 例 |

三份报告用不同样本/方法测同一指标，33.3% 和 100% 并存，未互相说明差异。`tests/PROJECT_EVALUATION.md` 引用"96% 端到端"未标来源，有 cherry-picking 嫌疑。

**最坦诚**: `phase0_validation_report.md`（标注 embedding "SKIP"、虚继承"不可用"）和 `tests/TEST_DOC_FUSION.md`（89% 通过率 + 失败根因）。

**Embedding 关联实质不可用**: Phase 0 跳过评估，默认 `rebuild_embeddings=False`，所有报告未测，README 却宣称可用。

### 【2026-07-07 复现验证】全部修复后重跑全量测试

重跑 `validation/full_test`（基于全部修复 + 重建库），结果覆盖旧 `full_test_report.md`：

| 维度 | 旧报告（6-25，修复前） | 复现（当前库） | 结论 |
|---|---|---|---|
| 功能正确性（9 工具） | ❌ 3 失败 | **✅ 9/9 全通过** | 矛盾消除 |
| 调用关系 Recall | **33.3%** ❌ | **100%（TP=3/FN=0）** ✅ | 第五节核心矛盾解决 |
| 类定义/继承/函数签名 | 100% | 100% | 保持 |
| type_alias/friend | 100% | 100% | 保持 |
| 准确性总评 | ❌ 不达标 | **✅ 6/6 全达标** | — |
| 性能 | 9.4x | 10.3x | 保持 |

**复现暴露并修复的新回归（belongs_to 边，E-2 引入）**：全量测试功能验证发现 `get_overrides` 返回 class_name 全空——根因是 E-2（is_definition 过滤）后，成员函数只保留 `.cpp` 定义节点，但 belongs_to 边的 parent_key 用函数的 `.cpp` 路径拼类节点 key，与类节点在 `.h` 的 unique_key 失配 → 边丢失（覆盖率从旧DB 73% 跌到 15%）。修复：belongs_to parent_key 改用父类 cursor 的 `.h` location（`ast_visitor.py`）+ `_get_owning_class` 加 namespace 末段 fallback（`polymorphism_query.py`）。修复后 belongs_to 覆盖率 15%→**90%**，get_overrides class_name 恢复正确。另校正 2 个过时测试断言（get_file_symbols 应查 .h、search_class 语义是搜类名不含子类）。详见第七节 belongs_to 回归修复记录。

**关于三份报告矛盾的根本结论**：矛盾根源是**验证资产用 OTA 子集手工采样**（`clangd_baseline.json` 仅 2 类/6 函数/2 调用，`scope: hq_ota_service`），非代码普适性问题。经复查，**图谱工具架构与功能是普适的**（见附录：核心代码无 OTA 硬编码、配置驱动、语言特性覆盖齐全）；验证受限于当前仅有 OTA 一个真实项目可测。旧 `accuracy_report.md`/`full_validation_report.md` 基于修复前旧库，数字已失效，以最新 `full_test_report.md`（全绿）为准。Embedding 仍未验证，README 宣称保留待确认。

---

## 六、提升路线图

### 第一优先：修 P0（正确性）
1. P0-1 override fallback 限定基类链 + json_extract
2. P0-2 解析失败跳过清理
3. P0-3 钻石继承防环修正
4. P0-4 接入 ingest_config_associations
5. P0-6 MEMBER_REF_EXPR 过滤 ref.kind
（P0-5 误报，无需修）

### 第二优先：修 P1 主题 A/B（一致性）
6. LIKE 子串匹配全改精确/后缀匹配
7. import_parse_result 改调 upsert_node 刷新行号
8. 加 pending_edge 表或多趟解析补未解析边
9. CASCADE 删入边问题：删节点前把入边来源加入重解析集
10. finally 块条件执行（rollback 时跳过文档入库）
11. 补 STRUCT_DECL + _worker_parse 传 directory + MCP 兜底异常/重连/校验

### 第三优先：性能
12. 删 5 个冗余索引
13. 加 SQLite PRAGMA 调优
14. 节点批量 executemany / UPSERT 语法
15. 长事务拆分（重解析移事务外）
16. N+1 消除

### 第四优先：架构债
17. schema 版本管理
18. 消除重复（RelationType/_get_namespace/_parse_extra/格式化函数）
19. GraphDB 公共事务 API
20. 删死代码（TemplateExtractor/_create_tables_inline）
21. 统一验证报告，embedding 补验证或标"实验性"
22. P2-1 修 _infer_project_name

---

## 七、本次修复记录

### 修复清单

| # | 文件 | 修复 | 验证 |
|---|------|------|------|
| P0-1 | `db/graph_db.py:481-502` | override fallback 限定 `base_classes` 集合内 + `json_extract` 判断 is_virtual | full-parse 后 override 边抽样 5 条全部正确（McuUpdate→BasePeriUpdate 同基类链），无误连 |
| P0-2 | `incremental_updater.py:360` | `_cleanup_removed_nodes` 跳过 failed 结果 | 73 TU 全成功（0 失败），逻辑保证失败时不误删节点 |
| P0-3 | `query/inheritance_query.py:248-268` | 删除 `path[1:]` 恒 True 的防环检查，改为递归前 `child not in path` | `get_diamond_inheritance`/`_find_inheritance_paths` 不报错（项目无钻石继承，深度路径逻辑修正） |
| P0-4 | `pipeline.py:198` + `incremental_updater.py:387` + `parser/config.py:70` + `cpp_semantic_graph.yaml` | 接入 `ingest_config_associations`；`docs_config` 转绝对路径；`docs_dir` 改绝对路径 | doc_section **0→630**，文档关联边 **0→1133**（双向对称） |
| P0-6 | `parser/ast_visitor.py:586` | MEMBER_REF_EXPR 过滤 `ref.kind`（只保留 CXX_METHOD/FUNCTION_DECL） | 常见字段名（size/length/begin/end 等）calls_direct **0 次**，虚假边消除 |
| P0-5 | — | 误报撤销（实际版本 call_line 修复完好） | `schema.sql:33` + `ast_visitor.py:735` 确认 |

### full-parse 验证结果

- 翻译单元: 73（成功 73 / 失败 0，0% 失败率）
- 数据库: 1527 节点 / 2718 边 / 29026 includes
- 边类型: calls_direct 1683, belongs_to 663, type_alias 126, calls_virtual 119, overrides 114, inherits_public 13
- 文档: 630 doc_section + 1133 文档关联边（修复前为 0）
- 耗时: 解析 677s + 入库 23s = 704s

### 迁移引入回归（部分已修）

| 回归 | 状态 |
|------|------|
| `docs_dir` 相对路径失效（`../../docs` → `/mnt/docs` 不存在） | ✅ 已修（改绝对路径） |
| `docs_config` 相对路径依赖 CWD | ✅ 已修（config.py 转绝对） |
| `_infer_project_name` 正则不匹配新路径 | ✅ 已修（读 yaml `project.name` + 环境变量，见 P2-1 修复记录） |

### P1 主题 B（数据一致性）修复

| # | 文件 | 修复 | 验证 |
|---|------|------|------|
| B-1 | `db/graph_db.py:409-416` | `import_parse_result` UPDATE 刷新 `start_line/end_line/namespace/file_path`（原只更新 extra_info，函数移行后 DB 存旧行号） | 语法/import OK |
| B-2 | `incremental_updater.py:164,197,208-225` | finally 块加 `success` 标志，主事务 rollback 时跳过文档入库与关联重建（避免基于回滚旧数据建关联） | 语法 OK，rollback 路径不写文档 |
| B-3 | `db/graph_db.py:778-787` | `delete_removed_nodes` 删节点前 warning 入边来源文件（CASCADE 会删其他文件入边） | 告知版；完整追加重解析列为后续增强（涉及增量流程重构+循环风险） |
| B-4 | `db/graph_db.py:625-647` | `import_results` 两轮导入：第一轮节点全入库后，第二轮重试因 to 节点缺失而丢弃的跨 TU 边 | 语法 OK，UNIQUE 约束保证已插入边跳过 |

### P1 主题 A（LIKE 子串匹配）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| A-1 | `db/graph_db.py:319` `_find_base_classes` | `namespace LIKE %ns%` → `::` 边界匹配（`=` / `ns::%` / `%::ns`） | McuUpdate→BasePeriUpdate 正确，ns 限定不误匹配 |
| A-2 | `db/graph_db.py:461-520` override 主路径+fallback | `namespace LIKE %base_name%` + `unique_key LIKE` → Python 端 namespace 末段精确匹配（消除子串+`_` 通配符误匹配） | 现有 8 条 override 边全部合理（to 末段 ∈ from 基类链） |
| A-3 | `db/graph_db.py:276` `get_includers` | `included_file LIKE %header%` → basename 精确（`= ? OR LIKE '%/'‖?`） | `Adapter.h`=0 不误匹配 `MccAdapter.h` |
| A-4 | `query/include_query.py:103` BFS | `included_file LIKE %(basename)%` → basename 精确 | 同 A-3 逻辑 |
| A-5 | `query/call_query.py:433` `_find_function_ids` | `class_name in namespace` 子串 → `split("::")` 段精确 | `class=Update` 匹配 0（原 `in` 子串会误匹配 **203 个**末段含 Update 的函数） |
| A-6 | `query/doc_query.py:83` `search_documentation` | `extra_info` 整体 LIKE → `json_extract(doc_title/content_preview)` + `json_each(tags)` 精确 | 不存在词 0 条，`tag=架构设计` 3 条 |

### P1 主题 C（性能）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| C-1 | `db/schema.sql:58-71` | 删 4 个被 UNIQUE 约束自动索引覆盖的冗余索引（`idx_node_unique_key`/`idx_edge_from_id`/`idx_include_source`/`idx_parse_status_file`） | 现有 DB DROP 后剩 10 个自定义索引，查询正常 |
| C-2 | `db/graph_db.py:34-42` | 加 PRAGMA 调优：`synchronous=NORMAL`/`temp_store=MEMORY`/`cache_size=64MB`/`mmap_size=256MB`/`journal_size_limit=64MB` | 5 项 PRAGMA 全部生效 |
| C-3 | — | 保留 `idx_edge_from_type(from_id, relation_type)`（报告判断过严：UNIQUE 最左前缀跳过 to_id，不覆盖此组合，查询 `from_id+relation_type` 需要它） | — |
| 待办 | N+1 查询、`executemany`、长事务拆分 | 项目规模小（1527 代码节点），收益有限，列为后续 | — |

### P1 主题 D（MCP 可用性）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| D-1 | `mcp_server/server.py` 9 处工具 | `except FileNotFoundError` → `except Exception` + `_query_error` helper（统一兜底 `database is locked` 等异常，不传播到 MCP 框架层） | `_query_error(ValueError)` 返回友好串 + logger 记录 |
| D-2 | `mcp_server/server.py` `cpp_get_inheritance`/`cpp_traverse_graph` | direction 枚举校验 + depth 范围校验（继承 [-1,10]、遍历 [1,6]）+ max_results [1,500] | 非法参数返回错误提示，合法参数正常进 try |
| D-3 | `pipeline.py:105,238` | `_worker_parse` 加 `directory` 参数，并行模式传 `e.directory`（原硬编码 `""` 导致相对 `-I` 解析失败、图谱不完整） | 语法 OK |
| 待办 | 连接重连（full-parse 重建 DB 后缓存连接失效）、并发构建锁 | 改动大，列为后续 | — |

### P1 主题 F（查询正确性）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| F-1 | `query/call_query.py:333,365` | `expand_virtual_dispatch` 用 `PRAGMA database_list` hack → `str(self.db.db_path)`（`:memory:` 库不再失效，省去每次新建连接的开销） | 展开 PerformUpgrade→4 个 override 正确 |
| F-3 | `query/inheritance_query.py:73` | `get_full_inheritance_chain` max_depth=-1 设安全上限 20（防密集继承图指数级遍历） | BasePeriUpdate down 返回 4 子类，不指数级 |
| F-4 | `query/polymorphism_query.py:195` | `_collect_overrides_recursive` 加 depth 参数 + 上限 20（防深层 override 链栈溢出） | 4 个 override，不栈溢出 |
| F-5 | `query/traverse.py:133-264` | BFS queue + DFS 签名加 start_key，多起始节点 path 各自记录起始节点（原永远取 `start_ids[0]`） | 10 路径 3 个不同 start_key，不再全指向第一个 |
| F-2 | `query/call_query.py:366` | 语义限制：虚分派展开需 class_name（无 class_name 时无法确定基类），后续可从 calls_virtual 边 extra_info 推断 | 标注后续增强 |

### P2-1（迁移回归）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| P2-1 | `mcp_server/server.py:500` `_infer_project_name` | 路径正则失效（DB 迁到 `/mnt/code1/cpp_semantic_graph/` 无 `/app/`）→ 三级策略：环境变量 `CPP_PROJECT_NAME` > DB 同目录 yaml `project.name` > 路径正则 fallback | DB 路径推断返回 `hq_ota_service`（从 yaml），环境变量优先，旧路径正则仍 fallback |

### P2-2~P2-7（次要问题）修复（2026-07-07）

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| P2-2 | `parser/models.py` + `db/relation_types.py` | RelationType 双重定义（两处各 15 成员靠人工同步）→ **单一权威来源**：models.py 保留完整定义（含 `inherits_types`/`call_types`/`doc_types`/`from_str` 分类方法），`db/relation_types.py` 改为 `from ..parser.models import RelationType` 重导出，只保留入库用的 `RELATION_CATEGORIES`。models 仅依赖 stdlib，反向导入不成环 | `models.RelationType is db.relation_types.RelationType` = True（同一对象），classmethod + RELATION_CATEGORIES(15) 可用 |
| P2-3 | 新增 `parser/cursor_utils.py` + `query/query_utils.py` | `_get_namespace`（4 处复制）→ `cursor_utils.get_namespace`；`_get_parent_class_name`（2 处）→ `cursor_utils.get_parent_class_name`；`_parse_extra`（6 处复制）→ `query_utils.parse_extra`。各处改为委托/重导入（保留原函数名，调用点零改动） | full-parse 回归：function 523/class 77/struct 23/doc_section 657 **与重构前完全一致**；override 57、belongs_to 85、命名空间格式正确、**orphan 边 0**（key 一致性未破坏） |
| P2-4 | `mcp_server/server.py` | `_tools` 过时注释——P2-1 修 `_infer_project_name` 时已连带清理，注释更新为 `/app/<project>/` | 全项目 grep `_tools` 无残留 |
| P2-5 | 删除 `parser/template_extractor.py`（129 行） | TemplateExtractor 死代码（libclang 不为模板特化产生独立 CLASS_DECL，ast_visitor 已注释不用）→ 删除死文件，ast_visitor 注释更新说明。INSTANTIATES 枚举值保留（schema 一部分） | 全项目无活代码引用；import 验证 `template_extractor` 已 ModuleNotFoundError（预期）；full-parse 正常 |
| P2-6 | `db/graph_db.py`（`SCHEMA_VERSION=2` + `_apply_schema_version`）+ `schema.sql` | 无 schema 版本管理 → 加 `PRAGMA user_version`：新库写入当前版本，旧库版本落后仅告警（不自动迁移，避免静默破坏）。版本 2 = unique_key 加参数签名（E-3）。加 `schema_version()` 公共查询方法 | full-parse 后 `PRAGMA user_version` = 2 |
| P2-7 | `db/graph_db.py` + `incremental_updater.py` | `_autocommit` 私有属性被 incremental_updater 直接改（`db._autocommit = False/True`）→ 加公共接口 `begin_manual_transaction`/`commit_manual_transaction`/`rollback_manual_transaction`，incremental_updater 改用公共方法（finally 保留兜底赋值防中途异常） | 增量更新事务边界逻辑不变，import 验证通过 |

### P1 主题 E（解析正确性）修复

| # | 位置 | 修复 | 验证 |
|---|------|------|------|
| E-1 | `parser/ast_visitor.py:208,285,321` | 三处 `parent/grandparent.kind == CLASS_DECL` → 含 `STRUCT_DECL`；BELONGS_TO 边 `parent_key` 的 type 按 parent.kind 区分 CLASS/STRUCT（struct 成员函数/嵌套类不再丢 BELONGS_TO 边） | full-parse 验证：165 struct 节点，3 个带成员函数的 struct（OtaPackageManifest/SoftwareVersion/ReactorHolder）恢复 belongs_to 边（to=struct 4 条）；belongs_to 总 532（to=class 528 + to=struct 4）。修复前 struct 成员函数 BELONGS_TO 边因 parent_type 仅认 CLASS_DECL 丢失 |
| E-2 | `parser/ast_visitor.py:308` | `_extract_functions` 加 `is_definition()` 过滤跳过 .h 声明节点（纯虚 `=0` 例外保留），避免声明/定义产生两个节点 | full-parse 验证：.h+.cpp 同名 function 重复对 381→1（99.7% 消除，对比旧DB），function 总数 1804→897（减 907 声明节点）。剩 1 个 `operator<<` 为重载特例（.h:30 bool 参 / .cpp:57 short 参，参数不同非重复）。is_definition 过滤 + 纯虚例外生效 |
| **E-3** | `models.py`（`make_func_sig_suffix` + `NodeInfo.__post_init__`）、`ast_visitor.py`（`_make_function_key` + 两处调用边）、`graph_db.py`（callee 三级匹配）、`friend_extractor.py` | **unique_key 加参数签名区分重载**（2026-07-07）。function key `type\|ns\|name\|file` → `type\|ns\|name\|file\|params[\|c]`（const 加 c 标记）。节点侧与提取侧（caller key）共用 `make_func_sig_suffix` 保证逐字节一致；调用边加 `callee_param_types`，graph_db callee 三级匹配（params 精确 → parent → name 回退）。class/struct 无后缀，向后兼容。**通用工具正确性缺陷**（非 OTA 特有，任何有重载/operator/模板的 C++ 项目都踩） | full-parse 验证全绿：**①重载区分** `OtaLogStream::operator<<` 1→16 节点（源码 17 重载）；**②调用边精确** `HandleError→OtaError(OtaErrorCode,string&)` 带参构造、`ClearError→OtaError()` 无参构造各自精确指向（修复前坍缩为 1 节点乱指）；**③回归无损** override 57、McuUpdate→BasePeriUpdate 14、4 子类全在、doc_section 657、field 虚假边 0；**④orphan 边 0**（caller/node key 一致）；**⑤** function 502→523，src-gen 仍 0 |

| **E-4** | `parser/ast_visitor.py`（belongs_to parent_key）+ `query/polymorphism_query.py`（`_get_owning_class` fallback） | **belongs_to 边回归修复**（2026-07-07，全量测试复现暴露）。E-2（is_definition 过滤）后成员函数只留 `.cpp` 定义节点，但 belongs_to 的 parent_key 用函数的 `.cpp` 路径拼类节点 key，与类节点在 `.h` 的 unique_key 失配 → 边丢失（覆盖率 73%→15%，434/515 类成员函数丢边）。修复：parent_key 改用父类 cursor 的 `.h` location（`parent.location.file`，与嵌套类路径一致）；`_get_owning_class` 加 namespace 末段 fallback（双保险）。连带修复 `get_overrides` 返回 class_name 全空 | full-parse 验证：belongs_to 覆盖率 **15%→90%**（475/523，超旧DB 73%）；`SocUpdate::PerformUpgrade` 正确指向 `SocUpdate@soc_update.h`；`get_overrides` class_name 恢复 `[Gnss/Mcu/Soc/SwitchUpdate]`；**全量测试功能 9/9 ✅**；回归无损（override 57/operator<< 16/orphan 0/doc 657） |

### 修复进度小结

- **P0**：5 个真问题全部修复+验证（P0-5 误报撤销）。full-parse 73 TU 0 失败，doc_section 0→630，override 边抽样正确，字段虚假边消除。
- **P1 主题 A**（LIKE 子串匹配）：6 处全部精确化（A-5 消除 203 个误匹配最显著）。
- **P1 主题 B**（数据一致性）：4 项修复。B-3 为告知版。
- **P1 主题 C**（性能）：删 4 冗余索引 + PRAGMA 调优。N+1/executemany/长事务列为后续。
- **P1 主题 D**（MCP 可用性）：异常兜底（9 处）+ 边界校验（2 工具）+ directory 修复。连接重连/并发锁列为后续。
- **P1 主题 E**（解析正确性）：E-1（STRUCT_DECL）+ E-2（is_definition）+ **E-3（unique_key 加参数签名区分重载）+ E-4（belongs_to 边回归修复）** 已修 + full-parse 验证通过。E-3 消除重载坍缩（operator<< 1→16 节点，调用边精确指向具体重载），caller/node key 一致（orphan 边 0）。E-4 修复 E-2 引入的 belongs_to 失配（覆盖率 15%→90%），全量测试功能 9/9 全通过。
- **full-parse 4 失败 TU finding**（无法在本项目修复）：167 TU 中 4 失败——①`VehicleService.cpp`/`main.cpp`：`recoverynotifyservice/impl_type_stopwatchdognotifyenum.h` 全项目缺失（src-gen 产物未生成，src-gen/hq_vehicle_service/include 下无 recoverynotifyservice 目录）；②`log_app.cpp`：`collector/mcu_log_parser.h` 在 `hq_eth_log_app/src/` 但 log_app 的 -I 不含该路径（compile_commands include 配置缺失）；③`diag_key_store.cpp`：历史失败（旧DB 亦 failed）。均属 build 产物/compile_commands.json 配置问题，非本次 P1-E 修改引入（fatal 检查 `ast_visitor.py:76` 为 initial release 既有逻辑）。旧DB 此 3 TU success 系解析时头文件存在或环境差异。
- **P1 主题 F**（查询正确性）：F-1/F-3/F-4/F-5 修复。F-2 虚分派需 class_name 为语义限制，标注后续。
- **P2-1**（迁移回归）：`_infer_project_name` 三级策略修复，MCP 工具描述恢复显示项目名 `hq_ota_service`。
- **P2-2~P2-7**（次要问题）全部修复（2026-07-07）：P2-2 RelationType 单一来源（消除人工同步）、P2-3 抽 `cursor_utils`/`query_utils` 公共模块（消除 4+6 处重复）、P2-4 过时注释已随 P2-1 清理、P2-5 删 TemplateExtractor 死代码、P2-6 加 schema `user_version=2`、P2-7 `_autocommit` 公共接口封装。full-parse 回归数据与重构前一致（function 523/orphan 边 0）。

### 配置优化：排除 src-gen + 并行解析（2026-07-07）

| 项 | 决策 | 依据 |
|---|------|------|
| **src-gen 移出解析范围** | `generated_paths` 清空 + `exclude_paths` 加 `src-gen` | src-gen（ARA COM 生成代码）**每次 AP 建模都变**，占 43% 代码节点且不稳定，增量解析会因其变化频繁全量重提。排除影响面经查极小：业务类继承 src-gen **0 边**、业务调用 src-gen **仅 1 条**（`MakeErrorCode→GetServiceErrorDomain` 错误码样板，无业务价值）、src-gen→业务/继承业务均 0。排除后代码节点 1735→**602**（-65%），聚焦业务代码 |
| **max_workers 1→8** | 16 核机器并行解析 | 串行 167 TU 需 19+ 分钟只用 1 核；并行 8 worker **362 秒**（快 3 倍）。full-parse 常跑，并行显著省时 |

**排除 src-gen 后 full-parse 验证（167 TU，362s）**：src-gen 节点 0 ✓；业务数据无回归——override 边 57（McuUpdate→BasePeriUpdate 14）、BasePeriUpdate 4 子类（McuUpdate/GnssUpdate/SocUpdate/SwitchUpdate）全在、.h+.cpp 重复对 1（operator<< 重载特例）、field 虚假边 0、doc_section 657。业务核心类（BasePeriUpdate/McuUpdate/OtaService/OtaLogManager）均在。

---

## 八、结论

**值得肯定**: 在 libclang 限制下做出了一套可用的 C++ 语义图谱，config 驱动、延迟边解析、增量策略、虚分派展开、call_line 多调用点区分都是有想法的设计。7 月以来的几次 commit（call_line、虚继承、full-parse 文档索引、null guard）持续在修正确性问题。

**最大风险**: 正确性。P0-1（override 连错）、P0-2（解析失败删节点）、P0-3（钻石继承失效）、P0-6（虚假调用边）都会让图谱**静默产出错误数据**——这类工具的价值完全依赖准确性，错了比没有更危险。LIKE 子串匹配是系统性缺陷，影响面分析、调用链、include 查询都受波及。

**完成度问题**: 文档关联 5 层策略只实现了 2 层（content_scan + 未验证的 embedding），manual/config/rule 是死代码，manual_links 配置不生效；embedding 从未验证却被 README 宣称可用。验证报告存在 33.3% vs 100% 矛盾。

**建议**: 先修 5 个真 P0（多数是一行到十几行，成本低收益高），再系统性治理 LIKE 匹配和数据一致性，性能优化（删冗余索引 + PRAGMA）几乎免费。架构债慢慢还。

---

## 附录：架构 & 功能普适性复查（2026-07-07）

针对"是否用 OTA 项目定义了图谱工具"的疑问，系统复查核心代码（parser/query/db/mcp_server），结论：**架构与功能普适，无 OTA 硬编码**。

| 维度 | 结论 | 证据 |
|---|---|---|
| 核心代码 OTA 硬编码 | ✅ 无 | 所有 OTA 符号（BasePeriUpdate 等）仅出现在注释/docstring 示例，无一进功能逻辑 |
| 配置覆盖度 | ✅ 全 | `cpp_semantic_graph.yaml` 可配：项目名/compile_commands/源码·生成·排除路径/libclang 路径/交叉编译 target_triple+toolchain_includes/文档目录。换项目只改 YAML |
| 平台/绝对路径 | ✅ 仅默认值 | 唯一绝对路径 `/usr/lib/llvm-18/...libclang.so.1` 是默认值，YAML 可覆盖；无 `/mnt/code1`、无项目路径写死 |
| 生成代码识别 | ✅ config 驱动 | `generated_paths`/`exclude_paths` 配置优先，`src-gen` 仅作 fallback（AUTOSAR AP 通用约定，非 OTA 专属） |
| 语言特性覆盖 | ✅ 主流齐全 | 类/结构体/模板/继承/虚函数/override/构造析构/调用/成员引用/include |
| 使用接口 | ✅ 通用 | 9 个 MCP 查询接口签名传符号名/类名，不含项目语义 |

**唯一普适性缺口（非缺陷）**：README/示例均用 OTA 符号举例，新用户可能误以为工具与 OTA 绑定——观感问题，不影响功能。

**验证局限**：`clangd_baseline.json` 是 OTA 子集手工采样（2 类/6 函数/2 调用），样本量小且项目相关。当前仅有 OTA 一个真实项目可测，验证充分性受此约束；代码普适性已通过上述静态复查确认。
