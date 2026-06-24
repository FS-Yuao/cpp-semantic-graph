"""关联边入库

将 DocAssociation 列表写入 SQLite edge 表。
支持手动标记和自动关联两种来源。
"""

from __future__ import annotations

import json
import logging
import time

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType
from .doc_association import DocAssociationParser, Association

logger = logging.getLogger(__name__)


class AssociationIngester:
    """关联边入库器"""

    def __init__(self, db_path: str, project_config: "ProjectConfig | None" = None):
        """初始化

        Args:
            db_path: 数据库路径
            project_config: 项目配置，用于获取 source_paths 过滤范围。
                为 None 则不做路径过滤（兼容旧行为，但不推荐——会关联到 SDK/生成代码）。
        """
        from ..parser.config import ProjectConfig
        self.db = GraphDB(db_path)
        self.parser = DocAssociationParser(self.db)
        self.project_config = project_config

    def _get_source_path_prefixes(self) -> list[str]:
        """从 project_config 推导 DB 中 file_path 的合法前缀列表

        make_relative_path 将 source_paths 中的路径截断：
          source_paths="hq_ota_service/src" → 截断 "hq_ota_service/src" 后保留
          如 /path/hq_ota_service/src/ota_manager/ota_manager.h → ota_manager/ota_manager.h
          此时 file_path 不含 "src/" 前缀（因为 source_paths 含 "/" 所以直接去掉整段）

          source_paths="src" → 截断 "src" 后加前缀
          如 /path/src/main.cpp → src/main.cpp

        核心观察：
        - 匹配 source_paths 的文件：file_path 可能带子目录前缀（如 ota_manager/）或只是文件名
        - 匹配 generated_paths 的文件：file_path 带生成目录前缀（如 ara/）
        - 不匹配的 fallback：file_path 只是文件名（如 ppscontrol.h）

        所以无法仅用 file_path 前缀区分"项目根文件"和"SDK fallback 文件"。
        改用排除法：从 DB 中提取所有含 '/' 的目录前缀，排除 exclude_paths 中的和
        明确不在 source_paths/generated_paths 中的（如 ara/ 是 generated，OK；
        ppscontrol 不在任何路径中，是 SDK）。

        最终方案更简洁：标记哪些 file_path 是项目代码，在 SQL 中用 NOT IN 排除
        非项目目录。
        """
        # 不再使用前缀推导，改为在 ingest_content_scan_associations 中直接判断
        return []

    def _is_project_code_node(self, file_path: str, namespace: str,
                               extra_info: dict | None = None) -> bool:
        """判断 DB 中的代码节点是否属于项目源码

        判断逻辑（按优先级）：
        1. extra_info 中有 is_project 字段 → 直接使用（最可靠，入库时标记）
        2. file_path 含 '/' → 来自 source_paths 截断，是项目代码
        3. file_path 匹配 generated_paths 前缀 → 生成代码（也关联）
        4. file_path 只是文件名（无 '/'）→ 用 namespace 排除已知的 SDK 前缀
        5. 无 project_config → 默认不过滤

        注意：不使用 extra_info 中的 is_project 标记，因为 libclang 对 SDK cursor
        可能报项目 TU 的 location，导致 is_project 误标为 True。
        file_path + namespace 启发式判断更可靠。
        """
        if not self.project_config:
            return True  # 无配置时不做过滤

        # file_path 含 '/' → 来自 source_paths 截断，是项目代码
        if "/" in file_path:
            for ep in self.project_config.exclude_paths:
                last = ep.rstrip("/").split("/")[-1]
                if file_path.startswith(f"{last}/"):
                    return False
            return True

        # file_path 只是文件名（无 '/'）→ 用 SDK namespace 排除
        sdk_hints = self._get_sdk_namespace_hints()
        for hint in sdk_hints:
            if namespace.startswith(hint):
                return False

        return True

    def _get_sdk_namespace_hints(self) -> list[str]:
        """从 DB 中自动推断 SDK 的 namespace 前缀

        只在 _is_project_code_node 的 fallback 路径中使用（根目录文件、无 is_project 标记时）。

        启发式：
        1. 收集 DB 中所有含 '/' 的 file_path 的顶层目录名 → 项目/生成代码的子目录
        2. 收集根目录文件的 namespace 顶层
        3. 如果 namespace 顶层和任何子目录名有 >=4 字符的共同子串 → 项目代码
        4. 否则 → SDK

        这利用了 C++ 项目的常见惯例：namespace 和目录名有对应关系。
        """
        conn = self.db.conn

        # 收集子目录名
        subdir_rows = conn.execute('''
            SELECT DISTINCT file_path FROM node
            WHERE type IN ('class', 'function')
            AND file_path LIKE '%/%'
            AND file_path != ''
        ''').fetchall()
        project_dirs = set()
        for r in subdir_rows:
            project_dirs.add(r["file_path"].split("/")[0])

        # 加入 source_paths / generated_paths 推导的目录名
        if self.project_config:
            for sp in self.project_config.source_paths + self.project_config.generated_paths:
                project_dirs.add(sp.rstrip("/").split("/")[-1])

        # 根目录文件的 namespace
        root_ns_rows = conn.execute('''
            SELECT DISTINCT namespace FROM node
            WHERE type IN ('class', 'function')
            AND file_path NOT LIKE '%/%'
            AND file_path != ''
            AND namespace != ''
        ''').fetchall()

        def _is_likely_project(ns_top: str) -> bool:
            """namespace 顶层与项目目录名是否有 >=4 字符共同子串"""
            ns_lower = ns_top.lower()
            for d in project_dirs:
                d_lower = d.lower()
                for i in range(len(ns_lower) - 3):
                    if ns_lower[i:i+4] in d_lower:
                        return True
            return False

        sdk_tops = set()
        for r in root_ns_rows:
            top = r["namespace"].split("::")[0]
            if not _is_likely_project(top):
                sdk_tops.add(top)

        return [f"{top}::" for top in sdk_tops]

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ingest_manual_associations(self) -> dict:
        """扫描所有 doc_section，解析 [[...]] 标记并入库

        Returns:
            统计信息
        """
        stats = {"docs_scanned": 0, "links_found": 0, "edges_created": 0, "unmatched": 0}

        # 查所有 doc_section 节点
        conn = self.db.conn
        rows = conn.execute(
            "SELECT id, unique_key, extra_info FROM node WHERE type='doc_section'"
        ).fetchall()

        for row in rows:
            doc_id = row["id"]
            doc_key = row["unique_key"]
            extra = row["extra_info"]
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except (json.JSONDecodeError, TypeError):
                    extra = {}

            content = extra.get("content_preview", "")
            if not content:
                continue

            stats["docs_scanned"] += 1

            # 解析 [[...]] 标记
            associations = self.parser.parse_manual_links(doc_key, content)
            links_in_doc = len(associations) // 2  # 正反向各一
            stats["links_found"] += links_in_doc

            # 入库
            for assoc in associations:
                # 解析 from/to ID
                if assoc.relation_type == RelationType.DOC_DESCRIBES_CODE.value:
                    from_key = doc_key
                    to_key = assoc.code_unique_key
                else:  # code_refers_to_doc
                    from_key = assoc.code_unique_key
                    to_key = doc_key

                from_row = conn.execute(
                    "SELECT id FROM node WHERE unique_key=?", (from_key,)
                ).fetchone()
                to_row = conn.execute(
                    "SELECT id FROM node WHERE unique_key=?", (to_key,)
                ).fetchone()

                if not from_row or not to_row:
                    stats["unmatched"] += 1
                    continue

                from_id = from_row["id"]
                to_id = to_row["id"]

                edge_extra = {
                    "confidence": assoc.confidence,
                    "method": assoc.method,
                    "link_text": assoc.link_text,
                }
                if assoc.extra_info:
                    edge_extra.update(assoc.extra_info)

                edge_id = self.db.insert_edge(
                    from_id, to_id, assoc.relation_type, edge_extra
                )
                if edge_id:
                    stats["edges_created"] += 1

        self.db.conn.commit()
        return stats

    def ingest_rule_associations(self) -> dict:
        """基于规则自动关联（无需 [[...]] 标记）

        规则:
        - 文档标题包含类名 → 自动关联（confidence=0.8）
        - 文档标签与代码命名空间匹配 → 自动关联（confidence=0.6）

        Returns:
            统计信息
        """
        stats = {"rules_applied": 0, "edges_created": 0}

        conn = self.db.conn

        # 查所有 doc_section
        doc_rows = conn.execute(
            "SELECT id, unique_key, name, extra_info FROM node WHERE type='doc_section'"
        ).fetchall()

        # 查所有类节点
        class_rows = conn.execute(
            "SELECT id, unique_key, name, namespace FROM node WHERE type='class'"
        ).fetchall()
        class_names = {r["name"]: r for r in class_rows}

        for doc_row in doc_rows:
            doc_id = doc_row["id"]
            doc_key = doc_row["unique_key"]
            doc_name = doc_row["name"]

            # 规则 1: 文档标题包含类名
            for class_name, class_row in class_names.items():
                if class_name in doc_name and len(class_name) > 3:
                    # 检查是否已有手动关联
                    existing = conn.execute(
                        """SELECT 1 FROM edge
                           WHERE from_id=? AND to_id=? AND relation_type=?""",
                        (doc_id, class_row["id"],
                         RelationType.DOC_DESCRIBES_CODE.value)
                    ).fetchone()
                    if existing:
                        continue

                    # 创建关联
                    for rel_type, from_id, to_id in [
                        (RelationType.DOC_DESCRIBES_CODE.value, doc_id, class_row["id"]),
                        (RelationType.CODE_REFERS_TO_DOC.value, class_row["id"], doc_id),
                    ]:
                        edge_id = self.db.insert_edge(
                            from_id, to_id, rel_type,
                            {"confidence": 0.8, "method": "rule_title_match",
                             "matched_name": class_name}
                        )
                        if edge_id:
                            stats["edges_created"] += 1

                    stats["rules_applied"] += 1

        self.db.conn.commit()
        return stats

    def ingest_content_scan_associations(self) -> dict:
        """内容扫描关联：扫描文档内容中出现的类名/函数名，自动建边

        策略:
        1. 收集项目源码范围内的代码节点名称（由 project_config 判定范围）
        2. 对每个文档切片的 content_preview 做全词匹配
        3. 匹配到类名 → confidence=0.7, method="content_scan"
        4. 匹配到函数名 → confidence=0.6, method="content_scan"
        5. 过滤太短的名称（< 3 字符）和常见的动宾短语（get/set/run 等）

        核心防误匹配机制：只关联项目源码范围内的代码节点（_is_project_code_node），
        不关联 SDK（如 DESY::ppscontrol）和第三方代码的节点。
        范围由 project_config 的 source_paths / generated_paths / exclude_paths 决定。

        Returns:
            统计信息
        """
        stats = {"docs_scanned": 0, "matches_found": 0, "edges_created": 0,
                 "skipped_short": 0, "skipped_out_of_scope": 0}

        conn = self.db.conn

        # 1. 收集代码节点名称，按项目范围过滤
        has_project_config = self.project_config is not None

        # 类名（含 extra_info 用于 is_project 判断）
        class_rows = conn.execute(
            "SELECT id, unique_key, name, namespace, file_path, extra_info FROM node WHERE type='class'"
        ).fetchall()

        # 函数名（去重，含 namespace + file_path 用于过滤）
        func_rows = conn.execute(
            "SELECT DISTINCT name, namespace, file_path FROM node WHERE type='function' AND LENGTH(name) >= 4"
        ).fetchall()

        # 统计总节点数
        total_class = len(class_rows)
        total_func = len(func_rows)

        # 过滤非项目代码节点
        if has_project_config:
            def _parse_extra(row):
                extra = row["extra_info"] if "extra_info" in row.keys() else None
                if isinstance(extra, str):
                    try:
                        return json.loads(extra)
                    except (json.JSONDecodeError, TypeError):
                        return {}
                return extra or {}

            class_rows = [r for r in class_rows
                          if self._is_project_code_node(r["file_path"], r["namespace"],
                                                         _parse_extra(r))]
            filtered_func_rows = [r for r in func_rows
                                  if self._is_project_code_node(r["file_path"], r["namespace"])]
            stats["skipped_out_of_scope"] = (total_class - len(class_rows)) + (total_func - len(filtered_func_rows))
            func_rows = filtered_func_rows

        # 常见动宾短语 — 太短太通用，全词匹配容易误命中文档日常用词
        _common_verb_phrases = {
            "get", "set", "init", "run", "start", "stop",
            "open", "close", "read", "write", "update",
            "create", "delete", "check", "handle", "process",
            "notify", "log", "send", "receive",
        }

        # 构建名称 → 节点 ID 列表的映射
        # 类名精确匹配
        class_name_to_ids: dict[str, list[int]] = {}
        for r in class_rows:
            name = r["name"]
            if len(name) < 3:
                stats["skipped_short"] += 1
                continue
            class_name_to_ids.setdefault(name, []).append(r["id"])

        # 函数名精确匹配（只取前 N 个同名的，避免太多）
        func_name_set: set[str] = set()
        for r in func_rows:
            name = r["name"]
            if name in _common_verb_phrases:
                continue
            func_name_set.add(name)

        # 太多函数名会很慢，限制数量
        if len(func_name_set) > 200:
            # 只保留长度 >= 6 的函数名，减少误匹配
            func_name_set = {n for n in func_name_set if len(n) >= 6}

        # 函数名 → 节点 ID（取项目范围内第一个匹配的）
        func_name_to_id: dict[str, int] = {}
        for name in func_name_set:
            if has_project_config:
                # 只在项目范围内查找
                row = conn.execute(
                    "SELECT id, file_path, namespace FROM node WHERE name=? AND type='function' LIMIT 100",
                    (name,)
                ).fetchall()
                for r in row:
                    if self._is_project_code_node(r["file_path"], r["namespace"]):
                        func_name_to_id[name] = r["id"]
                        break
            else:
                row = conn.execute(
                    "SELECT id FROM node WHERE name=? AND type='function' LIMIT 1",
                    (name,)
                ).fetchone()
                if row:
                    func_name_to_id[name] = row["id"]

        # 2. 扫描文档内容
        doc_rows = conn.execute(
            "SELECT id, unique_key, name, extra_info FROM node WHERE type='doc_section'"
        ).fetchall()

        # 合并类名和函数名，统一匹配
        all_names = {}
        for name, ids in class_name_to_ids.items():
            for nid in ids:
                all_names[name] = (nid, "class")
        for name, nid in func_name_to_id.items():
            if name not in all_names:  # 类名优先
                all_names[name] = (nid, "function")

        # 构建正则：全词匹配所有名称
        import re
        # 按名称长度降序排列，长名优先匹配
        sorted_names = sorted(all_names.keys(), key=len, reverse=True)
        if not sorted_names:
            return stats

        # 用 \b 全词边界匹配
        pattern = r'\b(' + '|'.join(re.escape(n) for n in sorted_names) + r')\b'
        name_re = re.compile(pattern)

        for doc_row in doc_rows:
            doc_id = doc_row["id"]
            extra = doc_row["extra_info"]
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except (json.JSONDecodeError, TypeError):
                    extra = {}

            content = extra.get("content_preview", "")
            if not content:
                continue

            stats["docs_scanned"] += 1

            # 匹配
            matches = set(name_re.findall(content))
            if not matches:
                continue

            stats["matches_found"] += len(matches)

            for matched_name in matches:
                code_id, code_type = all_names[matched_name]

                # 检查是否已有关联边（去重）
                existing = conn.execute(
                    """SELECT 1 FROM edge
                       WHERE from_id=? AND to_id=? AND relation_type=?""",
                    (doc_id, code_id, RelationType.DOC_DESCRIBES_CODE.value)
                ).fetchone()
                if existing:
                    continue

                confidence = 0.7 if code_type == "class" else 0.6

                # 创建双向边
                for rel_type, from_id, to_id in [
                    (RelationType.DOC_DESCRIBES_CODE.value, doc_id, code_id),
                    (RelationType.CODE_REFERS_TO_DOC.value, code_id, doc_id),
                ]:
                    edge_id = self.db.insert_edge(
                        from_id, to_id, rel_type,
                        {
                            "confidence": confidence,
                            "method": "content_scan",
                            "matched_name": matched_name,
                            "code_type": code_type,
                        }
                    )
                    if edge_id:
                        stats["edges_created"] += 1

        self.db.conn.commit()
        return stats

    def ingest_config_associations(self, config_path: str | None = None) -> dict:
        """配置文件手动标记关联（不侵入文档原文）

        配置格式（写在 doc_config.yaml 或独立文件中）:
        ```yaml
        manual_links:
          - doc: "OTA_flow/OTA_COMPLETE_FLOW.md"     # 文档路径
            heading: "刷写与激活"                      # 可选：限定到特定切片
            code:                                      # 关联的代码实体
              - "BasePeriUpdate"
              - "SocUpdate"
              - "PerformUpgrade"
        ```

        Args:
            config_path: 配置文件路径，None 则从 doc_config.yaml 读取

        Returns:
            统计信息
        """
        import yaml
        from pathlib import Path

        stats = {"links_configured": 0, "edges_created": 0, "unmatched": 0}

        if not config_path:
            return stats

        config_path = Path(config_path)
        if not config_path.exists():
            return stats

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        links = config.get("manual_links", [])
        conn = self.db.conn

        for link in links:
            doc_path = link.get("doc", "")
            heading = link.get("heading", "")
            code_names = link.get("code", [])

            if not doc_path or not code_names:
                continue

            # 找文档切片
            if heading:
                doc_rows = conn.execute(
                    """SELECT id, unique_key FROM node
                       WHERE type='doc_section' AND file_path LIKE ?
                       AND name=?""",
                    (f"%{doc_path}%", heading)
                ).fetchall()
            else:
                doc_rows = conn.execute(
                    """SELECT id, unique_key FROM node
                       WHERE type='doc_section' AND file_path LIKE ?""",
                    (f"%{doc_path}%",)
                ).fetchall()

            for doc_row in doc_rows:
                doc_id = doc_row["id"]

                for code_name in code_names:
                    stats["links_configured"] += 1

                    # 查代码节点
                    code_row = conn.execute(
                        """SELECT id FROM node
                           WHERE name=? AND type IN ('class', 'function', 'struct')
                           LIMIT 1""",
                        (code_name,)
                    ).fetchone()

                    if not code_row:
                        stats["unmatched"] += 1
                        continue

                    code_id = code_row["id"]

                    # 检查是否已存在
                    existing = conn.execute(
                        """SELECT 1 FROM edge
                           WHERE from_id=? AND to_id=? AND relation_type=?""",
                        (doc_id, code_id, RelationType.DOC_DESCRIBES_CODE.value)
                    ).fetchone()
                    if existing:
                        continue

                    # 创建双向边（confidence=1.0，手动标记）
                    for rel_type, from_id, to_id in [
                        (RelationType.DOC_DESCRIBES_CODE.value, doc_id, code_id),
                        (RelationType.CODE_REFERS_TO_DOC.value, code_id, doc_id),
                    ]:
                        edge_id = self.db.insert_edge(
                            from_id, to_id, rel_type,
                            {"confidence": 1.0, "method": "manual_config",
                             "link_text": code_name}
                        )
                        if edge_id:
                            stats["edges_created"] += 1

        self.db.conn.commit()
        return stats

    def ingest_embedding_associations(self, *,
                                       model_name: str = "all-MiniLM-L6-v2",
                                       threshold: float = 0.45,
                                       batch_size: int = 64) -> dict:
        """Embedding 语义关联：用向量相似度发现文档和代码的隐含关联

        策略:
        1. 对每个代码节点，构造描述文本（namespace + name + signature/role）
        2. 对每个文档切片，用 content_preview 作为文本
        3. 用 sentence-transformers 编码，计算余弦相似度
        4. 超过阈值 → 自动建边（confidence=相似度，method="embedding"）
        5. 跳过已有 content_scan/manual_config 关联的（避免重复）

        Args:
            model_name: sentence-transformers 模型名
            threshold: 相似度阈值（0~1），越低越多关联但噪声越大
            batch_size: 编码批大小

        Returns:
            统计信息
        """
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
        except ImportError:
            logger.error("sentence-transformers 未安装，跳过 embedding 关联")
            return {"error": "sentence_transformers_not_installed"}

        stats = {"docs_encoded": 0, "code_encoded": 0,
                 "pairs_above_threshold": 0, "edges_created": 0,
                 "skipped_existing": 0}

        conn = self.db.conn

        # 1. 收集代码节点（只取项目代码）
        code_rows = conn.execute('''
            SELECT id, name, namespace, type, file_path, extra_info
            FROM node
            WHERE type IN ('class', 'function')
        ''').fetchall()

        if self.project_config:
            def _parse_extra(row):
                extra = row["extra_info"] if "extra_info" in row.keys() else None
                if isinstance(extra, str):
                    try:
                        return json.loads(extra)
                    except (json.JSONDecodeError, TypeError):
                        return {}
                return extra or {}
            code_rows = [r for r in code_rows
                         if self._is_project_code_node(r["file_path"], r["namespace"],
                                                        _parse_extra(r))]

        # 构造代码描述文本
        code_texts = []
        for r in code_rows:
            extra = json.loads(r["extra_info"]) if isinstance(r["extra_info"], str) else (r["extra_info"] or {})
            ns = r["namespace"]
            name = r["name"]
            ctype = r["type"]

            if ctype == "class":
                # 类描述：namespace + 类名 + 角色
                desc = f"{ns}::{name}" if ns else name
                if extra.get("is_abstract"):
                    desc += " abstract base class"
                else:
                    desc += " class"
                # 加上所属文件目录作为上下文
                fdir = r["file_path"].rsplit("/", 1)[0] if "/" in r["file_path"] else ""
                if fdir:
                    desc += f" in {fdir}"
            else:
                # 函数描述：签名
                sig = extra.get("signature", "")
                parent = extra.get("parent_class", "")
                desc = sig if sig else f"{ns}::{name}" if ns else name
                if parent:
                    desc = f"{parent}::{desc}"

            code_texts.append(desc)

        # 2. 收集文档切片
        doc_rows = conn.execute('''
            SELECT id, name, extra_info FROM node WHERE type='doc_section'
        ''').fetchall()

        doc_texts = []
        for r in doc_rows:
            extra = json.loads(r["extra_info"]) if isinstance(r["extra_info"], str) else (r["extra_info"] or {})
            heading = r["name"]
            preview = extra.get("content_preview", "")
            tags = extra.get("tags", [])
            tag_str = " ".join(tags) if tags else ""
            doc_texts.append(f"{tag_str} {heading}: {preview}" if tag_str else f"{heading}: {preview}")

        if not code_texts or not doc_texts:
            logger.warning("无代码或文档节点，跳过 embedding 关联")
            return stats

        # 3. 编码
        logger.info("加载模型 %s ...", model_name)
        model = SentenceTransformer(model_name)

        logger.info("编码 %d 个代码描述...", len(code_texts))
        code_embs = model.encode(code_texts, batch_size=batch_size,
                                  normalize_embeddings=True, show_progress_bar=False)
        stats["code_encoded"] = len(code_texts)

        logger.info("编码 %d 个文档描述...", len(doc_texts))
        doc_embs = model.encode(doc_texts, batch_size=batch_size,
                                normalize_embeddings=True, show_progress_bar=False)
        stats["docs_encoded"] = len(doc_texts)

        # 4. 计算相似度矩阵 (doc × code)
        sim_matrix = doc_embs @ code_embs.T  # (n_docs, n_codes)

        # 5. 找超阈值的配对，建边
        for i, doc_row in enumerate(doc_rows):
            doc_id = doc_row["id"]
            sims = sim_matrix[i]

            # 取超阈值的
            above = np.where(sims >= threshold)[0]
            for j in above:
                confidence = float(sims[j])
                code_row = code_rows[j]
                code_id = code_row["id"]

                # 去重：已有 content_scan/manual 关联的跳过
                existing = conn.execute(
                    """SELECT 1 FROM edge
                       WHERE from_id=? AND to_id=? AND relation_type=?""",
                    (doc_id, code_id, RelationType.DOC_DESCRIBES_CODE.value)
                ).fetchone()
                if existing:
                    stats["skipped_existing"] += 1
                    continue

                stats["pairs_above_threshold"] += 1

                for rel_type, from_id, to_id in [
                    (RelationType.DOC_DESCRIBES_CODE.value, doc_id, code_id),
                    (RelationType.CODE_REFERS_TO_DOC.value, code_id, doc_id),
                ]:
                    edge_id = self.db.insert_edge(
                        from_id, to_id, rel_type,
                        {
                            "confidence": round(confidence, 3),
                            "method": "embedding",
                            "similarity": round(confidence, 3),
                        }
                    )
                    if edge_id:
                        stats["edges_created"] += 1

        self.db.conn.commit()
        logger.info("Embedding 关联完成: %d pairs above %.2f, %d edges created",
                     stats["pairs_above_threshold"], threshold, stats["edges_created"])
        return stats
