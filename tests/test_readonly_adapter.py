#!/usr/bin/env python3
"""Verify real write rejection against a disposable SQLite database."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile

BASE=Path(__file__).resolve().parents[1] / 'scripts'


def main():
    original_connect=sqlite3.connect
    with tempfile.TemporaryDirectory(prefix='cppsg-ro-check-') as temporary:
        root=Path(temporary)
        path=root/'fixture.db'
        with original_connect(path) as conn:
            conn.execute('PRAGMA user_version=5')
            conn.execute('CREATE TABLE specimen(value TEXT)')
            conn.execute("INSERT INTO specimen VALUES ('original')")
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        os.environ['CPP_GRAPH_DB']=str(path)
        os.environ['CPP_GRAPH_TELEMETRY']=str(root/'telemetry.jsonl')
        spec=importlib.util.spec_from_file_location('cppsg_readonly',BASE/'cppsg_readonly.py')
        adapter=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        server=adapter.configure()
        from cpp_semantic_graph.db.graph_db import GraphDB
        db=GraphDB(str(path))
        assert db.conn.execute('SELECT value FROM specimen').fetchone()[0]=='original'
        failures=[]
        for statement in ["INSERT INTO specimen VALUES ('bad')",'CREATE TABLE bad(value TEXT)',
                          'PRAGMA user_version=6']:
            try:db.conn.execute(statement)
            except sqlite3.OperationalError:failures.append(statement)
            else:raise AssertionError('Write was not rejected: '+statement)
        db.close()
        missing=root/'must-not-be-created.db'
        try:sqlite3.connect(missing)
        except FileNotFoundError:pass
        else:raise AssertionError('Missing DB unexpectedly created')
        assert not missing.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
        names={t.name for t in server.mcp._tool_manager.list_tools()}
        assert names==adapter.ALLOWED
        result={'passed':True,'insert_schema_and_version_writes_rejected':len(failures),
                'fixture_unchanged':True,'missing_db_not_created':True,
                'tools_exposed':sorted(names)}
        # The fixture-only test prints its result; it does not write into the checkout.
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
