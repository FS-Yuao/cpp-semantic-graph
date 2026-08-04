#!/usr/bin/env python3
"""
Task 8: 旧文档迁移 — 自动批量补 YAML frontmatter

按 02_doc_rules.md 规则，给 legacy 文档加 frontmatter：
  - doc_id: 从路径推断
  - type: 从目录推断
  - date: 从文件名/内容/文件时间推断
  - status: task/review 留空待人工填
  - tags: 从标题关键词推断

迁移策略：
  Batch 1: diary/        → type=diary
  Batch 2: task/         → type=task
  Batch 3: review_reports/ → type=review
  Batch 4: 其余目录      → type=design/link/report/doc
  Batch 5: origin_requier/ → 跳过 (P3)

Usage:
  python3 migrate_legacy.py --dry-run    # 预览不写
  python3 migrate_legacy.py              # 执行迁移
  python3 migrate_legacy.py --batch diary # 只迁移 diary
"""

from __future__ import annotations
import os, re, sys, argparse, datetime
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent  # docs/

# ─── 类型推断 ─────────────────────────────────────────

TYPE_MAP = {
    "diary": "diary",
    "task": "task",
    "review_reports": "review",
    "AB_Switch": "design",
    "OTA_flow": "link",
    "Doip_Uds": "link",
    "du_mcc": "link",
    "nvidia_switch_upgrade": "link",
    "ADC4.0_System_architecture": "design",
    "Completion status": "report",
    "Claude_Code_Graph": "report",
    "Claude_harness": "report",
    "origin_requier": "requirement",  # P3 跳过
}

# P3 跳过目录
SKIP_DIRS = {"origin_requier"}


def infer_doc_type(rel_path: str) -> str:
    """从相对路径推断文档类型"""
    parts = rel_path.split("/")
    for part in parts:
        if part in TYPE_MAP:
            return TYPE_MAP[part]
    # task/doc_graph/ 下的也算 task
    if "task/doc_graph" in rel_path:
        return "task"
    return "doc"


def infer_date(filepath: Path, rel_path: str, content: str) -> str:
    """从文件名/内容/文件时间推断日期"""
    # 1. 文件名中的日期 (diary/2026-07-13.md, review_reports/2026-06-01_xxx.md)
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", filepath.name)
    if m:
        return m.group(1)
    # 2. 正文中的日期
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", content[:500])
    if m:
        return m.group(1)
    # 3. 文件修改时间
    try:
        ts = os.path.getmtime(filepath)
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except:
        return "2026-07-01"


def infer_doc_id(rel_path: str, doc_type: str) -> str:
    """从路径生成 doc_id (kebab-case)"""
    # 去掉 .md
    p = rel_path.replace("\\", "/").replace(".md", "")
    # 替换 / 为 -
    p = p.replace("/", "-")
    # 替换空格和特殊字符
    p = re.sub(r"\s+", "-", p)
    p = re.sub(r"[^\w\-]", "", p)
    return p


def extract_title(content: str) -> str:
    """提取文档标题（第一个 # 标题）"""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    # fallback: 用文件名
    return ""


def infer_tags(title: str, doc_type: str, rel_path: str) -> list[str]:
    """从标题和路径推断标签"""
    tags = set()
    # 从标题提取关键词
    keyword_map = {
        "分区": "A/B分区",
        "切换": "分区切换",
        "升级": "升级",
        "回滚": "回滚",
        "MCU": "MCU",
        "SOC": "SOC",
        "Switch": "Switch",
        "GNSS": "GNSS",
        "OTA": "OTA",
        "覆盖率": "覆盖率",
        "审查": "审查",
        "并行": "并行升级",
        "持久化": "持久化",
        "状态机": "状态机",
        "签名": "验签",
        "差分": "差分升级",
        "Bootloader": "Bootloader",
        "MCC": "MCC",
        "DU": "DU Link",
        "进度": "进度上报",
        "json": "JSON",
        "vajson": "vajson",
        "日志": "日志",
        "SecLog": "SecLog",
        "DriveUpdate": "DriveUpdate",
        "manifest": "manifest",
        "Ucmm": "Ucmm",
    }
    text = title + " " + rel_path
    for kw, tag in keyword_map.items():
        if kw in text:
            tags.add(tag)
    return sorted(tags)[:5]  # 最多 5 个标签


def generate_frontmatter(doc_id: str, doc_type: str, date: str,
                         title: str, tags: list[str],
                         rel_path: str) -> str:
    """生成 YAML frontmatter"""
    lines = ["---"]
    lines.append(f"doc_id: {doc_id}")
    lines.append(f"type: {doc_type}")
    
    # status: task/review 留空待人工填
    if doc_type in ("task", "review"):
        lines.append("status: ")  # 空值，待人工填
    elif doc_type == "design":
        lines.append("status: 定稿")
    
    lines.append(f"date: {date}")
    
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    
    lines.append("---")
    return "\n".join(lines)


def needs_migration(filepath: Path) -> bool:
    """检查文件是否已有 frontmatter"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            head = f.read(10)
        return not head.startswith("---")
    except:
        return False


def fix_diary_backticks(content: str) -> str:
    """Batch 1 附加: diary "关联" 字段加反引号

    规则: - **关联**: xxx, `code_func`, yyy
    → 给驼峰/下划线开头的词加反引号，但不给全小写的工具名加
    """
    lines = content.split("\n")
    result = []
    for line in lines:
        if line.strip().startswith("- **关联**"):
            # 提取冒号后的内容
            m = re.match(r"^(\s*-\s+\*\*关联\*\*:\s*)(.*)$", line)
            if m:
                prefix, items_str = m.groups()
                # 按逗号分割
                items = [s.strip() for s in items_str.split(",")]
                fixed_items = []
                for item in items:
                    item = item.strip()
                    if not item:
                        continue
                    # 已经有反引号的跳过
                    if item.startswith("`"):
                        fixed_items.append(item)
                        continue
                    # 判断是否是代码符号:
                    # - 包含 :: (ClassName::Method)
                    # - 驼峰开头 (parseIvcRxHeader, TryActivate)
                    # - 全大写_下划线 (DU_MCC_E_OK)
                    # - 包含下划线且不是全小写 (HandlePartitionSwitch)
                    is_symbol = False
                    if "::" in item:
                        is_symbol = True
                    elif re.match(r"^[A-Z][a-z]+[A-Z]", item):  # 驼峰
                        is_symbol = True
                    elif re.match(r"^[A-Z][A-Z_]+$", item):  # 全大写常量
                        is_symbol = True
                    elif re.match(r"^[a-z][a-zA-Z]+[A-Z][a-zA-Z]*$", item):  # camelCase
                        is_symbol = True
                    elif re.match(r"^[A-Z_][A-Z_]+[A-Z_]$", item) and "_" in item:
                        is_symbol = True
                    
                    if is_symbol:
                        fixed_items.append(f"`{item}`")
                    else:
                        fixed_items.append(item)
                
                result.append(prefix + ", ".join(fixed_items))
                continue
        result.append(line)
    return "\n".join(result)


def fix_plain_text_links(content: str) -> str:
    """Batch 3 附加: review 文档纯文本路径 → markdown 链接

    task/xxx.md → [xxx](task/xxx.md)
    """
    # 匹配: 任务文档：task/xxx.md (不在反引号或链接内)
    def replace_link(m):
        prefix = m.group(1)
        path = m.group(2)
        # 从路径提取显示文本
        display = Path(path).stem.replace("_", " ")
        return f"{prefix}[{display}]({path})"
    
    # 匹配: "xxx.md" 出现在 "任务文档：" 后面，且不是 [text](path) 格式
    content = re.sub(
        r"(任务文档[：:]\s*)([\w/]+\.md)",
        replace_link,
        content
    )
    content = re.sub(
        r"(关联文档[：:]\s*)([\w/]+\.md)",
        replace_link,
        content
    )
    return content


def migrate_file(filepath: Path, rel_path: str, dry_run: bool = False,
                 fix_backticks: bool = False, fix_links: bool = False) -> dict:
    """迁移单个文件"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"path": rel_path, "status": "error", "error": str(e)}
    
    if content.startswith("---"):
        return {"path": rel_path, "status": "skip", "reason": "已有 frontmatter"}
    
    doc_type = infer_doc_type(rel_path)
    title = extract_title(content)
    date = infer_date(filepath, rel_path, content)
    doc_id = infer_doc_id(rel_path, doc_type)
    tags = infer_tags(title, doc_type, rel_path)
    
    fm = generate_frontmatter(doc_id, doc_type, date, title, tags, rel_path)
    
    # 附加修复
    new_content = content
    if fix_backticks and doc_type == "diary":
        new_content = fix_diary_backticks(new_content)
    if fix_links and doc_type == "review":
        new_content = fix_plain_text_links(new_content)
    
    # 在内容前面插入 frontmatter
    new_content = fm + "\n\n" + new_content
    
    if not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
    
    return {
        "path": rel_path,
        "status": "migrated",
        "doc_id": doc_id,
        "type": doc_type,
        "date": date,
        "tags": tags,
    }


def find_markdown_files(docs_root: Path, batch: str | None = None) -> list[Path]:
    """找到所有需要迁移的 markdown 文件"""
    files = []
    for filepath in sorted(docs_root.rglob("*.md")):
        rel_path = str(filepath.relative_to(docs_root))
        
        # 跳过 doc_graph 目录下的文件（这些是新创建的，已有 frontmatter）
        if "task/doc_graph/" in rel_path:
            continue
        
        # 跳过 _template.md
        if filepath.name == "_template.md":
            continue
        
        # 按批次过滤
        if batch:
            if batch == "diary" and not rel_path.startswith("diary/"):
                continue
            if batch == "task" and not rel_path.startswith("task/"):
                continue
            if batch == "review" and not rel_path.startswith("review_reports/"):
                continue
            if batch == "design_link":
                doc_type = infer_doc_type(rel_path)
                if doc_type not in ("design", "link"):
                    continue
            if batch == "report":
                doc_type = infer_doc_type(rel_path)
                if doc_type != "report":
                    continue
        
        # 跳过 P3
        parts = rel_path.split("/")
        if parts[0] in SKIP_DIRS:
            continue
        
        files.append(filepath)
    
    return files


def main():
    parser = argparse.ArgumentParser(description="Task 8: 旧文档迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览不写文件")
    parser.add_argument("--batch", choices=["diary", "task", "review", "design_link", "report"],
                        help="只迁移指定批次")
    parser.add_argument("--no-backticks", action="store_true", help="跳过 diary 反引号修复")
    parser.add_argument("--no-links", action="store_true", help="跳过 review 链接修复")
    args = parser.parse_args()
    
    print(f"扫描目录: {DOCS_ROOT}")
    print(f"模式: {'预览' if args.dry_run else '写入'}")
    if args.batch:
        print(f"批次: {args.batch}")
    print()
    
    files = find_markdown_files(DOCS_ROOT, args.batch)
    
    migrated = 0
    skipped = 0
    errors = 0
    results_by_type = {}
    
    for filepath in files:
        rel_path = str(filepath.relative_to(DOCS_ROOT))
        
        result = migrate_file(
            filepath, rel_path,
            dry_run=args.dry_run,
            fix_backticks=not args.no_backticks,
            fix_links=not args.no_links,
        )
        
        status = result["status"]
        if status == "migrated":
            migrated += 1
            doc_type = result["type"]
            results_by_type.setdefault(doc_type, []).append(result)
            if args.dry_run:
                print(f"  [预览] {rel_path}")
                print(f"          → doc_id={result['doc_id']}, type={doc_type}, "
                      f"date={result['date']}, tags={result['tags']}")
            else:
                print(f"  ✅ {rel_path} → {result['doc_id']}")
        elif status == "skip":
            skipped += 1
        else:
            errors += 1
            print(f"  ❌ {rel_path}: {result.get('error', 'unknown error')}")
    
    print(f"\n{'='*60}")
    print(f"迁移完成: {migrated} 个文件")
    print(f"跳过(已有frontmatter): {skipped} 个")
    print(f"错误: {errors} 个")
    
    if results_by_type:
        print(f"\n按类型统计:")
        for doc_type, items in sorted(results_by_type.items()):
            print(f"  {doc_type}: {len(items)} 个")
    
    if not args.dry_run and migrated > 0:
        print(f"\n下一步: 重新运行 parser.py 刷新数据库")
        print(f"  cd {Path(__file__).parent} && python3 parser.py ../.. --db doc_graph.db --json doc_graph_data.json")


if __name__ == "__main__":
    main()
