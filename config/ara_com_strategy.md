# ARA COM 生成代码处理策略

## 1. 生成代码位置

| 路径 | 内容 |
|------|------|
| `ap-model/integration/src-gen/hq_ota_service/include/` | 生成的头文件（Proxy/Skeleton/类型定义） |
| `ap-model/integration/src-gen/hq_ota_service/src/` | 生成的源文件（Proxy/Skeleton 实现、序列化/反序列化） |

## 2. 入库策略

| 范围 | 处理方式 | 理由 |
|------|---------|------|
| 生成的头文件（接口声明） | **入库** | 业务代码调用 Proxy 方法（BootChainChanged/GetSocBootChain/EnterUpgrade），需要这些接口定义 |
| 生成的源文件（内部实现） | **不入库** | 内部实现与业务逻辑无关，且每次重新生成后节点 ID 不稳定 |
| `.arxml` 服务描述文件 | **不入库**（阶段 3 文档融合后可入库为 doc 节点） | 非代码实体 |

## 3. 头文件入库范围

只入库以下类型的符号：
- Proxy 类定义及其方法声明（如 `OtaServiceInterfaceProxy::BootChainChanged`）
- Skeleton 类定义及其方法声明
- Service Interface 类定义
- 类型定义（`_types.h` 中的 struct/enum）

不入库：
- 内部实现类（`*BackendInterface`, `*HandleType` 等）
- 序列化/反序列化代码
- IPC binding 内部代码

## 4. 生成代码文件过滤规则

```yaml
# 入库的头文件模式
include_patterns:
  - "**/include/otaservice/*.h"
  - "**/include/ara/doiphandle/*.h"
  - "**/include/service/*.h"

# 不入库的源文件模式
exclude_patterns:
  - "**/src/amsr/ipc_binding/**"
  - "**/src/amsr/someip_protocol/**"
  - "**/src/amsr/com/ara/**"
  - "**/src/amsr/com/otaservice/**"
  - "**/src/ara/core/**"
  - "**/src/amsr/socal/internal/**"
```

## 5. libclang 验证结果

- ✅ 生成代码的头文件可被 libclang 正确解析（0 Error）
- ✅ Proxy 类识别成功：`OtaServiceInterfaceProxy`, `DoipServiceInterfaceProxy`
- ✅ Proxy 方法调用可通过 `MEMBER_REF_EXPR` 提取
- ⚠️ 生成代码的 .cpp 文件 compile_commands.json 中有完整编译参数，可解析但不入库

## 6. 代码量统计

| 类别 | 文件数 |
|------|--------|
| 手写源文件 | 25 个 .cpp |
| 生成源文件 | 27 个 .cpp |
| 生成头文件 | ~20 个 .h |

生成代码占总编译单元的 ~52%，按此策略入库后预计减少约 40% 的噪声节点。
