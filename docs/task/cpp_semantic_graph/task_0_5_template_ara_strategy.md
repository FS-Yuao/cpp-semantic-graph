# 阶段 0-5：模板白名单与 ARA COM 生成代码策略

## 目标

定义模板特化的入库白名单机制，确定 ARA COM 生成代码的处理策略，确保核心业务实体不丢失、生成代码不引入噪声。

## 现状问题

- 项目大量使用模板（ARA COM Proxy/Skeleton、`ara::com` 模板），默认只导出模板定义会丢失最关键的调用关系
- 全量展开模板实例化会导致图谱体积爆炸（同一模板在多个翻译单元实例化）
- ARA COM 代码生成器的输出在 build 目录，不入库丢失调用关系，全量入库引入噪声且节点 ID 不稳定
- 原计划只在避坑提示中提了一句"初期只导出显式实例化"，没有可执行的策略

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/config/template_whitelist.yaml` | 新建，模板白名单配置 |
| `docs/task/cpp_semantic_graph/ara_com_strategy.md` | 新建，ARA COM 策略文档 |

## 设计方案

### 1. 模板白名单机制

配置文件格式（YAML）：

```yaml
# 模板白名单：指定哪些模板特化必须导出为独立节点
template_whitelist:
  # ARA COM Proxy/Skeleton — 核心业务实体
  - template: "ara::com::proxy::Proxy"
    specializations:
      - "OtaServiceProxy"
      - "VehicleServiceProxy"
    action: "instantiate_as_node"  # 导出为独立节点

  - template: "ara::com::service::Skeleton"
    specializations:
      - "OtaServiceSkeleton"
    action: "instantiate_as_node"

  # 其他模板 — 只导出定义，不展开实例化
default_action: "definition_only"
```

白名单条目的筛选标准：
- 项目代码中有 `using` / `typedef` 别名的模板特化（如 `using OtaServiceProxy = Proxy<...>`）
- 在业务逻辑中被直接调用的模板方法（如 `proxy->Method()`）
- 被多个模块共用的模板特化

### 2. ARA COM 生成代码策略

| 范围 | 处理方式 | 理由 |
|------|---------|------|
| 生成的头文件（接口声明） | **入库** — 提取接口签名（方法名、参数、返回类型） | 业务代码调用 Proxy 方法，需要这些接口定义 |
| 生成的源文件（内部实现） | **不入库** | 内部实现与业务逻辑无关，且每次重新生成后节点 ID 不稳定 |
| `.arxml` 服务描述文件 | **不入库**（阶段 3 文档融合后可入库为 doc 节点） | 非代码实体，作为文档处理更合适 |

### 3. 模板实例化去重策略

同一模板特化出现在多个翻译单元时：
- 按 `template_name + template_args` 生成唯一 key
- 首次遇到时创建节点，后续遇到时跳过（但记录出现在哪些翻译单元中）
- 翻译单元信息存入 `extra_info`，支持增量更新时按翻译单元清理

## 验收标准

- [ ] 模板白名单配置文件已创建，覆盖项目中核心模板特化（ARA COM Proxy/Skeleton）
- [ ] 白名单中的模板特化能被 libclang 正确识别并提取
- [ ] ARA COM 生成代码策略已定义：头文件接口签名入库、源文件内部实现不入库
- [ ] 模板实例化去重逻辑已定义，同一特化不会重复创建节点
- [ ] 盘点完成：列出项目中所有高频模板特化，确认白名单覆盖

## 风险点

1. **ARA COM 生成的头文件编译参数**：生成代码的编译参数可能不在 compile_commands.json 中，libclang 可能无法正确解析
2. **模板白名单维护成本**：新增模板特化时需手动更新白名单，需设计检测机制提醒
3. **模板特化的提取精度**：libclang 对模板实例化的识别能力需阶段 0-3 验证确认

## 实施步骤

1. 盘点项目中高频使用的模板特化（grep `using.*=.*Proxy` / `using.*=.*Skeleton`）
2. 编写模板白名单配置文件
3. 定义 ARA COM 生成代码的入库策略文档
4. 用 libclang 验证白名单中的模板特化能被正确提取
5. 定义去重策略，记录在配置文件中

## 实际结果

- 模板白名单已创建：OtaServiceInterfaceProxy、DoipServiceInterfaceProxy、PowerStatusServiceInterfaceProxy
- ARA COM 策略：仅入库生成的头文件（接口声明），不入库生成的源文件
- 生成代码统计：27 个 .cpp 文件（占总条目 52%），过滤后减少约 40% 噪声
- 配置文件已创建：`config/template_whitelist.yaml`、`config/ara_com_strategy.md`

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | 白名单与 ARA COM 策略已定义，27 个生成 .cpp 过滤后减少 40% 噪声 | 通过，进入阶段 0-6 |
