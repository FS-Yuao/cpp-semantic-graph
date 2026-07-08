# 阶段 4-1：增量解析更新（基于 include 依赖图）

## 目标

实现基于 include 依赖图的增量更新机制：文件变更时自动确定影响范围，仅重新解析受影响的翻译单元，无需全量重跑。

## 现状问题

- 当前每次代码修改都需要全量重解析，耗时数分钟
- 日常开发中代码频繁修改，全量重解析不现实
- 改一个头文件可能影响数十个翻译单元，必须基于 include 依赖图确定影响范围
- 增量更新是新工具从"一次性工具"变成"日常基础设施"的关键

## 依赖

- 阶段 1-2：include_dep 表已建好
- 阶段 1-5：include 依赖查询接口已实现
- 阶段 1-1：AST visitor 已实现

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/updater/__init__.py` | 新建，增量更新包 |
| `tools/cpp_semantic_graph/updater/change_detector.py` | 新建，文件变更检测 |
| `tools/cpp_semantic_graph/updater/impact_analyzer.py` | 新建，影响范围分析 |
| `tools/cpp_semantic_graph/updater/incremental_updater.py` | 新建，增量更新主逻辑 |

## 设计方案

### 1. 文件变更检测

```python
class ChangeDetector:
    def detect_changes(self, since: str = None) -> FileChangeSet:
        """检测项目文件变更
        方式1（推荐）：Git diff
          - since 参数指定基准 commit/branch
          - 通过 git diff --name-only 获取变更文件列表
        方式2：文件系统监听
          - 使用 watchdog 库监听文件变更事件
          - 适合实时监听场景
        返回：
          - changed_files: 变更文件列表
          - new_files: 新增文件列表
          - deleted_files: 删除文件列表
        """
```

### 2. 影响范围分析（🔴 关键）

```python
class ImpactAnalyzer:
    def analyze_impact(self, changed_files: list[str]) -> ImpactReport:
        """分析变更文件的影响范围
        - .cpp 文件修改：仅影响该翻译单元
        - .h 文件修改：查询 include_dep 表，找到所有直接和间接依赖的翻译单元
        - 新增文件：需加入 compile_commands.json 后解析
        - 删除文件：删除对应节点和关联边
        返回：
          - affected_translation_units: 需重新解析的翻译单元列表
          - impact_chain: 影响链路（哪个头文件 → 哪些翻译单元）
        """

    def _get_all_includers(self, header_path: str) -> list[str]:
        """递归查询所有直接和间接 include 该头文件的翻译单元
        利用 include_dep 表的递归查询
        """
```

### 3. 增量更新流程

```
1. 检测文件变更（ChangeDetector）
2. 分析影响范围（ImpactAnalyzer）
3. 对受影响的翻译单元：
   a. 删除旧数据：
      - 查询该翻译单元对应的所有节点（按 file_path 和 extra_info.translation_units 过滤）
      - 删除仅属于该翻译单元的节点
      - 删除相关的边
      - 更新属于多个翻译单元的节点（移除该翻译单元的记录）
   b. 重新解析：
      - 用 AST visitor 重新解析受影响的翻译单元
      - 插入新节点和边
4. 更新 include_dep 表（如有新增/删除的 include 关系）
5. 输出更新统计
```

### 4. Git 集成

```python
def incremental_update_from_git(self, base_ref: str = "HEAD~1"):
    """基于 Git 提交触发增量更新
    - base_ref: 基准 commit（默认上一个提交）
    - 自动检测 commit 间的文件变更
    - 适合 CI/CD 流水线集成
    """

def incremental_update_from_diff(self, diff_output: str):
    """基于 git diff 输出触发增量更新
    - 适合手动触发
    """
```

## 验收标准

- [ ] 单个 `.cpp` 文件修改后，图谱更新耗时 < 1s
- [ ] 单个 `.h` 头文件修改后，图谱更新耗时 < 5s（含所有受影响翻译单元的重解析）
- [ ] 头文件变更的影响范围正确：所有直接和间接 include 该头文件的翻译单元都被重解析
- [ ] 增量更新后数据一致性正确：删除旧数据、插入新数据，无残留无遗漏
- [ ] 支持两种触发方式：Git diff 和文件系统监听
- [ ] 新增文件和删除文件都能正确处理

## 风险点

1. **include 依赖图的完整性**：如果阶段 1 提取的 include 关系不完整，增量更新会遗漏受影响的翻译单元
2. **节点归属多个翻译单元**：同一类定义在头文件中，被多个 .cpp include，删除旧数据时不能误删其他翻译单元还在用的节点
3. **并发修改**：多人同时修改代码时，增量更新的顺序可能影响结果

## 实施步骤

1. 编写 change_detector.py，实现文件变更检测（Git diff + 文件系统监听）
2. 编写 impact_analyzer.py，实现影响范围分析（基于 include_dep 表）
3. 编写 incremental_updater.py，实现增量更新主逻辑
4. 测试单文件修改的增量更新（.cpp 和 .h 分别测试）
5. 测试多文件修改的增量更新
6. 性能测试，确认耗时达标

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| 2026-06-24 | 增量更新实现完成 | 3 个新模块 + 4 个 graph_db 删除方法 + CLI 子命令；.cpp 更新 7.5s（16×加速），.h 递归 7 个 TU，节点零丢失，幂等性验证通过 |
