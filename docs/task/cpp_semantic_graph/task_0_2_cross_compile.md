# 阶段 0-2：交叉编译 / sysroot 兼容性验证

## 目标

验证 libclang 在 ADC4.0 交叉编译环境下的解析可行性，确认 sysroot、目标平台头文件、编译器内置宏能否正确对齐。

## 现状问题

- ADC4.0 是 NVIDIA DRIVE AGX 交叉编译项目，目标平台为 aarch64
- libclang 解析时必须知道目标 triple、sysroot 路径、交叉编译工具链的 include 路径
- 如果本地 clang 版本与交叉编译工具链不匹配，内置宏和内置类型会对不上，导致解析失败或结果不准
- 原计划未考虑交叉编译问题，这是阶段 0 的第一道墙

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `docs/task/cpp_semantic_graph/cross_compile_verification.md` | 新建，记录验证结果与方案 |

## 设计方案

1. **收集交叉编译环境信息**：
   - 目标 triple（如 `aarch64-linux-gnu`）
   - sysroot 路径（NVIDIA SDK）
   - 交叉编译工具链版本（GCC 版本）
   - 本地 clang 版本

2. **验证 libclang 传参方式**：
   ```python
   import clang.cindex
   index = clang.cindex.Index.create()
   tu = index.parse("soc_update.cpp", args=[
       "-std=c++17",
       "--target=aarch64-linux-gnu",
       "--sysroot=/path/to/sysroot",
       "-isystem", "/path/to/cross/include",
       # ... 从 compile_commands.json 提取
   ])
   ```

3. **逐项验证**：
   - sysroot 路径是否存在且可访问
   - 目标平台头文件能否被找到（`ara/core/...`、`nvds/...` 等）
   - 编译器内置宏是否对齐（`__aarch64__`、`__GNUC__` 版本等）
   - 解析结果与 clangd 查询是否一致

4. **确定运行环境**：
   - 如果本地解析可行 → 本地运行
   - 如果本地 clang 版本不兼容 → 评估在构建服务器上运行解析
   - 如果都不行 → 评估替代方案（如使用构建服务器的 clangd）

## 验收标准

- [ ] libclang 能在交叉编译环境下成功解析核心文件（`soc_update.cpp`、`base_peri_update.h`）
- [ ] 解析结果与 clangd `get_type_hierarchy` 结果一致（如 BasePeriUpdate 的 4 个子类）
- [ ] 明确运行环境方案：本地 / 构建服务器 / 其他
- [ ] 输出验证报告，记录参数配置与问题清单

## 风险点

1. **本地 clang 版本与交叉编译工具链版本差异**：可能需要安装与工具链版本匹配的 clang
2. **sysroot 路径在本地不存在**：可能需要从构建服务器同步 sysroot
3. **NVIDIA SDK 专用头文件**：某些 SDK 头文件可能有特殊宏，libclang 可能无法正确处理

## 实现步骤

1. 收集交叉编译环境信息（triple、sysroot、工具链版本）
2. 配置 libclang 传参，尝试解析核心文件
3. 对比解析结果与 clangd 查询结果
4. 测试不同运行环境（本地 / 构建服务器）
5. 输出验证报告与运行环境方案

## 实际结果

- libclang-18 可直接解析，0 Error、0 Warning
- 无需 --sysroot 或 -target 参数，因为 -isystem 已指向 BSW SDK aarch64 头文件
- 交叉编译工具链为 GCC 13.2.0（aarch64-buildroot-linux-gnu）
- 本地 clang 版本为 clangd-18 / libclang1-18

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | libclang-18 直接解析 0 Error/0 Warning，-isystem 已覆盖 BSW SDK 头文件，无需额外 sysroot 配置 | 通过，进入阶段 0-3 |
