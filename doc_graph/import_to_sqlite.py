#!/usr/bin/env python3
"""将 PoC JSON 数据导入 SQLite，建表 + FTS5 索引"""

import json
import os
import sqlite3
import sys

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "poc_doc_graph.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "doc_graph.db")

def main():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data["nodes"]
    edges = data["edges"]

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── 建表 ──
    c.execute("""
        CREATE TABLE node (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            doc_type    TEXT,
            title       TEXT,
            path        TEXT,
            line        INTEGER DEFAULT 0,
            ktype       TEXT,
            conclusion  TEXT,
            session     TEXT,
            status      TEXT,
            date        TEXT,
            tags        TEXT,
            manual      INTEGER DEFAULT 0,
            legacy      INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE edge (
            src     TEXT NOT NULL,
            dst     TEXT NOT NULL,
            rel     TEXT NOT NULL,
            manual  INTEGER DEFAULT 0,
            PRIMARY KEY (src, dst, rel)
        )
    """)
    c.execute("CREATE INDEX idx_edge_src ON edge(src)")
    c.execute("CREATE INDEX idx_edge_dst ON edge(dst)")
    c.execute("CREATE INDEX idx_edge_rel ON edge(rel)")

    # ── 插入节点 ──
    for n in nodes:
        c.execute("""
            INSERT OR REPLACE INTO node
            (id, type, doc_type, title, path, line, ktype, conclusion, session, status, date, tags, manual, legacy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
        """, (
            n["id"], n["type"], n.get("doc_type", ""), n.get("title", ""),
            n.get("path", ""), n.get("line", 0), n.get("ktype", ""),
            n.get("conclusion", ""), n.get("session", ""),
            n.get("status", ""), n.get("date", ""),
            json.dumps(n.get("tags", []), ensure_ascii=False),
        ))

    # ── 插入边（去重） ──
    for e in edges:
        c.execute("""
            INSERT OR IGNORE INTO edge (src, dst, rel, manual)
            VALUES (?, ?, ?, 0)
        """, (e["src"], e["dst"], e["rel"]))

    # ── 建 FTS5 虚拟表 ──
    try:
        c.execute("""
            CREATE VIRTUAL TABLE doc_fts USING fts5(
                doc_id, title, content_preview, tags,
                tokenize='unicode61'
            )
        """)
        # 索引文档节点
        for n in nodes:
            if n["type"] == "document":
                c.execute("""
                    INSERT INTO doc_fts (doc_id, title, content_preview, tags)
                    VALUES (?, ?, ?, ?)
                """, (
                    n["id"],
                    n.get("title", ""),
                    n.get("path", ""),
                    json.dumps(n.get("tags", []), ensure_ascii=False),
                ))
    except Exception as ex:
        print(f"⚠️ FTS5 创建跳过（SQLite 版本可能不支持）: {ex}")

    conn.commit()

    # ── 统计输出 ──
    print("=" * 60)
    print(f"✅ SQLite 数据库已创建: {DB_PATH}")
    print(f"   文件大小: {os.path.getsize(DB_PATH) / 1024:.0f} KB")
    print(f"   节点数: {c.execute('SELECT COUNT(*) FROM node').fetchone()[0]}")
    print(f"   边数:   {c.execute('SELECT COUNT(*) FROM edge').fetchone()[0]}")
    print("=" * 60)

    print("\n📊 节点类型分布:")
    for row in c.execute("SELECT type, COUNT(*) FROM node GROUP BY type ORDER BY COUNT(*) DESC"):
        print(f"   {row[0]:12s}: {row[1]}")

    print("\n📊 文档类型分布:")
    for row in c.execute("SELECT doc_type, COUNT(*) FROM node WHERE type='document' GROUP BY doc_type ORDER BY COUNT(*) DESC"):
        print(f"   {row[0]:12s}: {row[1]}")

    print("\n📊 边类型分布:")
    for row in c.execute("SELECT rel, COUNT(*) FROM edge GROUP BY rel ORDER BY COUNT(*) DESC"):
        print(f"   {row[0]:20s}: {row[1]}")

    # ── 示例查询 ──
    print("\n" + "=" * 60)
    print("📋 示例：文档间关联（relates_to）前 10 条")
    print("=" * 60)
    for row in c.execute("""
        SELECT e.src, n1.title, e.dst, n2.title
        FROM edge e
        JOIN node n1 ON e.src = n1.id
        JOIN node n2 ON e.dst = n2.id
        WHERE e.rel = 'relates_to'
        LIMIT 10
    """):
        print(f"   {row[1][:30]:30s} → {row[3][:30]}")

    print("\n📋 示例：被引用最多的代码符号（top 10）")
    print("=" * 60)
    for row in c.execute("""
        SELECT e.dst AS symbol, COUNT(*) AS ref_count
        FROM edge e
        WHERE e.rel = 'mentions_symbol'
        GROUP BY e.dst
        ORDER BY ref_count DESC
        LIMIT 10
    """):
        print(f"   {row[0]:40s}  ({row[1]} 次引用)")

    print("\n📋 示例：孤立文档（无任何边）")
    print("=" * 60)
    for row in c.execute("""
        SELECT n.id, n.title
        FROM node n
        WHERE n.type = 'document'
          AND n.id NOT IN (SELECT src FROM edge WHERE src LIKE 'doc:%')
          AND n.id NOT IN (SELECT dst FROM edge WHERE dst LIKE 'doc:%')
    """):
        print(f"   {row[0]:40s}  {row[1][:40]}")

    # FTS5 搜索示例
    try:
        print("\n📋 示例：FTS5 搜索 '分区'")
        print("=" * 60)
        for row in c.execute("""
            SELECT doc_id, bm25(doc_fts) AS score
            FROM doc_fts
            WHERE doc_fts MATCH '分区'
            ORDER BY score
            LIMIT 5
        """):
            print(f"   {row[0]:40s}  score={row[1]:.2f}")
    except:
        print("   (FTS5 不可用，跳过)")

    conn.close()
    print(f"\n💡 用 VS Code 打开 {DB_PATH} 即可可视化浏览（需安装 SQLite Viewer 插件）")


if __name__ == "__main__":
    main()
