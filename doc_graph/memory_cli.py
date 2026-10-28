#!/usr/bin/env python3
"""Same durable memory operations for clients awaiting MCP tool reload.

Read a single JSON request from stdin; print a single JSON result. No shell eval.
"""
import json
import os
from pathlib import Path
import sys

import knowledge_memory as memory


def main():
    request = json.load(sys.stdin)
    action = request.pop("action")
    request["db_path"] = os.environ.get("DOC_GRAPH_DB", str(Path(__file__).with_name("doc_graph.db")))
    operations = {"register": memory.register_project, "capture": memory.capture_document,
                  "remember": memory.remember, "recall": memory.recall,
                  "get": memory.get_memory, "supersede": memory.supersede, "status": memory.status}
    if action not in operations:
        raise ValueError("unknown action")
    print(json.dumps(operations[action](**request), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
