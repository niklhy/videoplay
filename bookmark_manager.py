#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
书签管理模块 - 设备分组版（树形结构）
支持按设备分组显示，自定义显示名称
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import os


class BookmarkManager:
    """书签管理器 - 设备分组树形版"""

    def __init__(self, parent_frame, config_manager, db_manager, on_select_callback, debug_utils=None):
        self.parent = parent_frame
        self.config = config_manager
        self.db = db_manager
        self.on_select = on_select_callback
        self.debug = debug_utils
        
        self.frame = ttk.LabelFrame(self.parent, text="书签管理器")
        self.frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # 创建树形控件（替代原来的Listbox）
        self.tree = ttk.Treeview(self.frame, selectmode="browse", show="tree")
        self.tree.heading("#0", text="书签分组", anchor=tk.W)
        
        # 滚动条
        scroll_y = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # 绑定事件
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._show_context_menu)
        
        # 按钮栏
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=2)
        
        ttk.Button(btn_frame, text="+ 添加书签", command=self.add_bookmark, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="⚙ 重命名", command=self._rename_selected, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="- 删除", command=self.remove_selected, width=8).pack(side=tk.LEFT, padx=2)
        
        # 设备信息标签
        self.device_label = ttk.Label(self.frame, text="", font=("微软雅黑", 8), foreground="gray")
        self.device_label.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=2)
        
        # 右键菜单
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="打开", command=self._open_selected)
        self.context_menu.add_command(label="在资源管理器中打开", command=self._open_in_explorer)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="重命名显示名称", command=self._rename_selected)
        self.context_menu.add_command(label="修改设备名称", command=self._rename_device)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="删除", command=self.remove_selected)
        
        # 初始化显示
        self.refresh_from_config()
        self._update_device_label()

    def _update_device_label(self):
        """更新底部设备信息"""
        current = self.config.device_id
        name = getattr(self.config, 'current_device_name', current)
        if name != current:
            self.device_label.config(text=f"当前设备: {name} ({current})")
        else:
            self.device_label.config(text=f"当前设备: {current}")

    def refresh_from_config(self):
        """从配置刷新树形结构"""
        # 清空树
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        current_device = self.config.device_id
        devices = self.config.get_all_devices()
        
        # 当前设备置顶
        if current_device in devices:
            self._add_device_node(current_device, devices[current_device], is_current=True)
        
        # 其他设备
        for dev_id, dev_data in devices.items():
            if dev_id != current_device:
                self._add_device_node(dev_id, dev_data, is_current=False)

    def _add_device_node(self, dev_id, dev_data, is_current=False):
        """添加设备节点"""
        display_name = dev_data.get('display_name', dev_id)
        
        # 设备节点样式
        if is_current:
            node_text = f"📱 {display_name} (本机)"
            tag = "current"
            self.tree.tag_configure("current", background="#e3f2fd", font=("微软雅黑", 9, "bold"))
        else:
            node_text = f"💻 {display_name}"
            tag = "other"
            self.tree.tag_configure("other", foreground="#666666")
        
        device_node = self.tree.insert("", "end", text=node_text, values=[f"device:{dev_id}"], tags=[tag], open=is_current)
        
        # 添加该书签下的文件夹
        bookmarks = dev_data.get('bookmarks', [])
        for bm in bookmarks:
            bm_path = bm['path']
            bm_display = bm['display_name']
            bm_id = bm['id']
            
            # 检查路径是否存在
            if not os.path.exists(bm_path):
                bm_display = f"[×] {bm_display}"
                self.tree.tag_configure("missing", foreground="#ff6b6b")
                item_tag = "missing"
            else:
                item_tag = ""
            
            # 书签节点：存储完整路径和ID
            self.tree.insert(device_node, "end", text=f"  📂 {bm_display}", 
                           values=[f"bookmark:{bm_path}:{bm_id}:{dev_id}"], 
                           tags=[item_tag])

    def _on_select(self, event):
        """处理选择事件"""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.tree.item(item, 'values')
        if not values:
            return
        
        value_str = values[0]
        
        # 只处理书签选择，不处理设备节点选择
        if value_str.startswith("bookmark:"):
            parts = value_str.split(":", 3)
            if len(parts) >= 3:
                folder = parts[1]
                if os.path.exists(folder) and self.on_select:
                    self.on_select(folder)
                elif not os.path.exists(folder):
                    messagebox.showerror("路径不存在", f"该文件夹已无法访问:\n{folder}")

    def _on_double_click(self, event):
        """双击展开/收起设备节点，或打开书签"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        values = self.tree.item(item, 'values')
        if values and values[0].startswith("device:"):
            # 切换展开状态
            if self.tree.item(item, 'open'):
                self.tree.item(item, open=False)
            else:
                self.tree.item(item, open=True)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            # 根据选中项类型启用/禁用菜单项
            values = self.tree.item(item, 'values')
            if values:
                if values[0].startswith("device:"):
                    # 设备节点：禁用部分菜单
                    self.context_menu.entryconfigure("打开", state="disabled")
                    self.context_menu.entryconfigure("在资源管理器中打开", state="disabled")
                    self.context_menu.entryconfigure("重命名显示名称", state="disabled")
                    self.context_menu.entryconfigure("删除", state="disabled")
                    self.context_menu.entryconfigure("修改设备名称", state="normal")
                else:
                    # 书签节点：启用所有
                    self.context_menu.entryconfigure("打开", state="normal")
                    self.context_menu.entryconfigure("在资源管理器中打开", state="normal")
                    self.context_menu.entryconfigure("重命名显示名称", state="normal")
                    self.context_menu.entryconfigure("删除", state="normal")
                    self.context_menu.entryconfigure("修改设备名称", state="disabled")
            
            self.context_menu.post(event.x_root, event.y_root)

    def _open_selected(self):
        """打开选中的书签"""
        self._on_select(None)

    def _open_in_explorer(self):
        """在资源管理器中打开"""
        selection = self.tree.selection()
        if not selection:
            return
        
        values = self.tree.item(selection[0], 'values')
        if values and values[0].startswith("bookmark:"):
            parts = values[0].split(":", 3)
            if len(parts) >= 3:
                folder = parts[1]
                if os.path.exists(folder):
                    os.startfile(folder)

    def _rename_selected(self):
        """重命名选中项"""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.tree.item(item, 'values')
        if not values:
            return
        
        value_str = values[0]
        
        if value_str.startswith("bookmark:"):
            # 重命名书签
            parts = value_str.split(":", 3)
            if len(parts) >= 4:
                bm_id = parts[2]
                current_name = self.tree.item(item, 'text').replace("  📂 ", "").replace("[×] ", "")
                new_name = simpledialog.askstring("重命名书签", "请输入显示名称:", 
                                                  initialvalue=current_name)
                if new_name:
                    if self.config.update_bookmark_display_name(bm_id, new_name):
                        self.refresh_from_config()
                        
        elif value_str.startswith("device:"):
            # 重命名设备
            self._rename_device()

    def _rename_device(self):
        """修改当前设备显示名称"""
        current = getattr(self.config, 'current_device_name', self.config.device_id)
        new_name = simpledialog.askstring("修改设备名称", 
                                          "请输入本机显示名称（仅本地显示）:",
                                          initialvalue=current)
        if new_name:
            self.config.set_device_display_name(new_name)
            self._update_device_label()
            self.refresh_from_config()

    def add_bookmark(self):
        """添加新书签"""
        folder = filedialog.askdirectory(title="选择要添加的文件夹")
        if folder:
            folder = os.path.normpath(folder.strip())
            
            # 询问自定义显示名称
            default_name = os.path.basename(folder)
            display_name = simpledialog.askstring("书签名称", 
                                                  "请输入显示名称（留空使用默认）:",
                                                  initialvalue=default_name)
            if display_name is None:  # 用户取消
                return
            
            display_name = display_name.strip() or default_name
            
            if self.config.add_bookmark(folder, display_name):
                self.refresh_from_config()
                messagebox.showinfo("成功", f"已添加书签:\n{display_name}")
            else:
                messagebox.showwarning("提示", "该文件夹已在当前设备的书签中，或路径无效")

    def remove_selected(self):
        """移除选中的书签"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移除的书签")
            return
        
        item = selection[0]
        values = self.tree.item(item, 'values')
        if not values or not values[0].startswith("bookmark:"):
            messagebox.showwarning("提示", "请选择具体的书签文件夹，而非设备分组")
            return
        
        parts = values[0].split(":", 3)
        if len(parts) >= 4:
            bm_path = parts[1]
            dev_id = parts[3]
            
            # 确认删除
            bm_name = self.tree.item(item, 'text').replace("  📂 ", "").replace("[×] ", "")
            if messagebox.askyesno("确认删除", f"确定从书签中移除 '{bm_name}' 吗？\n\n路径: {bm_path}\n\n注意：这不会删除实际文件夹，仅移除书签和关联标签。"):
                # 找到索引并移除
                device_data = self.config.devices.get(dev_id, {})
                bookmarks = device_data.get('bookmarks', [])
                for idx, bm in enumerate(bookmarks):
                    if bm['path'] == bm_path:
                        if self.config.remove_bookmark(idx):
                            self.refresh_from_config()
                        break

    def get_selected(self):
        """获取当前选中的书签路径"""
        selection = self.tree.selection()
        if not selection:
            return None
        
        values = self.tree.item(selection[0], 'values')
        if values and values[0].startswith("bookmark:"):
            parts = values[0].split(":", 3)
            if len(parts) >= 3:
                return parts[1]
        return None
    
    def get_selected_bookmark_id(self):
        """获取当前选中的书签ID和详细信息"""
        selection = self.tree.selection()
        if not selection:
            return None
        
        values = self.tree.item(selection[0], 'values')
        if values and values[0].startswith("bookmark:"):
            parts = values[0].split(":", 3)
            if len(parts) >= 4:
                bm_id = parts[2]
                bm_path = parts[1]
                bm_name = self.tree.item(selection[0], 'text').replace("  📂 ", "").replace("[×] ", "")
                return (bm_id, bm_path, bm_name)
        return None