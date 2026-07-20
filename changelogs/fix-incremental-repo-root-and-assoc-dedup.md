# 修复:增量更新仓库根推断 + 文档关联去重不对称

日期:2026-07-20

## 问题

### 问题 1:增量更新"没更新"(主线 bug)

不传 `--repo-root` 走推断时,`_ensure_repo_root` 推断到错误的仓库根
`/mnt/code1/adc4.0`(顶层损坏空 `.git`,只有 `info/` 子目录),而非真正的
`ap-aa` 仓库根。

**根因(双重)**:

- A. `Path(compile_commands).parent.parent` 跳过了 ap-aa 本身
  - `compile_commands` 在 `ap-aa/compile_commands.json`,`parent` = ap-aa(正确起点)
  - 代码用 `parent.parent` = ap,跳过了 ap-aa
- B. `.git` 存在性检查不验有效性
  - `adc4.0/.git` 是损坏空目录(只有 `info/`),`.exists()` 返回 True 误判为仓库根
  - ap-aa/.git 是符号链接 -> `ap-aa.git`(真 bare 仓库,含 HEAD)

**后果**:在 adc4.0(非有效仓库)跑 `git diff` 失败,返回 0 变更,
增量"无文件变更"啥都不做 -- 表现为"增量没更新"。

### 问题 2:增量后 code_refers_to_doc 不对称丢失(副发现)

真实增量后 `code_refers_to_doc` 1875 -> 1349(减 526),`doc_describes_code`
1875 不变,双向不对称。每次增量都丢受影响 TU 的 `code_refers_to_doc` 边,
持续衰减。

**根因(双重)**:

- A. 删边策略"只删出边(from_id 在该文件的边),保留入边"
  ([incremental_updater.py:15](../incremental_updater.py#L15),**有意设计**,
  防 [task_4_1:117](../docs/task/cpp_semantic_graph/task_4_1_incremental_update.md#L117)
  共享节点误删 + review 2026-07-06:142 跨文件调用边丢失):
  - `code_refers_to_doc`(from_id=代码节点,受影响 TU)是**出边** -> 被 `delete_edges_from_file` 删
  - `doc_describes_code`(from_id=文档节点,文档未变)是**入边** -> 保留
- B. 重建 content_scan 去重**只查 `doc_describes_code`**
  ([association_ingester.py:453](../parser/association_ingester.py#L453)):
  入边还在就 `continue` 跳过整对 -> 被删的 `code_refers_to_doc`(出边)永不补回

**删边策略不动**(有意设计,见上述)。只修重建去重。

## 改动文件

| 文件 | 改动 |
|---|---|
| [parser/change_detector.py:190](../parser/change_detector.py#L190) | `_ensure_repo_root`:`parent.parent` -> `parent`(从 ap-aa 起)+ HEAD 有效性验证(符号链接/file/目录均含 HEAD);去掉无意义的 `for sp` 死循环(循环体未用 `sp`) |
| [parser/association_ingester.py:449](../parser/association_ingester.py#L449) | content_scan 去重双向分别检查:各方向独立查重,缺失才插入(不再共用一次去重跳过整对) |
| [tests/test_doc_fusion.py:50,194](../tests/test_doc_fusion.py#L50) | 适配 v4 schema:`extra_info` 列 -> `content_preview`/`tags`(v4 拆列后测试脚本过时遗留,非本次引入) |

## 验收(系统级,不只测改动点)

| 指标 | 修前 | 修后 |
|---|---|---|
| 推断 repo_root | adc4.0(❌ 空 .git) | ap-aa(✅ 真仓库) |
| 检测变更 | 0 | 10(M6+A4) |
| 受影响 TU | 0 | 9 |
| impact_chain | 无 | file_handler.h->7TU / json_util.h->4 / soc_package_parser.h->2 |
| 真实重解析 | - | 9/9 成功,0 失败,24s |
| code_refers_to_doc | 1875 -> 1349(❌ 减 526) | 1875 -> 1875(✅ 不变) |
| doc_describes_code | 1875 | 1875 |
| 双向对称 | ❌ | ✅ |
| 其余 7 类边 | - | 全 +0(belongs_to/calls_direct/calls_virtual/inherits/overrides/type_alias) |
| test_doc_fusion | 跑不起来(`no such column: extra_info`) | 全过,双向对称 ✅ (1875 vs 1875) |

三场景等价性(去重双向化):
- 全量初次:双向都不存在 -> 都插(同原逻辑)
- 增量重建:doc_describes_code 在->跳过,code_refers_to_doc 缺->补插(恢复对称)
- 幂等重跑:双向都在 -> 都跳(同原逻辑)

## 风险点

- 改动 2(去重双向化)影响全量 + 增量 `content_scan`。全量等价(见上三场景),
  无回归。已用 test_doc_fusion 验证(双向对称 ✅)。
- 删边策略 `delete_edges_from_file` 未动;B-3(删节点 CASCADE 删入边,
  review 2026-07-06:142)未触碰(本次 `nodes_removed=0` 未触发),保持告知版状态。

## 附带发现

[task_4_1:111](../docs/task/cpp_semantic_graph/task_4_1_incremental_update.md#L111)
验收原列"支持两种触发方式:Git diff 和**文件系统监听**"--设计原计划有 watcher
自动触发,但**未实现**。当前只有手动 CLI `python -m cpp_semantic_graph incremental`。
README 的"自动"指增量自动追踪 include 依赖,非自动触发运行。
