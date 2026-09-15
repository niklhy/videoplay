#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
书签管理模块 - 设备分组版（树形结构）
支持按设备分组显示，自定义显示名称
版本：2.0 - 支持启动恢复和用户点击分离
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import os
import threading


class BookmarkManager:
    """书签管理器 - 设备分组树形版"""

    def __init__(self, parent_frame, config_manager, db_manager, on_select_callback, debug_utils=None):
        import time
        
        self.parent = parent_frame
        self.config = config_manager
        self.db = db_manager
        self.on_select = on_select_callback
        self.debug = debug_utils
        
        # 【新增】启动恢复标志，禁止在恢复过程中保存状态
        self._is_restoring = True  # 启动时处于恢复模式
        self._pending_restore_id = None  # 待恢复的书签ID
        self._pending_restore_callback = None  # 待恢复完成后的回调
        
        # 【新增】记录上次选中的书签ID（用于防重复保存）
        self._last_selected_bookmark_id = None
        
        # 【关键修复】程序自动选中标志，防止 selection_set 触发 <<TreeviewSelect>> 时
        # 被误判为用户点击而保存 last_folder
        self._programmatic_selecting = False
        
        # 关键修复：书签树生成完成标志，确保树完全构建后才允许保存和回调
        self._is_tree_ready = False
        
        # 记录开始时间
        t0 = time.time()
        
        self.frame = ttk.LabelFrame(self.parent, text="书签管理器")
        self.frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        
        t1 = time.time()
        if self.debug:
            self.debug.print(f"[书签管理器] 框架创建完成 (耗时 {(t1-t0)*1000:.1f}ms)")

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
        # 【关键】选择事件需要区分用户点击和程序选中
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
        
        t2 = time.time()
        if self.debug:
            self.debug.print(f"[书签管理器] UI控件创建完成 (耗时 {(t2-t1)*1000:.1f}ms)")
        
        # 更新设备标签
        self._update_device_label()
        
        # 延迟100ms后异步加载书签（让UI先渲染出来）
        self.frame.after(100, self._async_refresh_bookmarks)
        
        t3 = time.time()
        if self.debug:
            self.debug.print(f"[书签管理器] 初始化完成，数据加载已调度 (总耗时 {(t3-t0)*1000:.1f}ms)")

    def _async_refresh_bookmarks(self):
        """异步刷新书签（避免阻塞UI主线程）"""
        import threading
        if self.debug:
            self.debug.print("[书签管理器] 启动后台线程加载书签数据...")
        
        # 关键修复：重置树生成完成标志
        self._is_tree_ready = False
        
        # 在后台线程执行数据准备（路径检查等可能阻塞的操作）
        thread = threading.Thread(target=self._prepare_bookmarks_data, daemon=True)
        thread.start()

    def _prepare_bookmarks_data(self):
        """后台线程准备书签数据（可包含阻塞式操作）"""
        import time
        t0 = time.time()
        
        current_device = self.config.device_id
        devices = self.config.get_all_devices()
        
        # 准备数据（此时可以进行耗时的路径检查）
        prepared_data = []
        for dev_id, dev_data in devices.items():
            device_info = {
                'dev_id': dev_id,
                'dev_data': dev_data,
                'is_current': (dev_id == current_device),
                'bookmarks_status': []  # 预计算书签状态
            }
            
            # 检查每个书签的路径（可能阻塞，但在后台线程安全）
            for bm in dev_data.get('bookmarks', []):
                bm_path = bm.get('path', '')
                # 使用快速检查或超时检查
                exists = self._quick_path_check(bm_path)
                device_info['bookmarks_status'].append({
                    'bm': bm,
                    'exists': exists
                })
            
            prepared_data.append(device_info)
        
        t1 = time.time()
        if self.debug:
            self.debug.print(f"[书签管理器] 数据准备完成 (耗时 {(t1-t0)*1000:.1f}ms)，回到主线程更新UI...")
        
        # 回到主线程更新UI
        self.frame.winfo_toplevel().after(0, lambda: self._update_tree_with_data(prepared_data))

    def _quick_path_check(self, path):
        """快速检查路径是否存在（避免网络路径阻塞）"""
        if not path:
            return False
        
        # 本地路径：使用标准检查
        if not path.startswith('\\\\'):
            try:
                return os.path.exists(path)
            except:
                return False
        
        # 网络路径：快速检查（避免阻塞）
        return True  # 网络路径假设存在，实际访问时再验证

    def _update_tree_with_data(self, prepared_data):
        """使用准备好的数据更新树形控件（在主线程执行）"""
        import time
        t0 = time.time()
        
        # 清空树
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # 清空路径到节点的映射（用于快速查找）
        self._path_to_item = {}  # {bookmark_id: item_id}
        
        # 按顺序添加设备节点（当前设备置顶）
        current_device_data = None
        other_devices = []
        
        for device_info in prepared_data:
            if device_info['is_current']:
                current_device_data = device_info
            else:
                other_devices.append(device_info)
        
        # 先添加当前设备
        if current_device_data:
            self._add_device_node_with_status(current_device_data)
        
        # 再添加其他设备
        for device_info in other_devices:
            self._add_device_node_with_status(device_info)
        
        t1 = time.time()
        if self.debug:
            self.debug.print(f"[书签管理器] UI树更新完成 (耗时 {(t1-t0)*1000:.1f}ms)")
        
        # 关键修复：标记书签树生成完成
        self._is_tree_ready = True
        if self.debug:
            self.debug.print("[书签管理器] 书签树生成完成，已允许保存和回调")
        
        # 【关键】树更新完成后，执行待恢复的书签选中
        self._execute_pending_restore()

    def _add_device_node_with_status(self, device_info):
        """添加设备节点（使用预计算的状态，避免在UI线程做路径检查）"""
        dev_id = device_info['dev_id']
        dev_data = device_info['dev_data']
        is_current = device_info['is_current']
        
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
        
        # 使用预计算的书签状态（不再调用 os.path.exists）
        for bm_status in device_info['bookmarks_status']:
            bm = bm_status['bm']
            exists = bm_status['exists']
            
            bm_path = bm['path']
            bm_display = bm['display_name']
            bm_id = bm['id']
            
            # 使用预计算的状态
            if not exists:
                bm_display = f"[×] {bm_display}"
                self.tree.tag_configure("missing", foreground="#ff6b6b")
                item_tag = "missing"
            else:
                item_tag = ""
            
            # 书签节点：存储完整路径和ID
            item_id = self.tree.insert(device_node, "end", text=f"  📂 {bm_display}", 
                           values=[f"bookmark:{bm_path}:{bm_id}:{dev_id}"], 
                           tags=[item_tag])
            
            # 【新增】建立书签ID到树节点的映射，方便快速恢复选中
            self._path_to_item[bm_id] = item_id

    def _update_device_label(self):
        """更新底部设备信息"""
        current = self.config.device_id
        name = getattr(self.config, 'current_device_name', current)
        if name != current:
            self.device_label.config(text=f"当前设备: {name} ({current})")
        else:
            self.device_label.config(text=f"当前设备: {current}")

    # ==================== 核心：选择事件处理（区分用户点击和程序选中）====================
    
    def _on_select(self, event):
        """
        处理选择事件 - 只有用户鼠标点击时才触发保存
        
        关键：通过检查事件来源来区分用户点击和程序选中
        - 程序选中时不会触发 <<TreeviewSelect>> 事件，或触发时 event 为 None
        - 用户点击时 event 不为 None 且包含坐标信息
        """
        # 【关键修复】如果正在程序自动选中，直接返回，不保存，不触发回调
        if self._programmatic_selecting:
            return
        
        # 【关键修复】如果书签树尚未生成完成，不保存，不触发回调（避免启动期间误操作）
        if not self._is_tree_ready:
            if self.debug:
                self.debug.print("[书签管理器] 树尚未就绪，跳过保存")
            return
        
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
            try:
                content = value_str[len("bookmark:"):]
                parts = content.rsplit(":", 2)
                if len(parts) >= 3:
                    folder = parts[0]
                    bm_id = parts[1]
                    dev_id = parts[2]
                    
                    # 【关键】判断是否是用户主动点击
                    is_user_click = self._is_user_click_event(event)
                    
                    if is_user_click:
                        # 用户点击：保存最后选中的书签到 config.json
                        self._save_last_selected_bookmark(bm_id, folder)
                    
                    # 触发外部回调（加载图库）
                    if os.path.exists(folder) and self.on_select:
                        self.on_select(folder)
                    elif not os.path.exists(folder):
                        messagebox.showerror("路径不存在", f"该文件夹已无法访问:\n{folder}")
                        
            except Exception as e:
                if self.debug:
                    self.debug.print(f"书签选择解析错误: {e}, value={value_str}")
    
    def _is_user_click_event(self, event):
        """
        判断事件是否由用户鼠标点击触发
        
        Returns:
            bool: True 表示用户点击，False 表示程序选中
        """
        # 恢复期间，任何选择都不应保存
        if self._is_restoring:
            return False
        
        # 如果没有事件对象，肯定是程序选中
        if event is None:
            return False
        
        # 检查事件是否有有效的坐标（用户点击会有鼠标位置）
        try:
            if hasattr(event, 'x') and hasattr(event, 'y'):
                # 用户点击会有有效的坐标
                if event.x > 0 or event.y > 0:
                    return True
        except:
            pass
        
        # 检查是否是有效的 Tkinter 事件对象
        if hasattr(event, 'type') and str(event.type) == 'ButtonPress':
            return True
        
        # 默认认为是用户点击（但结合恢复标志判断）
        return True
    
    def _save_last_selected_bookmark(self, bookmark_id, folder_path):
        """
        保存最后选中的书签到 config.json
        
        Args:
            bookmark_id: 书签ID
            folder_path: 书签路径
        """
        # 防重复：如果和上次保存的ID相同，不重复保存
        if self._last_selected_bookmark_id == bookmark_id:
            if self.debug:
                self.debug.print(f"[书签保存] 跳过重复保存: {bookmark_id}")
            return
        
        self._last_selected_bookmark_id = bookmark_id
        
        # 保存到 config.json 的 last_folder
        self.config.last_folder = folder_path
        
        # 保存到 bookmark_states（可选，用于记录书签状态）
        # 注意：这里只保存最后打开的文件夹，不保存树状态（树状态由 TreeExplorer 管理）
        self.config.save_bookmark_state(
            bookmark_id,
            folder_path,
            tree_state={},  # 树状态由 TreeExplorer 管理，这里不覆盖
            gallery_scroll_y=0
        )
        
        self.config.save_config()
        
        if self.debug:
            self.debug.print(f"[书签保存] 用户点击书签: {bookmark_id} -> {folder_path}")

    # ==================== 启动恢复相关方法 ====================
    
    def set_pending_restore(self, bookmark_id, callback=None):
        """
        设置待恢复的书签（在树加载完成后执行）
        
        Args:
            bookmark_id: 要恢复的书签ID
            callback: 恢复完成后的回调
        """
        self._pending_restore_id = bookmark_id
        self._pending_restore_callback = callback
        if self.debug:
            self.debug.print(f"[书签管理器] 设置待恢复书签: {bookmark_id}")
        
        # 如果树已经加载完成，立即执行恢复
        if hasattr(self, '_path_to_item') and self._path_to_item:
            self._execute_pending_restore()
    
    def _execute_pending_restore(self):
        """执行待恢复的书签选中"""
        if not self._pending_restore_id:
            return
        
        bookmark_id = self._pending_restore_id
        callback = self._pending_restore_callback
        
        # 清空待恢复记录
        self._pending_restore_id = None
        self._pending_restore_callback = None
        
        # 执行恢复选中
        success = self._select_bookmark_by_id(bookmark_id, trigger_callback=False)
        
        if success:
            if self.debug:
                self.debug.print(f"[书签管理器] 已恢复书签: {bookmark_id}")
        else:
            if self.debug:
                self.debug.print(f"[书签管理器] 未找到待恢复书签: {bookmark_id}")
        
        # 【关键】恢复完成后，延迟关闭恢复标志
        # 延迟500ms确保所有初始化完成
        def finish_restore():
            self._is_restoring = False
            if self.debug:
                self.debug.print("[书签管理器] 恢复阶段结束，已启用用户点击保存")
        
        self.frame.after(500, finish_restore)
        
        # 执行回调
        if callback:
            callback(success)
    
    def _select_bookmark_by_id(self, bookmark_id, trigger_callback=False):
        """
        根据书签ID选中对应的树节点
        
        Args:
            bookmark_id: 书签ID
            trigger_callback: 是否触发 on_select 回调（启动时设为 False）
            
        Returns:
            bool: 是否成功选中
        """
        if not bookmark_id:
            return False
        
        # 查找书签节点
        item_id = self._path_to_item.get(bookmark_id)
        if not item_id:
            if self.debug:
                self.debug.print(f"[书签管理器] 未找到书签节点: {bookmark_id}")
            return False
        
        # 临时禁用选择事件（避免触发保存）
        original_on_select = self.on_select
        if not trigger_callback:
            self.on_select = None
        
        try:
            # 【关键修复】标记程序自动选中，防止 <<TreeviewSelect>> 事件穿透
            self._programmatic_selecting = True
            
            # 确保父节点展开
            parent = self.tree.parent(item_id)
            if parent:
                self.tree.item(parent, open=True)
            
            # 选中节点
            self.tree.selection_set(item_id)
            self.tree.see(item_id)
            self.tree.focus(item_id)
            
            # 如果需要触发回调，手动调用
            if trigger_callback and original_on_select:
                values = self.tree.item(item_id, 'values')
                if values and values[0].startswith("bookmark:"):
                    content = values[0][len("bookmark:"):]
                    parts = content.rsplit(":", 2)
                    if len(parts) >= 3:
                        folder = parts[0]
                        if os.path.exists(folder):
                            original_on_select(folder)
            
            return True
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"[书签管理器] 选中书签失败: {e}")
            return False
        finally:
            # 【关键修复】恢复标志
            self._programmatic_selecting = False
            if not trigger_callback:
                self.on_select = original_on_select

    # ==================== 兼容旧接口 ====================
    
    def refresh_from_config(self):
        """从配置刷新树形结构 - 异步优化"""
        # 清空树
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self._path_to_item = {}
        
        current_device = self.config.device_id
        devices = self.config.get_all_devices()
        
        # 当前设备置顶
        if current_device in devices:
            self._add_device_node(current_device, devices[current_device], is_current=True)
        
        # 其他设备
        for dev_id, dev_data in devices.items():
            if dev_id != current_device:
                self._add_device_node(dev_id, dev_data, is_current=False)
        
        # 延迟检查路径（避免阻塞主线程）
        self.frame.after_idle(self._check_bookmarks_async)

    def _add_device_node(self, dev_id, dev_data, is_current=False):
        """添加设备节点 - 快速显示，路径检查延迟"""
        display_name = dev_data.get('display_name', dev_id)
        
        if is_current:
            node_text = f"📱 {display_name} (本机)"
            tag = "current"
            self.tree.tag_configure("current", background="#e3f2fd", font=("微软雅黑", 9, "bold"))
        else:
            node_text = f"💻 {display_name}"
            tag = "other"
            self.tree.tag_configure("other", foreground="#666666")
        
        device_node = self.tree.insert("", "end", text=node_text, values=[f"device:{dev_id}"], tags=[tag], open=is_current)
        
        # 添加书签节点
        bookmarks = dev_data.get('bookmarks', [])
        for bm in bookmarks:
            bm_path = bm['path']
            bm_display = bm['display_name']
            bm_id = bm['id']
            
            item_id = self.tree.insert(device_node, "end", text=f"  📂 {bm_display}", 
                           values=[f"bookmark:{bm_path}:{bm_id}:{dev_id}"], 
                           tags=[""])
            self._path_to_item[bm_id] = item_id

    def _check_bookmarks_async(self):
        """后台线程检查书签路径是否存在"""
        if not self.tree.winfo_exists():
            return
        
        items_to_check = []
        for device_item in self.tree.get_children():
            for bookmark_item in self.tree.get_children(device_item):
                values = self.tree.item(bookmark_item, 'values')
                if values and values[0].startswith('bookmark:'):
                    content = values[0][len("bookmark:"):]
                    parts = content.rsplit(":", 2)
                    if len(parts) >= 3:
                        bm_path = parts[0]
                        items_to_check.append((bookmark_item, bm_path))
        
        if not items_to_check:
            return
        
        def worker():
            missing_items = []
            for item_id, bm_path in items_to_check:
                try:
                    if not os.path.exists(bm_path):
                        missing_items.append(item_id)
                except Exception:
                    missing_items.append(item_id)
            self.frame.winfo_toplevel().after(0, lambda: self._mark_missing_bookmarks(missing_items))
        
        threading.Thread(target=worker, daemon=True).start()

    def _mark_missing_bookmarks(self, missing_items):
        """标记缺失的书签路径"""
        if not self.tree.winfo_exists():
            return
        self.tree.tag_configure("missing", foreground="#ff6b6b")
        for item_id in missing_items:
            try:
                text = self.tree.item(item_id, 'text')
                if text and "[×]" not in text:
                    clean_text = text.replace("  📂 ", "").strip()
                    self.tree.item(item_id, text=f"  📂 [×] {clean_text}", tags=["missing"])
            except Exception:
                pass

    def _on_double_click(self, event):
        """双击展开/收起设备节点，或打开书签"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        values = self.tree.item(item, 'values')
        if values and values[0].startswith("device:"):
            if self.tree.item(item, 'open'):
                self.tree.item(item, open=False)
            else:
                self.tree.item(item, open=True)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            # 【关键修复】右键菜单选中节点时不触发保存和回调
            try:
                self._programmatic_selecting = True
                self.tree.selection_set(item)
            finally:
                self._programmatic_selecting = False
            values = self.tree.item(item, 'values')
            if values:
                if values[0].startswith("device:"):
                    self.context_menu.entryconfigure("打开", state="disabled")
                    self.context_menu.entryconfigure("在资源管理器中打开", state="disabled")
                    self.context_menu.entryconfigure("重命名显示名称", state="disabled")
                    self.context_menu.entryconfigure("删除", state="disabled")
                    self.context_menu.entryconfigure("修改设备名称", state="normal")
                else:
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
            try:
                content = values[0][len("bookmark:"):]
                parts = content.rsplit(":", 2)
                if len(parts) >= 3:
                    folder = parts[0]
                    if os.path.exists(folder):
                        os.startfile(folder)
            except:
                pass

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
            try:
                content = value_str[len("bookmark:"):]
                parts = content.rsplit(":", 2)
                if len(parts) >= 3:
                    bm_id = parts[1]
                    current_name = self.tree.item(item, 'text').replace("  📂 ", "").replace("[×] ", "")
                    new_name = simpledialog.askstring("重命名书签", "请输入显示名称:", 
                                                      initialvalue=current_name)
                    if new_name:
                        if self.config.update_bookmark_display_name(bm_id, new_name):
                            self.refresh_from_config()
            except:
                pass
        elif value_str.startswith("device:"):
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
            
            default_name = os.path.basename(folder)
            display_name = simpledialog.askstring("书签名称", 
                                                  "请输入显示名称（留空使用默认）:",
                                                  initialvalue=default_name)
            if display_name is None:
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
        
        try:
            content = values[0][len("bookmark:"):]
            parts = content.rsplit(":", 2)
            if len(parts) >= 3:
                bm_path = parts[0]
                bm_id = parts[1]
                dev_id = parts[2]
            else:
                messagebox.showerror("错误", "书签数据格式无效")
                return
        except Exception as e:
            messagebox.showerror("错误", f"解析书签数据失败: {e}")
            return
        
        bm_name = self.tree.item(item, 'text').replace("  📂 ", "").replace("[×] ", "")
        if messagebox.askyesno("确认删除", f"确定从书签中移除 '{bm_name}' 吗？\n\n路径: {bm_path}\n\n注意：这不会删除实际文件夹，仅移除书签和关联标签。"):
            device_data = self.config.devices.get(dev_id, {})
            bookmarks = device_data.get('bookmarks', [])
            
            for idx, bm in enumerate(bookmarks):
                if bm['path'] == bm_path or bm['id'] == bm_id:
                    if self.config.remove_bookmark(idx):
                        self.refresh_from_config()
                        messagebox.showinfo("成功", f"书签 '{bm_name}' 已移除")
                    break

    def get_selected(self):
        """获取当前选中的书签路径"""
        selection = self.tree.selection()
        if not selection:
            return None
        
        values = self.tree.item(selection[0], 'values')
        if values and values[0].startswith("bookmark:"):
            try:
                content = values[0][len("bookmark:"):]
                parts = content.rsplit(":", 2)
                if len(parts) >= 3:
                    return parts[0]
            except:
                pass
        return None
    
    def get_selected_bookmark_id(self):
        """获取当前选中的书签ID和详细信息"""
        selection = self.tree.selection()
        if not selection:
            return None
        
        values = self.tree.item(selection[0], 'values')
        if values and values[0].startswith("bookmark:"):
            try:
                content = values[0][len("bookmark:"):]
                parts = content.rsplit(":", 2)
                if len(parts) >= 3:
                    bm_path = parts[0]
                    bm_id = parts[1]
                    bm_name = self.tree.item(selection[0], 'text').replace("  📂 ", "").replace("[×] ", "")
                    return (bm_id, bm_path, bm_name)
            except:
                pass
        return None

    def select_by_id(self, bookmark_id, trigger_callback=True):
        """根据书签ID在树中选中对应节点并确保可见（兼容旧接口）"""
        return self._select_bookmark_by_id(bookmark_id, trigger_callback)

    def restore_last_bookmark(self, bookmark_id, trigger_callback=True):
        """
        恢复上次使用的书签状态
        
        Args:
            bookmark_id: 上次使用的书签ID
            trigger_callback: 是否触发 on_select 回调（启动时设为 False）
        """
        if not bookmark_id:
            if self.debug:
                self.debug.print("[书签管理器] 无上次书签ID，跳过恢复")
            return
        
        # 设置待恢复（等待树加载完成后执行）
        self.set_pending_restore(bookmark_id, 
            callback=lambda success: self._on_restore_complete(success, trigger_callback))
    
    def _on_restore_complete(self, success, trigger_callback):
        """恢复完成后的回调"""
        if self.debug:
            if success:
                action = "触发回调" if trigger_callback else "静默恢复"
                self.debug.print(f"[书签管理器] 恢复完成，{action}")
            else:
                self.debug.print(f"[书签管理器] 恢复失败")