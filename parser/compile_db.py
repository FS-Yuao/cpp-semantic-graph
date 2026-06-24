"""
compile_commands.json 加载与参数清洗模块

Phase 0 验证发现:
- -isystem 和路径是分开的两个参数 → 合并为 -isystem/path
- -o output, -c, -W*, -pedantic 需要删除
- 源文件路径由 parse() 单独传入
"""

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CompileCommand:
    """单个编译单元的编译命令"""
    file: str
    directory: str
    args: list[str]
    output: str = ""

    @property
    def is_generated(self) -> bool:
        """是否是 ARA COM 生成代码"""
        return "src-gen" in self.file

    @property
    def is_header(self) -> bool:
        return self.file.endswith((".h", ".hpp", ".hxx"))

    @property
    def is_source(self) -> bool:
        return self.file.endswith((".cpp", ".cxx", ".cc", ".c"))


class CompileDB:
    """compile_commands.json 加载与清洗"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._entries: list[CompileCommand] = []
        self._load()

    def _load(self):
        """加载 compile_commands.json"""
        if not self.db_path.exists():
            raise FileNotFoundError(f"compile_commands.json not found: {self.db_path}")

        with open(self.db_path) as f:
            data = json.load(f)

        for entry in data:
            file_path = entry.get("file", "")
            directory = entry.get("directory", "")
            command = entry.get("command", "")

            if not file_path or not command:
                continue

            # Parse command string to args
            raw_args = shlex.split(command)
            # Skip compiler path (first element)
            raw_args = raw_args[1:]

            # Clean args
            clean_args = self._clean_args(raw_args)
            output = self._extract_output(raw_args)

            # Make file path absolute
            if not Path(file_path).is_absolute():
                file_path = str(Path(directory) / file_path)

            self._entries.append(CompileCommand(
                file=file_path,
                directory=directory,
                args=clean_args,
                output=output,
            ))

    @staticmethod
    def _clean_args(raw_args: list[str]) -> list[str]:
        """清洗编译参数，使其适用于 libclang 解析

        Phase 0 验证的清洗规则:
        1. 合并 -isystem + path → -isystem/path
        2. 删除 -o output
        3. 删除 -c
        4. 删除 -W* / -pedantic
        5. 删除源文件路径
        """
        clean = []
        skip_next = False

        for i, arg in enumerate(raw_args):
            if skip_next:
                skip_next = False
                continue

            # -o output: skip both
            if arg == "-o":
                skip_next = True
                continue

            # -c: skip
            if arg == "-c":
                continue

            # Source file: skip
            if arg.endswith((".cpp", ".cxx", ".cc", ".c")):
                continue

            # -isystem + path: merge
            if arg == "-isystem" and i + 1 < len(raw_args):
                clean.append(f"-isystem{raw_args[i + 1]}")
                skip_next = True
                continue

            # Warning flags: skip (not needed for parsing, may cause issues)
            if arg.startswith("-W") or arg == "-pedantic":
                continue

            clean.append(arg)

        return clean

    @staticmethod
    def _extract_output(raw_args: list[str]) -> str:
        """提取 -o 输出路径"""
        for i, arg in enumerate(raw_args):
            if arg == "-o" and i + 1 < len(raw_args):
                return raw_args[i + 1]
        return ""

    def get_entries(self, filter_path: str = None,
                    include_generated: bool = True,
                    include_headers: bool = False) -> list[CompileCommand]:
        """获取编译单元条目

        Args:
            filter_path: 只返回包含此路径的条目
            include_generated: 是否包含 ARA COM 生成代码
            include_headers: 是否包含头文件（compile_commands.json 通常不含）
        """
        entries = self._entries

        if filter_path:
            entries = [e for e in entries if filter_path in e.file]

        if not include_generated:
            entries = [e for e in entries if not e.is_generated]

        if not include_headers:
            entries = [e for e in entries if not e.is_header]

        return entries

    def get_entry_for_file(self, file_path: str) -> CompileCommand | None:
        """获取指定文件的编译条目"""
        for entry in self._entries:
            if entry.file == file_path:
                return entry
        # Fuzzy match
        for entry in self._entries:
            if file_path in entry.file or entry.file.endswith(file_path):
                return entry
        return None

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def source_count(self) -> int:
        return sum(1 for e in self._entries if e.is_source and not e.is_generated)

    @property
    def generated_count(self) -> int:
        return sum(1 for e in self._entries if e.is_generated)
