#!/usr/bin/env python3
"""生成文档知识图谱可视化 HTML（纯 Canvas + 原生 JS，零外部依赖）"""

import json
import math
import os
import html as html_mod

JSON_PATH = os.path.join(os.path.dirname(__file__), "doc_graph_data.json")
OUT_HTML = os.path.join(os.path.dirname(__file__), "doc_graph_viz.html")

DOC_TYPE_COLORS = {
    "diary":       {"bg": "#3b82f6", "label": "日记"},
    "task":        {"bg": "#22c55e", "label": "任务"},
    "review":      {"bg": "#f97316", "label": "审查"},
    "design":      {"bg": "#a855f7", "label": "设计"},
    "link":        {"bg": "#14b8a6", "label": "链路"},
    "requirement": {"bg": "#ec4899", "label": "需求"},
    "report":      {"bg": "#6b7280", "label": "报告"},
    "doc":         {"bg": "#9ca3af", "label": "其他"},
}

EDGE_COLORS = {
    "has_knowledge":   "#9ca3af",
    "relates_to":      "#3b82f6",
    "mentions_symbol": "#22c55e",
}

EDGE_LABELS = {
    "has_knowledge": "文档→知识点",
    "relates_to": "文档间关联",
    "mentions_symbol": "符号引用",
}


def generate():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data["nodes"]
    edges = data["edges"]

    from collections import Counter
    node_types = Counter(n["type"] for n in nodes)
    doc_types = Counter(n.get("doc_type", "") for n in nodes)
    edge_types = Counter(e["rel"] for e in edges)

    # ── 从 mentions_symbol 边自动创建 symbol 节点 ──
    existing_ids = set(n["id"] for n in nodes)
    symbol_nodes = []
    symbol_ref_count = {}  # symbol_id → 被引用次数
    for e in edges:
        if e["rel"] == "mentions_symbol" and e["dst"] not in existing_ids:
            sid = e["dst"]
            symbol_ref_count[sid] = symbol_ref_count.get(sid, 0) + 1
            if sid not in [s["id"] for s in symbol_nodes]:
                # 从 symbol:ClassName::method 提取可读名称
                name = sid.replace("symbol:", "")
                symbol_nodes.append({
                    "id": sid,
                    "type": "symbol",
                    "doc_type": "symbol",
                    "title": name,
                    "path": "",
                    "status": "",
                    "date": "",
                    "tags": [],
                    "legacy": 0,
                    "ref_count": symbol_ref_count[sid],
                })
        elif e["rel"] == "mentions_symbol":
            # 已存在的 symbol 节点也要计数
            sid = e["dst"]
            symbol_ref_count[sid] = symbol_ref_count.get(sid, 0) + 1

    # 更新已有 symbol 节点的 ref_count
    for n in nodes:
        if n.get("type") == "symbol":
            n["ref_count"] = symbol_ref_count.get(n["id"], 0)

    all_nodes = nodes + symbol_nodes
    symbol_count = len(symbol_nodes)

    # 为 JS 准备数据
    js_nodes = []
    for n in all_nodes:
        js_nodes.append({
            "id": n["id"],
            "type": n["type"],
            "doc_type": n.get("doc_type", "doc"),
            "title": n.get("title", n["id"]),
            "path": n.get("path", ""),
            "status": n.get("status", ""),
            "date": n.get("date", ""),
            "tags": n.get("tags", []),
            "legacy": n.get("legacy", 0),
            "ref_count": n.get("ref_count", 0),
        })

    js_edges = []
    for e in edges:
        js_edges.append({
            "src": e["src"],
            "dst": e["dst"],
            "rel": e["rel"],
        })

    # 统计信息
    stats = (f"节点: <b>{len(all_nodes)}</b> | "
             f"边: <b>{len(edges)}</b> | "
             f"文档: <b>{node_types.get('document', 0)}</b> | "
             f"知识点: <b>{node_types.get('knowledge', 0)}</b> | "
             f"符号: <b>{symbol_count}</b>")

    # 图例 - 边
    legend_edges = ""
    for et, color in EDGE_COLORS.items():
        cnt = edge_types.get(et, 0)
        legend_edges += (f'<label class="legend-item">'
                         f'<input type="checkbox" id="filter-{et}" checked>'
                         f'<span class="legend-line" style="background:{color}"></span>'
                         f'{EDGE_LABELS[et]} ({cnt})</label>\n')

    # 图例 - 节点（文档类型）
    legend_nodes = ""
    for dt, c in DOC_TYPE_COLORS.items():
        cnt = doc_types.get(dt, 0)
        if cnt > 0:
            legend_nodes += (f'<label class="legend-item">'
                             f'<input type="checkbox" class="filter-doctype" value="{dt}" checked>'
                             f'<span class="legend-dot" style="background:{c["bg"]}"></span>'
                             f'{c["label"]} ({cnt})</label>\n')

    # 知识点 + 符号 的开关
    legend_extras = (
        '<label class="legend-item">'
        '<input type="checkbox" id="show-knowledge" checked>'
        '<span class="legend-dot" style="background:#9ca3af; width:8px; height:8px; border-radius:50%;"></span>'
        f'知识点 ({node_types.get("knowledge", 0)})</label>\n'
        '<label class="legend-item">'
        '<input type="checkbox" id="show-symbol" checked>'
        '<span class="legend-dot" style="background:#22c55e; width:8px; height:8px; border-radius:50%;"></span>'
        f'代码符号 ({symbol_count})</label>\n'
    )

    js_nodes_str = json.dumps(js_nodes, ensure_ascii=False)
    js_edges_str = json.dumps(js_edges, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>文档知识图谱可视化</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f3f4f6; overflow: hidden; }}
#header {{ background: #1f2937; color: white; padding: 10px 20px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}
#header h1 {{ font-size: 16px; font-weight: 600; white-space: nowrap; }}
.stats {{ font-size: 13px; color: #9ca3af; }}
#main {{ display: flex; height: calc(100vh - 48px); }}
#sidebar {{ width: 250px; background: white; border-right: 1px solid #e5e7eb; padding: 12px; overflow-y: auto; flex-shrink: 0; }}
#sidebar h3 {{ font-size: 12px; color: #6b7280; text-transform: uppercase; margin-bottom: 6px; margin-top: 14px; }}
#sidebar h3:first-child {{ margin-top: 0; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 3px; color: #4b5563; cursor: pointer; }}
.legend-item input {{ width: 14px; height: 14px; }}
.legend-dot {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; flex-shrink: 0; }}
.legend-line {{ width: 18px; height: 3px; display: inline-block; flex-shrink: 0; }}
.btn {{ display: inline-block; padding: 5px 10px; border: 1px solid #d1d5db; border-radius: 5px; background: white; font-size: 11px; cursor: pointer; margin-right: 4px; margin-top: 6px; }}
.btn:hover {{ background: #f9fafb; }}
.btn-primary {{ background: #3b82f6; color: white; border-color: #3b82f6; }}
.btn-primary:hover {{ background: #2563eb; }}
#canvas-container {{ flex: 1; position: relative; overflow: hidden; }}
canvas {{ display: block; cursor: grab; }}
canvas:active {{ cursor: grabbing; }}
#detail-panel {{ position: absolute; bottom: 12px; right: 12px; width: 340px; max-height: 320px; background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; overflow-y: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: none; z-index: 10; }}
#detail-panel h4 {{ font-size: 13px; margin-bottom: 8px; color: #1f2937; }}
#detail-panel .detail-row {{ font-size: 11px; margin-bottom: 3px; color: #4b5563; word-break: break-all; }}
#detail-panel .detail-key {{ font-weight: 600; color: #6b7280; }}
#detail-panel .close-btn {{ position: absolute; top: 6px; right: 10px; cursor: pointer; color: #9ca3af; font-size: 16px; }}
#loading {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); color: #6b7280; font-size: 14px; z-index: 5; }}
#search-box {{ width: 100%; padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 12px; margin-bottom: 6px; }}
</style>
</head>
<body>
<div id="header">
  <h1>📊 文档知识图谱</h1>
  <div class="stats">{stats}</div>
</div>
<div id="main">
  <div id="sidebar">
    <input type="text" id="search-box" placeholder="🔍 搜索节点..." oninput="onSearch(this.value)">
    <h3>边类型</h3>
    {legend_edges}
    <h3>文档类型</h3>
    {legend_nodes}
    <h3>其他节点</h3>
    {legend_extras}
    <h3>操作</h3>
    <button class="btn btn-primary" onclick="fitView()">适应窗口</button>
    <button class="btn" onclick="onlyDocs()">仅看文档</button>
    <button class="btn" onclick="togglePhysics()">停止物理</button>
  </div>
  <div id="canvas-container">
    <div id="loading">正在计算布局...</div>
    <canvas id="canvas"></canvas>
    <div id="detail-panel">
      <span class="close-btn" onclick="closeDetail()">×</span>
      <h4 id="detail-title"></h4>
      <div id="detail-content"></div>
    </div>
  </div>
</div>
<script>
const ALL_NODES = {js_nodes_str};
const ALL_EDGES = {js_edges_str};
const COLORS = {json.dumps(DOC_TYPE_COLORS, ensure_ascii=False)};
const EDGE_COLORS = {json.dumps(EDGE_COLORS, ensure_ascii=False)};

// ─── 状态 ───
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const container = document.getElementById('canvas-container');
let W = 0, H = 0;
let scale = 1, offsetX = 0, offsetY = 0;
let isDragging = false, isPanning = false;
let dragNode = null;
let lastMouseX = 0, lastMouseY = 0;
let physicsRunning = true;
let hoveredNode = null;
let selectedNode = null;
let searchTerm = '';

// 节点位置（力导向布局）
const positions = new Map();
const velocities = new Map();
const nodeMap = new Map(ALL_NODES.map(n => [n.id, n]));

// 邻接表（双向）
const adj = new Map();
for (const e of ALL_EDGES) {{
  if (!adj.has(e.src)) adj.set(e.src, []);
  adj.get(e.src).push(e.dst);
  if (!adj.has(e.dst)) adj.set(e.dst, []);
  adj.get(e.dst).push(e.src);
}}

// ─── 初始化位置 ───
function initPositions() {{
  const docs = ALL_NODES.filter(n => n.type === 'document');
  const knows = ALL_NODES.filter(n => n.type === 'knowledge');
  const syms = ALL_NODES.filter(n => n.type === 'symbol');
  const cx = W / 2, cy = H / 2;

  // 文档：大圆
  for (let i = 0; i < docs.length; i++) {{
    const angle = (i / docs.length) * Math.PI * 2;
    const r = Math.min(W, H) * 0.35;
    positions.set(docs[i].id, {{x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r}});
  }}
  // 知识点：在父文档附近
  for (const n of knows) {{
    const parent = adj.get(n.id);
    if (parent && parent.length > 0) {{
      const p = positions.get(parent[0]);
      if (p) {{
        const a = Math.random() * Math.PI * 2;
        const r = 25 + Math.random() * 40;
        positions.set(n.id, {{x: p.x + Math.cos(a) * r, y: p.y + Math.sin(a) * r}});
      }} else {{
        positions.set(n.id, {{x: cx + (Math.random()-0.5)*300, y: cy + (Math.random()-0.5)*300}});
      }}
    }} else {{
      positions.set(n.id, {{x: cx + (Math.random()-0.5)*400, y: cy + (Math.random()-0.5)*400}});
    }}
  }}
  // 符号：在引用它们的文档附近（外圈）
  for (const n of syms) {{
    const parents = adj.get(n.id) || [];
    if (parents.length > 0) {{
      // 取第一个父文档位置
      const p = positions.get(parents[0]);
      if (p) {{
        const a = Math.random() * Math.PI * 2;
        const r = 40 + Math.random() * 60;
        positions.set(n.id, {{x: p.x + Math.cos(a) * r, y: p.y + Math.sin(a) * r}});
      }} else {{
        positions.set(n.id, {{x: cx + (Math.random()-0.5)*500, y: cy + (Math.random()-0.5)*500}});
      }}
    }} else {{
      positions.set(n.id, {{x: cx + (Math.random()-0.5)*500, y: cy + (Math.random()-0.5)*500}});
    }}
  }}
  for (const n of ALL_NODES) {{
    velocities.set(n.id, {{x: 0, y: 0}});
  }}
}}

// ─── 力导向布局 ───
let physicsIterations = 0;
const MAX_PHYSICS_ITERS = 300;

function physicsStep() {{
  if (!physicsRunning || physicsIterations >= MAX_PHYSICS_ITERS) return;
  physicsIterations++;

  const visibleNodes = getVisibleNodes();
  const nodeIds = new Set(visibleNodes.map(n => n.id));

  // 斥力（节点间）
  const N = visibleNodes.length;
  for (let i = 0; i < N; i++) {{
    const a = visibleNodes[i];
    const pa = positions.get(a.id);
    const va = velocities.get(a.id);
    if (!pa || !va) continue;

    for (let j = i+1; j < N; j++) {{
      const b = visibleNodes[j];
      const pb = positions.get(b.id);
      const vb = velocities.get(b.id);
      if (!pb || !vb) continue;

      let dx = pa.x - pb.x;
      let dy = pa.y - pb.y;
      let dist = Math.sqrt(dx*dx + dy*dy);
      if (dist < 1) dist = 1;

      // 文档间斥力大，其他小
      let force = 6000;
      if (a.type === 'document' && b.type === 'document') force = 12000;
      else if (a.type === 'symbol' || b.type === 'symbol') force = 1500;
      const f = force / (dist * dist);
      va.x += (dx/dist) * f;
      va.y += (dy/dist) * f;
      vb.x -= (dx/dist) * f;
      vb.y -= (dy/dist) * f;
    }}
  }}

  // 引力（边）
  for (const e of ALL_EDGES) {{
    if (!nodeIds.has(e.src) || !nodeIds.has(e.dst)) continue;
    const pa = positions.get(e.src);
    const pb = positions.get(e.dst);
    const va = velocities.get(e.src);
    const vb = velocities.get(e.dst);
    if (!pa || !pb || !va || !vb) continue;

    let dx = pb.x - pa.x;
    let dy = pb.y - pa.y;
    let dist = Math.sqrt(dx*dx + dy*dy);
    if (dist < 1) dist = 1;

    const springLen = e.rel === 'has_knowledge' ? 35 : (e.rel === 'relates_to' ? 180 : 60);
    const k = e.rel === 'has_knowledge' ? 0.06 : (e.rel === 'relates_to' ? 0.015 : 0.02);
    const force = k * (dist - springLen);
    va.x += (dx/dist) * force;
    va.y += (dy/dist) * force;
    vb.x -= (dx/dist) * force;
    vb.y -= (dy/dist) * force;
  }}

  // 中心引力
  const cx = W/2, cy = H/2;
  for (const n of visibleNodes) {{
    const p = positions.get(n.id);
    const v = velocities.get(n.id);
    if (!p || !v) continue;
    const centerForce = n.type === 'document' ? 0.002 : 0.0005;
    v.x += (cx - p.x) * centerForce;
    v.y += (cy - p.y) * centerForce;
  }}

  // 更新位置 + 阻尼
  for (const n of visibleNodes) {{
    const p = positions.get(n.id);
    const v = velocities.get(n.id);
    if (!p || !v) continue;
    v.x *= 0.82;
    v.y *= 0.82;
    p.x += Math.max(-20, Math.min(20, v.x));
    p.y += Math.max(-20, Math.min(20, v.y));
  }}

  if (physicsIterations % 30 === 0) {{
    document.getElementById('loading').textContent = `布局计算中... ${{Math.round(physicsIterations/MAX_PHYSICS_ITERS*100)}}%`;
  }}
  if (physicsIterations >= MAX_PHYSICS_ITERS) {{
    document.getElementById('loading').style.display = 'none';
    physicsRunning = false;
  }}
}}

// ─── 过滤 ───
function getVisibleNodes() {{
  const docTypes = new Set();
  document.querySelectorAll('.filter-doctype').forEach(cb => {{
    if (cb.checked) docTypes.add(cb.value);
  }});
  const showKnowledge = document.getElementById('show-knowledge') ? document.getElementById('show-knowledge').checked : true;
  const showSymbol = document.getElementById('show-symbol') ? document.getElementById('show-symbol').checked : true;

  return ALL_NODES.filter(n => {{
    // 搜索过滤
    if (searchTerm) {{
      const t = (n.title + ' ' + n.id + ' ' + (n.tags||[]).join(' ')).toLowerCase();
      if (!t.includes(searchTerm.toLowerCase())) return false;
    }}
    // 按类型过滤
    if (n.type === 'document') return docTypes.has(n.doc_type);
    if (n.type === 'knowledge') return showKnowledge;
    if (n.type === 'symbol') return showSymbol;
    return false;
  }});
}}

function getVisibleEdges() {{
  const filters = {{
    'relates_to': document.getElementById('filter-relates_to').checked,
    'has_knowledge': document.getElementById('filter-has_knowledge').checked,
    'mentions_symbol': document.getElementById('filter-mentions_symbol').checked,
  }};
  const nodeIds = new Set(getVisibleNodes().map(n => n.id));
  return ALL_EDGES.filter(e => filters[e.rel] && nodeIds.has(e.src) && nodeIds.has(e.dst));
}}

// ─── 渲染 ───
function render() {{
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(offsetX, offsetY);
  ctx.scale(scale, scale);

  const visibleNodes = getVisibleNodes();
  const visibleEdges = getVisibleEdges();
  const nodeIds = new Set(visibleNodes.map(n => n.id));

  // 高亮集合（选中节点的邻居）
  const highlightIds = new Set();
  if (selectedNode) {{
    highlightIds.add(selectedNode);
    for (const e of ALL_EDGES) {{
      if (e.src === selectedNode && nodeIds.has(e.dst)) highlightIds.add(e.dst);
      if (e.dst === selectedNode && nodeIds.has(e.src)) highlightIds.add(e.src);
    }}
  }}

  // 画边
  for (const e of visibleEdges) {{
    const pa = positions.get(e.src);
    const pb = positions.get(e.dst);
    if (!pa || !pb) continue;

    const isHighlighted = selectedNode && (e.src === selectedNode || e.dst === selectedNode);
    const dim = selectedNode && !isHighlighted;

    ctx.strokeStyle = EDGE_COLORS[e.rel] || '#ddd';
    if (dim) {{
      ctx.globalAlpha = 0.03;
    }} else if (isHighlighted) {{
      ctx.globalAlpha = 0.8;
    }} else {{
      ctx.globalAlpha = e.rel === 'has_knowledge' ? 0.35 : (e.rel === 'relates_to' ? 0.6 : 0.3);
    }}
    ctx.lineWidth = (e.rel === 'relates_to' ? 1.5 : (e.rel === 'mentions_symbol' ? 1.0 : 0.8)) / scale;
    ctx.beginPath();
    ctx.moveTo(pa.x, pa.y);
    ctx.lineTo(pb.x, pb.y);
    ctx.stroke();

    // 箭头（仅 relates_to）
    if (e.rel === 'relates_to' && (!dim || isHighlighted)) {{
      const angle = Math.atan2(pb.y - pa.y, pb.x - pa.x);
      const arrowSize = 6 / scale;
      ctx.globalAlpha = dim ? 0.1 : 0.7;
      ctx.beginPath();
      ctx.moveTo(pb.x, pb.y);
      ctx.lineTo(pb.x - Math.cos(angle - 0.4) * arrowSize, pb.y - Math.sin(angle - 0.4) * arrowSize);
      ctx.lineTo(pb.x - Math.cos(angle + 0.4) * arrowSize, pb.y - Math.sin(angle + 0.4) * arrowSize);
      ctx.closePath();
      ctx.fillStyle = EDGE_COLORS[e.rel];
      ctx.fill();
    }}
  }}

  // 画节点
  ctx.globalAlpha = 1;
  for (const n of visibleNodes) {{
    const p = positions.get(n.id);
    if (!p) continue;

    const c = COLORS[n.doc_type] || {{bg: '#9ca3af'}};
    const isDoc = n.type === 'document';
    const isKnow = n.type === 'knowledge';
    const isSym = n.type === 'symbol';
    const isHovered = hoveredNode === n.id;
    const isSelected = selectedNode === n.id;
    const isHighlighted = highlightIds.has(n.id);
    const dim = selectedNode && !isHighlighted;

    // 节点大小
    let radius;
    if (isDoc) radius = 6;
    else if (isSym) radius = Math.min(5, 2 + (n.ref_count || 1) * 0.3);
    else radius = 2.5;

    // 光晕
    if (isHovered || isSelected) {{
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius + 5, 0, Math.PI * 2);
      ctx.fillStyle = (c.bg || '#9ca3af') + '40';
      ctx.fill();
    }}

    // 节点圆
    ctx.globalAlpha = dim ? 0.15 : 1;
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    if (isDoc) {{
      ctx.fillStyle = c.bg;
    }} else if (isSym) {{
      ctx.fillStyle = '#22c55e';
    }} else {{
      ctx.fillStyle = '#9ca3af';
    }}
    ctx.fill();
    ctx.strokeStyle = isSelected ? '#000' : '#ffffff60';
    ctx.lineWidth = isSelected ? 2 : 0.5;
    ctx.stroke();
    ctx.globalAlpha = 1;

    // 标签
    if (isDoc) {{
      const label = n.title.length > 25 ? n.title.substring(0, 25) + '…' : n.title;
      ctx.font = `${{dim ? 9 : 10}}px -apple-system, sans-serif`;
      ctx.fillStyle = dim ? '#9ca3af' : '#1f2937';
      ctx.textAlign = 'center';
      ctx.fillText(label, p.x, p.y - radius - 4);
    }} else if (isHovered || isSelected || isHighlighted) {{
      const label = n.title.length > 30 ? n.title.substring(0, 30) + '…' : n.title;
      ctx.font = '9px sans-serif';
      ctx.fillStyle = '#4b5563';
      ctx.textAlign = 'center';
      ctx.fillText(label, p.x, p.y - radius - 3);
    }}
  }}

  ctx.restore();
}}

// ─── 动画循环 ───
function animate() {{
  if (physicsRunning) {{
    for (let i = 0; i < 3; i++) physicsStep();
  }}
  render();
  requestAnimationFrame(animate);
}}

// ─── 交互 ───
function resize() {{
  W = container.clientWidth;
  H = container.clientHeight;
  canvas.width = W;
  canvas.height = H;
}}

function screenToWorld(sx, sy) {{
  return {{x: (sx - offsetX) / scale, y: (sy - offsetY) / scale}};
}}

function findNodeAt(sx, sy) {{
  const w = screenToWorld(sx, sy);
  const visibleNodes = getVisibleNodes();
  for (let i = visibleNodes.length - 1; i >= 0; i--) {{
    const n = visibleNodes[i];
    const p = positions.get(n.id);
    if (!p) continue;
    const r = n.type === 'document' ? 9 : (n.type === 'symbol' ? 7 : 5);
    const dx = w.x - p.x;
    const dy = w.y - p.y;
    if (dx*dx + dy*dy < r*r) return n;
  }}
  return null;
}}

canvas.addEventListener('mousedown', (e) => {{
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const node = findNodeAt(mx, my);

  if (node) {{
    dragNode = node.id;
    selectedNode = node.id;
    showDetail(node);
  }} else {{
    isPanning = true;
    selectedNode = null;
    closeDetail();
  }}
  lastMouseX = mx;
  lastMouseY = my;
}});

canvas.addEventListener('mousemove', (e) => {{
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;

  if (dragNode) {{
    const w = screenToWorld(mx, my);
    const p = positions.get(dragNode);
    if (p) {{ p.x = w.x; p.y = w.y; }}
    velocities.set(dragNode, {{x: 0, y: 0}});
  }} else if (isPanning) {{
    offsetX += mx - lastMouseX;
    offsetY += my - lastMouseY;
  }} else {{
    const node = findNodeAt(mx, my);
    const newHover = node ? node.id : null;
    if (newHover !== hoveredNode) {{
      hoveredNode = newHover;
      canvas.style.cursor = node ? 'pointer' : 'grab';
    }}
  }}
  lastMouseX = mx;
  lastMouseY = my;
}});

canvas.addEventListener('mouseup', () => {{
  dragNode = null;
  isPanning = false;
}});

canvas.addEventListener('wheel', (e) => {{
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 0.87;
  const newScale = Math.max(0.05, Math.min(8, scale * factor));
  offsetX = mx - (mx - offsetX) * (newScale / scale);
  offsetY = my - (my - offsetY) * (newScale / scale);
  scale = newScale;
}}, {{passive: false}});

// ─── 搜索 ───
function onSearch(val) {{
  searchTerm = val.trim();
  if (searchTerm && physicsIterations >= MAX_PHYSICS_ITERS) {{
    // 搜索时自动适应到结果
    render();
  }}
}}

// ─── 详情面板 ───
function showDetail(node) {{
  const panel = document.getElementById('detail-panel');
  const title = document.getElementById('detail-title');
  const content = document.getElementById('detail-content');

  title.textContent = node.title;

  const typeLabel = {{'document': '文档', 'knowledge': '知识点', 'symbol': '代码符号'}}[node.type] || node.type;
  const fields = [
    ['ID', node.id],
    ['类型', typeLabel],
  ];
  if (node.type === 'document') {{
    fields.push(['分类', node.doc_type]);
    fields.push(['路径', node.path]);
    if (node.status) fields.push(['状态', node.status]);
    if (node.date) fields.push(['日期', node.date]);
    if (node.tags && node.tags.length) fields.push(['标签', node.tags.join(', ')]);
  }}
  if (node.type === 'symbol' && node.ref_count) {{
    fields.push(['引用次数', String(node.ref_count)]);
  }}

  // 关联边
  const related = ALL_EDGES.filter(e => e.src === node.id || e.dst === node.id);
  fields.push(['关联边数', String(related.length)]);

  // 按边类型分类统计
  const byRel = {{}};
  for (const e of related) {{
    byRel[e.rel] = (byRel[e.rel] || 0) + 1;
  }}
  const relNames = {{'relates_to': '文档关联', 'has_knowledge': '知识点', 'mentions_symbol': '符号引用'}};
  for (const [rel, cnt] of Object.entries(byRel)) {{
    fields.push(['  ↳ ' + (relNames[rel] || rel), String(cnt)]);
  }}

  // 关联节点
  const relatedDocs = new Set();
  const relatedSyms = new Set();
  for (const e of related) {{
    const other = e.src === node.id ? e.dst : e.src;
    const n = nodeMap.get(other);
    if (!n) continue;
    if (n.type === 'document') relatedDocs.add(n.title);
    else if (n.type === 'symbol') relatedSyms.add(n.title);
  }}
  if (relatedDocs.size > 0) {{
    const arr = Array.from(relatedDocs);
    fields.push(['关联文档', arr.slice(0,5).join(', ') + (arr.length > 5 ? ` ...(+${{arr.length-5}})` : '')]);
  }}
  if (relatedSyms.size > 0) {{
    const arr = Array.from(relatedSyms);
    fields.push(['关联符号', arr.slice(0,5).join(', ') + (arr.length > 5 ? ` ...(+${{arr.length-5}})` : '')]);
  }}

  content.innerHTML = fields.map(([k, v]) =>
    `<div class="detail-row"><span class="detail-key">${{k}}:</span> ${{v}}</div>`
  ).join('');

  panel.style.display = 'block';
}}

function closeDetail() {{
  document.getElementById('detail-panel').style.display = 'none';
  selectedNode = null;
}}

// ─── 按钮 ───
function fitView() {{
  const visibleNodes = getVisibleNodes();
  if (visibleNodes.length === 0) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of visibleNodes) {{
    const p = positions.get(n.id);
    if (!p) continue;
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }}
  const padding = 50;
  const tw = maxX - minX + padding * 2;
  const th = maxY - minY + padding * 2;
  scale = Math.min(W / tw, H / th, 2);
  offsetX = W/2 - (minX + maxX)/2 * scale;
  offsetY = H/2 - (minY + maxY)/2 * scale;
}}

function onlyDocs() {{
  document.getElementById('filter-has_knowledge').checked = false;
  document.getElementById('filter-mentions_symbol').checked = false;
  document.getElementById('filter-relates_to').checked = true;
  document.getElementById('show-knowledge').checked = false;
  document.getElementById('show-symbol').checked = false;
}}

function togglePhysics() {{
  physicsRunning = !physicsRunning;
  if (physicsRunning) {{
    physicsIterations = 0;
    document.getElementById('loading').style.display = 'block';
    document.getElementById('loading').textContent = '正在计算布局...';
  }}
}}

// 过滤变化时重新渲染
document.querySelectorAll('input[type="checkbox"]').forEach(cb => {{
  cb.addEventListener('change', () => {{
    if (physicsIterations >= MAX_PHYSICS_ITERS) render();
  }});
}});

// ─── 启动 ───
window.addEventListener('resize', resize);
resize();
initPositions();
animate();
</script>
</body>
</html>"""

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ 生成可视化: {OUT_HTML}")
    print(f"   节点: {len(all_nodes)} (文档 {node_types.get('document',0)} + 知识点 {node_types.get('knowledge',0)} + 符号 {symbol_count})")
    print(f"   边: {len(edges)}")
    print(f"   零外部依赖（纯 Canvas + 原生 JS）")


if __name__ == "__main__":
    generate()
