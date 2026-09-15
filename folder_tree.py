# -*- coding: utf-8 -*-
"""文件夹树模块（对应设计文档第十章）。

左侧 Tree2：文件夹懒加载树，负责：
- 以根路径建立首节点，子文件夹懒加载（展开时才扫描）；
- 展开节点时优先从内存索引同步取子文件夹，未命中则委托异步扫描器
  异步扫描，完成后经 ``widget.after(0, ...)`` 回到 UI 线程回填；
- 选中文件夹时发布 ``EVENT_FOLDER_SELECTED`` 事件；
- 记录展开节点列表，供启动恢复（LastState.tree2_expanded）使用。

节点 iid 规则：
- 文件夹节点：``"node:{规范化绝对路径}"``（路径中的 ``\\`` 统一为 ``/``）
- 懒加载占位子节点：``"ph:{父节点规范化路径}"``

路径与 iid 的映射通过 ``_node_map``（路径 -> iid）与
``_iid_to_path``（iid -> 路径）双向维护。

仅使用标准库 + tkinter。事件常量一律从 events.py 导入；
async_scanner 仅按其设计文档冻结签名使用：
- ``get_media_in_folder(folder_path) -> list[str]``
- ``scan_folder(folder_path, recursive, on_complete) -> Future``
- ``scan_folder_sync(folder_path, recursive) -> dict``
- ``folder_index`` 属性（FolderIndex，提供 ``get_folder(path)``）
"""
import os
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from events import (
    EVENT_FOLDER_EXPANDED,
    EVENT_FOLDER_SELECTED,
    EVENT_FOLDER_SCAN_COMPLETE,
)


class FolderTree:
    """文件夹懒加载树控件。

    属性:
        tree_widget: ttk.Treeview   树控件（单列，显示文件夹名）
        event_bus: EventBus         事件总线实例
        async_scanner: AsyncScanner 异步扫描器实例
        current_root_path: str      当前根目录绝对路径
        current_root_id: str        当前根目录 ID
        _node_map: dict             规范化绝对路径 -> 节点 iid
        _expanded_nodes: set        展开的节点规范化路径集合
    """

    def __init__(self, parent_widget, event_bus, async_scanner):
        """在 parent_widget 内创建树控件与滚动条并绑定事件。

        参数:
            parent_widget: tkinter 父容器（Frame 等）
            event_bus: EventBus 事件总线实例
            async_scanner: AsyncScanner 异步扫描器实例
        """
        self.event_bus = event_bus
        self.async_scanner = async_scanner
        self.current_root_path = ""
        self.current_root_id = ""
        self._node_map = {}
        self._iid_to_path = {}
        self._expanded_nodes = set()

        # 容器 + 树 + 垂直滚动条
        container = ttk.Frame(parent_widget)
        container.pack(fill=tk.BOTH, expand=True)

        self.tree_widget = ttk.Treeview(container, selectmode="browse")
        self.tree_widget.heading("#0", text="文件夹")
        self.tree_widget.column("#0", width=280, anchor="w", stretch=True)

        scrollbar = ttk.Scrollbar(
            container, orient="vertical", command=self.tree_widget.yview
        )
        self.tree_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 绑定选中/展开/折叠事件（均在 UI 线程触发）
        self.tree_widget.bind("<<TreeviewSelect>>", self._on_select)
        self.tree_widget.bind("<<TreeviewOpen>>", self._on_expand)
        self.tree_widget.bind("<<TreeviewClose>>", self._on_collapse)

    # ------------------------------------------------------------------
    # 路径与 iid 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(path: str) -> str:
        """规范化路径：normpath 并将 Windows 分隔符统一为 ``/``。"""
        if not path:
            return ""
        norm = os.path.normpath(str(path))
        return norm.replace("\\", "/")

    @staticmethod
    def _make_iid(norm_path: str) -> str:
        """由规范化路径生成节点 iid。"""
        return "node:%s" % norm_path

    @staticmethod
    def _placeholder_iid(norm_path: str) -> str:
        """由父节点规范化路径生成占位子节点 iid。"""
        return "ph:%s" % norm_path

    def _path_to_node_id(self, folder_path: str) -> Optional[str]:
        """将文件夹绝对路径映射为节点 iid。

        参数:
            folder_path: 文件夹绝对路径（兼容 ``\\`` 与 ``/`` 分隔符）
        返回:
            节点 iid；路径不存在于树中时返回 None
        """
        return self._node_map.get(self._normalize(folder_path))

    def _get_relative_path(self, norm_path: str) -> str:
        """将规范化绝对路径转为保存用的相对路径（含根 ID 前缀）。

        例如根路径返回 ``"D1"``，其子文件夹返回 ``"D1/电影/动作"``。
        """
        root_norm = self._normalize(self.current_root_path)
        if not root_norm:
            return norm_path
        if norm_path == root_norm:
            return self.current_root_id
        prefix = root_norm.rstrip("/") + "/"
        if norm_path.startswith(prefix):
            rel = norm_path[len(prefix):]
            if self.current_root_id:
                return "%s/%s" % (self.current_root_id, rel)
            return rel
        return norm_path

    # ------------------------------------------------------------------
    # 根目录与树构建
    # ------------------------------------------------------------------
    def set_root(self, root_id: str, root_path: str) -> None:
        """记录当前根目录（不清树，配合 build_from_root 使用）。

        参数:
            root_id: 根目录唯一标识
            root_path: 根目录绝对路径
        """
        self.current_root_id = str(root_id or "")
        self.current_root_path = os.path.normpath(str(root_path or ""))

    def build_from_root(self) -> None:
        """清空树并以当前根路径建立首节点。

        首节点带懒加载占位子节点，并立即触发一次子文件夹加载。
        """
        self.clear()
        if not self.current_root_path:
            return

        root_norm = self._normalize(self.current_root_path)
        name = os.path.basename(root_norm.rstrip("/")) or root_norm
        iid = self._make_iid(root_norm)
        self.tree_widget.insert("", tk.END, iid=iid, text=name)
        self._node_map[root_norm] = iid
        self._iid_to_path[iid] = root_norm

        # 占位子节点 + 记录展开状态 + 立即加载首层
        self.tree_widget.insert(
            iid, tk.END, iid=self._placeholder_iid(root_norm), text="（加载中…）"
        )
        self.tree_widget.item(iid, open=True)
        self._expanded_nodes.add(root_norm)
        self._load_children(iid, root_norm)

    # ------------------------------------------------------------------
    # 懒加载
    # ------------------------------------------------------------------
    def _load_children(self, parent_iid: str, parent_norm: str) -> None:
        """为指定节点加载子文件夹（索引快速路径 + 异步扫描回退）。

        1. 优先从 async_scanner.folder_index 内存索引同步取子文件夹，
           命中则立即回填；
        2. 未命中则调用 scan_folder 异步扫描，完成后通过
           ``widget.after(0, ...)`` 回到 UI 线程回填，并发布
           EVENT_FOLDER_SCAN_COMPLETE 事件。
        """
        subfolders = self._get_subfolders_from_index(parent_norm)
        if subfolders is not None:
            self._add_child_nodes(parent_norm, subfolders)
            self.event_bus.publish_sync(
                EVENT_FOLDER_SCAN_COMPLETE,
                folder_path=parent_norm,
                root_id=self.current_root_id,
                subfolders=subfolders,
            )
            return

        # 异步扫描（on_complete 在工作线程回调，需切回 UI 线程）
        def on_complete(result=None):
            def ui_update():
                subs = self._extract_subfolders(result)
                self._add_child_nodes(parent_norm, subs)
                self.event_bus.publish_sync(
                    EVENT_FOLDER_SCAN_COMPLETE,
                    folder_path=parent_norm,
                    root_id=self.current_root_id,
                    subfolders=subs,
                )

            try:
                self.tree_widget.after(0, ui_update)
            except Exception:
                # 窗口已销毁等极端情况：尽力同步回填
                try:
                    ui_update()
                except Exception:
                    pass

        try:
            self.async_scanner.scan_folder(
                parent_norm, recursive=False, on_complete=on_complete
            )
        except Exception:
            # 回退：同步扫描，保证占位节点能被替换
            try:
                result = self.async_scanner.scan_folder_sync(
                    parent_norm, recursive=False
                )
                subs = self._extract_subfolders(result)
                self._add_child_nodes(parent_norm, subs)
                self.event_bus.publish_sync(
                    EVENT_FOLDER_SCAN_COMPLETE,
                    folder_path=parent_norm,
                    root_id=self.current_root_id,
                    subfolders=subs,
                )
            except Exception:
                self._add_child_nodes(parent_norm, [])

    def _get_subfolders_from_index(self, folder_path: str) -> Optional[list]:
        """从内存索引（folder_index）读取子文件夹列表。

        参数:
            folder_path: 文件夹规范化路径
        返回:
            子文件夹名称列表；索引不可用或未收录时返回 None
        """
        try:
            index = getattr(self.async_scanner, "folder_index", None)
            if index is None:
                return None
            node = index.get_folder(folder_path)
        except Exception:
            return None
        if node is None:
            return None
        return [str(n) for n in (getattr(node, "subfolders", None) or [])]

    @staticmethod
    def _extract_subfolders(result) -> list:
        """从扫描结果中稳健地提取子文件夹名称列表。

        兼容多种可能的返回形态：
        - dict：取 subfolders / folders / dirs / children 键；
        - 带 subfolders 属性的对象（如 FolderNode）；
        - list：字符串列表视为名称列表，dict 列表取 name/path 字段。
        """
        if result is None:
            return []
        if isinstance(result, dict):
            for key in ("subfolders", "folders", "dirs", "children"):
                value = result.get(key)
                if isinstance(value, list):
                    return [str(v) for v in value]
            node = result.get("node")
            if node is not None:
                subs = getattr(node, "subfolders", None)
                if isinstance(subs, list):
                    return [str(v) for v in subs]
            return []
        subs = getattr(result, "subfolders", None)
        if isinstance(subs, list):
            return [str(v) for v in subs]
        if isinstance(result, list):
            names = []
            for item in result:
                if isinstance(item, str) and item:
                    names.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("path")
                    if name:
                        names.append(str(name))
            return names
        return []

    def _add_child_nodes(self, parent_path: str, subfolders: list) -> None:
        """为每个子文件夹建节点并放一个占位子节点实现懒加载。

        参数:
            parent_path: 父文件夹规范化路径
            subfolders: 子文件夹名称列表
        """
        parent_norm = self._normalize(parent_path)
        parent_iid = self._node_map.get(parent_norm)
        if parent_iid is None or not self.tree_widget.exists(parent_iid):
            return

        # 移除占位子节点（若仍存在）
        for child in list(self.tree_widget.get_children(parent_iid)):
            if child.startswith("ph:"):
                self.tree_widget.delete(child)

        for name in subfolders or []:
            if not name:
                continue
            name = str(name)
            child_norm = parent_norm.rstrip("/") + "/" + name
            child_iid = self._make_iid(child_norm)
            if self.tree_widget.exists(child_iid):
                continue
            self.tree_widget.insert(parent_iid, tk.END, iid=child_iid,
                                    text=name)
            self._node_map[child_norm] = child_iid
            self._iid_to_path[child_iid] = child_norm
            # 占位子节点：下次展开时再真正加载
            self.tree_widget.insert(
                child_iid, tk.END,
                iid=self._placeholder_iid(child_norm),
                text="（加载中…）",
            )

    def _ensure_children_loaded(self, norm_path: str) -> None:
        """确保某节点的子节点已加载（供 expand_path 逐级展开使用）。

        若该节点仍是占位状态，则同步加载（索引或 scan_folder_sync）。
        """
        iid = self._node_map.get(norm_path)
        if iid is None or not self.tree_widget.exists(iid):
            return
        children = self.tree_widget.get_children(iid)
        if not any(c.startswith("ph:") for c in children):
            return
        subfolders = self._get_subfolders_from_index(norm_path)
        if subfolders is None:
            try:
                result = self.async_scanner.scan_folder_sync(
                    norm_path, recursive=False
                )
                subfolders = self._extract_subfolders(result)
            except Exception:
                subfolders = []
        self._add_child_nodes(norm_path, subfolders)

    # ------------------------------------------------------------------
    # 对外操作
    # ------------------------------------------------------------------
    def expand_node(self, folder_path: str) -> None:
        """展开指定文件夹节点。

        参数:
            folder_path: 文件夹绝对路径
        """
        iid = self._path_to_node_id(folder_path)
        if iid and self.tree_widget.exists(iid):
            self.tree_widget.item(iid, open=True)

    def select_node(self, folder_path: str) -> bool:
        """选中指定文件夹节点并使其可见。

        参数:
            folder_path: 文件夹绝对路径
        返回:
            是否选中成功
        """
        iid = self._path_to_node_id(folder_path)
        if not iid or not self.tree_widget.exists(iid):
            return False
        self.tree_widget.selection_set(iid)
        self.tree_widget.see(iid)
        return True

    def expand_path(self, path_parts) -> None:
        """逐级展开相对路径（如 "D1/电影/动作"）。

        首段为根 ID（或根目录名）时自动跳过；逐段定位并展开节点，
        中途节点尚未加载时先同步加载子节点再继续。

        参数:
            path_parts: 路径段列表，或以 ``/``、``\\`` 分隔的路径字符串
        """
        if isinstance(path_parts, str):
            parts = [p for p in path_parts.replace("\\", "/").split("/") if p]
        else:
            parts = [str(p) for p in (path_parts or []) if str(p)]
        if not parts:
            return

        root_norm = self._normalize(self.current_root_path)
        if not root_norm:
            return

        # 跳过根段（root_id 或根目录显示名）
        root_name = os.path.basename(root_norm.rstrip("/"))
        if parts[0] == self.current_root_id or parts[0] == root_name:
            parts = parts[1:]

        current = root_norm
        for part in parts:
            target = current.rstrip("/") + "/" + part
            iid = self._node_map.get(target)
            if iid is None or not self.tree_widget.exists(iid):
                # 子节点可能尚未加载，先加载当前层再找
                self._ensure_children_loaded(current)
                iid = self._node_map.get(target)
            if iid is None or not self.tree_widget.exists(iid):
                break
            self.tree_widget.item(iid, open=True)
            self._expanded_nodes.add(target)
            current = target

    def get_selected_folder(self) -> Optional[str]:
        """获取当前选中的文件夹绝对路径。

        返回:
            绝对路径（原生分隔符）；未选中时返回 None
        """
        selection = self.tree_widget.selection()
        if not selection:
            return None
        iid = selection[0]
        if iid.startswith("ph:"):
            return None
        norm_path = self._iid_to_path.get(iid)
        if not norm_path:
            return None
        return os.path.normpath(norm_path.replace("/", os.sep))

    def get_expanded_nodes(self) -> list:
        """获取展开节点路径列表（保存状态用）。

        返回:
            相对路径列表（含根 ID 前缀，如 ["D1", "D1/电影"]），
            与 LastState.tree2_expanded 格式一致。
        """
        root_norm = self._normalize(self.current_root_path)
        result = []
        for path in sorted(self._expanded_nodes):
            if not root_norm:
                continue
            if path == root_norm:
                result.append(self.current_root_id)
            else:
                prefix = root_norm.rstrip("/") + "/"
                if path.startswith(prefix):
                    rel = path[len(prefix):]
                    if self.current_root_id:
                        result.append("%s/%s" % (self.current_root_id, rel))
                    else:
                        result.append(rel)
        return result

    def clear(self) -> None:
        """清空整棵树并重置节点映射与展开状态。"""
        for iid in self.tree_widget.get_children(""):
            self.tree_widget.delete(iid)
        self._node_map.clear()
        self._iid_to_path.clear()
        self._expanded_nodes.clear()

    # ------------------------------------------------------------------
    # 事件处理（均在 UI 线程触发，使用 publish_sync 同步发布）
    # ------------------------------------------------------------------
    def _on_select(self, event) -> None:
        """选中节点时发布 EVENT_FOLDER_SELECTED 事件。"""
        selection = self.tree_widget.selection()
        if not selection:
            return
        iid = selection[0]
        if iid.startswith("ph:"):
            return
        norm_path = self._iid_to_path.get(iid)
        if not norm_path:
            return
        absolute = os.path.normpath(norm_path.replace("/", os.sep))
        relative = self._get_relative_path(norm_path)
        self.event_bus.publish_sync(
            EVENT_FOLDER_SELECTED,
            folder_path=absolute,
            root_id=self.current_root_id,
            relative_path=relative,
        )

    def _on_expand(self, event) -> None:
        """展开节点时记录状态、发布事件并触发懒加载。"""
        iid = self.tree_widget.focus()
        if not iid or iid.startswith("ph:"):
            return
        norm_path = self._iid_to_path.get(iid)
        if not norm_path:
            return

        self._expanded_nodes.add(norm_path)
        self.event_bus.publish_sync(
            EVENT_FOLDER_EXPANDED,
            folder_path=norm_path,
            root_id=self.current_root_id,
        )

        # 子节点仍是占位子节点时，才真正加载子文件夹
        children = self.tree_widget.get_children(iid)
        if any(c.startswith("ph:") for c in children):
            self._load_children(iid, norm_path)

    def _on_collapse(self, event) -> None:
        """折叠节点时清理整棵子树并重新放置占位子节点。"""
        iid = self.tree_widget.focus()
        if not iid or iid.startswith("ph:"):
            return
        norm_path = self._iid_to_path.get(iid)
        if norm_path:
            self._expanded_nodes.discard(norm_path)
        self._collapse_subtree(iid)
        # 重新放置占位节点，下次展开时重新加载
        if self.tree_widget.exists(iid):
            try:
                self.tree_widget.insert(
                    iid, tk.END,
                    iid=self._placeholder_iid(norm_path or iid),
                    text="（加载中…）",
                )
            except tk.TclError:
                pass

    def _collapse_subtree(self, iid: str) -> None:
        """递归删除节点子树（不含节点本身）并清理路径映射。"""
        if not self.tree_widget.exists(iid):
            return
        for child in list(self.tree_widget.get_children(iid)):
            self._collapse_subtree(child)
            child_path = self._iid_to_path.pop(child, None)
            if child_path:
                self._node_map.pop(child_path, None)
                self._expanded_nodes.discard(child_path)
            try:
                self.tree_widget.delete(child)
            except tk.TclError:
                pass
