# feat: MCP 惰性增量更新（task_4_5）

日期:2026-07-20

## 新增

MCP 工具调用时自动检测新合入 commit，有才增量一次，同一 commit no-op。
替代手动 CLI `incremental`，代码合入后图谱自动跟上。

补 [task_4_1:111](../docs/task/cpp_semantic_graph/task_4_1_incremental_update.md#L111) 验收
"文件系统监听"未实现项（改用惰性形态，适配"改动频率不大"）。

## 形态选择

惰性（按需）而非监听（常驻）：
- 查询频率 >> 改动频率 -> 查询时 rev-parse 比较（99% no-op）有变更才增量
- 复用 MCP server（本就常驻），不起独立 daemon
- 基准是 commit（合入），不碰工作区未提交改动

## 改动文件

| 文件 | 改动 |
|---|---|
| [db/schema.sql](../db/schema.sql) | 加 `incremental_state` 表（key PK, value, updated_at） |
| [db/graph_db.py](../db/graph_db.py) | `SCHEMA_VERSION` 4->5（v4->v5 仅加表，自动升不告警）；`get/set_last_incremented_ref()` |
| [parser/change_detector.py](../parser/change_detector.py) | `get_current_ref()`（git rev-parse HEAD） |
| [parser/config.py](../parser/config.py) + yaml | `lazy_increment_enabled`(默认 true) / `threshold`(默认 20) |
| [incremental_updater.py](../incremental_updater.py) | `run()` 加 `record_state` 参数，成功后写 `last_ref=HEAD` |
| [mcp_server/server.py](../mcp_server/server.py) | `_ensure_fresh()`（rev-parse 节流+阈值降级+连接刷新）；`_get_queries` 注入；config 从 DB 同目录 yaml |
| [README_zh.md](../README_zh.md) | 加「惰性增量（MCP 自动）」段 |

## 流程

`_ensure_fresh()`（MCP 查询入口 `_get_queries` 调用前）：
1. `enabled=False` / config 缺失 -> return
2. `rev-parse HEAD`（<1ms）；失败 -> return（降级用旧图谱）
3. `head == _last_checked_head` -> return（同 commit 不重复读 DB）
4. 读 `last_incremented_ref`；空（首次）-> 记录 HEAD 不增量，return
5. `head == last_ref` -> no-op return
6. 变更文件数 > `threshold` -> warning + return（降级）
7. 跑增量 `base_ref=last_ref`（`record_state=True` 更新 `last_ref=HEAD`）
8. 刷新查询连接（`_gq` 等置 None，下次重建）

## 验收（9 项全过）

1. 新 commit 第一次查询触发增量（2 TU 重解析，0 失败）
2. 同一 commit 第二次查询 no-op（rev-parse 相等，无增量日志）
3. 变更 > threshold 跳过 + warning（threshold=1 变更 2 验证）
4. 增量后连接刷新（`_gq=None`）
5. CLI `incremental` 后 MCP no-op（`record_state` 默认 True，CLI/MCP 状态共享）
6. 工作区未提交改动不触发（基准 commit HEAD）
7. `enabled=False` 功能关闭
8. 非 git 仓库 / config 缺失降级用旧图谱（不阻塞查询）
9. 首次记录 HEAD 不增量（full-parse 已最新）

对称保持：增量后 `code_refers_to_doc == doc_describes_code`（1875==1875，方案 C 修复生效）。
schema v4->v5 自动升级（仅加 `incremental_state` 表，IF NOT EXISTS 安全，不告警）。

## 风险

- 增量阻塞首次查询（~25s/9TU）--阈值降级 + `enabled` 可关
- config 文件移动 -- DB 同目录约定，移动则 fallback return
- 并发：多 MCP 实例同时增量 -- WAL + DB 锁（review D:159），first-wins 其余 fallback
- `last_ref` 失效（rebase/reset）-- `git diff last_ref..HEAD` 失败 catch，fallback

## 配置

```yaml
lazy_increment:
  enabled: true     # false=仅手动 CLI incremental
  threshold: 20     # 变更文件数超此跳过同步增量
```
