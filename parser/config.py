"""
项目配置加载模块

从 cpp_semantic_graph.yaml 加载项目配置，提供统一的路径判断接口。
工具的所有过滤逻辑都基于此配置，不硬编码任何项目名称。
"""

import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectConfig:
    """项目配置"""
    name: str = ""
    compile_commands: str = ""
    source_paths: list[str] = field(default_factory=list)
    generated_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    libclang_path: str = "/usr/lib/llvm-18/lib/libclang.so.1"
    skip_function_bodies: bool = False
    max_workers: int = 1
    template_whitelist: str = ""

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "ProjectConfig":
        """从 YAML 文件加载配置"""
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        project = data.get("project", {})
        parse_opts = data.get("parse_options", {})

        return cls(
            name=project.get("name", ""),
            compile_commands=project.get("compile_commands", ""),
            source_paths=data.get("source_paths", []),
            generated_paths=data.get("generated_paths", []),
            exclude_paths=data.get("exclude_paths", []),
            libclang_path=data.get("libclang_path", "/usr/lib/llvm-18/lib/libclang.so.1"),
            skip_function_bodies=parse_opts.get("skip_function_bodies", False),
            max_workers=parse_opts.get("max_workers", 1),
            template_whitelist=data.get("template_whitelist", ""),
        )

    # ------------------------------------------------------------------
    # 核心路径判断接口 — 所有过滤逻辑统一走这里
    # ------------------------------------------------------------------

    def is_project_source(self, file_path: str) -> bool:
        """判断文件是否属于项目源码范围

        包括 source_paths 和 generated_paths 中的文件。
        用于：节点提取过滤、调用关系 callee 过滤。
        """
        return self._matches_any(file_path, self.source_paths + self.generated_paths)

    def is_project_owned(self, file_path: str) -> bool:
        """判断文件是否属于项目自有代码（不含生成代码）

        用于：决定节点的归属权重、去重策略等。
        """
        return self._matches_any(file_path, self.source_paths)

    def is_generated(self, file_path: str) -> bool:
        """判断文件是否属于生成代码（如 ARA COM src-gen）

        用于：生成代码入库策略（头文件入库、源文件不入库）。
        """
        return self._matches_any(file_path, self.generated_paths)

    def is_excluded(self, file_path: str) -> bool:
        """判断文件是否应完全忽略"""
        return self._matches_any(file_path, self.exclude_paths)

    def should_extract_node(self, file_path: str) -> bool:
        """判断是否应从该文件提取节点

        条件：在项目源码或生成代码范围内，且不在排除列表中。
        """
        if self.is_excluded(file_path):
            return False
        return self.is_project_source(file_path)

    def should_extract_call(self, callee_file: str) -> bool:
        """判断是否应提取指向该文件的调用关系

        条件：callee 在项目源码或生成代码范围内。
        这确保 Proxy 方法等生成代码的调用能被保留。
        """
        return self.is_project_source(callee_file)

    @staticmethod
    def _matches_any(file_path: str, patterns: list[str]) -> bool:
        """检查 file_path 是否匹配任一 pattern

        匹配规则：pattern 是 file_path 的子串。
        例如 pattern="hq_ota_service/src" 匹配
        "/path/to/hq_ota_service/src/main.cpp"
        """
        if not file_path or not patterns:
            return False
        for pattern in patterns:
            if pattern in file_path:
                return True
        return False

    def make_relative_path(self, file_path: str) -> str:
        """将绝对路径转为相对路径

        基于 source_paths 中的第一个匹配项做截断。
        例如: /path/to/hq_ota_service/src/main.cpp → src/main.cpp
        """
        if not file_path:
            return ""
        for pattern in self.source_paths:
            if pattern in file_path:
                idx = file_path.index(pattern)
                rel = file_path[idx + len(pattern):].lstrip("/")
                return f"{pattern.split('/')[-1]}/{rel}" if "/" not in pattern else rel
        for pattern in self.generated_paths:
            if pattern in file_path:
                idx = file_path.index(pattern)
                return file_path[idx:].lstrip("/")
        # Fallback: just filename
        return Path(file_path).name
