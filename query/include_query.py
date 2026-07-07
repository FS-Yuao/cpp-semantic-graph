"""include 依赖查询接口

基于 include_dep 表，提供头文件影响面分析与 include 树查询。
为阶段 4 增量更新铺路：改了某个头文件，能快速查到所有受影响的翻译单元。

3 个核心接口:
  - get_direct_includers: 直接 include 该头文件的翻译单元
  - get_all_includers:    所有直接 + 间接 include 的翻译单元（BFS，防环）
  - get_include_tree:    指定翻译单元的完整 include 树（嵌套结构）

注意: include_dep 存的是 (source_file -> included_file)。
  - "谁 include 了 X" = 查 included_file=X 的 source_file 集合（反向）
  - "X include 了谁" = 查 source_file=X 的 included_file 集合（正向）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IncludeNode:
    """include 树节点"""
    file: str
    children: list["IncludeNode"] = field(default_factory=list)
    is_system: bool = False


class IncludeQuery:
    """include 依赖查询"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 1. 直接 includers — 哪些翻译单元直接 include 了某头文件
    # ------------------------------------------------------------------

    def get_direct_includers(self, header_path: str) -> list[str]:
        """查询哪些翻译单元直接 include 了该头文件

        Args:
            header_path: 头文件路径（支持部分匹配，如 "base_peri_update.h"）

        Returns:
            source_file 列表（去重，排序）
        """
        rows = self.conn.execute(
            "SELECT DISTINCT source_file FROM include_dep "
            "WHERE included_file LIKE ? ORDER BY source_file",
            (f"%{header_path}%",),
        ).fetchall()
        return [r["source_file"] for r in rows]

    # ------------------------------------------------------------------
    # 2. 全部 includers — 直接 + 间接（BFS，防环）
    # ------------------------------------------------------------------

    def get_all_includers(self, header_path: str, *,
                          max_depth: int = 20) -> list[str]:
        """查询所有直接和间接 include 了该头文件的翻译单元

        递归向上追溯：A include B，B include C，则改 C 会影响 A 和 B。
        本方法返回所有会因 header_path 变更而受影响的翻译单元。

        Args:
            header_path: 头文件路径（部分匹配）
            max_depth: BFS 最大深度（防环路兜底）

        Returns:
            受影响翻译单元列表（去重，排序）
        """
        affected: set[str] = set()
        # BFS：从 header_path 出发，反向找所有 include 它的文件，
        # 再把这些文件当作被 include 的对象继续反向找
        queue: list[str] = []
        visited: set[str] = set()

        # 初始：直接 includer
        direct = self.get_direct_includers(header_path)
        for f in direct:
            if f not in visited:
                visited.add(f)
                queue.append(f)
                affected.add(f)

        depth = 0
        while queue and depth < max_depth:
            next_queue: list[str] = []
            for current in queue:
                # 找谁 include 了 current（current 可能本身也是头文件）
                # included_file 存 basename，精确匹配避免子串误匹配（主题A-4）
                current_base = Path(current).name
                rows = self.conn.execute(
                    "SELECT DISTINCT source_file FROM include_dep "
                    "WHERE included_file = ? OR included_file LIKE '%/' || ?",
                    (current_base, current_base),
                ).fetchall()
                for r in rows:
                    f = r["source_file"]
                    if f not in visited:
                        visited.add(f)
                        affected.add(f)
                        next_queue.append(f)
            queue = next_queue
            depth += 1

        return sorted(affected)

    # ------------------------------------------------------------------
    # 3. include 树 — 指定翻译单元 include 了哪些（正向，嵌套）
    # ------------------------------------------------------------------

    def get_include_tree(self, source_path: str, *,
                         max_depth: int = 20,
                         skip_system: bool = False) -> IncludeNode:
        """查询指定翻译单元的完整 include 树

        Args:
            source_path: 翻译单元路径（部分匹配）
            max_depth: 递归最大深度（防环兜底）
            skip_system: 是否跳过系统头文件（is_system=1）

        Returns:
            IncludeNode 根节点，children 为直接 include，递归嵌套
        """
        # 精确定位 source_file（取第一个匹配）
        row = self.conn.execute(
            "SELECT source_file FROM include_dep WHERE source_file LIKE ? LIMIT 1",
            (f"%{source_path}%",),
        ).fetchone()
        root_name = row["source_file"] if row else source_path

        visited: set[str] = set()
        return self._build_tree(root_name, max_depth, skip_system, visited)

    def _build_tree(self, file_path: str, max_depth: int,
                   skip_system: bool, visited: set[str]) -> IncludeNode:
        """递归构建 include 树"""
        node = IncludeNode(file=file_path)

        if file_path in visited or max_depth <= 0:
            return node
        visited.add(file_path)

        rows = self.conn.execute(
            "SELECT included_file, is_system FROM include_dep "
            "WHERE source_file = ? ORDER BY included_file",
            (file_path,),
        ).fetchall()

        for r in rows:
            if skip_system and r["is_system"]:
                continue
            child = self._build_tree(
                r["included_file"], max_depth - 1, skip_system, visited)
            child.is_system = bool(r["is_system"])
            node.children.append(child)

        return node

    # ------------------------------------------------------------------
    # 辅助：统计
    # ------------------------------------------------------------------

    def count_includers(self, header_path: str) -> int:
        """直接 includer 数量（影响面快速评估）"""
        return len(self.get_direct_includers(header_path))


def render_tree(node: IncludeNode, indent: int = 0) -> str:
    """把 include 树渲染为可读文本"""
    prefix = "  " * indent
    tag = " [sys]" if node.is_system else ""
    lines = [f"{prefix}{node.file}{tag}"]
    for child in node.children:
        lines.append(render_tree(child, indent + 1))
    return "\n".join(lines)
