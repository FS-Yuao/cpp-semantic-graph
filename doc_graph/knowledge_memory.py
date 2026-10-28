"""Project-scoped document memory in the existing doc-graph database.

Source snapshots and evidence are durable; parser rebuilds only derived indexes.
No model/API dependency. Similarity retrieves candidates, never verifies a claim.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone

import finding_store as fs

MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect(db_path):
    conn = fs._get_conn(db_path)
    # Individual DDL is idempotent. The schema does not delete old tables/data.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_project(
            project_id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_document(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL,
            title TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
            revision TEXT NOT NULL, created_at TEXT NOT NULL, current INTEGER NOT NULL,
            UNIQUE(project_id,path,content_hash,revision));
        CREATE INDEX IF NOT EXISTS memory_doc_project ON memory_document(project_id,current);
        CREATE TABLE IF NOT EXISTS memory_chunk(
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL, heading TEXT NOT NULL, content TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_chunk_doc ON memory_chunk(document_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            chunk_id UNINDEXED, title, heading, content, tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS memory_edge(
            src TEXT NOT NULL, dst TEXT NOT NULL, relation TEXT NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(src,dst,relation));
    """)
    conn.execute("BEGIN IMMEDIATE")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memory_document)")}
    if "status" not in columns:
        conn.execute("ALTER TABLE memory_document ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    conn.commit()
    return conn


def register_project(project_id, root, db_path):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", project_id):
        raise ValueError("project_id must be a stable 1..80 character identifier")
    if not root or not Path(root).expanduser().is_absolute():
        raise ValueError("project root must be an explicit absolute path")
    root = str(Path(root).expanduser().resolve(strict=True))
    if not Path(root).is_dir():
        raise ValueError("project root must be a directory")
    conn = connect(db_path)
    try:
        old = conn.execute("SELECT root FROM memory_project WHERE project_id=?", (project_id,)).fetchone()
        if old and old[0] != root:
            raise ValueError("project_id already belongs to another root")
        alias = conn.execute("SELECT project_id FROM memory_project WHERE root=?", (root,)).fetchone()
        if alias and alias[0] != project_id:
            raise ValueError("this root is already registered as " + alias[0])
        conn.execute("INSERT OR IGNORE INTO memory_project VALUES(?,?,?)", (project_id, root, _now()))
        conn.commit()
    finally:
        conn.close()
    return {"project_id": project_id, "root": root}


def _root(conn, project_id):
    row = conn.execute("SELECT root FROM memory_project WHERE project_id=?", (project_id,)).fetchone()
    if not row:
        raise ValueError("register this project before storing or recalling knowledge")
    return Path(row[0])


def _read_source(root, source_path):
    path = Path(source_path)
    path = (root / path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("source must be inside the registered project root")
    if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".rst", ".adoc"}:
        raise ValueError("source must be a UTF-8 Markdown/text document")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds the 2 MiB limit")
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES or b"\0" in raw:
        raise ValueError("source is oversized or binary")
    return str(path.relative_to(root)), raw.decode("utf-8")


def _chunks(content):
    lines = content.splitlines()
    heading = ""
    start = 0
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        is_heading = not in_fence and re.match(r"^#{1,6}\s+", line)
        if i > start and (is_heading or i - start >= 80):
            yield start + 1, i, heading, "\n".join(lines[start:i])
            start = i
        if is_heading:
            heading = line.lstrip("# ").strip()
    if len(lines) > start:
        yield start + 1, len(lines), heading, "\n".join(lines[start:])


def capture_document(project_id, source_path, revision, db_path):
    conn = connect(db_path)
    try:
        root = _root(conn, project_id)
        path, content = _read_source(root, source_path)
        digest = _hash(content)
        doc_id = "memory-doc:" + _hash(json.dumps([project_id, path, digest, revision]))
        title = next((x.lstrip("# ") for x in content.splitlines() if x.startswith("# ")), path)
        state = "archived" if any(x.lower() in {"archive", "archived", "legacy"} for x in Path(path).parts) else "active"
        if content.startswith("---"):
            frontmatter = content.split("---", 2)
            if len(frontmatter) > 2:
                match = re.search(r"(?m)^status:\s*['\"]?(archived|revoked|superseded|deprecated)\b", frontmatter[1])
                if match:
                    state = match[1]
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM memory_document WHERE project_id=? AND path=? AND current=1",
                               (project_id, path)).fetchone()
            if old and old["id"] == doc_id:
                return {"action": "unchanged", "document_id": doc_id, "content_hash": digest}
            conn.execute("UPDATE memory_document SET current=0 WHERE project_id=? AND path=?", (project_id, path))
            conn.execute("""INSERT OR IGNORE INTO memory_document(id,project_id,path,title,content,content_hash,revision,created_at,current,status) VALUES(?,?,?,?,?,?,?,?,1,?)""",
                         (doc_id, project_id, path, title, content, digest, revision, _now(), state))
            conn.execute("UPDATE memory_document SET current=1 WHERE id=?", (doc_id,))
            exists = conn.execute("SELECT 1 FROM memory_chunk WHERE document_id=? LIMIT 1", (doc_id,)).fetchone()
            if not exists:
                for start, end, heading, text in _chunks(content):
                    chunk_id = f"{doc_id}:{start}"
                    conn.execute("INSERT INTO memory_chunk VALUES(?,?,?,?,?,?)", (chunk_id, doc_id, start, end, heading, text))
                    conn.execute("INSERT INTO memory_fts VALUES(?,?,?,?)", (chunk_id,
                                 fs._preprocess_cjk_for_fts(title), fs._preprocess_cjk_for_fts(heading),
                                 fs._preprocess_cjk_for_fts(text)))
            if old:
                conn.execute("INSERT OR IGNORE INTO memory_edge VALUES(?,?,?,?,?)",
                             (doc_id, old["id"], "previous_snapshot", "new source snapshot", _now()))
                conn.execute("UPDATE finding SET status='stale' WHERE source_document_id=? AND status='active'", (old["id"],))
            return {"action": "captured", "document_id": doc_id, "project_id": project_id,
                    "path": path, "content_hash": digest, "revision": revision,
                    "previous_document_id": old["id"] if old else None}
    finally:
        conn.close()


def remember(project_id, title, detail, document_id, conditions, db_path,
             revision="", scope="project", ftype="lesson", confidence="suspected",
             evidence_key="", symbols=None):
    conn = connect(db_path)
    try:
        _root(conn, project_id)
        doc = conn.execute("SELECT * FROM memory_document WHERE id=? AND project_id=? AND current=1 AND status='active'",
                           (document_id, project_id)).fetchone()
        if not doc:
            raise ValueError("capture a current source document from this project first")
        if not detail.strip() or not conditions.strip():
            raise ValueError("conclusion detail and applicability conditions are required")
        if _source_state(conn, doc) != "unchanged":
            raise ValueError("source has changed or is missing; capture it again first")
        if revision and doc["revision"] and revision != doc["revision"]:
            raise ValueError("conclusion revision differs from its source snapshot")
        source = str(_root(conn, project_id) / doc["path"]) + "#sha256=" + doc["content_hash"]
    finally:
        conn.close()
    result = fs.record_finding(title, detail, ftype=ftype, symbols=symbols, source=source,
                              confidence=confidence, db_path=db_path, project_id=project_id,
                              scope=scope, revision=revision or doc["revision"], conditions=conditions,
                              evidence_key=evidence_key, source_document_id=document_id)
    if "error" not in result:
        conn = connect(db_path)
        try:
            with conn:
                # Immutable source citation is also in finding.source_document_id;
                # this edge is a navigation index, not the sole provenance record.
                conn.execute("INSERT OR IGNORE INTO memory_edge VALUES(?,?,?,?,?)",
                             (result["id"], document_id, "derived_from", conditions, _now()))
        finally:
            conn.close()
    return result


def _source_state(conn, doc):
    try:
        _, text = _read_source(_root(conn, doc["project_id"]), doc["path"])
        return "unchanged" if _hash(text) == doc["content_hash"] else "changed"
    except (OSError, ValueError, UnicodeError):
        return "missing_or_unreadable"


def recall(project_id, keyword, db_path, revision="", include_portable=True,
           include_history=False, limit=10):
    if not keyword.strip() or len(keyword) > 2048 or not 1 <= limit <= 50:
        raise ValueError("keyword required (max 2048 chars), limit must be 1..50")
    conn = connect(db_path)
    try:
        _root(conn, project_id)
        # Filter by project BEFORE LIMIT; common words in other projects cannot
        # crowd out local evidence. CJK phrase fallback avoids char-only noise.
        tokens = [keyword, *fs.expand_synonyms(keyword)]
        expression = fs._fts_tokens(" ".join(tokens))
        rows = conn.execute("""SELECT c.*, d.project_id,d.path,d.title,d.content_hash,d.revision,d.current,
                     d.id AS doc_id,bm25(memory_fts) AS score
            FROM memory_fts JOIN memory_chunk c ON c.id=memory_fts.chunk_id
            JOIN memory_document d ON d.id=c.document_id
            WHERE memory_fts MATCH ? AND d.project_id=? AND ((d.current=1 AND d.status='active') OR ?)
            ORDER BY (instr(c.content, ?) > 0) DESC, score LIMIT ?""",
            (expression, project_id, include_history, keyword, limit * 5)).fetchall() if expression else []
        docs = {}
        matches = []
        for row in rows:
            r = dict(row)
            if r["doc_id"] not in docs:
                d = conn.execute("SELECT * FROM memory_document WHERE id=?", (r["doc_id"],)).fetchone()
                docs[r["doc_id"]] = _source_state(conn, d)
            r["source_state"] = docs[r["doc_id"]]
            r["source_path"] = str(_root(conn, project_id) / r["path"])
            r["revision_matches"] = (not revision or r["revision"] == revision)
            r["excerpt_truncated"] = len(r["content"]) > 1200
            r["content"] = r["content"][:1200]
            matches.append(r)
        findings = fs.search_findings(keyword=keyword, limit=limit, db_path=db_path,
                                     status="active,stale,superseded,revoked" if include_history else "active",
                                     project_id=project_id, include_portable=include_portable)
        known = {f["id"] for f in findings}
        if not include_history:
            for f in fs.get_findings_by_symbols([keyword], db_path=db_path, project_id=project_id):
                if f["id"] not in known and len(findings) < limit:
                    findings.append(f)
                    known.add(f["id"])
        for finding in findings:
            if finding["project_id"] != project_id:
                finding["reuse_state"] = "check_portable_conditions"
            elif finding["status"] != "active":
                finding["reuse_state"] = "historical"
            elif revision and finding["revision"] != revision:
                finding["reuse_state"] = "revision_requires_revalidation"
            else:
                finding["reuse_state"] = "caller_confirmed" if finding["confidence"] == "confirmed" else "unverified"
            doc_id = finding.get("source_document_id")
            doc = conn.execute("SELECT * FROM memory_document WHERE id=?", (doc_id,)).fetchone() if doc_id else None
            finding["source_state"] = _source_state(conn, doc) if doc else "legacy_source_not_snapshotted"
            if doc and (not doc["current"] or finding["source_state"] != "unchanged"):
                finding["reuse_state"] = "source_requires_revalidation"
            # Keep initial recall bounded; get_memory reads the complete record.
            finding["detail_truncated"] = len(finding["detail"]) > 1200
            finding["detail"] = finding["detail"][:1200]
        ids = [f["id"] for f in findings] + [m["doc_id"] for m in matches[:limit]]
        edges = []
        if ids:
            ph = ",".join("?" for _ in ids)
            edges = [dict(r) for r in conn.execute(
                f"SELECT * FROM memory_edge WHERE src IN ({ph}) ORDER BY created_at DESC LIMIT 100", ids)]
        return {"project_id": project_id, "query": keyword, "revision": revision,
                "findings": findings, "documents": matches[:limit], "edges": edges,
                "document_matches_truncated": len(matches) > limit or len(rows) == limit * 5,
                "guidance": "Read source/conditions before reuse. confirmed is a caller report; similarity and symbol existence do not verify correctness."}
    finally:
        conn.close()


def get_memory(memory_id, project_id, db_path, offset=0, max_chars=12000, snapshot_hash=""):
    if offset < 0 or not 1 <= max_chars <= 30000:
        raise ValueError("invalid text page")
    conn = connect(db_path)
    try:
        _root(conn, project_id)
        doc = conn.execute("""SELECT * FROM memory_document WHERE id=? AND (project_id=? OR id IN (
            SELECT source_document_id FROM finding WHERE scope='portable' AND status='active'))""",
                           (memory_id, project_id)).fetchone()
        if doc:
            item = dict(doc)
            field = "content"
            item["source_state"] = _source_state(conn, doc)
            item["source_path"] = str(_root(conn, doc["project_id"]) / doc["path"])
        else:
            row = conn.execute("SELECT * FROM finding WHERE id=? AND (project_id=? OR scope='portable')",
                               (memory_id, project_id)).fetchone()
            if not row:
                raise ValueError("memory not found in this project/portable scope")
            item = fs._row_to_finding(row)
            if snapshot_hash:
                historic = conn.execute("SELECT snapshot FROM finding_revision WHERE finding_id=? AND snapshot_hash=?",
                                        (memory_id, snapshot_hash)).fetchone()
                if not historic:
                    raise ValueError("finding revision not found")
                item = json.loads(historic[0])
                item["historical_snapshot"] = True
            item["history"] = [{"snapshot_hash": r[0], "saved_at": r[1]} for r in conn.execute(
                "SELECT snapshot_hash,saved_at FROM finding_revision WHERE finding_id=? ORDER BY saved_at DESC", (memory_id,))]
            field = "detail"
        text = item[field]
        item[field] = text[offset:offset + max_chars]
        item.update(offset=offset, total_chars=len(text), next_offset=offset + max_chars if len(text) > offset + max_chars else None)
        item["edges"] = [dict(r) for r in conn.execute("SELECT * FROM memory_edge WHERE src=? OR dst=?", (memory_id, memory_id))]
        return item
    finally:
        conn.close()


def supersede(project_id, old_id, replacement_id, reason, db_path):
    if not reason.strip() or old_id == replacement_id:
        raise ValueError("distinct findings and a reason are required")
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM finding WHERE id IN (?,?) AND project_id=?",
                                (old_id, replacement_id, project_id)).fetchall()
            records = {r["id"]: r for r in rows}
            if len(records) != 2 or records[replacement_id]["status"] != "active" or records[old_id]["status"] != "active":
                raise ValueError("both findings must be active and belong to this project")
            conn.execute("UPDATE finding SET status='superseded' WHERE id=?", (old_id,))
            conn.execute("INSERT INTO memory_edge VALUES(?,?,?,?,?)", (replacement_id, old_id, "supersedes", reason, _now()))
        return {"superseded": old_id, "replacement": replacement_id}
    finally:
        conn.close()


def status(db_path):
    conn = connect(db_path)
    try:
        return {"schema": "project-memory-v1", "projects": [dict(x) for x in conn.execute("SELECT * FROM memory_project")],
                "documents": conn.execute("SELECT COUNT(*) FROM memory_document WHERE current=1").fetchone()[0],
                "findings_by_project": [dict(x) for x in conn.execute("SELECT project_id,scope,status,COUNT(*) AS count FROM finding GROUP BY project_id,scope,status")],
                "edges": conn.execute("SELECT COUNT(*) FROM memory_edge").fetchone()[0]}
    finally:
        conn.close()
