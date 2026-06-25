# 三层测试用例表：问题 → 工具调用 → 代码验证

> 核心洞察：端到端价值 = **L1→L2 翻译准确** × **L2→L3 返回准确**  
> 现有测试只覆盖 L2→L3。本表补全 L1→L2，验证 AI 能否从真实问题正确选工具+传参。

---

## 三层模型

```
L1: 用户问题              "GetSocBootChain 谁调用了？"
    ↓ AI 翻译（选工具 + 组织参数）     ← 本表重点验证
L2: 工具调用              get_callers("getActiveBootChain")
    ↓ 图谱查询（已有测试覆盖）
L3: 返回结果              OtaManager::CheckBootChain, ...
    ↓ 对比源码/clangd
L4: 回答用户问题          "OtaManager 和 OtaServiceInterface 调用了它"
```

---

## 1 架构理解类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-01 | "BasePeriUpdate 有哪些子类？" | `get_inheritance("BasePeriUpdate", direction="down", depth=-1)` | GnssUpdate, McuUpdate, SocUpdate, SwitchUpdate | ✅ 清晰列出 4 个子类 | 无——问题直述类名和关系 | ✅ |
| T3-02 | "能不能在 BasePeriUpdate 加虚函数？影响多大？" | `get_inheritance("BasePeriUpdate", down, -1)` → 4子类 + `get_file_symbols("base_peri_update.h")` → 20符号 | 4 子类 + 20 个成员 | ✅ 4 个子类都会受影响，20 个已有成员说明类已很重 | 需两步：先查子类数，再看类规模 | ✅ |
| T3-03 | "MccAdapter 和 OtaServiceInterface 什么关系？" | `search_class("MccAdapter")` → 查成员 → `search_class("OtaServiceInterface")` → 对比成员 | MccAdapter 成员: {setDefaultBootChain, getDefaultBootChain, getActiveBootChain, setNextBootChain, GetInstance, init} | ✅ MccAdapter 是 MCC 通道适配器，接口和 OtaServiceInterface 镜像(同名方法) | 问题隐含"关系"，需判断是继承(否)还是接口镜像(是) | ✅ |
| T3-04 | "升级服务整体架构是怎样的？" | `traverse_graph("OtaManager", depth=2, direction="both")` | 47 节点 / 46 边 | ✅ 覆盖核心管理类+所有关联 | "整体架构"需选起点(OtaManager)和遍历深度，不选对会太浅或太散 | ✅ |

## 2 调用链追踪类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-05 | "SocUpdate 升级流程怎么走的？" | `get_callees("PerformUpgrade", class_name="SocUpdate")` | Logger::Info, ExecuteDriveUpdate, StartContentServer, NotifyProgress, StopContentServer, ... | ✅ 9 步流程清晰 | "升级流程"需映射到 PerformUpgrade，且限定 SocUpdate(否则返回全部子类) | ✅ |
| T3-06 | "OtaManager 初始化做了什么？" | `get_callees("Init", class_name="OtaManager")` | ChangeState, CheckPartitionSwitchResult, LoadTaskData, SetDefaultBootChain, ... (25 个) | ✅ 25 步初始化流程 | "初始化"需映射到 Init，且 OtaManager 有 Init，不是别的类的 Init | ✅ |
| T3-07 | "Rollback 怎么做的？" | `get_callees("PerformRollback", class_name="OtaManager")` | DeleteBackupPoint, RestoreFirmwareFile, RestoreFromBackup, RollbackConfiguration, ... | ✅ 7 步回滚流程 | "Rollback"需映射到 PerformRollback，不是 Rollback(子类虚函数名) | ✅ |
| T3-08 | "谁调用了 FileExists？" | `get_callers("FileExists", class_name="FileHandler")` | 15 个函数: gnss/mcu/soc/switch_update + file_handler 内部 | ✅ 所有使用文件存在性检查的地方 | "FileExists"需限 class_name 否则同名函数噪音多 | ✅ |

## 3 业务约束验证类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-09 | "GetSocBootChain 不能用 SetError，为什么？" | `search_function("SetError", class_name="OtaServiceInterface")` → 0 个 | SetError 不存在于 OtaServiceInterface | ✅ 没有 SetError 方法，调用会编译错误 | "GetSocBootChain" 是业务概念名，实际函数是 getActiveBootChain；需理解 IDL 约束 | ✅ |
| T3-10 | "kApplicationErrorMap 是空的吗？" | `search_class("ErrorMap")` + `search_function("ErrorMap")` → 0 个 | ErrorMap 不在图谱中(是 IDL 常量，非 C++ 函数/类) | ⚠️ 图谱不覆盖 IDL 常量，需 grep 补充 | "kApplicationErrorMap" 是 ARA COM 生成常量，图谱只存 class/function | ⚠️ 需降级 grep |
| T3-11 | "不要在 BasePeriUpdate 加只有单个子类用的回调" — 怎么验证？ | `get_inheritance("BasePeriUpdate", down, -1)` → 4 子类 + `get_overrides("TryPrepare", "BasePeriUpdate")` → 4 | 所有 4 子类都 override TryPrepare | ✅ TryPrepare 是所有子类共需的，加到基类合理；若只有 1 子类 override 则不该加 | 需理解设计原则(通用才放基类)，逐个虚函数检查 | ✅ |

## 4 Bug/修复验证类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-12 | "type_alias 功能实现了吗？" | `SELECT COUNT(*) FROM edge WHERE relation_type='type_alias'` | 9 条 | ✅ 9 > 0 = 已实现且工作 | "功能实现了吗"不是标准查询，需转为边计数检查 | ✅ |
| T3-13 | "using_decl 和 friend_of 为什么 0 条？是没实现还是没有？" | 源码 grep `using \w+::\w+` → 1处 + grep `friend` → 0处 | 源码仅 1 处 literal operator using，0 处 friend | ✅ 功能已实现，源码本就无此语法 | "0 条"有歧义：可能是实现 bug 也可能源码没有——需两步验证(图谱+源码) | ✅ |
| T3-14 | "graph_db SyntaxError 修复了没？" | type_alias 边数(同 T3-12) + ota_manager.cpp parse_status | 9 条边 + status=success | ✅ SyntaxError 阻断入库→修复后边恢复+解析成功 | 需理解因果链(SyntaxError→import失败→0边) | ✅ |
| T3-15 | "交叉编译 target 问题修了没？" | `SELECT status FROM parse_status WHERE source_file LIKE '%ota_manager.cpp%'` | success | ✅ 修复前 failed，修复后 success | 需知道具体文件和修复内容 | ✅ |

## 5 影响面分析类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-16 | "改 base_peri_update.h 会影响什么？" | `get_inheritance("BasePeriUpdate", down, -1)` + `include_dep` 查谁 include 了该头文件 | 4 子类 + 2 个 TU 直接 include | ✅ 4 个子类必须重编译，2 个 TU 直接依赖 | "影响什么"可选继承、调用、include 多个维度——需组合 | ✅ |
| T3-17 | "改 soc_update.h 要重解析几个 TU？" | `SELECT COUNT(DISTINCT source_file) FROM include_dep WHERE included_file LIKE '%soc_update.h%'` | 2 个 TU | ✅ 增量更新只需重解析 2 个 TU | 需选 include_dep 查询，不是 traverse_graph | ✅ |
| T3-18 | "PerformUpgrade 改了会影响哪些调用方？" | `get_overrides("PerformUpgrade", "BasePeriUpdate")` + `get_callers("PerformUpgrade")` | 4 个重写类 + 多个调用方 | ✅ 改基类虚函数=4 个子类 override 都受影响 | "改 PerformUpgrade" 有歧义：改基类(影响子类)还是改子类(影响调用方)？需追问 | ✅ |

## 6 代码查阅类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-19 | "ota_manager.cpp 里有什么？" | `get_file_symbols("ota_manager.cpp")` | 52 个函数 | ✅ 核心管理类全貌 | 无——文件名直接映射 | ✅ |
| T3-20 | "SmUpdateSessionClient 做什么？" | `get_file_symbols("sm_update_session_client")` | 16 个: SmUpdateSessionClient, IsAvailable, ResetMachine, ReactorHolder, ... | ✅ 状态机会话客户端，负责可用性检查和状态重置 | 类名→文件名的模糊匹配 | ✅ |
| T3-21 | "PerformUpgrade 的签名是什么？" | `search_function("PerformUpgrade", class_name="SocUpdate")` | void PerformUpgrade() override, soc_update.h:18 | ✅ | 无 | ✅ |
| T3-22 | "SocUpdate 在哪定义的？" | `search_class("SocUpdate")` | update::SocUpdate, soc_update.h:9-61 | ✅ | 无 | ✅ |

## 7 文档-代码融合类问题

| ID | L1 用户问题 | L2 正确工具调用 | L3 返回 | L4 是否回答了问题 | L1→L2 难点 | 判定 |
|----|-----------|---------------|--------|----------------|-----------|------|
| T3-23 | "BootChain 相关的设计文档？" | `search_docs("BootChain")` | 3 个文档切片,关联 BootChainChanged/SocUpdate/TryActivate | ✅ 同时看到设计文档和相关代码 | "设计文档"需映射到 search_docs 而非 search_class | ✅ |
| T3-24 | "升级流程的设计文档在哪？" | `search_docs("升级", tag="架构设计")` | 5 个结果,含 AB 分区/差分升级等 | ✅ 文档直接定位 | "升级流程"→keyword="升级" | ✅ |
| T3-25 | "OTA 架构文档在哪？" | `search_docs("OTA")` | 3 个: ARCHITECTURE.md, STATE_MANAGER_ANALYSIS.md, CR_1804 | ✅ | 无 | ✅ |

---

## L1→L2 翻译难点分析

从 22 条用例中提炼出 **5 类翻译难点**，是 1→2 层准确性的关键风险点：

| 难点类型 | 说明 | 风险 | 缓解措施 |
|---------|------|------|---------|
| **名字映射** | 用户说 "GetSocBootChain"，实际函数名 "getActiveBootChain"；说 "Rollback"，实际 "PerformRollback" | 高 | MCP instructions 加常见别名映射；AI 优先模糊搜索 |
| **工具选择** | "影响什么"可选 inheritance/callers/include/traverse；"实现了吗"需边计数不是搜索 | 中 | MCP instructions 加典型问题→工具映射 few-shot |
| **参数限定** | "谁调用了 FileExists"需 class_name 限定否则噪音；"SocUpdate 的 PerformUpgrade"需双参数 | 中 | 工具 description 强调 class_name 用法 |
| **多步组合** | "能不能加虚函数"需继承+override 两步；"影响面"需 include+inheritance 组合 | 中 | traverse_graph 作为万能组合入口 |
| **概念→代码** | "升级流程"→PerformUpgrade；"初始化"→Init；"kApplicationErrorMap"→IDL 常量(图谱外) | 高 | 概念词典+降级规则(图谱→grep) |

---

## 三层汇总

| 层 | 测试覆盖 | 结果 |
|----|---------|------|
| **L1→L2** (问题→工具调用) | 25 条真实问题 | 23 条翻译正确(✅), 1 条需降级 grep(⚠️), 1 条需追问消歧(✅) |
| **L2→L3** (工具调用→返回) | 已有 TEST_CASES.md 覆盖 | 61/62 通过(98.4%) |
| **L1→L3** (问题→答案) | 25 条端到端 | 24 条回答了问题(✅), 1 条部分回答(⚠️ IDL常量) |

**端到端准确率: 24/25 = 96.0%** (唯一未覆盖场景为 IDL 常量，属于图谱设计边界，需 grep 降级)
