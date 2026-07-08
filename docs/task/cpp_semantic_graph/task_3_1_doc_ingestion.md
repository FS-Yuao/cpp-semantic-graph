# 阶段 3-1：MD 文档切片与入库

## 目标

实现 MD 文档按章节自动切片并入库为 `doc_section` 节点，为代码-文档双向关联奠定基础。

## 现状问题

- 项目 docs/ 目录下有大量设计文档、任务文档、架构说明，但与代码完全割裂
- AI 查代码时拿不到设计说明，查文档时定位不到代码实现
- 文档内容没有结构化入库，只能靠全文搜索，效率低

## 依赖

- 阶段 1-2：SQLite 图谱库已建好，支持 `doc_section` 节点类型
- 阶段 0-6：embedding 模型已选定

## 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `tools/cpp_semantic_graph/parser/__init__.py` | 修改，添加文档解析入口 |
| `tools/cpp_semantic_graph/parser/doc_parser.py` | 新建，MD 文档解析与切片 |
| `tools/cpp_semantic_graph/parser/doc_ingester.py` | 新建，文档节点入库 |
| `tools/cpp_semantic_graph/config/doc_config.yaml` | 新建，文档解析配置 |

## 设计方案

### 1. 文档切片规则

- 按**二级标题**（`##`）自动切片，粒度对齐代码模块 / 类
- 一级标题（`#`）作为文档元数据（文档标题）
- 三级及以下标题（`###`、`####`）不单独切片，归入所属二级标题切片
- 如果文档无二级标题，整个文档作为一个切片

### 2. 文档节点字段

```json
{
  "type": "doc_section",
  "name": "SocUpdate 升级流程设计",
  "namespace": null,
  "file_path": "docs/OTA_flow/soc_upgrade_design.md",
  "start_line": 15,
  "end_line": 67,
  "extra_info": {
    "doc_title": "OTA 升级流程设计",
    "section_level": 2,
    "heading": "SocUpdate 升级流程设计",
    "content_preview": "SocUpdate 继承 BasePeriUpdate，实现 SoC 固件升级...",
    "content_hash": "sha256:abc123...",
    "tags": ["架构设计", "SocUpdate"],
    "word_count": 350
  }
}
```

### 3. 文档解析配置

```yaml
# doc_config.yaml
doc_dirs:
  - "docs/"
  - "drive-vendor/ap/ap-aa/app/hq_ota_service/docs/"

exclude_patterns:
  - "*.html"
  - "*/build/*"

tag_rules:
  # 按目录自动打标签
  - path_pattern: "docs/OTA_flow/**"
    tags: ["架构设计", "OTA"]
  - path_pattern: "docs/task/**"
    tags: ["开发任务"]
  - path_pattern: "docs/Doip_Uds/**"
    tags: ["接口规约", "UDS"]

section_split:
  min_level: 2        # 按 ## 切片
  min_word_count: 20  # 少于 20 字的切片合并到上一节
```

### 4. 批量入库流程

```
1. 扫描 doc_dirs 下的所有 .md 文件
2. 对每个文件：
   a. 解析标题层级结构
   b. 按 ## 切片
   c. 生成 content_preview（前 200 字）
   d. 计算内容 hash（用于增量更新检测）
   e. 按目录规则自动打标签
3. 批量写入 node 表（type='doc_section'）
4. 输出统计：文档数 / 切片数 / 标签分布
```

## 验收标准

- [ ] 文档按二级标题自动切片，切片粒度合理（不会把整篇文档切成一个，也不会切得太碎）
- [ ] 文档节点字段完整：标题、所属文档、内容预览、标签
- [ ] 批量扫描脚本可自动遍历 docs/ 目录，全量切片入库
- [ ] 短切片（< 20 字）合并到上一节，不产生无意义切片
- [ ] 内容 hash 可用于增量更新检测（内容变化时重新入库）
- [ ] 标签按目录规则自动打标，覆盖主要文档目录

## 风险点

1. **切片粒度问题**：某些文档的二级标题下内容很少，会产生无意义切片；某些标题下内容很多，切片过大
2. **中文分词**：content_preview 和后续 embedding 需要处理中文分词
3. **文档格式不一致**：部分文档可能不是标准 Markdown（如混入 HTML 标签）

## 实施步骤

1. 编写 doc_parser.py，实现 Markdown 解析与切片
2. 编写 doc_config.yaml，定义文档目录和标签规则
3. 编写 doc_ingester.py，实现文档节点入库
4. 对项目 docs/ 目录做全量切片入库
5. 检查切片质量，调整切片参数

## 审查记录

| 日期 | 报告 | 结论 |
|------|------|------|
| | | |
