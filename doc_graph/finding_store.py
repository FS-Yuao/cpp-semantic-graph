#!/usr/bin/env python3
"""
经验结论存储层（finding store）

设计依据：tasks/memory_upgrade/01_design.md §4.1-4.2

功能：
  1. finding / finding_symbol / finding_fts 三表 schema（幂等迁移）
  2. record_finding：写入 + 同 title 幂等合并
  3. search_findings：FTS5 关键词 + 符号反查 + 类型过滤
  4. get_findings_by_symbols：符号交集查询（供 query.py 的 doc_graph_search join）
  5. export_findings / import_findings：parser 全量重建时的防丢失通道

与 node/edge 表的关系：完全独立。node/edge/doc_fts 由 parser.py 全量重建管理，
finding 三表由本模块在线写入管理，parser 重建时通过 export/import 保留。
"""

from __future__ import annotations
import os, sys, json, sqlite3, re, hashlib
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(SCRIPT_DIR, "doc_graph.db")
# cppsg 代码图谱：与 query.py 同默认（上级目录 semantic_graph_full.db）
CPPSG_DB = os.environ.get("CPP_GRAPH_DB",
                          os.path.join(SCRIPT_DIR, "..", "semantic_graph_full.db"))

# ftype 合法值（工程语义裁剪自 TencentDB-Agent-Memory 的 L1 atom 分类）
FTYPES = ("fact", "constraint", "decision", "lesson", "risk")
STATUSES = ("active", "stale", "revoked")
CONFIDENCES = ("confirmed", "suspected")

# 中英技术同义表：查询自动扩展（2026-08-18 LLM 路线决策的落地——语义理解分层：
# 调用侧 AI 负责自由改写（工具描述引导），服务端同义表覆盖高频技术口语对照，
# 均为零成本确定性方案；云 LLM API 不引入，因为 MCP 调用方本身就是 LLM）
SYNONYMS = {
    "崩溃": ["crash", "abort", "panic", "崩", "挂", "挂掉", "异常退出"],
    "挂": ["崩溃", "crash", "挂掉"],
    "失败": ["fail", "failure", "错误", "error"],
    "报错": ["错误", "error", "失败", "fail"],
    "超时": ["timeout", "超时时间"],
    "阻塞": ["block", "卡死", "卡住", "hang"],
    "升级": ["upgrade", "update", "刷写", "flash", "更新"],
    "回滚": ["rollback", "回退", "恢复"],
    "重启": ["reboot", "restart", "重新启动"],
    "校验": ["验证", "verify", "check", "检查"],
    "签名": ["signature", "验签"],
    "冲突": ["conflict", "碰撞"],
    "覆盖": ["overwrite", "覆写"],
    "误报": ["false", "正报", "错误告警"],
    "生命周期": ["lifecycle", "状态机", "state"],
    "架构": ["architecture", "设计", "结构"],
}
# 反向索引：值 → 键（"crash" 查询也扩展出 "崩溃"）
_SYNONYM_INDEX: dict[str, list[str]] = {}
for _k, _vs in SYNONYMS.items():
    for _v in _vs:
        _SYNONYM_INDEX.setdefault(_v, []).append(_k)


def expand_synonyms(keyword: str) -> str:
    """关键词同义扩展：命中同义表的词补出对照词（大小写不敏感）。

    只扩展，不替换——原词保底命中，同义词增加召回面。
    """
    extra: list[str] = []
    lowered = (keyword or "").lower()
    for word, alts in {**SYNONYMS, **_SYNONYM_INDEX}.items():
        if word.lower() in lowered:
            for alt in alts:
                if alt.lower() not in lowered:
                    extra.append(alt)
    return extra


def _preprocess_cjk_for_fts(text: str) -> str:
    """FTS 写入预处理：CJK 逐字 + camelCase/snake_case 标识符拆分。

    拆分让子串词可命中（存 "kApplicationErrorMap" 后查 "application" 能找到）。
    查询侧 _fts_tokens 做同样的拆分，保证写入/查询对称。
    """
    text = text or ""
    # snake_case → 空格
    text = text.replace("_", " ")
    # camelCase 边界插入空格（kApplicationErrorMap → k Application Error Map）
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text)
    # CJK 字符间插入空格（unicode61 逐字分词）
    return re.sub(r'([\u4e00-\u9fff])(?=[\u4e00-\u9fff])', r'\1 ', text)


def _ensure_schema(conn: sqlite3.Connection):
    """幂等建表：finding 三表不存在则创建（parser 重建后/老库升级均安全）"""
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS finding (
            id          TEXT PRIMARY KEY,
            ftype       TEXT NOT NULL,
            title       TEXT NOT NULL,
            detail      TEXT DEFAULT '',
            symbols     TEXT DEFAULT '[]',
            source      TEXT DEFAULT '',
            status      TEXT DEFAULT 'active',
            confidence  TEXT DEFAULT 'confirmed',
            tags        TEXT DEFAULT '[]',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS finding_symbol (
            finding_id  TEXT NOT NULL,
            symbol_name TEXT NOT NULL,
            PRIMARY KEY (finding_id, symbol_name)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_fs_symbol ON finding_symbol(symbol_name)")
    try:
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS finding_fts USING fts5(
                finding_id, title, detail, tags,
                tokenize='unicode61'
            )
        """)
    except Exception as e:
        print(f"[finding_store] finding_fts 创建跳过: {e}", file=sys.stderr)
    conn.commit()


def _get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL：降低 MCP 在线写入与 parser 重建的锁冲突
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    _ensure_embedding_column(conn)
    return conn


def _make_id(title: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:4]
    return f"finding:{ts}-{h}"


def _fts_tokens(keyword: str) -> str:
    """关键词 → FTS5 MATCH 表达式（与写入预处理 _preprocess_cjk_for_fts 对称：
    CJK 逐字 + camelCase/snake_case 拆分后 OR 连接）"""
    keyword = (keyword or "").replace("_", " ")
    keyword = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', keyword)
    tokens = re.findall(r'[A-Za-z]+|\d+|[\u4e00-\u9fff]+', keyword)
    parts = []
    for t in tokens:
        if re.match(r'^[\u4e00-\u9fff]+$', t):
            parts.extend(list(t))
        else:
            parts.append(t.lower())
    return " OR ".join(parts)


def record_finding(title: str, detail: str = "", ftype: str = "fact",
                   symbols: list[str] | None = None, source: str = "",
                   tags: list[str] | None = None,
                   confidence: str = "confirmed",
                   db_path: str = DEFAULT_DB) -> dict:
    """写入一条经验结论。同 title 已存在 → 更新（幂等合并，防重复沉淀）。

    Returns:
        {"action": "created"|"updated", "id": ..., "symbols_anchored": n}
    """
    title = (title or "").strip()
    if not title:
        return {"error": "title 不能为空"}
    if ftype not in FTYPES:
        return {"error": f"ftype 非法: {ftype}，合法值: {FTYPES}"}
    if confidence not in CONFIDENCES:
        return {"error": f"confidence 非法: {confidence}，合法值: {CONFIDENCES}"}

    symbols = sorted(set(s for s in (symbols or []) if s and s.strip()))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 向量提前算（created/updated 共用；None=向量功能降级中）
    emb = _embed_one(f"{title}。{detail}" if detail else title)
    conn = _get_conn(db_path)
    try:
        c = conn.cursor()
        existing = c.execute(
            "SELECT id, created_at FROM finding WHERE title = ?", (title,)).fetchone()

        if existing:
            fid, created_at = existing["id"], existing["created_at"]
            c.execute("""
                UPDATE finding SET detail=?, ftype=?, symbols=?, source=?,
                    confidence=?, tags=?, updated_at=?, embedding=?
                WHERE id=?
            """, (detail, ftype, json.dumps(symbols, ensure_ascii=False), source,
                  confidence, json.dumps(tags or [], ensure_ascii=False), now, emb, fid))
            action = "updated"
            # 重建锚定表：先删后插，避免残留已移除的符号
            c.execute("DELETE FROM finding_symbol WHERE finding_id=?", (fid,))
            c.execute("DELETE FROM finding_fts WHERE finding_id=?", (fid,))
        else:
            fid = _make_id(title)
            c.execute("""
                INSERT INTO finding
                (id, ftype, title, detail, symbols, source, status, confidence, tags, created_at, updated_at, embedding)
                VALUES (?,?,?,?,?,?, 'active', ?, ?, ?, ?, ?)
            """, (fid, ftype, title, detail,
                  json.dumps(symbols, ensure_ascii=False), source,
                  confidence, json.dumps(tags or [], ensure_ascii=False),
                  now, now, emb))
            action = "created"

        for sym in symbols:
            c.execute("INSERT OR IGNORE INTO finding_symbol (finding_id, symbol_name) VALUES (?,?)",
                      (fid, sym))

        c.execute("""
            INSERT INTO finding_fts (finding_id, title, detail, tags)
            VALUES (?,?,?,?)
        """, (fid, _preprocess_cjk_for_fts(title), _preprocess_cjk_for_fts(detail),
              _preprocess_cjk_for_fts(json.dumps(tags or [], ensure_ascii=False))))
        conn.commit()
        return {"action": action, "id": fid, "symbols_anchored": len(symbols)}
    finally:
        conn.close()


def _row_to_finding(r: sqlite3.Row) -> dict:
    d = dict(r)
    d.pop("embedding", None)  # BLOB 不外发
    for k in ("symbols", "tags"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (json.JSONDecodeError, TypeError):
            d[k] = []
    return d


def backfill_embeddings(db_path: str = DEFAULT_DB) -> int:
    """给存量无向量的 finding 补算 embedding（升级部署时跑一次）。"""
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, detail FROM finding WHERE embedding IS NULL").fetchall()
        n = 0
        for r in rows:
            emb = _embed_one(f"{r['title']}。{r['detail']}" if r["detail"] else r["title"])
            if emb is not None:
                conn.execute("UPDATE finding SET embedding=? WHERE id=?", (emb, r["id"]))
                n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def search_findings(keyword: str = "", symbol: str = "", ftype: str = "",
                    status: str = "active,stale",
                    limit: int = 10, db_path: str = DEFAULT_DB) -> list[dict]:
    """检索经验结论：关键词（FTS5 词法 + 向量语义 RRF 融合）/ 符号锚定 / 类型 可组合。

    词法路命中字面词（符号名/崩溃），向量路命中同义改述（"程序挂了"→"崩溃"）。
    向量依赖 fastembed，不可用时自动降级为纯词法，不硬失败。
    """
    conn = _get_conn(db_path)
    try:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        ph = ",".join("?" * len(statuses))
        sql = f"SELECT * FROM finding WHERE status IN ({ph})"
        params: list = list(statuses)

        if ftype:
            sql += " AND ftype = ?"
            params.append(ftype)

        if symbol:
            sql += """ AND id IN (
                SELECT finding_id FROM finding_symbol WHERE symbol_name LIKE ?)"""
            params.append(f"%{symbol}%")

        rows = conn.execute(sql, params).fetchall()

        if not keyword:
            rows = sorted(rows, key=lambda r: r["updated_at"] or "", reverse=True)
            return [_row_to_finding(r) for r in rows[:limit]]

        # ── 词法路：finding_fts MATCH 命中 id 集合（含同义表扩展）──
        expanded = " ".join([keyword] + expand_synonyms(keyword))
        match = _fts_tokens(expanded)
        lexical_ids: set[str] = set()
        if match:
            try:
                fts_rows = conn.execute(
                    "SELECT finding_id FROM finding_fts WHERE finding_fts MATCH ?",
                    (match,)).fetchall()
                lexical_ids = {r["finding_id"] for r in fts_rows}
            except Exception as e:
                print(f"[finding_store] 词法路异常（跳过该路）: {e}", file=sys.stderr)

        # ── 向量路：查询向量 vs 候选行 embedding 余弦 ──
        # 实测边界（2026-08-18, bge-small-zh-v1.5）：对"短中文查询 vs 短技术结论"，
        # 绝对相似度与负例校准 margin 均无判别力（无关词 0.457 vs "程序挂了" 0.488，
        # 排序对但无法设阈值分离，详见 04_phase2_plan.md 实测数据）。故向量路定位为
        # 【排序增强】而非独立召回源：仅在词法路有命中时对命中集做语义加权排序；
        # 词法零命中时不召回，避免"最不相关里挑最相关"的垃圾结果。
        # finding 库规模化后换 bge-m3/bge-base 可重新评估独立召回。
        qblob = _embed_one(keyword, mode="query")
        vec_scored: list[tuple[float, str]] = []
        if qblob is not None and lexical_ids:
            for r in rows:
                if r["id"] in lexical_ids and r["embedding"]:
                    sim = _cos_blob(qblob, r["embedding"])
                    vec_scored.append((sim, r["id"]))
            vec_scored.sort(reverse=True)
        vector_ids = [fid for _, fid in vec_scored[:limit * 2]]

        # ── RRF 融合（词法命中为基础，向量增强排序；两路皆空则无结果）──
        if not lexical_ids and not vector_ids:
            return []
        lexical_ranked = [r["id"] for r in
                          sorted((r for r in rows if r["id"] in lexical_ids),
                                 key=lambda r: r["updated_at"] or "", reverse=True)]
        fused = _rrf_merge(lexical_ranked, vector_ids, limit=limit)

        by_id = {r["id"]: r for r in rows}
        return [_row_to_finding(by_id[fid]) for fid in fused if fid in by_id]
    finally:
        conn.close()


def get_findings_by_symbols(symbol_names: list[str],
                            limit_per_symbol: int = 3,
                            db_path: str = DEFAULT_DB) -> list[dict]:
    """按符号名集合批量取锚定的 findings（供 doc_graph_search join 用）。

    匹配规则：finding_symbol.symbol_name 与传入名相等，或互为 ClassName::Method
    的同类前缀（如 finding 锚定 QueryBootChain，BFS 收集到同名符号）。
    """
    if not symbol_names:
        return []
    names = set()
    for s in symbol_names:
        if not s:
            continue
        base = s[7:] if s.startswith("symbol:") else s
        names.add(base)
        names.add(base.split("::")[0])  # ClassName::Method → 也匹配 ClassName 锚定
    if not names:
        return []

    conn = _get_conn(db_path)
    try:
        ph = ",".join("?" * len(names))
        rows = conn.execute(f"""
            SELECT f.* FROM finding f
            JOIN finding_symbol fs ON fs.finding_id = f.id
            WHERE fs.symbol_name IN ({ph}) AND f.status IN ('active', 'stale')
            ORDER BY f.updated_at DESC
        """, list(names)).fetchall()
        # 去重（一个 finding 锚定多个符号时 join 出多行）
        seen, result = set(), []
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                result.append(_row_to_finding(r))
        return result[: max(limit_per_symbol * len(names), 10)]
    finally:
        conn.close()


# ─── stale 自动检测（T7）──────────────────────────────────────

def _is_code_symbol(name: str) -> bool:
    """锚定是否为 C++ 代码符号（而非服务名/文件名/工具名等运维对象）。

    判据：含 '.'（.service/.sh/.py 文件/服务名特征）→ 非代码符号，跳过 stale 检测。
    C++ 标识符特征：字母数字 + :: + _ + ~。
    """
    return bool(name) and "." not in name and re.match(r'^[A-Za-z_][\w:~]*$', name)


def _symbol_exists_in_cppsg(symbol_name: str, cppsg_conn: sqlite3.Connection) -> bool:
    """符号在 cppsg 代码图谱中是否存在（宽松判定，三级匹配）。

    宽松原则：LIKE 短名也算存在——stale 误标的代价（AI 无视有效结论）
    高于漏标（过期结论多留一会儿），宁可漏标。
    """
    base = symbol_name[7:] if symbol_name.startswith("symbol:") else symbol_name
    short = base.split("::")[-1]

    # Level 1: 精确匹配
    if cppsg_conn.execute(
            "SELECT 1 FROM node WHERE name = ? AND type IN ('class','struct','function') LIMIT 1",
            (short,)).fetchone():
        return True
    # Level 2: ClassName::Method 拆分
    if "::" in base:
        cls, method = base.rsplit("::", 1)
        if cppsg_conn.execute(
                "SELECT 1 FROM node WHERE name = ? AND parent_class = ? LIMIT 1",
                (method, cls)).fetchone():
            return True
    # Level 3: 短名 LIKE（宽松兜底）
    if cppsg_conn.execute(
            "SELECT 1 FROM node WHERE name LIKE ? LIMIT 1",
            (f"%{short}%",)).fetchone():
        return True
    return False


def check_freshness(db_path: str = DEFAULT_DB, cppsg_db_path: str = CPPSG_DB) -> dict:
    """检测 finding 锚定符号在 cppsg 的存在性，幂等双向状态迁移。

    全部锚定符号消失 → active → stale（结论可能已失效）
    stale 且任一符号回归 → stale → active（自动恢复）

    Returns:
        {"checked": n, "stale_marked": n, "recovered": n,
         "detail": [{id, title, from, to, missing_symbols}]}
    """
    if not os.path.exists(cppsg_db_path):
        return {"error": f"cppsg 数据库不存在: {cppsg_db_path}"}

    conn = _get_conn(db_path)
    cppsg_conn = sqlite3.connect(cppsg_db_path)
    cppsg_conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT f.id, f.title, f.status, f.symbols
            FROM finding f WHERE f.status IN ('active', 'stale')
        """).fetchall()

        result = {"checked": len(rows), "stale_marked": 0, "recovered": 0, "detail": []}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in rows:
            try:
                symbols = json.loads(r["symbols"] or "[]")
            except (json.JSONDecodeError, TypeError):
                symbols = []
            # 只检测代码符号锚定；服务名/文件名等运维锚定不适用 cppsg 存在性判据
            symbols = [s for s in symbols if _is_code_symbol(s)]
            if not symbols:
                continue  # 无代码符号锚定的结论无法检测，跳过

            missing = [s for s in symbols if not _symbol_exists_in_cppsg(s, cppsg_conn)]
            all_gone = len(missing) == len(symbols)

            if all_gone and r["status"] == "active":
                conn.execute("UPDATE finding SET status='stale', updated_at=? WHERE id=?",
                             (now, r["id"]))
                result["stale_marked"] += 1
                result["detail"].append({"id": r["id"], "title": r["title"],
                                         "from": "active", "to": "stale",
                                         "missing_symbols": missing})
            elif not all_gone and r["status"] == "stale":
                conn.execute("UPDATE finding SET status='active', updated_at=? WHERE id=?",
                             (now, r["id"]))
                result["recovered"] += 1
                result["detail"].append({"id": r["id"], "title": r["title"],
                                         "from": "stale", "to": "active",
                                         "missing_symbols": missing})
        conn.commit()
        return result
    finally:
        conn.close()
        cppsg_conn.close()


# ─── 向量语义检索（T8，fastembed 可选依赖，不可用时自动降级）───

_EMBED_MODEL = None          # lazy 单例（HTTP 常驻进程只加载一次）
_EMBED_MODEL_STATE = "uninit"  # uninit | ready | unavailable
# 模型可配置（默认 bge-small-zh：向量仅做排序增强，小模型排序可靠且省 10 倍资源。
# 实验结论 2026-08-18：e5-large(560M) 校准 margin -0.041 仍不可分离且排序反而出错——
# "短查询 vs 短结论"的语义判别不是 encoder 量级问题，需 LLM 查询改写路线，见 04_phase2_plan.md）
EMBED_MODEL_NAME = os.environ.get("DOC_GRAPH_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
EMBED_DIM = int(os.environ.get("DOC_GRAPH_EMBED_DIM", "512"))
# bge 系列不需要前缀；切 e5 系列时设 DOC_GRAPH_EMBED_QUERY_PREFIX="query: " 等
EMBED_QUERY_PREFIX = os.environ.get("DOC_GRAPH_EMBED_QUERY_PREFIX", "")
EMBED_DOC_PREFIX = os.environ.get("DOC_GRAPH_EMBED_DOC_PREFIX", "")
# 模型缓存固定到家目录（fastembed 0.8 默认在 /tmp，重启即丢，需联网重下——不可接受）
EMBED_CACHE_DIR = os.environ.get("DOC_GRAPH_MODEL_CACHE",
                                 os.path.expanduser("~/.cache/fastembed"))
os.makedirs(EMBED_CACHE_DIR, exist_ok=True)


def _get_embed_model():
    """lazy 加载 embedding 模型。失败标记 unavailable，后续调用直接跳过。"""
    global _EMBED_MODEL, _EMBED_MODEL_STATE
    if _EMBED_MODEL_STATE == "ready":
        return _EMBED_MODEL
    if _EMBED_MODEL_STATE == "unavailable":
        return None
    try:
        from fastembed import TextEmbedding
        _EMBED_MODEL = TextEmbedding(model_name=EMBED_MODEL_NAME, cache_dir=EMBED_CACHE_DIR)
        _EMBED_MODEL_STATE = "ready"
        return _EMBED_MODEL
    except Exception as e:
        _EMBED_MODEL_STATE = "unavailable"
        print(f"[finding_store] fastembed 不可用，向量检索降级关闭: {e}", file=sys.stderr)
        return None


def _embed_one(text: str, mode: str = "doc") -> bytes | None:
    """文本 → 归一化向量（float32 序列化为 BLOB）。失败返回 None。

    mode: "doc"（结论入库，passage 前缀）| "query"（检索查询，query 前缀）。
    e5 系列模型规范要求区分前缀；bge 系列前缀设为空串即可。
    """
    model = _get_embed_model()
    if model is None or not text:
        return None
    prefix = EMBED_QUERY_PREFIX if mode == "query" else EMBED_DOC_PREFIX
    try:
        import struct
        vec = next(iter(model.embed([prefix + text[:1024]])))  # 截断防长文本（m3/e5 上限 512k token）
        # L2 归一化 → 余弦相似退化为点积
        norm = sum(x * x for x in vec) ** 0.5
        if norm == 0:
            return None
        return struct.pack(f'{len(vec)}f', *[x / norm for x in vec])
    except Exception as e:
        print(f"[finding_store] embed 失败: {e}", file=sys.stderr)
        return None


def _cos_blob(blob_a: bytes, blob_b: bytes) -> float:
    """两个归一化向量 BLOB 的点积（即余弦相似度）。长度不等返回 0。"""
    import struct
    n = min(len(blob_a), len(blob_b)) // 4
    if n == 0:
        return 0.0
    a = struct.unpack(f'{n}f', blob_a[:n * 4])
    b = struct.unpack(f'{n}f', blob_b[:n * 4])
    return sum(x * y for x, y in zip(a, b))


def _ensure_embedding_column(conn: sqlite3.Connection):
    """finding 表幂等追加 embedding 列（存量行为 NULL，检索时跳过）。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(finding)")]
    if "embedding" not in cols:
        conn.execute("ALTER TABLE finding ADD COLUMN embedding BLOB")
        conn.commit()


def _vector_search(conn: sqlite3.Connection, keyword: str,
                   top_k: int = 8) -> list[tuple[str, float]]:
    """向量路检索：查询向量 vs 库存 embedding 余弦 top-K。

    Returns: [(finding_id, similarity)]，不可用/无数据返回 []
    """
    qblob = _embed_one(keyword)
    if qblob is None:
        return []
    rows = conn.execute(
        "SELECT id, embedding FROM finding WHERE embedding IS NOT NULL").fetchall()
    scored = [(r["id"], _cos_blob(qblob, r["embedding"])) for r in rows]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


def _rrf_merge(lexical_ids: list[str], vector_ids: list[str],
               k: int = 60, limit: int = 10) -> list[str]:
    """FTS 词法路 + 向量路 RRF 融合排序。"""
    scores: dict[str, float] = {}
    for rank, fid in enumerate(lexical_ids):
        scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank + 1)
    for rank, fid in enumerate(vector_ids):
        scores[fid] = scores.get(fid, 0.0) + 1.0 / (k + rank + 1)
    return [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])][:limit]


# ─── parser 重建防丢失通道 ─────────────────────────────────────

def export_findings(db_path: str = DEFAULT_DB) -> dict:
    """导出 finding 三表全部行（内存快照），供 parser 重建后回写。"""
    if not os.path.exists(db_path):
        return {"finding": [], "finding_symbol": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='finding'"
        ).fetchone()
        if not has:
            return {"finding": [], "finding_symbol": []}
        findings = [dict(r) for r in conn.execute("SELECT * FROM finding")]
        anchors = [dict(r) for r in conn.execute("SELECT * FROM finding_symbol")]
        return {"finding": findings, "finding_symbol": anchors}
    finally:
        conn.close()


def import_findings(snapshot: dict, db_path: str = DEFAULT_DB) -> int:
    """回写 export_findings 的快照（在 parser 重建完 node/edge 后调用）。"""
    findings = snapshot.get("finding") or []
    anchors = snapshot.get("finding_symbol") or []
    if not findings:
        return 0
    conn = _get_conn(db_path)
    try:
        c = conn.cursor()
        for f in findings:
            c.execute("""
                INSERT OR REPLACE INTO finding
                (id, ftype, title, detail, symbols, source, status, confidence, tags, created_at, updated_at, embedding)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (f["id"], f["ftype"], f["title"], f.get("detail", ""),
                  f.get("symbols", "[]"), f.get("source", ""),
                  f.get("status", "active"), f.get("confidence", "confirmed"),
                  f.get("tags", "[]"), f["created_at"], f["updated_at"],
                  f.get("embedding")))
            c.execute("""
                INSERT INTO finding_fts (finding_id, title, detail, tags)
                VALUES (?,?,?,?)
            """, (f["id"], _preprocess_cjk_for_fts(f["title"]),
                  _preprocess_cjk_for_fts(f.get("detail", "")),
                  _preprocess_cjk_for_fts(f.get("tags", "[]"))))
        for a in anchors:
            c.execute("INSERT OR IGNORE INTO finding_symbol (finding_id, symbol_name) VALUES (?,?)",
                      (a["finding_id"], a["symbol_name"]))
        conn.commit()
        return len(findings)
    finally:
        conn.close()


# ─── CLI（调试用）─────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description='经验结论存储层（调试 CLI）')
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("record")
    pr.add_argument("title")
    pr.add_argument("--detail", default="")
    pr.add_argument("--ftype", default="fact", choices=FTYPES)
    pr.add_argument("--symbols", nargs="*", default=[])
    pr.add_argument("--source", default="")
    ps = sub.add_parser("search")
    ps.add_argument("keyword", nargs="?", default="")
    ps.add_argument("--symbol", default="")
    ps.add_argument("--ftype", default="")
    pg = sub.add_parser("get")
    pg.add_argument("--symbols", nargs="*", default=[])
    args = p.parse_args()

    if args.cmd == "record":
        print(json.dumps(record_finding(args.title, args.detail, args.ftype,
                                        args.symbols, args.source), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        for f in search_findings(args.keyword, args.symbol, args.ftype):
            print(f"[{f['ftype']}] {f['title']}  ({','.join(f['symbols']) or '-'})")
    elif args.cmd == "get":
        for f in get_findings_by_symbols(args.symbols):
            print(f"[{f['ftype']}] {f['title']}")


if __name__ == '__main__':
    main()
