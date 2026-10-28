# 文档图谱与跨项目工程记忆

同一个本机 HTTP MCP 服务由 Codex 与 CodeBuddy 共用。保存可维护文档的全文快照、从中提炼的结论、适用条件、代码符号及替代关系，供以后相似任务优先复用。存储仍是现有 `doc_graph.db`；没有另外引入云服务或模型 API。

## 日常使用

1. `memory_store(action="status", project_id="")` 查项目登记。按当前目录对应的最长 root 选择项目；新项目用 `register` 登记稳定 ID 和绝对根目录。
2. 分析前 `memory_recall(project_id, keyword, revision)`；先查当前项目和明确标为 portable 的通用经验。
3. 根据命中 ID 用 `memory_get` 读完整原文、适用条件和证据。文档返回原路径、行号、内容哈希；长内容可分页。
4. 形成可复用成果后先维护项目文档，再 `capture` 保存全文，`remember` 保存有充分 detail 和 conditions 的结论。两端写入同库，无需维护两份记忆。
5. 改变结论时保留旧版本；新结论推翻旧结论用 `supersede` 记录原因，旧条目退出默认召回。修改源文档后重新 capture 并更新受影响结论。

```json
{"action":"register","project_id":"my-project","root":"/absolute/project"}
{"action":"capture","project_id":"my-project","path":"docs/design.md","revision":"actual-baseline"}
{"action":"remember","project_id":"my-project","document_id":"返回的 memory-doc ID","title":"一句话结论","detail":"结论与证据","conditions":"适用前提和例外"}
```

`remember` 默认 `confidence="suspected"`。只有实际验证后才显式传 `confirmed` 及稳定的 `evidence_key`；重复提交同一验证不增加证据。工具保存调用方的验证声明，不替调用方判断证据是否充分。修改结论不算新证据，也不刷新确认时间。

`scope="project"` 保存项目事实；只有经提炼并说明适用条件的经验才用 `portable`。查询 portable 条目时仍要核对新平台条件。`include_history=true` 才查看已归档、失效、撤销和被替代记录。当前版本与保存版本不符、源文档变化或丢失时，返回重新验证提示。

## 工具与兼容

- `memory_store`：register / capture / remember / supersede / status。
- `memory_recall`：按项目及版本召回结论、全文章节和证据边。
- `memory_get`：分页读完整快照；结论 history 中的 snapshot_hash 可用于读取改写前版本。
- 原有 `doc_graph_search_tool`、`search_findings_tool`、`record_finding_tool`、`list_documents`、`get_doc_stats`、`check_finding_freshness_tool` 保留。
- `doc_search`、`doc_admin` 现于服务启动前注册。旧图谱工具仍查询原 ADC4 文档索引，新项目使用 memory 接口。旧记录默认归属 adc4，原来的置信度属于历史标签，不代表在当前基线重新验证。

接口尚未加载的当前聊天可使用同库 CLI：`python3 /mnt/code1/cpp_semantic_graph/doc_graph/memory_cli.py`，JSON 从 stdin 输入。action 为 register/capture/remember/recall/get/supersede/status。

## 数据保证与边界

- 传统 parser 重建只在一个 SQLite 事务中替换 node/edge/doc_fts；不删除数据库文件、不导出后覆盖在线 finding 和记忆表。失败回滚旧索引，并发写入由 SQLite 协调。
- 传统文档 FTS 索引完整正文，包括表格和正文后部；knowledge 节点保存完整章节。新的文档快照额外按章节/80 行切片，返回行号与全文哈希。
- 结论与独立验证、编辑历史分别保存。代码符号存在性只可作为新鲜度线索，不能增加证据计数。
- 检索是词法/同义词候选召回，可选向量仅对旧 finding 命中排序增强。没有全量召回或自动因果验证保证。`DOC_GRAPH_EMBED_ENABLED=0` 可完全关闭可选向量模型。
- 源文档在明确 capture 时更新；不是文件监听器，不会后台抽取全部聊天。两端的入口规则负责相关任务开始时召回、产出后保存。
- 文档 capture 仅接受注册项目根目录内的 UTF-8 md/txt/rst/adoc，单份最多 2 MiB。目录不能跨项目重绑定；查询范围是知识治理边界，不是操作系统多租户权限隔离。
- 完整备份使用 SQLite backup API，包括在线记忆和 WAL 中已提交内容。旧 export_findings/import_findings 仅用于旧 finding 数据交换，不作为完整记忆库备份入口。

## 部署与验证

沿用 `systemctl --user restart doc-graph-mcp` 和 `http://127.0.0.1:8930/mcp`。启动入口位于所有工具注册之后。修改客户端白名单后重新加载 MCP 工具。

本轮回归：`python3 -m unittest discover -s doc_graph -p test_memory.py -v`（临时数据库）；协议检查见交付目录 `mcp_smoke.py`。原有设计可查 `01_architecture.md`、`02_doc_rules.md`、`03_task_breakdown.md`。
