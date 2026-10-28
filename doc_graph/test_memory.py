"""Temporary-database tests for durable project memory and rebuild invariants."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finding_store as fs
import knowledge_memory as memory
import query

spec = importlib.util.spec_from_file_location("memory_doc_parser", Path(__file__).with_name("parser.py"))
parser = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = parser
spec.loader.exec_module(parser)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="doc-memory-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = str(self.root / "memory.db")
        self.a = self.root / "a"
        self.b = self.root / "b"
        self.a.mkdir()
        self.b.mkdir()
        memory.register_project("a", str(self.a), self.db)
        memory.register_project("b", str(self.b), self.db)
        self.embed = patch.object(fs, "_embed_one", return_value=None)
        self.embed.start()
        self.addCleanup(self.embed.stop)

    def capture(self, text="# Failure handling\n\n## Matrix\n\n| Key | Value |\n|---|---|\n| rollbacktoken | block bad version |\n", project="a", revision="r1"):
        root = self.a if project == "a" else self.b
        (root / "design.md").write_text(text)
        return memory.capture_document(project, "design.md", revision, self.db)["document_id"]

    def remember(self, doc, **kw):
        args = dict(project_id="a", title="rollbacktoken rule", detail="Reject incompatible firmware.",
                    document_id=doc, conditions="Only when the target version is incompatible.", db_path=self.db)
        args.update(kw)
        return memory.remember(**args)

    def test_fulltext_table_and_late_paragraph(self):
        self.capture("# Manual\n\n" + "Intro.\n" * 200 + "\n## Matrix\n| token | rule |\n|---|---|\n| tailtabletoken | keep |\n")
        out = memory.recall("a", "tailtabletoken", self.db)
        self.assertEqual(len(out["documents"]), 1)
        self.assertIn("tailtabletoken", out["documents"][0]["content"])
        self.assertGreater(out["documents"][0]["start_line"], 190)

    def test_project_filter_before_limit_and_same_title(self):
        da = self.capture()
        db = self.capture(project="b")
        fa = self.remember(da)
        fb = self.remember(db, project_id="b")
        self.assertNotEqual(fa["id"], fb["id"])
        out = memory.recall("b", "rollbacktoken", self.db, limit=1)
        self.assertEqual(out["findings"][0]["id"], fb["id"])
        self.assertTrue(all(x["project_id"] == "b" for x in out["documents"]))

    def test_portable_requires_conditions_and_is_explicit(self):
        doc = self.capture()
        out = self.remember(doc, scope="portable")
        recalled = memory.recall("b", "rollbacktoken", self.db)
        self.assertEqual(recalled["findings"][0]["reuse_state"], "check_portable_conditions")
        self.assertFalse(memory.recall("b", "rollbacktoken", self.db, include_portable=False)["findings"])
        with self.assertRaises(ValueError):
            self.remember(doc, conditions="", scope="portable")
        self.assertEqual(memory.get_memory(out["id"], "b", self.db)["project_id"], "a")
        self.assertEqual(memory.get_memory(doc, "b", self.db)["id"], doc)

    def test_confirmation_is_explicit_and_retries_do_not_add_evidence(self):
        doc = self.capture()
        f = self.remember(doc)
        self.assertEqual(f["confidence"], "suspected")
        self.assertEqual(f["evidence_count"], 0)
        self.assertIn("error", self.remember(doc, confidence="confirmed"))
        f = self.remember(doc, confidence="confirmed", evidence_key="experiment-1")
        repeat = self.remember(doc, confidence="confirmed", evidence_key="experiment-1")
        self.assertEqual(f["evidence_count"], 1)
        self.assertEqual(repeat["evidence_count"], 1)
        edited = self.remember(doc, detail="Edited conclusion with different scope.")
        self.assertEqual(edited["confidence"], "suspected")
        self.assertEqual(edited["evidence_count"], 0)
        row = memory.get_memory(f["id"], "a", self.db)
        self.assertIsNone(row["last_confirmed"])
        self.assertTrue(row["history"])
        old = memory.get_memory(f["id"], "a", self.db, snapshot_hash=row["history"][0]["snapshot_hash"])
        self.assertEqual(old["detail"], "Reject incompatible firmware.")
        self.assertTrue(old["historical_snapshot"])

    def test_edit_preserves_type_and_confidence_when_omitted(self):
        f = fs.record_finding("risk", "Risk description", ftype="risk", db_path=self.db, project_id="a")
        fs.record_finding("risk", db_path=self.db, project_id="a")
        row = memory.get_memory(f["id"], "a", self.db)
        self.assertEqual(row["ftype"], "risk")
        self.assertEqual(row["confidence"], "suspected")
        self.assertEqual(row["detail"], "Risk description")

    def test_source_change_and_revision_mismatch_prevent_blind_reuse(self):
        doc = self.capture()
        self.remember(doc, confidence="confirmed", evidence_key="test-1")
        out = memory.recall("a", "rollbacktoken", self.db, revision="r2")
        self.assertEqual(out["findings"][0]["reuse_state"], "revision_requires_revalidation")
        (self.a / "design.md").write_text("# Changed source\n")
        out = memory.recall("a", "rollbacktoken", self.db)
        self.assertEqual(out["findings"][0]["reuse_state"], "source_requires_revalidation")
        with self.assertRaises(ValueError):
            self.remember(doc)
        memory.capture_document("a", "design.md", "r2", self.db)
        self.assertFalse(memory.recall("a", "rollbacktoken", self.db)["findings"])
        self.assertTrue(memory.recall("a", "rollbacktoken", self.db, include_history=True)["findings"])
        new_doc = memory.capture_document("a", "design.md", "r2", self.db)["document_id"]
        fresh = self.remember(new_doc, detail="New rule after source update.")
        self.assertEqual(fresh["confidence"], "suspected")
        self.assertTrue(memory.recall("a", "rollbacktoken", self.db)["findings"])

    def test_missing_source_is_visible(self):
        doc = self.capture()
        self.remember(doc)
        (self.a / "design.md").unlink()
        out = memory.recall("a", "rollbacktoken", self.db)
        self.assertEqual(out["findings"][0]["source_state"], "missing_or_unreadable")

    def test_supersede_is_atomic_and_historical_only(self):
        doc = self.capture()
        old = self.remember(doc)
        new = self.remember(doc, title="rollbacktoken revised rule")
        memory.supersede("a", old["id"], new["id"], "new verification", self.db)
        ids = [f["id"] for f in memory.recall("a", "rollbacktoken", self.db)["findings"]]
        self.assertNotIn(old["id"], ids)
        full = memory.get_memory(old["id"], "a", self.db)
        self.assertEqual(full["status"], "superseded")
        self.assertTrue(any(e["relation"] == "supersedes" for e in full["edges"]))
        self.assertIn("error", self.remember(doc))
        with self.assertRaises(ValueError):
            memory.supersede("a", new["id"], old["id"], "cycle", self.db)

    def test_capture_idempotent_and_get_complete_paginated(self):
        doc = self.capture("# Long\n" + "0123456789\n" * 2000)
        self.assertEqual(memory.capture_document("a", "design.md", "r1", self.db)["action"], "unchanged")
        text = ""
        offset = 0
        while offset is not None:
            page = memory.get_memory(doc, "a", self.db, offset, 333)
            text += page["content"]
            offset = page["next_offset"]
        self.assertEqual(text, (self.a / "design.md").read_text())

    def test_source_scope_and_project_registration(self):
        self.capture()
        with self.assertRaises(ValueError):
            memory.capture_document("b", "../a/design.md", "", self.db)
        (self.b / "link.md").symlink_to(self.a / "design.md")
        with self.assertRaises(ValueError):
            memory.capture_document("b", "link.md", "", self.db)
        with self.assertRaises(ValueError):
            memory.register_project("a", str(self.b), self.db)
        with self.assertRaises(ValueError):
            memory.recall("unknown", "token", self.db)
        with self.assertRaises(ValueError):
            memory.register_project("relative", "", self.db)

    def test_exact_symbol_recall_when_title_does_not_contain_symbol(self):
        doc = self.capture()
        f = self.remember(doc, title="Firmware activation order", symbols=["ExactSymbol::Install"])
        out = memory.recall("a", "ExactSymbol::Install", self.db)
        self.assertIn(f["id"], [x["id"] for x in out["findings"]])

    def test_edit_time_is_not_validation_time(self):
        row = {"updated_at": "2099-01-01", "last_confirmed": None, "evidence_count": 0}
        self.assertEqual(fs._lifecycle_boost(row), 0)

    def parse(self, text, status=""):
        p = self.a / "legacy.md"
        p.write_text(text)
        nodes = []
        edges = parser.EdgeSet()
        parser.parse_doc(str(p), "legacy.md", nodes, edges, {}, [])
        if status:
            nodes[0].status = status
        return nodes, edges.to_list()

    def test_legacy_fulltext_and_table_knowledge(self):
        nodes, edges = self.parse("# Legacy\n\nFirst paragraph.\n\nsecondparagraphkey\n\n## Table\n| x | y |\n|---|---|\n| tableonlykey | yes |\n")
        parser.write_sqlite(nodes, edges, self.db)
        for token in ("secondparagraphkey", "tableonlykey"):
            out = query.doc_graph_search(token, bridge_to_code=False, db_path=self.db)
            self.assertGreater(out["docs_total"], 0)
        self.assertTrue(any(n.title == "Table" and n.content for n in nodes))

    def test_archive_excluded_from_search_and_graph_expansion(self):
        nodes, edges = self.parse("# archivedtoken\n\nOld behavior", "archived")
        parser.write_sqlite(nodes, edges, self.db)
        self.assertEqual(query.doc_graph_search("archivedtoken", db_path=self.db)["docs_total"], 0)
        out = query.doc_graph_search("archivedtoken", db_path=self.db, include_history=True)
        self.assertEqual(out["docs_total"], 1)
        self.capture("---\nstatus: archived\n---\n# archivedmemorytoken\n\nOld rule")
        self.assertFalse(memory.recall("a", "archivedmemorytoken", self.db)["documents"])
        self.assertTrue(memory.recall("a", "archivedmemorytoken", self.db, include_history=True)["documents"])

    def test_rebuild_preserves_online_memory_and_inode(self):
        doc = self.capture()
        finding = self.remember(doc)
        inode = Path(self.db).stat().st_ino
        nodes, edges = self.parse("# Legacy\n\nDocumentation")
        parser.write_sqlite(nodes, edges, self.db)
        self.assertEqual(Path(self.db).stat().st_ino, inode)
        self.assertEqual(memory.get_memory(finding["id"], "a", self.db)["id"], finding["id"])
        self.assertEqual(memory.get_memory(doc, "a", self.db)["id"], doc)

    def test_failed_rebuild_rolls_back_index_and_keeps_memory(self):
        doc = self.capture()
        nodes, edges = self.parse("# oldindexkey\n\nOld index")
        parser.write_sqlite(nodes, edges, self.db)
        bad = parser.Node(id=None, type=None)
        with self.assertRaises(sqlite3.IntegrityError):
            parser.write_sqlite([bad], [], self.db)
        self.assertGreater(query.doc_graph_search("oldindexkey", db_path=self.db)["docs_total"], 0)
        self.assertEqual(memory.get_memory(doc, "a", self.db)["id"], doc)

    def test_concurrent_record_deduplicates_same_project_title(self):
        def record(i):
            return fs.record_finding("parallel", "same result", project_id="a", db_path=self.db)["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(record, range(12)))
        self.assertEqual(len(set(ids)), 1)

    def test_writer_waits_for_rebuild_transaction_without_loss(self):
        doc = self.capture()
        nodes, edges = self.parse("# Concurrent\n\nIndex")
        entered = threading.Event()
        release = threading.Event()
        real_connect = parser.sqlite3.connect
        class PausingConnection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                return super().cursor(factory=PausingCursor)
        class PausingCursor(sqlite3.Cursor):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if sql == "BEGIN IMMEDIATE":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test did not release rebuild")
                return result
        def rebuild():
            with patch.object(parser.sqlite3, "connect", side_effect=lambda *a, **kw: real_connect(*a, factory=PausingConnection, **kw)):
                parser.write_sqlite(nodes, edges, self.db)
        # Patching sqlite3.connect would also affect the writer; the write is
        # queued on a pre-opened connection to model an existing HTTP request.
        writer = fs._get_conn(self.db)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(rebuild)
            self.assertTrue(entered.wait(5))
            release.set()
            writer.execute("INSERT INTO memory_edge VALUES(?,?,?,?,?)", ("concurrent", doc, "relates_to", "writer", "now"))
            writer.commit()
            pending.result(5)
        writer.close()
        with memory.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_edge WHERE src='concurrent'").fetchone()[0], 1)

    def test_qualified_code_bridge_avoids_shortname_distractors(self):
        code = str(self.root / "cpp.db")
        with sqlite3.connect(code) as conn:
            conn.executescript("CREATE TABLE node(id INTEGER,type TEXT,name TEXT,namespace TEXT,file_path TEXT,parent_class TEXT); CREATE TABLE edge(from_id INTEGER,to_id INTEGER,relation_type TEXT);")
            for i in range(6):
                conn.execute("INSERT INTO node VALUES(?,?,?,?,?,?)", (i,"function","Verify",f"Other{i}","other.cpp",f"Other{i}"))
            conn.execute("INSERT INTO node VALUES(?,?,?,?,?,?)", (9,"function","Verify","Target","target.cpp","Target"))
        rows = query.bridge_symbol_to_cppsg("Target::Verify", code)
        self.assertEqual([r["parent_class"] for r in rows], ["Target"])
        self.assertEqual(query.bridge_symbol_to_cppsg("Absent::Verify", code), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
