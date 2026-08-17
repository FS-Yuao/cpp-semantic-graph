#!/usr/bin/env python3
"""查询遥测分析：消费 query_telemetry.jsonl，输出使用分布与空结果排行

用途：定期跑一次，看哪些查询落空（覆盖缺口 / 高频误查 / 参数拼写错误），
为图谱覆盖范围与工具改进提供数据依据。

用法:
  python3 scripts/telemetry_stats.py                 # 默认 DB 同目录 jsonl
  python3 scripts/telemetry_stats.py --file a.jsonl  # 指定文件
  python3 scripts/telemetry_stats.py --top 20        # 空结果 top 20
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def load(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"[SKIP] 遥测文件不存在: {p}")
        return []
    records = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 多进程并发写的偶发残行，跳过
    return records


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * pct / 100), len(sorted_vals) - 1)
    return sorted_vals[idx]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(
        Path(__file__).resolve().parent.parent / "query_telemetry.jsonl"))
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    records = load(args.file)
    if not records:
        print("无遥测数据。")
        return

    ts = [r.get("ts", "") for r in records if r.get("ts")]
    print(f"总计 {len(records)} 次查询  时间范围: {min(ts)} ~ {max(ts)}")
    print()

    # 按工具聚合
    per_tool: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        per_tool[r.get("tool", "?")].append(r)

    print(f"{'工具':<28} {'调用':>5} {'空结果':>6} {'空率':>6} {'均耗时ms':>9} {'P95ms':>8}")
    print("-" * 70)
    empty_args: Counter = Counter()
    for tool in sorted(per_tool, key=lambda t: -len(per_tool[t])):
        rs = per_tool[tool]
        n = len(rs)
        n_empty = sum(1 for r in rs if r.get("n_results") == 0)
        durs = sorted(r.get("duration_ms", 0) for r in rs)
        avg = sum(durs) / n
        print(f"{tool:<28} {n:>5} {n_empty:>6} {n_empty / n:>6.0%} "
              f"{avg:>9.1f} {percentile(durs, 95):>8.1f}")
        for r in rs:
            if r.get("n_results") == 0:
                args_key = json.dumps(r.get("args", {}), ensure_ascii=False)
                empty_args[(tool, args_key)] += 1

    if empty_args:
        print(f"\n空结果 Top {args.top}（覆盖缺口/误查排查线索）:")
        for (tool, argk), cnt in empty_args.most_common(args.top):
            print(f"  {cnt:>3}x {tool} {argk}")
    else:
        print("\n无空结果查询。")


if __name__ == "__main__":
    main()
