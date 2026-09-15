# -*- coding: utf-8 -*-
"""设备树模块（对应设计文档第九章）。

左侧 Tree1：设备-根目录两级树，负责：
- 从配置管理器读取全部设备及其根目录构建两级树（设备节点 → 根目录节点）；
- 当前设备置顶并默认展开；
- 选中根目录时发布 ``EVENT_ROOT_SELECTED`` 事件；
- 选中/展开设备节点时发布 ``EVENT_DEVICE_SELECTED`` / ``EVENT_DEVICE_EXPANDED`` 事件。

节点 iid 规则：
- 设备节点：``"device:{device_id}"``
- 根目录节点：``"root:{device_id}:{root_id}"``

仅使用标准库 + tkinter。公共契约（数据类、事件常量）一律从
models.py / events.py 导入。
"""
import os
import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from events import (
    EVENT_DEVICE_SELECTED,
    EVENT_DEVICE_EXPANDED,
    EVENT_ROOT_SELECTED,
)
from models import RootConfig


class DeviceTree:
    """设备-根目录两级树控件。

    属性:
        tree_widget: ttk.Treeview      树控件（两列：设备/根目录、路径）
        event_bus: EventBus            事件总线实例
        config_manager: ConfigManager  配置管理器实例
        _device_nodes: dict            device_id -> 设备节点 iid
        _root_nodes: dict              root_id -> 根目录节点 iid
    """

    def __init__(self, parent_widget, event_bus, config_manager):
        """在 parent_widget 内创建树控件与滚动条并绑定事件。

        参数:
            parent_widget: tkinter 父容器（Frame 等）
            event_bus: EventBus 事件总线实例
            config_manager: ConfigManager 配置管理器实例
        """
        self.event_bus = event_bus
        self.config_manager = config_manager
        self._device_nodes = {}
        self._root_nodes = {}

        # 容器 + 树 + 垂直滚动条
        container = ttk.Frame(parent_widget)
        container.pack(fill=tk.BOTH, expand=True)

        self.tree_widget = ttk.Treeview(
            container, columns=("info",), selectmode="browse"
        )
        self.tree_widget.heading("#0", text="设备 / 根目录")
        self.tree_widget.heading("info", text="路径")
        self.tree_widget.column("#0", width=180, anchor="w", stretch=True)
        self.tree_widget.column("info", width=220, anchor="w", stretch=True)

        scrollbar = ttk.Scrollbar(
            container, orient="vertical", command=self.tree_widget.yview
        )
        self.tree_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 绑定选中与展开事件（事件回调均在 UI 线程触发）
        self.tree_widget.bind("<<TreeviewSelect>>", self._on_select)
        self.tree_widget.bind("<<TreeviewOpen>>", self._on_expand)

    # ------------------------------------------------------------------
    # 树构建
    # ------------------------------------------------------------------
    def build_tree(self) -> None:
        """从配置管理器读取全部设备及其 roots 构建两级树。

        当前设备置顶并展开；其余设备按配置顺序排列。
        """
        self.clear()

        # 读取全部设备配置（devices 为 config.json 中的公开结构）
        devices = {}
        data = getattr(self.config_manager, "data", None)
        if isinstance(data, dict):
            raw = data.get("devices", {})
            if isinstance(raw, dict):
                devices = raw

        # 当前设备 ID
        current_id = ""
        try:
            current_id = str(self.config_manager.get_current_device_id() or "")
        except Exception:
            current_id = ""

        # 当前设备置顶
        ordered = []
        if current_id and current_id in devices:
            ordered.append((current_id, devices[current_id]))
        for device_id, device_info in devices.items():
            if device_id != current_id:
                ordered.append((device_id, device_info))

        for device_id, device_info in ordered:
            if not isinstance(device_info, dict):
                device_info = {}
            device_name = str(device_info.get("name") or device_id)
            device_label = str(device_info.get("device_label") or "")
            roots = self._parse_roots(device_info.get("roots") or [])

            node_iid = self.add_device(device_id, device_name, roots)
            if device_label:
                try:
                    self.tree_widget.set(node_iid, "info", device_label)
                except tk.TclError:
                    pass
            if device_id == current_id:
                try:
                    self.tree_widget.item(node_iid, open=True)
                except tk.TclError:
                    pass

    @staticmethod
    def _parse_roots(raw_roots) -> list:
        """将配置中的 roots 原始数据（dict 列表）转换为 RootConfig 列表。

        参数:
            raw_roots: list[dict] 或 list[RootConfig]
        返回:
            RootConfig 对象列表
        """
        roots = []
        valid_keys = ("root_id", "name", "path", "root_type", "enabled")
        for item in raw_roots:
            if isinstance(item, RootConfig):
                roots.append(item)
            elif isinstance(item, dict):
                fields = {k: item[k] for k in valid_keys if k in item}
                # root_id 与 path 为必需字段
                if fields.get("root_id") and fields.get("path"):
                    roots.append(RootConfig(**fields))
        return roots

    # ------------------------------------------------------------------
    # 动态增删
    # ------------------------------------------------------------------
    def add_device(self, device_id: str, device_name: str, roots: list) -> str:
        """动态添加一个设备节点及其根目录子节点。

        参数:
            device_id: 设备唯一标识
            device_name: 设备显示名称
            roots: RootConfig 列表
        返回:
            设备节点 iid
        """
        device_id = str(device_id)
        iid = "device:%s" % device_id
        if self.tree_widget.exists(iid):
            return iid

        self.tree_widget.insert(
            "", tk.END, iid=iid, text=str(device_name), values=("设备",)
        )
        self._device_nodes[device_id] = iid

        for root_config in roots or []:
            self.add_root(device_id, root_config)
        return iid

    def add_root(self, device_id: str, root_config: RootConfig) -> str:
        """在指定设备节点下添加一个根目录节点。

        参数:
            device_id: 设备唯一标识
            root_config: RootConfig 数据对象
        返回:
            根目录节点 iid（设备不存在或参数无效时返回空字符串）
        """
        device_id = str(device_id)
        root_id = str(getattr(root_config, "root_id", "") or "")
        if not root_id:
            return ""

        parent_iid = self._device_nodes.get(device_id)
        if parent_iid is None or not self.tree_widget.exists(parent_iid):
            # 设备节点不存在时自动补建设备节点
            parent_iid = self.add_device(device_id, device_id, [])

        iid = "root:%s:%s" % (device_id, root_id)
        if self.tree_widget.exists(iid):
            return iid

        name = str(getattr(root_config, "name", "") or root_id)
        path = str(getattr(root_config, "path", "") or "")
        self.tree_widget.insert(parent_iid, tk.END, iid=iid, text=name,
                                values=(path,))
        self._root_nodes[root_id] = iid
        return iid

    # ------------------------------------------------------------------
    # 查询与操作
    # ------------------------------------------------------------------
    def select_root(self, root_id: str) -> bool:
        """选中指定根目录节点并使其可见。

        参数:
            root_id: 根目录唯一标识
        返回:
            是否选中成功
        """
        iid = self._root_nodes.get(str(root_id))
        if not iid or not self.tree_widget.exists(iid):
            return False
        self.tree_widget.selection_set(iid)
        self.tree_widget.see(iid)
        return True

    def expand_device(self, device_id: str) -> None:
        """展开指定设备节点。

        参数:
            device_id: 设备唯一标识
        """
        iid = self._device_nodes.get(str(device_id))
        if iid and self.tree_widget.exists(iid):
            self.tree_widget.item(iid, open=True)

    def get_selected_root(self) -> Optional[Tuple[str, str]]:
        """获取当前选中的根目录。

        返回:
            (device_id, root_id) 元组；未选中根目录时返回 None
        """
        selection = self.tree_widget.selection()
        if not selection:
            return None
        iid = selection[0]
        if iid.startswith("root:"):
            parts = iid.split(":", 2)
            if len(parts) == 3:
                return (parts[1], parts[2])
        return None

    def clear(self) -> None:
        """清空整棵树并重置节点映射。"""
        for iid in self.tree_widget.get_children(""):
            self.tree_widget.delete(iid)
        self._device_nodes.clear()
        self._root_nodes.clear()

    # ------------------------------------------------------------------
    # 事件处理（均在 UI 线程触发，使用 publish_sync 同步发布）
    # ------------------------------------------------------------------
    def _on_select(self, event) -> None:
        """选中节点时发布根目录/设备选中事件。"""
        selection = self.tree_widget.selection()
        if not selection:
            return
        iid = selection[0]

        if iid.startswith("root:"):
            # 发布根目录选中事件（root_path 取 info 列存储的绝对路径）
            parts = iid.split(":", 2)
            if len(parts) != 3:
                return
            _, device_id, root_id = parts
            root_path = ""
            try:
                root_path = self.tree_widget.set(iid, "info")
            except tk.TclError:
                pass
            self.event_bus.publish_sync(
                EVENT_ROOT_SELECTED,
                device_id=device_id,
                root_id=root_id,
                root_path=root_path,
            )
        elif iid.startswith("device:"):
            device_id = iid[len("device:"):]
            self.event_bus.publish_sync(
                EVENT_DEVICE_SELECTED, device_id=device_id
            )

    def _on_expand(self, event) -> None:
        """展开设备节点时发布设备展开事件。"""
        iid = self.tree_widget.focus()
        if iid and iid.startswith("device:"):
            device_id = iid[len("device:"):]
            self.event_bus.publish_sync(
                EVENT_DEVICE_EXPANDED, device_id=device_id
            )
