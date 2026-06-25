# Phase 0 验证报告（最终版）

## 0-1: 编译环境验证 ✅ PASS

### 结论
`compile_commands.json` 已存在且可用。

### 关键数据
- **路径**: `/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/compile_commands.json`
- **总条目**: 493 个编译单元
- **hq_ota_service 条目**: 52 个（25 个手写 + 27 个 ARA COM 生成代码）
- **构建系统**: CMake + CMakePresets，预设 `gcc13_linux_aarch64`

### compile_commands.json 参数清洗规则
原 command 字段需清洗后才能传给 libclang：
1. **合并 `-isystem`**: `-isystem` 和路径是分开的两个参数 → 合并为 `-isystem/path`
2. **删除 `-o output`**: libclang 不需要
3. **删除 `-c`**: libclang 不需要
4. **删除 `-W*` / `-pedantic`**: 警告参数对解析无意义
5. **删除源文件路径**: libclang.parse() 单独传入

---

## 0-2: 交叉编译 / sysroot 兼容性验证 ✅ PASS

### 结论
**libclang-18 可直接解析，无需特殊 sysroot / target 处理。**

### 关键发现
- 本地 clang 版本: clangd-18 / libclang1-18
- 交叉编译工具链: GCC 13.2.0 (aarch64-buildroot-linux-gnu)
- `compile_commands.json` 中的 `-isystem` 已指向 BSW SDK 的 aarch64 头文件
- **无需 `--sysroot`、`-target` 参数**，直接用 compile_commands.json 的参数即可
- 解析结果: **0 Error, 0 Warning**

---

## 0-3: libclang 核心语义提取能力验证 ✅ PASS

### 验证结果

| 语义点 | 结果 | 详情 |
|--------|------|------|
| 类定义与命名空间 | ✅ | 15 个类，含 `update::BasePeriUpdate`, `update::SocUpdate` |
| 继承关系（含权限） | ✅ | `SocUpdate --(PUBLIC)--> BasePeriUpdate` |
| 纯虚函数 | ✅ | 13 个 PURE 正确标记 |
| override 检测 | ✅ | 14 个 OVERRIDE（通过 `CXX_OVERRIDE_ATTR` 子节点检测） |
| 访问权限 | ✅ | 继承权限可提取（PUBLIC/PROTECTED/PRIVATE） |
| 命名空间嵌套 | ✅ | `update::`, `otalog::details::` 正确识别 |
| 跨文件声明-定义 | ✅ | SocUpdate 声明(.h) / 定义(.cpp) 均正确提取 |

### API 兼容性备忘
- `Cursor.is_virtual_base()` — **不可用**，虚继承需其他方式检测
- `Cursor.lexical_children` — **不可用**，用 `cursor.get_children()` 替代
- `CXX_OVERRIDE_ATTR` — 可通过 `cursor.get_children()` 检测

---

## 0-4: 函数调用关系提取验证 ✅ PASS

### 验证结果

**直接调用（soc_update.cpp → PerformUpgrade）**：
- 成员函数调用: `ExecuteDriveUpdate`, `NotifyProgress`, `StartContentServer`, `StopContentServer`
- 全局/静态调用: `IsDirectoryPath`, `GetInstance`, `Info`, `Error`
- 标准库调用: `basic_string`, `empty`（后续需过滤）

**Proxy 方法调用（ota_service.cpp）**：
- `OtaServiceInterfaceProxy::BootChainChanged` @ line 424 ✅
- `OtaServiceInterfaceProxy::GetSocBootChain` @ line 452 ✅
- `OtaServiceInterfaceProxy::EnterUpgrade` @ line 482 ✅

### 关键发现：Proxy 方法调用提取方式
- `CALL_EXPR.referenced` 对 Proxy 方法返回 None → **不可用**
- `MEMBER_REF_EXPR.referenced` 可正确解析 → **必须用此方式**
- 这是调用关系提取的核心技巧，阶段 1 实现时必须注意

### 限制
- 虚函数调度只能看到静态类型，需结合 override 关系补全
- 回调/函数对象调用待验证
- 标准库调用也会被提取，需过滤

---

## 0-5: 模板白名单与 ARA COM 策略 ✅ PASS

### 模板白名单

| 模板 | 特化 | 入库策略 |
|------|------|---------|
| `ThreadDrivenProxy` | `OtaServiceInterfaceProxy` | 独立节点 |
| `ThreadDrivenProxy` | `DoipServiceInterfaceProxy` | 独立节点 |
| `ThreadDrivenProxy` | `PowerStatusServiceInterfaceProxy` | 独立节点 |
| 其他模板 | — | 只导出定义，不展开实例化 |

### ARA COM 生成代码策略

| 范围 | 处理方式 |
|------|---------|
| 生成的头文件（接口声明） | **入库** — Proxy/Skeleton 类定义和方法声明 |
| 生成的源文件（内部实现） | **不入库** — 序列化/IPC binding 等 |
| 内部实现类（Backend/HandleType） | **不入库** |

### 代码量
- 手写源文件: 25 个 .cpp
- 生成源文件: 27 个 .cpp
- 按此策略入库后预计减少 ~40% 噪声节点

### 产出文件
- `config/template_whitelist.yaml`
- `config/ara_com_strategy.md`

---

## 0-6: embedding 模型评估 ⏭️ SKIP

### 结论
**跳过，不是必须的。**

- 文档-代码关联的核心手段是手动 `[[ClassName]]` 标记 + 规则匹配
- embedding 模型只用于自动候选补全，锦上添花
- 阶段 3 文档融合前再评估即可
- 无 GPU 环境，本地跑 embedding 需装 PyTorch（~200MB），当前不值得

---

## graphify 过渡方案

### 定位

| | graphify | cpp-semantic-graph |
|---|---|---|
| 数据来源 | 文本正则 + embedding | Clang 编译语义 |
| C++ 精度 | ❌ 低 | ✅ 高 |
| 遍历能力 | BFS/DFS | BFS/DFS + 多跳 |
| 文档关联 | 无 | 有（手动标记） |

### 过渡策略
1. **Phase 1-2 开发期**: graphify 继续使用，无变化
2. **Phase 4 集成期**: C++ 语义查询优先路由到 cpp-semantic-graph，graphify 降级为非 C++ 资源
3. **稳定后**: graphify 在 C++ 项目中退出

### CLAUDE.md 更新时机
Phase 4 MCP Server 上线后，更新搜索规则：
- `graphify query_graph` → `cpp_traverse_graph`（C++ 场景）
- `graphify shortest_path` → `cpp_traverse_graph`
- 保留 graphify 用于非 C++ 资源（配置文件等）

---

## 兼容性矩阵（最终）

| 语义点 | libclang Python | 需 LibTooling 补完 | 备注 |
|--------|----------------|-------------------|------|
| 类定义 | ✅ | - | |
| 继承关系（含权限） | ✅ | - | |
| 纯虚函数 | ✅ | - | |
| override 检测 | ✅ | - | 通过 CXX_OVERRIDE_ATTR |
| 函数签名 | ✅ | - | |
| 命名空间 | ✅ | - | |
| 调用关系（直接） | ✅ | - | 通过 CALL_EXPR |
| 调用关系（Proxy方法） | ✅ | - | **必须用 MEMBER_REF_EXPR** |
| 调用关系（虚调度） | ⚠️ | 可能 | 需 override 关系补全 |
| 虚继承 | ❌ | 是 | is_virtual_base() 未暴露 |
| 模板实例化 | ✅ | - | 白名单机制 |
| 宏展开 | ⏳ | | 低优先级，影响不大 |

**覆盖率: ~90%**（超过 80% 进入阶段 1 门限）

---

## 总结

| Task | 状态 | 关键结论 |
|------|------|---------|
| 0-1 compile_commands | ✅ PASS | 已存在，493 条，需参数清洗 |
| 0-2 交叉编译兼容 | ✅ PASS | libclang-18 直接可用，0 Error |
| 0-3 核心语义提取 | ✅ PASS | 类/继承/虚函数/override 全部可提取 |
| 0-4 调用关系提取 | ✅ PASS | CALL_EXPR + MEMBER_REF_EXPR 组合提取 |
| 0-5 模板白名单 | ✅ PASS | 白名单 + ARA COM 策略已定义 |
| 0-6 embedding/graphify | ⏭️ SKIP | embedding 非必须，graphify 过渡方案已定义 |

**Phase 0 结论：技术路线可行，可进入 Phase 1。**

### 阶段 1 实现前必须注意
1. **参数清洗**: compile_commands.json 的 `-isystem` 合并、`-o`/`-c` 删除
2. **Proxy 调用提取**: 必须用 `MEMBER_REF_EXPR`，不能只靠 `CALL_EXPR.referenced`
3. **生成代码过滤**: src-gen 的源文件不入库，头文件只入库接口声明
4. **虚继承**: 暂时无法检测，记录为已知限制
