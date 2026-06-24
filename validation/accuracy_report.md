# 准确性验证报告（clangd Ground Truth 对比）

**数据库**: `<PROJECT_SRC>/_tools/cpp_semantic_graph/semantic_graph.db`

**对比基准**: clangd MCP 采集，固化于 `clangd_baseline.json`


## 1. 汇总

| 维度 | TP | FP | FN | Precision | Recall | 门限(P/R) | 结果 |
|------|----|----|----|-----------|--------|-----------|------|
| 类定义 | 2 | 0 | 0 | 100.0% | 100.0% | 98%/95% | ✅ 达标 |
| 继承关系 | 5 | 0 | 0 | 100.0% | 100.0% | 95%/90% | ✅ 达标 |
| 函数签名 | 6 | 0 | 0 | 100.0% | 100.0% | 95%/90% | ✅ 达标 |
| 调用关系 | 2 | 0 | 0 | 100.0% | 100.0% | 85%/80% | ✅ 达标 |

**总体结论**: **全部达标，可进入下一阶段**

## 2. 逐维度对比详情

### 类定义

- Precision: 100.0% (门限 98%)
- Recall: 100.0% (门限 95%)

| 样本 | 期望 | 图谱返回 | TP | FP | FN | 备注 |
|------|------|---------|----|----|----|------|
| class:DeviceAdapter | DeviceAdapter | DeviceAdapter | DeviceAdapter | ∅ | ∅ |  abstract=True |
| class:FirmwareUpdate | FirmwareUpdate | FirmwareUpdate | FirmwareUpdate | ∅ | ∅ |  abstract=False |

### 继承关系

- Precision: 100.0% (门限 95%)
- Recall: 100.0% (门限 90%)

| 样本 | 期望 | 图谱返回 | TP | FP | FN | 备注 |
|------|------|---------|----|----|----|------|
| inherit_down:DeviceAdapter | SensorUpdate, McuUpdate, FirmwareUpdate, SwitchUpdate | SensorUpdate, McuUpdate, FirmwareUpdate, SwitchUpdate | SensorUpdate, McuUpdate, FirmwareUpdate, SwitchUpdate | ∅ | ∅ |  |
| inherit_up:FirmwareUpdate | DeviceAdapter | DeviceAdapter | DeviceAdapter | ∅ | ∅ |  |

### 函数签名

- Precision: 100.0% (门限 95%)
- Recall: 100.0% (门限 90%)

| 样本 | 期望 | 图谱返回 | TP | FP | FN | 备注 |
|------|------|---------|----|----|----|------|
| func:DeviceAdapter::PerformUpdate | bool PerformUpdate() | bool PerformUpdate() | bool PerformUpdate() | ∅ | ∅ | 属性一致 |
| func:FirmwareUpdate::PerformUpdate | bool PerformUpdate() | bool PerformUpdate() | bool PerformUpdate() | ∅ | ∅ | 属性一致 |
| func:SensorUpdate::PerformUpdate | bool PerformUpdate() | bool PerformUpdate() | bool PerformUpdate() | ∅ | ∅ | 属性一致 |
| func:SwitchUpdate::PerformUpdate | bool PerformUpdate() | bool PerformUpdate() | bool PerformUpdate() | ∅ | ∅ | 属性一致 |
| func:McuUpdate::PerformUpdate | bool PerformUpdate() | bool PerformUpdate() | bool PerformUpdate() | ∅ | ∅ | 属性一致 |
| func:FirmwareUpdate::GetPeriName | string GetPeriName() | string GetPeriName() | string GetPeriName() | ∅ | ∅ | 属性一致 |

### 调用关系

- Precision: 100.0% (门限 85%)
- Recall: 100.0% (门限 80%)

| 样本 | 期望 | 图谱返回 | TP | FP | FN | 备注 |
|------|------|---------|----|----|----|------|
| call:DeviceAdapter::PerformUpdate | device_manager/device_adapter.cpp | device_manager/device_adapter.cpp | device_manager/device_adapter.cpp | ∅ | ∅ | 对比调用方文件集合（find_references 的 call 引用） |
| call:FirmwareUpdate::ExecuteDriveUpdate | soc/chip_update.cpp | soc/chip_update.cpp | soc/chip_update.cpp | ∅ | ∅ | 对比调用方文件集合（find_references 的 call 引用） |


## 3. 不达标维度分析

全部维度达标，无需修复。
