"""
libclang AST Visitor — 核心语义提取

通用设计：所有过滤逻辑基于 ProjectConfig，不硬编码任何项目名称。
换项目只需修改 cpp_semantic_graph.yaml 配置文件。

Phase 0 验证结论:
- 类/继承/虚函数/override 通过 walk_preorder + get_children 提取
- Proxy 方法调用必须用 MEMBER_REF_EXPR (CALL_EXPR.referenced 对 Proxy 返回 None)
- CXX_OVERRIDE_ATTR 通过 get_children 检测
- 虚继承通过 clang_isVirtualBase C API 检测（Python 绑定未封装为方法，直接调 conf.lib）
- MEMBER_REF_EXPR 的 semantic_parent 链可能为空，需行号映射 fallback
"""

import logging

import clang.cindex
from clang.cindex import CursorKind

from .compile_db import CompileDB, CompileCommand
from .config import ProjectConfig
from .models import (
    NodeInfo, EdgeInfo, IncludeDep, ParseResult,
    NodeType, RelationType, make_func_sig_suffix,
)
from .alias_extractor import AliasExtractor
from .friend_extractor import FriendExtractor
from .cursor_utils import get_namespace  # P2-3: 统一 cursor 工具
# 模板特化不参与提取：libclang 对模板特化（如 ThreadDrivenProxy<X>）不产生独立的
# CLASS_DECL 节点（特化名只出现在 CONSTRUCTOR/TYPE_REF 的 spelling 中），
# walk_preorder 找不到含 '<' 的类声明，提取产不出数据。
# P2-5：原 template_extractor.py 为此死代码，已删除；未来若改用 LibTooling 或
# 基于 TYPE_REF 重建特化节点，再新增相应提取器。

logger = logging.getLogger(__name__)


class SemanticExtractor:
    """从 Clang AST 中提取 C++ 语义信息

    所有过滤逻辑通过 ProjectConfig 配置，不硬编码项目名称。
    """

    def __init__(self, config: ProjectConfig):
        """初始化

        Args:
            config: 项目配置（从 cpp_semantic_graph.yaml 加载）
        """
        clang.cindex.Config.set_library_file(config.libclang_path)
        self.index = clang.cindex.Index.create()
        self._config = config
        self._func_location_map: dict[str, list[tuple[int, int, str]]] = {}
        # 复杂场景提取器（阶段 2-3）：类型别名/using 声明、友元关系。
        # 复用 self._config 与 self._make_file_path，保证 file_path 与本类提取一致。
        self._alias_extractor = AliasExtractor()
        self._friend_extractor = FriendExtractor()

    def parse(self, entry: CompileCommand) -> ParseResult:
        """解析单个翻译单元

        Args:
            entry: 编译单元信息（来自 compile_commands.json）
        """
        self._func_location_map = {}
        result = ParseResult(source_path=entry.file)

        try:
            options = clang.cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            if self._config.skip_function_bodies:
                options |= clang.cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES

            tu = self.index.parse(entry.file, args=entry.args, options=options)

            # Check for fatal errors
            diags = list(tu.diagnostics)
            fatal_count = sum(1 for d in diags if d.severity >= 4)
            if fatal_count > 0:
                result.status = "failed"
                result.error_message = f"{fatal_count} fatal errors"
                return result

            # Build function location map first (for fast enclosing function lookup)
            self._build_func_location_map(tu)

            # Extract semantics
            self._extract_classes(tu, result)
            self._extract_functions(tu, result)
            self._extract_inheritance(tu, result)
            self._extract_calls(tu, result)
            self._extract_includes(tu, result)

            # 复杂场景（阶段 2-3）：类型别名/using 声明、友元关系。
            # 传入 config 与 _make_file_path，保证 file_path 与上面提取一致。
            self._extract_complex_scenarios(tu.cursor, result)

            # Deduplicate within this translation unit
            self._deduplicate(result)

        except Exception as e:
            result.status = "failed"
            result.error_message = str(e)
            logger.error(f"Failed to parse {entry.file}: {e}")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_include(self, cursor) -> bool:
        """判断 cursor 是否在项目源码范围内（基于 ProjectConfig）"""
        loc = cursor.location
        if not loc.file:
            return False
        return self._config.should_extract_node(str(loc.file.name))

    def _should_include_for_calls(self, file_path: str) -> bool:
        """判断文件是否应作为调用关系的 callee（基于 ProjectConfig）

        包括项目源码和生成代码（如 ARA COM src-gen）。
        """
        return self._config.should_extract_call(file_path)

    @staticmethod
    def _get_namespace(cursor) -> str:
        """获取 cursor 的完整命名空间路径（P2-3：统一实现见 cursor_utils.get_namespace）"""
        return get_namespace(cursor)

    def _make_file_path(self, file_name: str) -> str:
        """将绝对路径转为相对路径（基于 ProjectConfig）"""
        return self._config.make_relative_path(file_name)

    # ------------------------------------------------------------------
    # Function location map (for enclosing function lookup)
    # ------------------------------------------------------------------

    def _build_func_location_map(self, tu):
        """构建行号范围到函数 key 的映射表（加速 enclosing function 查找）

        key = file_path, value = [(start_line, end_line, func_key), ...]
        """
        for cursor in tu.cursor.walk_preorder():
            if cursor.kind in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL,
                               CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR):
                if not cursor.location.file:
                    continue
                file_name = str(cursor.location.file.name)
                func_key = self._make_function_key(cursor)
                if not func_key:
                    continue
                if file_name not in self._func_location_map:
                    self._func_location_map[file_name] = []
                self._func_location_map[file_name].append(
                    (cursor.extent.start.line, cursor.extent.end.line, func_key)
                )

    def _find_enclosing_function_key(self, cursor) -> str:
        """找到包含此 cursor 的函数的 unique_key

        Phase 0 发现: MEMBER_REF_EXPR 的 semantic_parent 可能不经过
        CXX_METHOD（尤其是 Proxy 方法调用），需要用行号范围匹配。
        """
        # Strategy 1: walk semantic_parent chain (fast, works for most cases)
        parent = cursor.semantic_parent
        while parent:
            if parent.kind in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL,
                               CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR):
                return self._make_function_key(parent)
            parent = parent.semantic_parent

        # Strategy 2: Use pre-built location map (for Proxy method calls etc.)
        if cursor.location.file:
            file_name = str(cursor.location.file.name)
            func_list = self._func_location_map.get(file_name, [])
            line = cursor.location.line
            best_match = None
            best_size = float('inf')
            for start, end, func_key in func_list:
                if start <= line <= end:
                    size = end - start
                    if size < best_size:
                        best_size = size
                        best_match = func_key
            if best_match:
                return best_match

        return ""

    def _make_function_key(self, func_cursor) -> str:
        """根据函数 cursor 生成 unique_key"""
        name = func_cursor.spelling
        namespace = self._get_namespace(func_cursor)
        file_path = ""
        if func_cursor.location.file:
            file_path = self._make_file_path(
                str(func_cursor.location.file.name)
            )

        # Include parent class in namespace
        grandparent = func_cursor.semantic_parent
        # P1-E-1: 含 STRUCT_DECL，struct 的成员函数也纳入所属类 namespace
        if grandparent and grandparent.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
            namespace = f"{self._get_namespace(grandparent)}::{grandparent.spelling}"

        # 参数签名后缀区分重载（与 _extract_functions 的 param_types/is_const 同源）
        # 用同一 make_func_sig_suffix 保证 caller key 与 node key 逐字节一致
        params = [
            (a.type.spelling if a.type else a.spelling)
            for a in func_cursor.get_arguments()
            if a.kind == CursorKind.PARM_DECL
        ]
        is_const = (
            func_cursor.is_const_method()
            if func_cursor.kind == CursorKind.CXX_METHOD else False
        )
        sig_suffix = make_func_sig_suffix(params, is_const)
        return f"{NodeType.FUNCTION.value}|{namespace}|{name}|{file_path}{sig_suffix}"

    # ------------------------------------------------------------------
    # Class extraction
    # ------------------------------------------------------------------

    def _extract_classes(self, tu, result: ParseResult):
        """提取类定义"""
        seen_keys = set()

        for cursor in tu.cursor.walk_preorder():
            if cursor.kind not in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                continue
            if not self._should_include(cursor):
                continue
            if not cursor.is_definition():
                continue

            name = cursor.spelling
            if not name:
                continue

            namespace = self._get_namespace(cursor)
            file_path = self._make_file_path(str(cursor.location.file.name))

            # Check for template
            template_params = []
            is_template_specialization = False
            for child in cursor.get_children():
                if child.kind == CursorKind.TEMPLATE_TYPE_PARAMETER:
                    template_params.append(child.spelling)
                if child.kind == CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION:
                    is_template_specialization = True

            # Check for abstract class
            is_abstract = False
            for child in cursor.get_children():
                if (child.kind == CursorKind.CXX_METHOD
                        and child.is_virtual_method()
                        and child.is_pure_virtual_method()):
                    is_abstract = True
                    break

            # Determine access for nested classes
            access = "public"
            if cursor.access_specifier:
                access = cursor.access_specifier.name.lower()

            # 标记是否属于项目源码（用于后续文档关联过滤）
            abs_path = str(cursor.location.file.name)
            is_project = self._config.is_project_source(abs_path)

            node = NodeInfo(
                type=NodeType.CLASS if cursor.kind == CursorKind.CLASS_DECL else NodeType.STRUCT,
                name=name,
                namespace=namespace,
                file_path=file_path,
                start_line=cursor.extent.start.line,
                end_line=cursor.extent.end.line,
                extra_info={
                    "is_abstract": is_abstract,
                    "access": access,
                    "template_params": template_params if template_params else None,
                    "is_template_specialization": is_template_specialization,
                    "is_project": is_project,
                },
            )

            if node.unique_key not in seen_keys:
                seen_keys.add(node.unique_key)
                result.nodes.append(node)

                # BELONGS_TO edge for nested classes
                parent = cursor.semantic_parent
                if parent and parent.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                    parent_ns = self._get_namespace(parent)
                    parent_file = self._make_file_path(str(parent.location.file.name))
                    # P1-E-1: parent 可能是 struct，type 要匹配否则 BELONGS_TO 边解析不到目标节点
                    parent_type = NodeType.CLASS.value if parent.kind == CursorKind.CLASS_DECL else NodeType.STRUCT.value
                    parent_key = f"{parent_type}|{parent_ns}|{parent.spelling}|{parent_file}"
                    result.edges.append(EdgeInfo(
                        relation_type=RelationType.BELONGS_TO,
                        from_unique_key=node.unique_key,
                        to_unique_key=parent_key,
                        extra_info={"access": access},
                    ))

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_functions(self, tu, result: ParseResult):
        """提取函数定义和声明"""
        seen_keys = set()

        for cursor in tu.cursor.walk_preorder():
            if cursor.kind not in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL,
                                    CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR):
                continue
            if not self._should_include(cursor):
                continue

            # P1-E-2: 跳过声明节点（.h），只保留定义（.cpp），避免声明/定义产生两个节点
            # 例外：纯虚函数只有声明（=0，无定义），必须保留
            is_pure_virtual_early = (cursor.kind == CursorKind.CXX_METHOD
                                     and cursor.is_virtual_method()
                                     and cursor.is_pure_virtual_method())
            if not cursor.is_definition() and not is_pure_virtual_early:
                continue

            name = cursor.spelling
            if not name:
                continue

            namespace = self._get_namespace(cursor)
            file_path = self._make_file_path(str(cursor.location.file.name))

            # Determine parent class
            parent_class = ""
            parent_type = NodeType.CLASS.value  # P1-E-1: struct 时改为 STRUCT，BELONGS_TO 边 type 匹配
            parent_file_for_key = file_path  # 默认同文件（.h 内联定义）
            parent = cursor.semantic_parent
            if parent and parent.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                parent_class = parent.spelling
                parent_type = NodeType.CLASS.value if parent.kind == CursorKind.CLASS_DECL else NodeType.STRUCT.value
                namespace = self._get_namespace(parent)
                # belongs_to 回归修复：out-of-line 定义（.cpp）的成员函数，其类节点在 .h。
                # parent_key 必须用父类 cursor 的 location file（.h），否则 file_path 用
                # 函数的 .cpp 路径 → 与 .h 的 class 节点 unique_key 失配 → belongs_to 边丢失。
                if parent.location.file:
                    parent_file_for_key = self._make_file_path(str(parent.location.file.name))

            # Function properties
            is_virtual = cursor.is_virtual_method() if cursor.kind == CursorKind.CXX_METHOD else False
            is_pure_virtual = cursor.is_pure_virtual_method() if is_virtual else False

            # Override detection via CXX_OVERRIDE_ATTR
            is_override = False
            for child in cursor.get_children():
                if child.kind == CursorKind.CXX_OVERRIDE_ATTR:
                    is_override = True
                    break

            is_static = cursor.is_static_method() if cursor.kind == CursorKind.CXX_METHOD else False
            is_const = cursor.is_const_method() if cursor.kind == CursorKind.CXX_METHOD else False

            # Build signature
            result_type = cursor.result_type.spelling if cursor.result_type else ""
            params = []
            for arg in cursor.get_arguments():
                if arg.kind == CursorKind.PARM_DECL:
                    params.append(arg.type.spelling if arg.type else arg.spelling)
            signature = f"{result_type} {name}({', '.join(params)})"

            if is_const:
                signature += " const"
            if is_override:
                signature += " override"
            if is_pure_virtual:
                signature += " = 0"

            # Access specifier
            access = "public"
            if cursor.access_specifier:
                access = cursor.access_specifier.name.lower()

            func_namespace = f"{namespace}::{parent_class}" if parent_class else namespace

            # 标记是否属于项目源码（用于后续文档关联过滤）
            abs_path = str(cursor.location.file.name)
            is_project = self._config.is_project_source(abs_path)

            node = NodeInfo(
                type=NodeType.FUNCTION,
                name=name,
                namespace=func_namespace,
                file_path=file_path,
                start_line=cursor.location.line,
                end_line=cursor.extent.end.line,
                extra_info={
                    "signature": signature,
                    "is_virtual": is_virtual,
                    "is_pure_virtual": is_pure_virtual,
                    "is_override": is_override,
                    "is_static": is_static,
                    "is_const": is_const,
                    "access": access,
                    "parent_class": parent_class,
                    "result_type": result_type,
                    "param_types": params,
                    "is_project": is_project,
                },
            )

            if node.unique_key not in seen_keys:
                seen_keys.add(node.unique_key)
                result.nodes.append(node)

                # BELONGS_TO edge
                if parent_class:
                    parent_key = f"{parent_type}|{namespace}|{parent_class}|{parent_file_for_key}"
                    result.edges.append(EdgeInfo(
                        relation_type=RelationType.BELONGS_TO,
                        from_unique_key=node.unique_key,
                        to_unique_key=parent_key,
                        extra_info={"access": access},
                    ))

                # OVERRIDES edge: from=派生类函数, to=基类虚函数
                # 注: AST 遍历时无法直接获取基类虚函数节点，to_unique_key
                #     在入库阶段通过继承边 + 函数名解析补全
                if is_override and parent_class:
                    result.edges.append(EdgeInfo(
                        relation_type=RelationType.OVERRIDES,
                        from_unique_key=node.unique_key,
                        to_unique_key="",
                        extra_info={
                            "function_name": name,
                            "derived_class": parent_class,  # 当前所属类（派生类）
                            "base_namespace": namespace,
                            "_needs_resolution": True,
                            "_resolve_hint": "override",  # 标记为 override 解析类型
                        },
                    ))

    # ------------------------------------------------------------------
    # Inheritance extraction
    # ------------------------------------------------------------------

    def _extract_inheritance(self, tu, result: ParseResult):
        """提取继承关系（含虚继承标记）"""
        for cursor in tu.cursor.walk_preorder():
            if cursor.kind not in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                continue
            if not self._should_include(cursor):
                continue
            if not cursor.is_definition():
                continue

            child_name = cursor.spelling
            child_namespace = self._get_namespace(cursor)
            child_file = self._make_file_path(str(cursor.location.file.name))
            child_key = f"{NodeType.CLASS.value}|{child_namespace}|{child_name}|{child_file}"

            for base_spec in cursor.get_children():
                if base_spec.kind != CursorKind.CXX_BASE_SPECIFIER:
                    continue

                parent_ref = base_spec.referenced
                parent_name = parent_ref.spelling if parent_ref else base_spec.spelling
                parent_namespace = self._get_namespace(parent_ref) if parent_ref else ""

                access = "public"
                if base_spec.access_specifier:
                    access = base_spec.access_specifier.name.lower()

                access_map = {
                    "public": RelationType.INHERITS_PUBLIC,
                    "protected": RelationType.INHERITS_PROTECTED,
                    "private": RelationType.INHERITS_PRIVATE,
                }
                relation_type = access_map.get(access, RelationType.INHERITS_PUBLIC)

                # 虚继承检测：直接调 libclang C API clang_isVirtualBase
                # Python 绑定注册表有此函数（cindex.py 函数表），但未封装为 Cursor 方法
                is_virtual_base = False
                try:
                    is_virtual_base = clang.cindex.conf.lib.clang_isVirtualBase(base_spec)
                except (AttributeError, TypeError):
                    pass  # C API 不可用时静默降级

                parent_file = ""
                if parent_ref and parent_ref.location.file:
                    parent_file = self._make_file_path(str(parent_ref.location.file.name))

                parent_key = f"{NodeType.CLASS.value}|{parent_namespace}|{parent_name}|{parent_file}"

                extra_info = {}
                if is_virtual_base:
                    extra_info["is_virtual"] = True

                result.edges.append(EdgeInfo(
                    relation_type=relation_type,
                    from_unique_key=child_key,
                    to_unique_key=parent_key,
                    extra_info=extra_info if extra_info else None,
                ))

    # ------------------------------------------------------------------
    # Call relationship extraction
    # ------------------------------------------------------------------

    # Standard library / internal patterns to filter out from call edges
    _CALL_FILTER_NAMES = {
        "basic_string", "allocator", "pair", "tuple", "vector", "map", "set",
        "unordered_map", "unordered_set", "shared_ptr", "unique_ptr", "weak_ptr",
        "_M_", "_r_", "__", "make_shared", "make_unique", "make_pair",
        "basic_ostream", "basic_istream",
    }

    def _should_filter_call(self, callee_name: str) -> bool:
        """过滤不需要的调用关系（标准库、编译器内部等）"""
        if not callee_name:
            return True
        # 过滤 C++ 运算符重载（operator+, operator= 等），
        # 但不过滤以 "operator" 开头的合法函数名（如 operationsManager）。
        # 运算符重载特征："operator" 后跟运算符符号（非字母数字）或刚好 8 字符。
        if callee_name.startswith("operator") and (
            len(callee_name) == 8 or not callee_name[8].isalnum()
        ):
            return True
        if callee_name.startswith(("_M_", "_r_", "__")):
            return True
        if callee_name in self._CALL_FILTER_NAMES:
            return True
        if len(callee_name) > 1 and callee_name[0] == "_" and callee_name[1].isupper():
            return True
        return False

    def _extract_calls(self, tu, result: ParseResult):
        """提取函数调用关系

        Phase 0 验证关键发现:
        - CALL_EXPR.referenced 对 Proxy 方法返回 None
        - 必须用 MEMBER_REF_EXPR 提取 Proxy 方法调用
        - 调用关系过滤基于 ProjectConfig.should_extract_call()
        """
        for cursor in tu.cursor.walk_preorder():
            if cursor.kind == CursorKind.CALL_EXPR:
                self._process_call_expr(cursor, result)
            elif cursor.kind == CursorKind.MEMBER_REF_EXPR:
                self._process_member_ref_expr(cursor, result)

    def _process_call_expr(self, cursor, result: ParseResult):
        """处理 CALL_EXPR — 直接函数调用"""
        callee = cursor.referenced
        if not callee:
            return

        callee_name = callee.spelling
        if self._should_filter_call(callee_name):
            return

        caller_key = self._find_enclosing_function_key(cursor)
        if not caller_key:
            return

        # Filter: only keep calls where callee is in project source or generated code
        if callee.location.file:
            callee_file = str(callee.location.file)
            if not self._should_include_for_calls(callee_file):
                return

        # Determine call type
        call_type = "direct"
        if callee.is_virtual_method():
            call_type = "virtual_dispatch"

        # Build callee info
        callee_namespace = self._get_namespace(callee)
        callee_parent = callee.semantic_parent
        callee_parent_class = ""
        if callee_parent and callee_parent.kind == CursorKind.CLASS_DECL:
            callee_parent_class = callee_parent.spelling
            callee_namespace = self._get_namespace(callee_parent)

        callee_file = ""
        if callee.location.file:
            callee_file = self._make_file_path(str(callee.location.file))

        # callee 参数类型：供 graph_db 精确匹配到具体重载（区分同名重载）
        callee_params = [
            (a.type.spelling if a.type else a.spelling)
            for a in callee.get_arguments()
            if a.kind == CursorKind.PARM_DECL
        ]
        callee_is_const = (
            callee.is_const_method()
            if callee.kind == CursorKind.CXX_METHOD else False
        )

        result.edges.append(EdgeInfo(
            relation_type=RelationType.CALLS_DIRECT if call_type == "direct" else RelationType.CALLS_VIRTUAL,
            from_unique_key=caller_key,
            to_unique_key="",
            extra_info={
                "callee_name": callee_name,
                "callee_namespace": callee_namespace,
                "callee_parent_class": callee_parent_class,
                "callee_file": callee_file,
                "callee_param_types": callee_params,
                "callee_is_const": callee_is_const,
                "call_type": call_type,
                "call_line": cursor.location.line,
                "_needs_resolution": True,
            },
        ))

    def _process_member_ref_expr(self, cursor, result: ParseResult):
        """处理 MEMBER_REF_EXPR — Proxy 等成员方法调用

        Phase 0 发现: 对 Proxy 方法调用 (如 BootChainChanged),
        CALL_EXPR.referenced 返回 None, 但 MEMBER_REF_EXPR.referenced 可以解析.
        """
        ref = cursor.referenced
        if not ref:
            return

        # 只处理方法/函数调用，过滤字段访问（P0-6 修复）
        # obj.field 的 ref 是 FIELD_DECL，其 semantic_parent 也是 CLASS_DECL，
        # 不加此过滤会产生 callee_name=字段名 的虚假调用边
        if ref.kind not in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL):
            return

        callee_name = ref.spelling
        if self._should_filter_call(callee_name):
            return

        # Only process if this is a method call (has class parent)
        parent = ref.semantic_parent
        if not parent or parent.kind != CursorKind.CLASS_DECL:
            return

        # Filter: only keep calls where callee is in project source or generated code
        if ref.location.file:
            ref_file = str(ref.location.file)
            if not self._should_include_for_calls(ref_file):
                return

        caller_key = self._find_enclosing_function_key(cursor)
        if not caller_key:
            return

        callee_parent_class = parent.spelling
        callee_namespace = self._get_namespace(parent)

        callee_file = ""
        if ref.location.file:
            callee_file = self._make_file_path(str(ref.location.file))

        is_virtual = ref.is_virtual_method()

        # callee 参数类型：供 graph_db 精确匹配到具体重载
        callee_params = [
            (a.type.spelling if a.type else a.spelling)
            for a in ref.get_arguments()
            if a.kind == CursorKind.PARM_DECL
        ]
        callee_is_const = (
            ref.is_const_method()
            if ref.kind == CursorKind.CXX_METHOD else False
        )

        result.edges.append(EdgeInfo(
            relation_type=RelationType.CALLS_VIRTUAL if is_virtual else RelationType.CALLS_DIRECT,
            from_unique_key=caller_key,
            to_unique_key="",
            extra_info={
                "callee_name": callee_name,
                "callee_namespace": callee_namespace,
                "callee_parent_class": callee_parent_class,
                "callee_file": callee_file,
                "callee_param_types": callee_params,
                "callee_is_const": callee_is_const,
                "call_type": "virtual_dispatch" if is_virtual else "member_call",
                "call_line": cursor.location.line,
                "_needs_resolution": True,
            },
        ))

    # ------------------------------------------------------------------
    # Include dependency extraction
    # ------------------------------------------------------------------

    def _extract_includes(self, tu, result: ParseResult):
        """提取 #include 依赖关系

        is_system 判定（libclang 能力边界）:
        - INCLUSION_DIRECTIVE.included_file 在本版本恒为 None
        - 无 is_system/is_angled 属性
        - displayname 已去除尖括号/引号，无法直接区分
        故用 token 读原始 #include 写法：`#include<...>` 为系统头，`#include"..."` 为项目头。
        这是 C++ 标准区分系统头/用户头的方式，比路径前缀判断可靠。
        """
        source_file = self._make_file_path(str(tu.spelling))

        for cursor in tu.cursor.get_children():
            if cursor.kind != CursorKind.INCLUSION_DIRECTIVE:
                continue

            included_file = ""
            if hasattr(cursor, 'included_file') and cursor.included_file:
                included_file = str(cursor.included_file)
            elif cursor.displayname:
                included_file = cursor.displayname.strip('"').strip('<>')

            if not included_file:
                continue

            # 用 token 原始写法判断系统头：< > = 系统，" " = 项目
            is_system = self._is_system_include(cursor)

            included_rel = self._make_file_path(included_file)
            result.includes.append(IncludeDep(
                source_file=source_file,
                included_file=included_rel,
                is_system=is_system,
            ))

    @staticmethod
    def _is_system_include(cursor) -> bool:
        """判断 include 是否为系统头（尖括号形式）

        通过 token 读原始 #include 写法：
          #include<header>    → True（系统/库头）
          #include"header"    → False（项目头）

        token 拆分实测为 ['#', 'include', '"header"' 或 '<' ...]，
        故扫描 token 找第一个以 < 或 " 开头者，而非写死索引。
        """
        try:
            for tok in cursor.get_tokens():
                s = tok.spelling
                if s.startswith("<"):
                    return True
                if s.startswith('"'):
                    return False
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Complex scenarios (阶段 2-3): 模板/别名/友元
    # ------------------------------------------------------------------

    def _extract_complex_scenarios(self, tu_cursor, result: ParseResult):
        """提取类型别名/using 声明/友元关系，并入 result

        模板实例化（TemplateExtractor）当前不调用：libclang 不为模板特化产生
        独立 CLASS_DECL 节点，walk_preorder 找不到含 '<' 的类，提取无产出。
        类型别名与友元由 AliasExtractor/FriendExtractor 提取，二者复用
        self._config 与 self._make_file_path，保证 file_path 与本类提取一致。
        """
        alias_nodes, alias_edges = self._alias_extractor.extract_type_aliases(
            tu_cursor, self._config, self._make_file_path,
        )
        friend_nodes, friend_edges = self._friend_extractor.extract_friends(
            tu_cursor, self._config, self._make_file_path,
        )
        result.nodes.extend(alias_nodes)
        result.nodes.extend(friend_nodes)
        result.edges.extend(alias_edges)
        result.edges.extend(friend_edges)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self, result: ParseResult):
        """翻译单元内部去重"""
        seen_nodes = {}
        unique_nodes = []
        for node in result.nodes:
            if node.unique_key not in seen_nodes:
                seen_nodes[node.unique_key] = node
                unique_nodes.append(node)
        result.nodes = unique_nodes

        seen_edges = set()
        unique_edges = []
        for edge in result.edges:
            rt = edge.relation_type.value if isinstance(edge.relation_type, RelationType) else edge.relation_type
            # 去重 key 加入 call_line，保留同一函数内多次调用同一目标的每个调用点
            # 修复前：(caller, callee) 去重导致 UpdateThread 内 7 处 HandleError 只保留 1 条
            call_line = (edge.extra_info or {}).get("call_line", 0)
            if edge.to_unique_key:
                key = (edge.from_unique_key, edge.to_unique_key, rt, call_line)
            else:
                # 未解析边：去重 key 需含 callee 的命名空间和父类，
                # 否则 A::init() 和 B::init() 会被误判为重复
                callee = (edge.extra_info or {}).get("callee_name", "")
                callee_ns = (edge.extra_info or {}).get("callee_namespace", "")
                callee_parent = (edge.extra_info or {}).get("callee_parent_class", "")
                key = (edge.from_unique_key,
                       f"__unresolved__{callee_ns}::{callee_parent}::{callee}@{call_line}", rt)
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(edge)
        result.edges = unique_edges
