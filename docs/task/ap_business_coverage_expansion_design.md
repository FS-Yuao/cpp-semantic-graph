# 设计文档：cppsg 覆盖范围扩展至 ap-aa 全业务模块 + doc_query 多关键词 + clangd 优先搜索规则

> 版本：v1 | 日期：2026-07-15 | 关联：README FAQ「条件编译盲区」、近两周 MCP 使用情况分析

## 1. 目标

1. **扩展代码覆盖范围**：把 cpp_semantic_graph（cppsg）的解析范围从单一 `hq_ota_service` 扩展到 ap-aa/app 下全部 8 个 `hq_*` 业务模块 + `util` 公共库（共 141 个 TU），构建覆盖整个 AP 业务层的语义图谱。
2. **doc_query 多关键词搜索**：`cpp_search_docs` 支持多关键词 OR 检索 + 按命中数排序，解决近两周 MCP 使用中"多义词拆词检索 60% 空结果"的问题。
3. **固化 clangd 优先搜索规则**：把"cppsg 无果 -> 符号在 SDK/BSW 或 ap 其它模块或 DB 不可用 -> 优先 clangd MCP -> 最后 grep"写入 memory 与 CLAUDE.md 搜索决策树。

## 2. 现状问题

### 2.1 覆盖范围过窄

当前 `cpp_semantic_graph.yaml`：

```yaml
source_paths:
  - "hq_ota_service/src"
  - "hq_ota_service/include"
```

compile_commands.json 共 669 个 TU，cppsg 仅覆盖 hq_ota_service 的 32 个（4.8%）。后果：

- 跨模块调用链断裂：OTA ↔ DoIP / 诊断 / 状态管理 / 车辆服务的调用关系完全不可见。
- `cpp_get_callers` / `cpp_traverse_graph` 在跨模块场景频繁 no_result。近两周分析中 `TransferData`、`DoipTransmit` 等符号无果，并非 SDK 符号，而是 hq_doip_service 业务代码——本应在覆盖范围内却缺失。

### 2.2 doc_query 不支持多关键词

`query/doc_query.py:76-81` 当前逻辑：

```python
sql = """SELECT * FROM node
         WHERE type='doc_section'
         AND (name LIKE ? OR doc_title LIKE ? OR content_preview LIKE ?)"""
params = [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
```

单个关键词作为整体子串匹配。输入"刷写 激活"会查找字面"刷写 激活"连续子串，几乎必然空结果。近两周 `cpp_search_docs` 空率约 60%，多关键词场景是主因之一。

### 2.3 搜索降级路径未固化

分析发现多处本应先用 clangd（覆盖全项目、两周内 100% 成功）却直接裸 grep 的过程问题。规则未写进 CLAUDE.md，易复发。

## 3. 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `cpp_semantic_graph.yaml` | 修改 | source_paths 扩到 12 项（8 模块 src + ota include + util 3 子目录锚定 `app/util/`）；project.name 更新 |
| `query/doc_query.py` | 修改 | 多关键词 OR + 按命中数排序 |
| `docs/task/ap_business_coverage_expansion_design.md` | 新增 | 本设计文档 |
| `docs/result_reports/` | 新增 | 扩展结果报告（节点/边数对比、跨模块调用链验证、9 工具回归） |
| adc4.0 `CLAUDE.md` | 修改 | 搜索决策树补 clangd 优先降级规则 |
| adc4.0 `memory/` | 新增 | clangd 优先搜索规则详细版（feedback 类） |
| `README.md` / `README_zh.md` | 同步 | 覆盖范围说明从"hq_ota_service"改为"ap-aa 业务模块"（如有相关措辞） |

> 注：`docs_dir` 仍指向 hq_ota_service/docs（单路径字符串，扩多目录需改 config.py+doc_ingester+change_detector 三文件，超出"包含代码"诉求，列为后续工作，见风险点 R3）。

## 4. 设计方案

### 4.1 覆盖边界：仅业务模块（于先生已确认）

**纳入（141 TU）**：

| 模块 | source_paths 项 | TU | 说明 |
|---|---|---|---|
| hq_ota_service | `hq_ota_service/src`、`hq_ota_service/include` | 32 | 现有，保留 include |
| hq_diag_did_rid_app | `hq_diag_did_rid_app/src` | 27 | |
| hq_state_manager | `hq_state_manager/src` | 16 | |
| hq_diag_app | `hq_diag_app/src` | 16 | |
| hq_log_app | `hq_log_app/src` | 16 | |
| hq_eth_log_app | `hq_eth_log_app/src` | 15 | |
| hq_vehicle_service | `hq_vehicle_service/src` | 12 | |
| hq_doip_service | `hq_doip_service/src` | 5 | |
| util | `app/util/did_access`、`app/util/log`、`app/util/service_base` | 2 | header-only 库随业务 TU #include 入库；锚定 `app/util/` 前缀（见 4.2） |

**排除（不纳入 source_paths，部分由 exclude_paths 兜底）**：

| 范围 | TU | 原因 |
|---|---|---|
| amsr-vector-fs-log-daemon | 3 | SDK 性质守护进程 |
| thirdparty | 8 | 第三方，exclude_paths 已含 |
| src-gen（amsr_diag_daemon 生成码） | 517 | 生成代码，exclude_paths 已含；不在 app/ 下自动排除 |

### 4.2 util 子目录枚举 + 路径锚定（关键正确性决策）

`_matches_any`（config.py:148-161）是**子串匹配**。util 路径模式有两层误匹配风险：

**风险一：裸 `"util"` 过宽**。会误匹配 `hq_ota_service/src/peri_utils`、`hq_diag_did_rid_app/src/utils`，且调用边过滤 `should_extract_call` 会把任何 callee 路径含 "util" 的 SDK 头误判为项目代码，污染图谱。故枚举三个子目录而非裸 `"util"`。

**风险二：裸 `"util/log"` 仍撞 SDK 头（首轮重建实测发现）**。子串匹配不只作用于 compile_commands 里的 TU 路径，还作用于**经 `#include` 进来的 SDK 头路径**——ast_visitor.py:116 对每个 cursor 的 location 文件做 `should_extract_node`，SDK 头被业务 TU include 后其符号也过这道过滤。实测 SDK 头 `amsr/log/util/log.h`（路径含子串 `util/log`）被命中，导致：误提取 8 个 `amsr::log::*` SDK 节点（Context/ScopedType/CreateLogger/Log 等）；`make_relative_path` 按 `util/log` 截断后剩余 `.h`，file_path 被截成无意义的 `".h"`。首轮设计只查 compile_commands 的 TU 路径就断言"无误匹配"，**漏了 #include 进来的 SDK 头路径**——教训：子串型 source_paths 必须同时验证 SDK include 路径，不能只看 TU 路径。

**对策：锚定到项目实际位置 `app/util/<name>`**。`app/util/log` 仍是 `.../ap-aa/app/util/log/Logger.h` 的子串（正确入库），但不是 `.../amsr/log/util/log.h` 的子串（无 `app/` 前缀，不再误匹配）。已验证：项目 util 源在 `app/util/{did_access,log,service_base}`，compile_commands 收录 2 个 TU（Logger.cpp/StringStream.cpp）；`app/util/<name>` 不匹配 `app/util/test/<name>` 测试拷贝；树中 `app/util/` 仅项目源一处（其余为 build/test-reports，已 exclude）。

util 的 did_access / service_base 是 header-only（0 个 .cpp），其类/函数定义随业务 TU 的 `#include` 被解析——ast_visitor.py:116 对每个 cursor 的 location 文件做 `should_extract_node` 过滤，header 路径命中 `app/util/did_access` 即入库。未被任何业务 TU 引用的 util 头不入库（libclang 只解析被 include 的代码），符合预期。

### 4.3 project.name 更新

`project.name` 仅用于 `mcp_server/server.py:64` 系统提示词展示标签（"适用于 {name} 项目的 C++ 代码查询场景"），无路径/过滤功能。从 `hq_ota_service` 改为 `ap-aa 业务模块`，使 MCP 自描述与新覆盖范围一致。

### 4.4 doc_query 多关键词 OR + 命中数排序

**输入约定**：关键词以空白分隔。单关键词保持现有行为；多关键词按 OR 逐词匹配，按命中词数降序排序。

**实现**（doc_query.py:76-81 区域）：

```python
# 按空白拆词；空关键词直接返回空
words = [w for w in keyword.split() if w]
if not words:
    return []  # 或现有空返回路径

# 每个词在 3 个字段任一命中即算该词命中；命中词数 = 排序权重
# 用 ? 占位防注入（LIKE 模式单独构造）
select = "SELECT * FROM node WHERE type='doc_section'"
where_clauses = []
params = []
for w in words:
    pat = f"%{w}%"
    where_clauses.append("(name LIKE ? OR doc_title LIKE ? OR content_preview LIKE ?)")
    params += [pat, pat, pat]
sql = f"{select} AND ({' OR '.join(where_clauses)})"
# 按命中词数降序：用 CASE 逐词计数
order_terms = " + ".join(
    f"CASE WHEN (name LIKE ? OR doc_title LIKE ? OR content_preview LIKE ?) THEN 1 ELSE 0 END"
    for _ in words
)
order_params = []
for w in words:
    pat = f"%{w}%"
    order_params += [pat, pat, pat]
sql += f" ORDER BY ({order_terms}) DESC"
params += order_params
```

**注意**：LIKE 默认大小写不敏感（SQLite `case_sensitive_like` pragma 默认 OFF），无需额外处理。`?` 占位防 SQL 注入。空关键词返回空（避免 `%%` 全表扫）。

### 4.5 clangd 优先搜索规则

写入 adc4.0 `CLAUDE.md` 搜索决策树末尾 + memory（feedback 类）：

> cppsg 无果时，先判断符号位置：若在 SDK/BSW、或 ap 其它尚未覆盖模块、或 DB 不可用 -> **优先 clangd MCP**（覆盖全项目 compile_commands 范围、两周内 100% 成功）-> 最后才用裸 grep。禁止 cppsg 无果直接 grep。

## 5. 验收标准

### 5.1 覆盖扩展（系统级指标）

- [ ] DB 节点数从 ~1242 显著增长（预期 5000+，8 模块代码体量数倍于 ota 单模块）。
- [ ] DB 边数从 ~5482 显著增长。
- [ ] `cpp_search_class VehicleService` / `DoipTransmit` / `TransferData` 有结果（此前 no_result 的业务符号现可命中）。
- [ ] **跨模块调用链**可见：`cpp_get_callers` 或 `cpp_traverse_graph` 能给出 OTA ↔ DoIP / 诊断 / 状态管理 的至少一条真实跨模块调用边。
- [ ] util 公共类型（如 `Logger`、`DidAccess`、`ManagedService`）作为节点存在且被业务模块调用边指向。

### 5.2 doc_query 多关键词

- [ ] 单关键词行为不变（回归现有用例）。
- [ ] 多关键词"刷写 激活"返回非空，且同时含两词的文档排在只含一词的之前。
- [ ] 空关键词返回空，不报错。
- [ ] 关键词含 `'` 等特殊字符不报错（占位符防护）。

### 5.3 无回归

- [ ] `full_test` 9 个 MCP 工具全部通过。
- [ ] `full_test` 6 维准确率不下降（vs clangd_baseline.json）。
- [ ] `needs_resolution` 仍为 0（无新增未解析边）。
- [ ] 节点 unique_key 无冲突（重载区分 `make_func_sig_suffix` 仍生效）。

### 5.4 搜索规则

- [ ] adc4.0 CLAUDE.md 搜索决策树含 clangd 优先降级规则。
- [ ] memory 新增 feedback 记录 + MEMORY.md 索引更新。

## 6. 风险点

| 编号 | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 子串匹配误匹配 | 裸 `util/log` 撞 SDK 头 `amsr/log/util/log.h`，误提取 8 个 amsr::log 节点 + file_path 截成 `.h`（首轮重建实测命中） | 锚定到 `app/util/<name>`：仍是项目源子串、不是 SDK 头子串（4.2 风险二） |
| R2 | 全量重解析耗时 | 141 TU vs 32 TU，max_workers=8 预计 6→15 分钟 | 可接受；增量解析后续按 git diff |
| R3 | docs_dir 仅 OTA 文档 | 代码扩到全业务但文档仍只 OTA，doc-to-code 关联覆盖下降 | 本次不动（单路径字符串改多目录涉及 3 文件），列为后续；doc_query 多关键词对 OTA 文档仍有效 |
| R4 | 跨模块重载/同名类冲突 | 不同模块同名类 unique_key 含 file_path 区分，理论无冲突 | full_test 验证 unique_key 无冲突 |
| R5 | util header-only 头未被任何 TU include | 该头不入库 | 符合预期（未被引用的代码不在图谱），非缺陷 |
| R6 | full_test 依赖 semantic_graph_full.db | 扩展后需重建该 DB 供 full_test | full-parse 生成后重跑 full_test |

## 7. 实施步骤

1. **写设计文档**（本文档）。
2. **改 yaml**：source_paths 扩 12 项 + project.name 更新。
3. **重建 DB**：`python3 -m cpp_semantic_graph.cli full-parse`（或现有入口），生成 semantic_graph_full.db。
4. **改 doc_query.py**：多关键词 OR + 命中数排序。
5. **系统级验证**：full_test 9 工具 + 6 维准确率 + 跨模块调用链抽检 + doc_query 多词用例。
6. **写结果报告**：docs/result_reports/，含前后节点/边数对比与验证记录。
7. **固化搜索规则**：CLAUDE.md 搜索决策树 + memory feedback + MEMORY.md 索引。
8. **Review**：自查 P0/P1/P2，MCP 工具验证影响面，更新 README 覆盖范围措辞 + 同步 HTML。
