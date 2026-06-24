"""MD 文档解析与切片

按二级标题（##）自动切片，粒度对齐代码模块/类。
一级标题作为文档元数据，三级及以下归入所属二级切片。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DocSection:
    """文档切片"""
    title: str                    ## 标题文字
    doc_title: str                # 一级标题（文档标题）
    file_path: str                # 文件路径（相对项目根）
    start_line: int               # 切片起始行号
    end_line: int                 # 切片结束行号
    section_level: int            # 切片标题级别（=2，按 ## 切）
    content: str                  # 切片内容（不含标题行）
    content_preview: str          # 切片全文（用于搜索和关联匹配）
    content_hash: str             # SHA256 内容哈希
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    unique_key: str = ""          # 去重键: doc_section|file_path|start_line

    def __post_init__(self):
        if not self.content_preview and self.content:
            self.content_preview = self.content  # 存全文，供搜索和关联匹配
        if not self.word_count:
            # 中英混合字数统计：中文按字计，英文按词计
            self.word_count = self._count_words(self.content)
        if not self.content_hash:
            self.content_hash = hashlib.sha256(
                self.content.encode("utf-8")
            ).hexdigest()[:16]
        if not self.unique_key:
            self.unique_key = f"doc_section|{self.file_path}|{self.start_line}"

    @staticmethod
    def _count_words(text: str) -> int:
        """中英混合字数统计"""
        # 中文字符数
        cn = len(re.findall(r'[一-鿿]', text))
        # 英文单词数（排除标点和代码块）
        en = len(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text))
        return cn + en


class DocParser:
    """Markdown 文档解析与切片"""

    def __init__(self, min_section_words: int = 20):
        """初始化

        Args:
            min_section_words: 低于此字数的切片合并到上一节
        """
        self.min_section_words = min_section_words

    def parse_file(self, file_path: str | Path) -> list[DocSection]:
        """解析单个 Markdown 文件，按 ## 切片

        Args:
            file_path: 文件路径

        Returns:
            切片列表
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning("文件不存在: %s", file_path)
            return []

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file_path.read_text(encoding="gbk")
            except Exception:
                logger.warning("无法读取文件: %s", file_path)
                return []

        lines = text.split("\n")
        doc_title = self._extract_doc_title(lines)
        sections = self._split_sections(lines, str(file_path), doc_title)

        # 合并短切片
        if self.min_section_words > 0:
            sections = self._merge_short_sections(sections)

        return sections

    def _extract_doc_title(self, lines: list[str]) -> str:
        """提取文档一级标题"""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()
        # 无一级标题，用文件名
        return ""

    def _split_sections(
        self,
        lines: list[str],
        file_path: str,
        doc_title: str,
    ) -> list[DocSection]:
        """按 ## 标题切片"""
        sections: list[DocSection] = []
        current_heading = ""
        current_level = 0
        current_start = 0
        current_lines: list[str] = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            # 检测 ## 标题
            match = re.match(r'^(#{2,6})\s+(.+)', stripped)
            if match:
                level = len(match.group(1))
                heading = match.group(2).strip()

                if level == 2:
                    # 保存上一个切片
                    if current_heading or current_lines:
                        sections.append(self._make_section(
                            heading=current_heading,
                            level=current_level or 2,
                            start_line=current_start,
                            end_line=i - 1,
                            content_lines=current_lines,
                            file_path=file_path,
                            doc_title=doc_title,
                        ))

                    # 开始新切片
                    current_heading = heading
                    current_level = level
                    current_start = i
                    current_lines = []
                else:
                    # ### 及以下：归入当前切片
                    current_lines.append(line)
            else:
                current_lines.append(line)

        # 最后一个切片
        if current_heading or current_lines:
            sections.append(self._make_section(
                heading=current_heading or doc_title or Path(file_path).stem,
                level=current_level or 2,
                start_line=current_start,
                end_line=len(lines) - 1,
                content_lines=current_lines,
                file_path=file_path,
                doc_title=doc_title,
            ))

        # 如果没有 ## 标题，整个文档作为一个切片
        if not sections:
            sections.append(self._make_section(
                heading=doc_title or Path(file_path).stem,
                level=1,
                start_line=0,
                end_line=len(lines) - 1,
                content_lines=lines,
                file_path=file_path,
                doc_title=doc_title,
            ))

        return sections

    def _make_section(
        self,
        heading: str,
        level: int,
        start_line: int,
        end_line: int,
        content_lines: list[str],
        file_path: str,
        doc_title: str,
    ) -> DocSection:
        """创建切片对象"""
        content = "\n".join(content_lines).strip()

        # 清理标题中的 markdown 链接语法
        clean_heading = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', heading)
        clean_heading = re.sub(r'`([^`]+)`', r'\1', clean_heading)

        return DocSection(
            title=clean_heading,
            doc_title=doc_title,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            section_level=level,
            content=content,
            content_preview=content,  # 存全文
            content_hash="",
            word_count=0,
            unique_key="",
        )

    def _merge_short_sections(self, sections: list[DocSection]) -> list[DocSection]:
        """合并短切片到上一节"""
        if not sections:
            return sections

        merged: list[DocSection] = [sections[0]]

        for sec in sections[1:]:
            if sec.word_count < self.min_section_words and merged:
                # 合并到上一节
                prev = merged[-1]
                prev.content += "\n\n" + sec.content
                prev.end_line = sec.end_line
                prev.content_preview = prev.content  # 存全文
                prev.content_hash = hashlib.sha256(
                    prev.content.encode("utf-8")
                ).hexdigest()[:16]
                prev.word_count = prev._count_words(prev.content)
                prev.unique_key = f"doc_section|{prev.file_path}|{prev.start_line}"
            else:
                merged.append(sec)

        return merged
