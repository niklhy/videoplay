#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资源管理器模块 - 树形文件夹浏览（优化修复版）
修复：
1. 空节点问题：程序自动展开节点时主动加载子节点
2. 展开状态保存问题：实时扫描树节点，不依赖可能累积的集合
3. 历史节点清理：关闭时只保存当前实际展开的节点
4. 恢复时仅展开选中节点的父级路径，不展开选中节点本身
5. 选中节点后强制折叠，防止意外展开
"""
import tkinter as tk
from tkinter import ttk
import os


class TreeExplorer:
    """树形资源管理器 - 优化修复版"""

    def __init__(self, parent_frame, db_manager, on_select_callback, debug_utils=None):
        self.parent = parent_frame
        self.db = db_manager
        self.on_select = on_select_callback
        self.debug = debug_utils

        self.root_path = None
        self.current_folder = None
        self._scroll_position = 0.0
        self._selected_path = None
        self._pending_scroll = None
        self._pending_select = None
        self._path_to_node = {}

        self._create_ui()
        self._bind_events()

    def _create_ui(self):
        self.frame = ttk.LabelFrame(self.parent, text="资源管理器")
        self.frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        self.tree = ttk.Treeview(self.frame, selectmode="browse")
        self.tree.heading("#0", text="文件夹", anchor=tk.W)

        scroll_y = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.tree.column("#0", width=200, minwidth=200)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _bind_events(self):
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_expand)
        self.tree.bind("<<TreeviewClose>>", self._on_collapse)
        self.tree.bind("<Configure>", self._on_configure)
        self.tree.bind("<MouseWheel>", self._on_scroll)
        self.tree.bind("<Button-4>", self._on_scroll)
        self.tree.bind("<Button-5>", self._on_scroll)

    # ==================== 状态获取 ====================
    def get_state(self):
        try:
            # 不再保存展开路径，避免恢复时展开无关节点
            expanded_paths = []  # 始终返回空
            selected_path = self.get_selected_path()
            scroll_pos = self.get_scroll_position()
            return {
                'expanded_paths': expanded_paths,
                'selected_path': selected_path,
                'scroll_position': scroll_pos,
                'root': self.root_path
            }
        except Exception as e:
            if self.debug:
                self.debug.print(f"获取树状态失败: {e}")
            return {
                'expanded_paths': [],
                'selected_path': None,
                'scroll_position': 0.0,
                'root': self.root_path
            }

    def _get_expanded_paths_from_tree(self, parent=''):
        expanded = []
        for child in self.tree.get_children(parent):
            values = self.tree.item(child, 'values')
            if values and len(values) > 0:
                path = values[0]
                if path and path != "placeholder" and os.path.isdir(path):
                    if self.tree.item(child, 'open'):
                        expanded.append(os.path.normpath(path))
                        expanded.extend(self._get_expanded_paths_from_tree(child))
        return expanded

    # ==================== 状态恢复（核心修改）====================
    def restore_state(self, expanded_paths=None, selected_path=None, scroll_position=0.0):
        """恢复树形控件状态 - 仅展开选中节点的父级路径，不展开选中节点本身"""
        self._pending_scroll = scroll_position
        self._pending_select = selected_path

        if selected_path:
            selected_path = os.path.normpath(selected_path)

        # 1. 展开选中节点的父级路径（确保选中节点可见）
        if selected_path and os.path.exists(selected_path):
            parent_path = os.path.dirname(selected_path)
            if parent_path and parent_path != selected_path:
                # 展开父级路径（包括加载其子节点，以便选中节点出现）
                self.expand_to_path(parent_path, load_children=True)
            # 如果选中节点本身就是根节点，则只需确保根节点展开
            elif selected_path == self.root_path:
                root_node = self._find_node_by_path(selected_path)
                if root_node:
                    self.tree.item(root_node, open=True)

        # 2. 选中目标节点（强制不展开）
        if selected_path and os.path.exists(selected_path):
            self.parent.after(50, lambda: self.select_path(selected_path, scroll_to_view=False))

        # 3. 恢复滚动位置
        if scroll_position is not None:
            self.parent.after(150, lambda: self.set_scroll_position(scroll_position))

    # ==================== 核心操作 ====================
    def expand_to_path(self, path, load_children=True):
        """展开到指定路径（确保所有父级都展开）"""
        if not path or not self.root_path:
            return False

        path = os.path.normpath(path)
        root = os.path.normpath(self.root_path)

        if path == root:
            root_node = self._find_node_by_path(root)
            if root_node:
                self.tree.item(root_node, open=True)
                self._ensure_children_loaded(root_node)
                return True
            return False

        if not path.startswith(root):
            if self.debug:
                self.debug.print(f"路径不在根目录下: {path} vs {root}")
            return False

        rel_path = path[len(root):].strip(os.sep)
        if not rel_path:
            return False

        parts = rel_path.split(os.sep)
        current_path = root
        success = True

        target_index = len(parts) if load_children else len(parts) - 1

        for i in range(target_index):
            part = parts[i]
            if not part:
                continue
            current_path = os.path.join(current_path, part)
            current_path = os.path.normpath(current_path)

            self.tree.update_idletasks()

            node_id = self._find_node_by_path(current_path)
            if node_id:
                self.tree.item(node_id, open=True)
                self._ensure_children_loaded(node_id)
            else:
                parent_path = os.path.dirname(current_path)
                parent_node = self._find_node_by_path(parent_path)
                if parent_node:
                    self._ensure_children_loaded(parent_node)
                    self.tree.update_idletasks()
                    node_id = self._find_node_by_path(current_path)
                    if node_id:
                        self.tree.item(node_id, open=True)
                        self._ensure_children_loaded(node_id)
                    else:
                        success = False
                        if self.debug:
                            self.debug.print(f"无法展开节点: {current_path}")
                else:
                    success = False
                    if self.debug:
                        self.debug.print(f"找不到父节点: {parent_path}")

        return success

    def _ensure_children_loaded(self, node_id):
        """检查节点是否有 placeholder 子节点，如有则加载真实子节点"""
        children = self.tree.get_children(node_id)
        if not children:
            return

        first_child = self.tree.item(children[0])
        if first_child['values'] and first_child['values'][0] == "placeholder":
            self.tree.delete(children[0])
            parent_path = self.tree.item(node_id)['values'][0]
            if parent_path and parent_path != "placeholder":
                self._load_nodes(node_id, parent_path)

    def select_path(self, path, scroll_to_view=True):
        """根据路径选中节点（确保节点不展开）"""
        try:
            if not path or not os.path.exists(path):
                if self.debug:
                    self.debug.print(f"选中失败：路径不存在或为空: {path}")
                return False

            path = os.path.normpath(path)

            if self.debug:
                self.debug.print(f"尝试选中节点: {os.path.basename(path)}")

            # 确保路径的父级已展开（但不加载目标节点的子节点）
            self.expand_to_path(path, load_children=False)

            self.tree.update_idletasks()

            node_id = None
            for attempt in range(3):
                node_id = self._find_node_by_path(path)
                if node_id:
                    break
                if self.debug:
                    self.debug.print(f"第{attempt+1}次查找节点失败，重试...")
                import time
                time.sleep(0.05 * (attempt + 1))
                self.tree.update_idletasks()

            if node_id:
                self.tree.selection_set(node_id)
                self.tree.focus(node_id)

                # 关键修复：强制折叠目标节点（防止被意外展开）
                if self.tree.item(node_id, 'open'):
                    self.tree.item(node_id, open=False)

                self.current_folder = path
                self._selected_path = path

                if scroll_to_view:
                    self.tree.see(node_id)

                if self.debug:
                    self.debug.print(f"✓ 成功选中节点: {os.path.basename(path)}")
                return True
            else:
                if self.debug:
                    self.debug.print(f"✗ 无法找到节点: {path}")
                return False

        except Exception as e:
            if self.debug:
                self.debug.print(f"选中路径异常 {path}: {e}")
                import traceback
                traceback.print_exc()
            return False

    # ==================== 节点查找 ====================
    def _find_node_by_path(self, path):
        if not path:
            return None

        path = os.path.normpath(path)

        if path in self._path_to_node:
            node_id = self._path_to_node[path]
            try:
                self.tree.item(node_id)
                return node_id
            except:
                del self._path_to_node[path]

        def search_children(parent=''):
            for child in self.tree.get_children(parent):
                values = self.tree.item(child, 'values')
                if values and len(values) > 0:
                    node_path = os.path.normpath(values[0])
                    self._path_to_node[node_path] = child
                    if node_path == path:
                        return child
                    result = search_children(child)
                    if result:
                        return result
            return None

        return search_children()

    # ==================== 事件处理 ====================
    def _on_configure(self, event=None):
        if self._pending_scroll is not None:
            self.set_scroll_position(self._pending_scroll)
            self._pending_scroll = None

    def _on_scroll(self, event=None):
        self._scroll_position = self.get_scroll_position()
        return None

    def _on_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return

        item = selection[0]
        item_data = self.tree.item(item)
        folder_path = item_data['values'][0] if item_data['values'] else None

        if folder_path and folder_path != "placeholder" and os.path.isdir(folder_path):
            folder_path = os.path.normpath(folder_path)
            self._selected_path = folder_path
            self.current_folder = folder_path
            if self.on_select:
                self.on_select(folder_path, item_data['text'])

    def _on_expand(self, event=None):
        item = self.tree.focus()
        if not item:
            return
        self._ensure_children_loaded(item)

    def _on_collapse(self, event=None):
        pass

    # ==================== 加载节点 ====================
    def _load_nodes(self, parent_node, parent_path):
        parent_path = os.path.normpath(parent_path)

        try:
            if self.db and self.db.is_ready:
                sub_folders = self.db.get_folders_by_parent(parent_path)
                if sub_folders:
                    for folder_info in sorted(sub_folders, key=lambda x: x['name']):
                        name = folder_info.get('name', '').strip()
                        if not name:
                            continue
                        folder_path = os.path.normpath(folder_info['path'])
                        node = self.tree.insert(
                            parent_node,
                            "end",
                            text=name,
                            values=[folder_path],
                            open=False
                        )
                        self._path_to_node[folder_path] = node
                        has_children = False
                        if self.db.is_ready:
                            sub_sub = self.db.get_folders_by_parent(folder_path)
                            has_children = len(sub_sub) > 0
                        if has_children:
                            self.tree.insert(node, "end", text="", values=["placeholder"])
                    return

            entries = sorted([e for e in os.scandir(parent_path) if e.is_dir()], key=lambda e: e.name)
            for entry in entries:
                if not entry.name or not entry.name.strip():
                    continue
                entry_path = os.path.normpath(entry.path)
                node = self.tree.insert(
                    parent_node,
                    "end",
                    text=entry.name,
                    values=[entry_path],
                    open=False
                )
                self._path_to_node[entry_path] = node
                try:
                    sub_entries = [e for e in os.scandir(entry_path) if e.is_dir()]
                    if sub_entries:
                        self.tree.insert(node, "end", text="", values=["placeholder"])
                except:
                    pass
        except Exception as e:
            if self.debug:
                self.debug.print(f"加载树节点失败 {parent_path}: {e}")

    # ==================== 公共接口 ====================
    def load_from_cache(self, root_path, sub_folders):
        self.clear()
        self.root_path = os.path.normpath(root_path)

        root_node = self.tree.insert(
            "",
            "end",
            text=os.path.basename(root_path),
            values=[self.root_path],
            open=True
        )
        self._path_to_node[self.root_path] = root_node

        for folder_info in sorted(sub_folders, key=lambda x: x['name']):
            name = folder_info.get('name', '').strip()
            if not name:
                continue
            folder_path = os.path.normpath(folder_info['path'])
            node = self.tree.insert(
                root_node,
                "end",
                text=name,
                values=[folder_path],
                open=False
            )
            self._path_to_node[folder_path] = node
            sub_sub = self.db.get_folders_by_parent(folder_path) if self.db and self.db.is_ready else []
            if sub_sub:
                self.tree.insert(node, "end", text="", values=["placeholder"])

        if self.debug:
            self.debug.print(f"从缓存加载树: {self.root_path}, {len(sub_folders)} 个子文件夹")

    def load_root(self, folder_path):
        self.clear()
        self.root_path = os.path.normpath(folder_path)
        self._path_to_node.clear()

        root_node = self.tree.insert(
            "",
            "end",
            text=os.path.basename(folder_path),
            values=[self.root_path],
            open=True
        )
        self._path_to_node[self.root_path] = root_node
        self._load_nodes(root_node, self.root_path)

        if self.debug:
            self.debug.print(f"加载根文件夹: {self.root_path}")

    def clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.root_path = None
        self._path_to_node.clear()
        self._scroll_position = 0.0
        self._selected_path = None

    def get_selected_path(self):
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            item_data = self.tree.item(item)
            if item_data['values']:
                path = item_data['values'][0]
                if path and path != "placeholder":
                    return os.path.normpath(path)
        return self._selected_path

    def get_scroll_position(self):
        try:
            yview = self.tree.yview()
            if yview and len(yview) == 2:
                return float(yview[0])
        except:
            pass
        return 0.0

    def set_scroll_position(self, position):
        try:
            if isinstance(position, (int, float)) and 0 <= float(position) <= 1:
                self.tree.yview_moveto(float(position))
        except Exception as e:
            if self.debug:
                self.debug.print(f"设置滚动位置失败: {e}")

    def get_current_root(self):
        return self.root_path

    def refresh(self):
        if self.root_path:
            current_state = self.get_state()
            self.load_root(self.root_path)
            self.restore_state(
                expanded_paths=current_state.get('expanded_paths', []),
                selected_path=current_state.get('selected_path'),
                scroll_position=current_state.get('scroll_position', 0.0)
            )