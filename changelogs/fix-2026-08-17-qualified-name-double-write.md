# fix-2026-08-17-qualified-name-double-write

## 问题（P2）

MCP 查询结果中 qualified name 类名双写：

- `cpp_get_callers("ExecuteDriveUpdate")` 返回 `update::FirmwareUpdate::FirmwareUpdate::PerformUpdate`
- `cpp_get_overrides` 同样输出 `update::FirmwareUpdate::FirmwareUpdate::TryPrepare`

## 根因

成员函数节点的 `namespace` 字段已含所属类（形如 `update::FirmwareUpdate`），而格式化层
又拼接了 `parent_class`（`FirmwareUpdate`），导致类名出现两次。数据层（DB）本身正确，
纯显示层 bug。

- `_fmt_call_info`：`ns + cls + name` 双分支（caller/callee）均中招
- `_fmt_override`：`ns + class_name + function_name` 同源问题

## 修复

`mcp_server/server.py` 新增 `_qualified(namespace, class_name, name)` 辅助函数：
当 `namespace` 已以 `::{class_name}` 结尾时不再重复拼接类名；否则按
`namespace::Class::name` 正常拼接。`_fmt_call_info` / `_fmt_override` 改用该函数。

## 验证

1. `tests/full_test.py` 全量通过：功能冒烟 11/11（4331 节点 / 17777 边）、
   准确性结果集正常生成、效率测试正常。
2. 端到端（kill MCP 进程重启加载新代码后实测）：
   - `cpp_get_callers("ExecuteDriveUpdate")` → `update::FirmwareUpdate::PerformUpdate` ✅
   - `cpp_get_overrides("TryPrepare", "DeviceAdapter")` → 4 个重写，
     均为 `update::XxxUpdate::TryPrepare`，无双写 ✅
