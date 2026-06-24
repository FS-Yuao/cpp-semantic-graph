"""架构分析查询接口

提供 3 个架构分析专用查询：
1. 查接口的所有实现 (get_interface_implementations)
2. 查虚函数的所有重写 (get_virtual_function_overrides)
3. 查类的虚函数清单 / 虚表 (get_class_virtual_table)

构建于 PolymorphismQuery 和 InheritanceQuery 之上。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .polymorphism_query import PolymorphismQuery, VirtualFuncInfo, OverrideInfo
from .inheritance_query import InheritanceQuery, InheritanceNode

logger = logging.getLogger(__name__)


@dataclass
class InterfaceImpl:
    """接口实现信息"""
    class_name: str
    namespace: str
    file_path: str
    depth: int                      # 距接口的继承深度
    is_abstract: bool               # 是否为抽象类


@dataclass
class VTableEntry:
    """虚表条目"""
    function_name: str
    signature: str
    declaring_class: str            # 首次声明的类
    is_pure_virtual: bool
    is_overridden: bool             # 是否有子类 override
    override_count: int
    override_implementations: list[str] = field(default_factory=list)
    # override_implementations 格式: "ClassName::funcName"


class ArchitectureQuery:
    """架构分析查询"""

    def __init__(self, db_path: str):
        self._pq = PolymorphismQuery(db_path)
        self._iq = InheritanceQuery(db_path)

    def close(self):
        self._pq.close()
        self._iq.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 1. 接口实现查询
    # ------------------------------------------------------------------

    def get_interface_implementations(
        self,
        interface_name: str,
        include_abstract: bool = False,
    ) -> list[InterfaceImpl]:
        """查询接口类的所有实现子类（递归所有派生）

        默认只返回非抽象的叶子类（实际可实例化的实现）。

        Args:
            interface_name: 接口/抽象类名
            include_abstract: 是否包含中间抽象类

        Returns:
            实现类信息列表
        """
        # 获取所有子类（递归 down）
        chain = self._iq.get_full_inheritance_chain(interface_name, "down")

        results: list[InterfaceImpl] = []
        for node in chain:
            is_abstract = self._pq._is_abstract_class(node.name)

            if not include_abstract and is_abstract:
                continue

            results.append(InterfaceImpl(
                class_name=node.name,
                namespace=node.namespace,
                file_path=node.file_path,
                depth=node.depth,
                is_abstract=is_abstract,
            ))

        return results

    # ------------------------------------------------------------------
    # 2. 虚函数重写查询
    # ------------------------------------------------------------------

    def get_virtual_function_overrides(
        self,
        func_name: str,
        class_name: str,
    ) -> list[OverrideInfo]:
        """查询指定虚函数的所有重写实现（跨所有子类）

        沿继承链向下递归，收集所有 override。

        Args:
            func_name: 虚函数名
            class_name: 首次声明该虚函数的基类名

        Returns:
            重写信息列表
        """
        return self._pq.get_all_overrides(func_name, class_name)

    # ------------------------------------------------------------------
    # 3. 虚函数清单 / 虚表
    # ------------------------------------------------------------------

    def get_class_virtual_table(
        self,
        class_name: str,
        include_inherited: bool = True,
    ) -> list[VTableEntry]:
        """查询指定类的所有虚函数清单（虚表分析）

        Args:
            class_name: 类名
            include_inherited: 是否包含从基类继承的虚函数

        Returns:
            虚表条目列表
        """
        vfuncs = self._pq.get_virtual_functions(
            class_name, include_inherited=include_inherited
        )

        results: list[VTableEntry] = []
        for vf in vfuncs:
            # 构造 override_implementations 列表
            impl_list = [
                f"{cls}::{vf.function_name}"
                for cls in vf.override_classes
                if cls  # 过滤空字符串
            ]

            results.append(VTableEntry(
                function_name=vf.function_name,
                signature=vf.signature,
                declaring_class=vf.class_name,
                is_pure_virtual=vf.is_pure_virtual,
                is_overridden=vf.is_overridden,
                override_count=vf.override_count,
                override_implementations=impl_list,
            ))

        return results
