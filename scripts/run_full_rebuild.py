"""全量重建 runner：跑 FullParsePipeline 并落 rebuild_report.json

用法: nohup python3 scripts/run_full_rebuild.py > rebuild.log 2>&1 &
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE.parent))

from cpp_semantic_graph.pipeline import FullParsePipeline

t0 = time.time()
pipe = FullParsePipeline(str(HERE / "cpp_semantic_graph.yaml"))
report = pipe.run(str(HERE / "semantic_graph_full.db"), reset_db=True)

data = report.to_dict() if hasattr(report, "to_dict") else {
    k: getattr(report, k) for k in dir(report)
    if not k.startswith("_") and not callable(getattr(report, k))}
data["elapsed"] = time.time() - t0
out = HERE / "rebuild_report.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
print(f"[DONE] {time.strftime('%H:%M:%S')} report -> {out}", flush=True)
