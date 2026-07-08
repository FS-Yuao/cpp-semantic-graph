# 阶段 0-1：编译环境验证与 compile_commands.json 生成

## 目标

验证目标项目可正常生成 `compile_commands.json`，为后续 libclang 语义解析提供编译数据库基础。

## 现状问题

- libclang 解析依赖 `compile_commands.json` 获取编译参数（sysroot、include 路径、宏定义等）
- ADC4.0 是交叉编译项目，编译参数可能分散在 CMake toolchain 文件中，需确认生成的 compile_commands.json 是否完整
- 未验证 compile_commands.json 的完整性就直接写解析器，后期可能因参数缺失导致大量解析失败

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `docs/task/cpp_semantic_graph/compile_commands_template.md` | 新建，记录配置模板与参数说明 |

## 设计方案

1. 确认项目构建系统类型（CMake / Makefile），选择生成方式：
   - CMake：`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`
   - Makefile：用 `bear` 或 `intercept-build` 拦截编译
2. 执行构建，生成 `compile_commands.json`
3. 检查生成的文件：
   - 每个编译单元是否都有条目
   - 每个条目的 `command` 字段是否包含完整编译参数（`-I`、`-D`、`--sysroot`、`-target` 等）
   - 交叉编译相关的 `-isystem`、`--sysroot` 路径是否存在且可访问
4. 用 `clang-check` 或 `libclang` 尝试解析一个核心文件，验证编译参数的完整性
5. 记录问题与解决方案，输出配置模板

## 验收标准

- [ ] `compile_commands.json` 成功生成，覆盖项目所有编译单元
- [ ] 每个条目包含完整编译参数（sysroot、include 路径、宏定义）
- [ ] 用 libclang 解析 `soc_update.cpp` 成功，无 "file not found" 类错误
- [ ] 输出配置模板文档，记录必要的编译参数

## 风险点

1. **交叉编译工具链路径**：`--sysroot` 指向的路径可能只在构建服务器上存在，本地开发环境需同步
2. **CMake toolchain 文件**：toolchain 文件中的编译参数可能不会被完整导出到 compile_commands.json
3. **生成代码的编译单元**：ARA COM 代码生成器的输出可能不在 compile_commands.json 中

## 实现步骤

1. 确认构建系统类型与构建流程
2. 配置并执行构建，生成 compile_commands.json
3. 检查完整性：编译单元数量 vs 源文件数量
4. 抽样验证：用 libclang 解析核心文件
5. 记录配置模板与问题清单

## 实际结果

- compile_commands.json 已存在于 `/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/compile_commands.json`
- 总条目 493 个，其中 hq_ota_service 相关 52 个（手写代码 25 个 + ARA COM 生成代码 27 个）
- 参数清洗规则已确认：合并 -isystem、移除 -o/-c/-W*/-pedantic、移除源文件路径
- 构建系统为 CMake + CMakePresets，preset 为 `gcc13_linux_aarch64`

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-23 | compile_commands.json 已存在且完整，493 条目含 52 个 hq_ota_service 相关条目 | 通过，进入阶段 0-2 |
