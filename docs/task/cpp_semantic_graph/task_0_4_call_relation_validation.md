# 阶段 0-4：函数调用关系提取专项验证

## 目标

专项验证 libclang 对函数调用关系（CALL_EXPR）的提取能力，确认能否满足"查谁调用了这个函数"的核心需求。如果不合格，明确方案 B。

## 现状问题

- 函数调用关系是本工具替代 grep 的核心能力之一
- libclang 对 CALL_EXPR 的遍历能力是关键依赖，但存在已知限制：
  - 通过指针/引用的多态调用（虚函数调度）只能看到静态类型，无法确定实际调用目标
  - 回调 / 函数对象的调用难以静态分析
  - 模板中的调用可能展开后才能看到
- 如果验证不通过，需要在阶段 1 之前确定替代方案

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/scripts/validate_calls.py` | 新建，调用关系验证脚本 |
| `docs/task/cpp_semantic_graph/call_validation_report.md` | 新建，验证报告 |

## 设计方案

### 验证场景与预期

| 场景 | 代码示例 | 预期能否提取 | 对比方式 |
|------|---------|------------|---------|
| 直接成员调用 | `obj.doUpdate()` | ✅ 能 | clangd `get_callees` |
| 指针调用 | `ptr->doUpdate()` | ⚠️ 看到静态类型 | clangd `get_callees` |
| 虚函数调度 | `basePtr->virtualFunc()` | ⚠️ 看到基类声明 | 需结合 override 关系补全 |
| 全局函数调用 | `RunCommand(...)` | ✅ 能 | clangd `get_callees` |
| 回调/函数对象 | `callback_()` | ❌ 难 | 不作为核心验收项 |
| 模板中的调用 | `proxy->Method()` | ⚠️ 依赖模板展开 | 阶段 0-6 模板白名单后验证 |
| 构造/析构调用 | `MyClass obj;` | ✅ 能 | clangd `get_callees` |

### 验证方法

1. 选取核心模块（soc_update.cpp），手动标注所有函数调用点
2. 用 libclang 遍历 CALL_EXPR，提取调用关系
3. 与 clangd `get_callees` / `get_callers` 结果对比
4. 统计精确率和召回率，按场景分类

### 方案 B（如果 libclang 不达标）

| 方案 | 可行性 | 工作量 | 风险 |
|------|--------|--------|------|
| LibTooling C++ AST visitor | 高 | 2~3 周 | C++ 开发，与 Python 栈不统一 |
| clang -Xclang -ast-dump + 后处理 | 中 | 1 周 | 输出格式不稳定，解析复杂 |
| clang Static Analyzer | 低 | 4+ 周 | 过重，不适合本项目 |
| 混合方案：libclang 提取直接调用 + override 关系补全虚调用 | 高 | 1~2 周 | 需虚函数体系已提取 |

**推荐**：混合方案 — libclang 提取直接调用关系，虚函数调度通过 "override 关系 + 基类调用点" 推导补全。

## 验收标准

- [ ] 直接成员调用、全局函数调用提取准确率 ≥ 95%（对比 clangd）
- [ ] 虚函数调度场景有明确结论：能否通过 override 关系补全
- [ ] 回调/函数对象场景有明确结论：是否纳入范围
- [ ] 整体调用关系覆盖率 ≥ 85%（允许回调/模板部分缺失）
- [ ] 如果不达标，方案 B 已验证可行或有明确推进计划

## 风险点

1. **虚函数调度**：这是 C++ 调用分析的核心难点，静态分析不可能 100% 解决，需明确可接受的精度范围
2. **回调与函数对象**：本项目使用 ARA COM 的回调机制，可能大量使用 `std::function` / 函数对象，静态分析天然受限
3. **模板展开**：ARA COM Proxy 的方法调用是模板实例化后的调用，需模板白名单机制配合

## 实现步骤

1. 在 soc_update.cpp 中手动标注所有函数调用点（ground truth）
2. 编写验证脚本，用 libclang 提取调用关系
3. 逐场景对比，统计精确率和召回率
4. 测试虚函数调度补全方案（override 关系推导）
5. 输出验证报告，给出 go/no-go 建议
6. 如果不达标，启动方案 B 验证

## 实际结果

- CALL_EXPR 可提取直接调用，但 Proxy 方法的 `referenced` 返回 None
- MEMBER_REF_EXPR.referenced 可正确解析 Proxy 方法调用（BootChainChanged/GetSocBootChain/EnterUpgrade）
- MEMBER_REF_EXPR 的 semantic_parent 链可能为空，需行号范围回退方案定位所在函数
- 虚函数调度需结合 override 关系补全
- 回调/函数对象调用未验证（低优先级，不阻塞）

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | 直接调用可提取，Proxy 方法需用 MEMBER_REF_EXPR 替代 CALL_EXPR，虚函数需 override 补全，回调暂不覆盖 | 通过，采用混合方案进入阶段 0-5 |
