# Task 4-5: MCP 惰性增量更新

日期:2026-07-20
状态:已实现（2026-07-20，9 项验收全过）

## 目标

MCP 工具调用时**惰性增量**:检测到新合入 commit 才增量一次,同一 commit no-op。
替代手动 CLI `incremental`,实现"代码合入后图谱自动跟上"。

**形态选择**(于先生定):惰性(按需)而非监听(常驻),适配"文件改动频率不大"。
- 查询频率 >> 改动频率 -> 查询时 `rev-parse` 比较(99% no-op)有变更才增量
- 复用 MCP server(本就常驻),不起独立 daemon
- 基准是 commit(合入),不碰工作区未提交改动

补 [task_4_1:111](task_4_1_incremental_update.md#L111) 验收"文件系统监听"未实现项(改用惰性形态)。

## 现状

- MCP server 只有 DB 路径(`CPP_GRAPH_DB`),无 config;但 `_infer_project_name`
  已从 DB 同目录读 `cpp_semantic_graph.yaml`(策略2,server.py:515),复用拿 config
- `IncrementalUpdater.run(base_ref)` 基准 commit 间 diff(`HEAD~1..HEAD`)
- `SCHEMA_VERSION=4`;查询类 `_gq/_cq/_pq/_tq/_dq` lazy init,各开连接
- `_ensure_repo_root` 已修(2026-07-20),MCP 走推断能正确定位 ap-aa 仓库根

## 改动文件清单

| 文件 | 改动 |
|---|---|
| [db/schema.sql](../../db/schema.sql) | 加 `incremental_state` 表(key PK, value, updated_at) |
| [db/graph_db.py](../../db/graph_db.py) | `SCHEMA_VERSION` 4->5;`get/set_last_incremented_ref()` |
| [incremental_updater.py](../../incremental_updater.py) | `run()` 加 `record_state` 参数,成功后写 `last_ref = HEAD` |
| [parser/change_detector.py](../../parser/change_detector.py) | `get_current_ref()`(git rev-parse HEAD) |
| [parser/config.py](../../parser/config.py) + yaml | `lazy_increment_enabled`(默认 true)+ `lazy_increment_threshold`(默认 20) |
| [mcp_server/server.py](../../mcp_server/server.py) | `_ensure_fresh()`(rev-parse 节流+阈值降级+连接刷新);`_get_queries` 前调;config 从 DB 同目录 yaml |
| [cli.py](../../cli.py) | `incremental` 命令 `record_state=True`(CLI 跑完 MCP no-op) |

## 设计方案

### `_ensure_fresh()` 流程(MCP 查询入口,`_get_queries` 调用前)

1. `lazy_increment_enabled=False` -> 直接 return(功能关闭)
2. 从 DB 同目录读 `cpp_semantic_graph.yaml` -> `ProjectConfig`(复用 `_infer_project_name` 策略2)
3. `ChangeDetector(config).get_current_ref()` -> `git rev-parse HEAD`(<1ms)
   - 失败(非 git 仓库/repo_root 推断失败) -> warning + return(降级用旧图谱)
4. `db.get_last_incremented_ref()` -> `last_ref`
   - `last_ref` 为空(首次/旧库) -> set `last_ref = HEAD` + return(记录当前,下次新 commit 才增量;full-parse 已是最新,不重复增量)
5. `head == last_ref` -> return(**no-op**,同一 commit)
6. `git diff --name-only last_ref..HEAD` 算变更文件数
   - `last_ref` 失效(rebase/reset 导致) -> catch, fallback `HEAD~1` 或 skip
7. 变更数 > `threshold` -> warning("变更 N 文件超阈值,请手动 `incremental`") + return(降级)
8. 变更数 <= `threshold` -> `IncrementalUpdater(config, db).run(base_ref=last_ref, record_state=True)`
9. 成功 -> 连接刷新(`_gq/_cq/_pq/_tq/_dq = None`,下次 `_get_queries` 重建)
10. 失败 -> warning + return(查询用旧图谱)

### `last_ref` 存储

- `incremental_state` 表:`key='last_incremented_ref'`, `value=<commit hash>`, `updated_at`
- `graph_db.get/set_last_incremented_ref()` 读写
- `IncrementalUpdater.run(record_state=True)` 成功后 set `last_ref = HEAD`

### config 来源

- DB 同目录 `cpp_semantic_graph.yaml`(复用 `_infer_project_name` 策略2)
- 不需新环境变量,MCP 启动配置不变

### 连接刷新

- 增量后 `_gq/_cq/_pq/_tq/_dq = None`,下次 `_get_queries()` 重建
- 解决 review D:156 缓存连接指向旧 DB 问题

### CLI 一致

- `cli incremental` 跑 `run(record_state=True)`,完后 `last_ref=HEAD`
- 下次 MCP 查询 no-op(CLI 与 MCP 状态共享)
- 避免 CLI 跑完 MCP 又跑一次

## 验收标准

1. 新 commit 后第一次 MCP 查询触发增量(`files_changed>0`,`last_ref` 更新到 HEAD)
2. 同一 commit 第二次查询 no-op(`rev-parse` 相等,<1ms,不增量)
3. 变更 > `threshold`(默认 20)跳过同步增量 + warning,查询用旧图谱
4. 增量后查询看到新数据(连接刷新生效)
5. CLI `incremental` 后 MCP no-op(`last_ref` 一致)
6. 工作区未提交改动不触发增量(基准是 commit HEAD)
7. `lazy_increment_enabled=False` 时功能关闭(`_ensure_fresh` 直接 return)
8. 非 git 仓库 / config 缺失 / `last_ref` 失效时降级用旧图谱(不阻塞查询)
9. 首次(`last_ref` 空)记录 HEAD 不增量(full-parse 已是最新)

## 风险点

1. **增量阻塞首次查询**(~25s/9TU)--阈值降级 + `lazy_increment_enabled` 可关
2. **config 文件移动**--DB 同目录约定,移动则 fallback return
3. **并发**:多 MCP 实例同时增量--WAL + 增量时 DB 锁(review D:159),first-wins 其余 fallback
4. **`last_ref` 失效**(commit 被 rebase/reset)--`git diff last_ref..HEAD` 失败 catch,fallback `HEAD~1` 或 skip
5. **首次 `last_ref` 为空**--记录 HEAD 不增量(设计如此,full-parse 已最新)

## 实施步骤

1. `schema.sql` 加 `incremental_state` 表 + `graph_db` SCHEMA_VERSION 5 + `get/set` 方法
2. `ChangeDetector.get_current_ref()`(git rev-parse HEAD)
3. `IncrementalUpdater.run` 加 `record_state` 参数,成功后写 `last_ref=HEAD`
4. `config.py` + yaml 加 `lazy_increment_enabled`/`threshold`
5. MCP `_ensure_fresh()` + `_get_queries` 注入 + config 从 DB 同目录读
6. `cli incremental` `record_state=True`
7. 测试:新 commit 触发 / 同一 commit no-op / 阈值降级 / 连接刷新 / CLI 一致 / 首次记录
8. Review(MCP 工具验证影响面)+ 文档 HTML + 更新 INDEX/CLAUDE.md
9. commit/push
