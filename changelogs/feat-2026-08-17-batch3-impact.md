# feat-2026-08-17-batch3-impact：include 影响面工具 + 遥测分析 + 空结果降级提示

> 设计文档：docs/plan-2026-08-17-batch3-impact.md
> 全量重建另见 rebuild_report.json（第 4 项，重建完成后补充结论）

## 范围决策

**砍掉"MCP 进程单例化"**：MCP 是 stdio 协议，每个 IDE 会话必须各起一个进程
（进程间不能共享 stdio 连接），多进程是协议要求而非泄漏；server 进程只查
SQLite 不加载 libclang，单实例很轻。单例化会破坏多会话可用性。

## 改动内容（3 项）

### 1. `cpp_get_include_impact` 工具（第 12 个 MCP 工具）
薄封装现成 `IncludeQuery.get_all_includers`（BFS 反向追溯，部分匹配）：
输入头文件 → 受影响 TU 清单（+传递包含的中间头文件，截 20）。
server.py 新增 `_iq` 懒初始化（`_get_include`），`_ensure_fresh` 增量后
连接刷新纳入 `_iq`。

### 2. 遥测分析脚本 `scripts/telemetry_stats.py`
纯 stdlib：按工具聚合调用数/空结果数/空率/均耗时/P95，
空结果 Top N（覆盖缺口与误查排查线索）。对残行（并发写偶发）跳过。

### 3. 空结果降级提示
5 个符号类工具（search_class/search_function/callers/callees/overrides）
空结果返回追加固定提示：图谱只覆盖 source_paths，SDK/BSW/foundation
符号建议 clangd——把项目 CLAUDE.md 的搜索决策树固化进工具输出。

## 验证

1. 函数级（客户端工具清单缓存未刷新前）：
   - `cpp_get_include_impact("base_device_update.h")` → 9 个受影响 TU，
     与继承体系完全吻合（sensor/mcu/soc/switch 4 子类 TU + base/factory/
     CommHub/update_manager×2）✅
   - 空结果提示输出正确 ✅
   - 遥测两条新记录（include 工具 n=1 / 空结果 n=0）✅
2. `full_test.py`：formatter 10/10 + 冒烟 11/11
3. 新工具注册验证：server 进程 `list_tools()` 12 个工具含新工具 ✅
   （CodeBuddy 客户端需重连/重启会话后新工具可见）

## 已知限制

- **新增 MCP 工具需客户端重新握手**：CodeBuddy 会缓存工具清单，改行为（旧
  工具名不变）kill 进程即生效；**新增工具**要等客户端重连/重启会话。
- 实施中脚注：批量文本替换曾把真实换行嵌进单引号 f-string 导致语法错误，
  已修复为转义拼接（5 处），ast.parse + 全量测试把关。

## 第 4 项：全量重建结论（rebuild_report.json）

**结果**：170 TU 全部成功（0 失败，旧报告 167/1 失败），8 worker，
冒烟 11/11 + formatter 10/10 全过。

**重要发现（P1，工具质量）**：重建揭示旧库（长期增量更新累积）存在系统性
重复数据，三处指标"下降"实为清洗：

| 指标 | 旧库（增量累积） | 重建后（真相） | 定性 |
|------|----------------|---------------|------|
| overrides 边 | 71 | **45** | 旧库 decl/def 重复对累积；TryPrepare 抽查 = 4 个真实重写，重建后分毫不差 |
| calls_virtual 边 | 84 | **39**（去重 39/39 一致） | 旧库重复累积 |
| 文档关联边 | 5927/5943（不对称） | **3227/3227（对称）** | 旧库不对称是累积伪影，重建 1:1 对称 |
| 节点 | 4368 | **3517** | 旧库含重复函数/文档节点 |
| db_node_count 口径 | — | 报告 2375 = 纯代码节点，冒烟 3517 = 含 doc_section | rebuild_report 只计代码节点，口径已澄清 |

→ **遗留改进项（P1）**：incremental upsert 去重不彻底（override decl/def、
doc section 重复入库）。建议：① upsert 前按业务键查重；② 或定期全量重建
对冲；③ 遥测脚本可加"边重复率"巡检指标。

**过程问题**：重建（reset_db 换文件）后旧 MCP 进程持旧句柄报
"database disk image is malformed"——kill 重连即恢复，DB 完整性 ok。
后续全量重建 SOP 应包含"重建后 kill MCP 进程"。
