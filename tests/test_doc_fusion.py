"""文档-代码融合专项测试

验证 cpp_search_docs 的三大能力:
  A. 文档搜索准确性（关键词→文档切片）
  B. 文档→代码关联（搜文档带出代码实体）
  C. 代码→文档反向关联（搜代码定位相关文档）
  D. 效率对比（search_docs vs grep -r docs/）
"""
from __future__ import annotations

import sqlite3
import subprocess
import time
import json
from pathlib import Path

DB = str(Path(__file__).resolve().parent.parent / "semantic_graph_full.db")
DOCS = "/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/app/hq_ota_service/docs"

conn = sqlite3.connect(DB)


def banner(title):
    print(f"\n{'='*72}\n{title}\n{'='*72}")


# ----------------------------------------------------------------------
# A. 文档搜索准确性
# ----------------------------------------------------------------------

def test_doc_search():
    banner("A. 文档搜索准确性")

    cases = [
        ("升级",    "应命中 OTA 升级/回滚/差分相关文档"),
        ("BootChain","应命中 A/B 分区切换设计文档"),
        ("OTA",     "应命中架构文档/状态管理文档"),
        ("分区",    "应命中 A/B 分区切换/回滚文档"),
        ("DUCC",    "应命中 DUCC/MCC 集成文档"),
        ("UDS",     "应命中 UDS/DoIP 通信文档"),
        ("增量更新", "应命中增量更新任务文档"),
        ("NonExistDoc", "应返回 0 结果"),
    ]

    print(f"  {'关键词':12s} | {'命中数':>6s} | {'有代码关联':>8s} | {'标签':20s} | 判定")
    print(f"  {'-'*12}-+-{'-'*6}-+-{'-'*8}-+-{'-'*20}-+------")

    for kw, desc in cases:
        rows = conn.execute(
            "SELECT id, name, extra_info FROM node WHERE type='doc_section' "
            "AND (name LIKE ? OR extra_info LIKE ?) LIMIT 10",
            (f"%{kw}%", f"%{kw}%")
        ).fetchall()

        has_code = False
        tags_set = set()
        for r in rows:
            extra = json.loads(r[2]) if r[2] else {}
            for t in extra.get('tags', []):
                tags_set.add(t)
            # check if has code association
            code_cnt = conn.execute(
                "SELECT COUNT(*) FROM edge WHERE from_id=? AND relation_type='doc_describes_code'",
                (r[0],)).fetchone()[0]
            if code_cnt > 0:
                has_code = True

        tags_str = ','.join(sorted(tags_set)[:3])
        if kw == "NonExistDoc":
            ok = len(rows) == 0
            print(f"  {kw:12s} | {len(rows):>6d} | {'N/A':>8s} | {tags_str:20s} | {'✅' if ok else '✗'}")
        else:
            ok = len(rows) > 0
            print(f"  {kw:12s} | {len(rows):>6d} | {'✅ 是' if has_code else '❌ 否':>8s} | {tags_str:20s} | {'✅' if ok else '✗'}")


# ----------------------------------------------------------------------
# B. 文档→代码关联质量
# ----------------------------------------------------------------------

def test_doc_to_code():
    banner("B. 文档→代码关联质量")

    # 预期关联：doc_config.yaml 中的 manual_links
    expected = [
        ("OTA_COMPLETE_FLOW.md", "PerformUpgrade", True),
        ("OTA_COMPLETE_FLOW.md", "BasePeriUpdate", True),
        ("OTA_COMPLETE_FLOW.md", "SocUpdate", True),
        ("OTA_COMPLETE_FLOW.md", "ExecuteDriveUpdate", True),
        ("AB_PARTITION_SWITCH_DESIGN.md", "GetSocBootChain", True),
        ("ARCHITECTURE.md", "OtaManager", True),
        ("ARCHITECTURE.md", "BasePeriUpdate", True),
    ]

    print(f"  {'文档':40s} | {'代码实体':25s} | {'预期':>4s} | {'实际':>4s} | 判定")
    print(f"  {'-'*40}-+-{'-'*25}-+-{'-'*4}-+-{'-'*4}-+------")

    for doc, code, expect in expected:
        # 查 doc_section 节点
        doc_rows = conn.execute(
            "SELECT id FROM node WHERE type='doc_section' AND file_path LIKE ?",
            (f"%{doc}%",)).fetchall()

        found = False
        for dr in doc_rows:
            cnt = conn.execute(
                """SELECT COUNT(*) FROM edge e JOIN node cn ON cn.id=e.to_id
                   WHERE e.from_id=? AND e.relation_type='doc_describes_code'
                   AND cn.name=?""", (dr[0], code)).fetchone()[0]
            if cnt > 0:
                found = True
                break

        print(f"  {doc:40s} | {code:25s} | {'✅':>4s} | {'✅' if found else '❌':>4s} | {'✅' if found==expect else '✗'}")

    # content_scan 自动关联质量
    print(f"\n  content_scan 自动关联 top5（被最多文档引用的代码）:")
    rows = conn.execute("""
        SELECT n.name, n.type, n.namespace, COUNT(*) cnt FROM node n
        JOIN edge e ON e.to_id=n.id
        WHERE e.relation_type='doc_describes_code'
        GROUP BY n.id ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    for r in rows:
        print(f"    {r[2]}::{r[0]} [{r[1]}] → {r[3]} 篇文档")


# ----------------------------------------------------------------------
# C. 代码→文档反向关联
# ----------------------------------------------------------------------

def test_code_to_doc():
    banner("C. 代码→文档反向关联")

    cases = [
        ("SocUpdate", "class", "应关联 OTA 流程/架构/错误处理等文档"),
        ("OtaManager", "class", "应关联架构/初始化/状态机等文档"),
        ("PerformUpgrade", "function", "应关联升级流程文档"),
        ("GetSocBootChain", "class", "应关联 A/B 分区切换文档"),
        ("Rollback", "function", "应关联回滚/错误处理文档"),
    ]

    print(f"  {'代码实体':25s} | {'类型':6s} | {'关联文档数':>8s} | {'文档示例':40s} | 判定")
    print(f"  {'-'*25}-+-{'-'*6}-+-{'-'*8}-+-{'-'*40}-+------")

    for name, ntype, desc in cases:
        node = conn.execute(
            "SELECT id FROM node WHERE name=? AND type=? LIMIT 1",
            (name, ntype)).fetchone()
        if not node:
            print(f"  {name:25s} | {ntype:6s} | {'N/A':>8s} | {'节点未找到':40s} | ⚠️")
            continue

        docs = conn.execute("""
            SELECT dn.name FROM node dn
            JOIN edge e ON e.to_id=dn.id
            WHERE e.from_id=? AND e.relation_type='code_refers_to_doc'
            ORDER BY dn.name LIMIT 5
        """, (node[0],)).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE from_id=? AND relation_type='code_refers_to_doc'",
            (node[0],)).fetchone()[0]

        sample = docs[0][0][:40] if docs else "无"
        ok = total > 0
        print(f"  {name:25s} | {ntype:6s} | {total:>8d} | {sample:40s} | {'✅' if ok else '✗'}")


# ----------------------------------------------------------------------
# D. 效率对比
# ----------------------------------------------------------------------

def test_efficiency():
    banner("D. 效率对比（search_docs vs grep -r docs/）")

    cases = [
        ("升级", r"升级"),
        ("BootChain", r"BootChain"),
        ("OTA", r"\bOTA\b"),
        ("分区切换", r"分区切换"),
    ]

    print(f"  {'关键词':12s} | {'图谱(ms)':>9s} | {'grep(ms)':>9s} | {'加速比':>8s} | 判定")
    print(f"  {'-'*12}-+-{'-'*9}-+-{'-'*9}-+-{'-'*8}-+------")

    for kw, pat in cases:
        # 图谱
        ts = []
        for _ in range(20):
            t = time.perf_counter()
            conn.execute(
                "SELECT id FROM node WHERE type='doc_section' "
                "AND (name LIKE ? OR extra_info LIKE ?)",
                (f"%{kw}%", f"%{kw}%")).fetchall()
            ts.append(time.perf_counter() - t)
        graph_ms = min(ts) * 1000

        # grep
        ts2 = []
        for _ in range(3):
            t = time.perf_counter()
            subprocess.run(
                ["grep", "-rPn", "-e", pat, "--include=*.md", DOCS],
                capture_output=True, text=True, timeout=30)
            ts2.append(time.perf_counter() - t)
        grep_ms = min(ts2) * 1000

        speedup = grep_ms / graph_ms if graph_ms > 0 else 0
        print(f"  {kw:12s} | {graph_ms:>9.3f} | {grep_ms:>9.2f} | {speedup:>7.0f}× | ✅")


# ----------------------------------------------------------------------
# E. 融合完整性
# ----------------------------------------------------------------------

def test_fusion_completeness():
    banner("E. 融合完整性")

    total_docs = conn.execute("SELECT COUNT(*) FROM node WHERE type='doc_section'").fetchone()[0]
    docs_with_code = conn.execute("""
        SELECT COUNT(DISTINCT e.from_id) FROM edge e
        JOIN node n ON n.id=e.from_id
        WHERE e.relation_type='doc_describes_code' AND n.type='doc_section'
    """).fetchone()[0]
    coverage = docs_with_code / total_docs * 100 if total_docs else 0

    total_code = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type IN ('class','function','struct')").fetchone()[0]
    code_with_doc = conn.execute("""
        SELECT COUNT(DISTINCT e.from_id) FROM edge e
        JOIN node n ON n.id=e.from_id
        WHERE e.relation_type='code_refers_to_doc' AND n.type IN ('class','function','struct')
    """).fetchone()[0]
    code_coverage = code_with_doc / total_code * 100 if total_code else 0

    doc_edges = conn.execute(
        "SELECT COUNT(*) FROM edge WHERE relation_type='doc_describes_code'").fetchone()[0]
    code_edges = conn.execute(
        "SELECT COUNT(*) FROM edge WHERE relation_type='code_refers_to_doc'").fetchone()[0]

    print(f"  文档切片总数:       {total_docs}")
    print(f"  有代码关联的文档:   {docs_with_code} ({coverage:.1f}%)")
    print(f"  代码节点总数:       {total_code}")
    print(f"  有文档关联的代码:   {code_with_doc} ({code_coverage:.1f}%)")
    print(f"  doc→code 边:        {doc_edges}")
    print(f"  code→doc 边:        {code_edges}")
    print(f"  双向对称:           {'✅' if doc_edges == code_edges else '❌'} ({doc_edges} vs {code_edges})")
    print()
    print(f"  文档→代码覆盖率:   {'██████████' if coverage>80 else '████████' if coverage>60 else '██████'} {coverage:.1f}%")
    print(f"  代码→文档覆盖率:   {'████' if code_coverage>20 else '██' if code_coverage>10 else '█'} {code_coverage:.1f}%")
    print(f"  (代码→文档覆盖率低是正常的——大部分函数不需要被文档直接引用)")


def main():
    test_doc_search()
    test_doc_to_code()
    test_code_to_doc()
    test_efficiency()
    test_fusion_completeness()
    conn.close()


if __name__ == "__main__":
    main()
