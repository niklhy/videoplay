#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
标签管理模块 - 跨设备版
适配新的 tags_v2 配置结构，支持显示多设备标签
添加标签排序功能
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os


class TagManager:
    """标签管理器 - 跨设备版"""
    
    def __init__(self, parent_frame, config_manager, debug_utils=None):
        self.parent = parent_frame
        self.config = config_manager  # 使用配置管理器而非数据库
        self.debug = debug_utils
        self.current_tag = None
        self.on_play_video = None
        self.on_tags_changed = None
        
        self._create_ui()
    
    def _create_ui(self):
        # 使用PanedWindow，设置左右面板的初始宽度比例
        paned = ttk.PanedWindow(self.parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧面板 - 标签列表，设置固定宽度400px
        left_frame = ttk.LabelFrame(paned, text="标签列表", width=400)
        left_frame.pack_propagate(False)  # 防止子控件撑开父容器
        left_frame.configure(width=300)
        
        # 添加设备筛选选项
        filter_frame = ttk.Frame(left_frame)
        filter_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(filter_frame, text="显示:", font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.device_filter = ttk.Combobox(filter_frame, state="readonly", width=15)
        self.device_filter.pack(side=tk.LEFT, padx=2)
        self.device_filter.bind("<<ComboboxSelected>>", self._on_device_filter_change)
        
        # 排序按钮框架
        sort_frame = ttk.Frame(left_frame)
        sort_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Button(sort_frame, text="⬆ 上移", command=self._move_tag_up, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(sort_frame, text="⬇ 下移", command=self._move_tag_down, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(sort_frame, text="📌 置顶", command=self._move_tag_top, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(sort_frame, text="📌 置底", command=self._move_tag_bottom, width=8).pack(side=tk.LEFT, padx=2)
        
        self.stats_label = ttk.Label(left_frame, text="", font=("微软雅黑", 9), foreground="blue")
        self.stats_label.pack(padx=5, pady=2)
        
        # 使用Listbox支持拖拽排序（后续可扩展）
        self.tag_listbox = tk.Listbox(left_frame, selectmode=tk.SINGLE, font=("微软雅黑", 10))
        self.tag_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tag_listbox.bind("<<ListboxSelect>>", self._on_tag_select)
        
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="添加", command=self._add_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="修改", command=self._rename_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除", command=self._delete_tag).pack(side=tk.LEFT, padx=2)
        
        # 右侧面板 - 已标记视频，权重设置为1（占据剩余所有空间）
        right_frame = ttk.LabelFrame(paned, text="已标记视频")
        
        # 添加到PanedWindow
        paned.add(left_frame, weight=0)  # weight=0 表示不自动拉伸
        paned.add(right_frame, weight=1)  # weight=1 表示自动拉伸填充剩余空间
        
        self.filter_label = ttk.Label(right_frame, text="选择标签查看视频", 
                                     font=("微软雅黑", 9, "italic"), foreground="gray")
        self.filter_label.pack(padx=5, pady=2)
        
        columns = ("folder", "filename", "tags", "device", "path")
        self.video_tree = ttk.Treeview(right_frame, columns=columns, show="headings", selectmode="extended")
        self.video_tree.heading("folder", text="文件夹")
        self.video_tree.heading("filename", text="文件名")
        self.video_tree.heading("tags", text="所有标签")
        self.video_tree.heading("device", text="设备")
        self.video_tree.heading("path", text="完整路径")
        
        self.video_tree.column("folder", width=150)
        self.video_tree.column("filename", width=150)
        self.video_tree.column("tags", width=150)
        self.video_tree.column("device", width=80)
        self.video_tree.column("path", width=250)
        
        scroll_y = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.video_tree.yview)
        scroll_x = ttk.Scrollbar(right_frame, orient=tk.HORIZONTAL, command=self.video_tree.xview)
        self.video_tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        
        self.video_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        video_btn_frame = ttk.Frame(right_frame)
        video_btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(video_btn_frame, text="移除标记", command=self._remove_video_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(video_btn_frame, text="刷新", command=self.refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(video_btn_frame, text="多标签视频", command=self._show_multi_tag).pack(side=tk.LEFT, padx=2)
        
        self._create_context_menu()
        self._update_device_filter()
        # 延迟异步加载标签数据，避免阻塞主线程和窗体显示
        self.parent.after(10, self.refresh)
    
    def _update_device_filter(self):
        """更新设备筛选下拉框"""
        devices = self.config.get_all_devices()
        current_dev = self.config.device_id
        
        choices = ["全部设备"]
        self.device_map = {"全部设备": None}
        
        # 当前设备置顶
        if current_dev in devices:
            name = devices[current_dev].get('display_name', current_dev)
            display = f"{name} (本机)"
            choices.append(display)
            self.device_map[display] = current_dev
        
        # 其他设备
        for dev_id, dev_data in devices.items():
            if dev_id != current_dev:
                name = dev_data.get('display_name', dev_id)
                choices.append(name)
                self.device_map[name] = dev_id
        
        self.device_filter['values'] = choices
        self.device_filter.current(0)  # 默认选择"全部设备"
    
    def _on_device_filter_change(self, event=None):
        """设备筛选改变时刷新"""
        self.refresh()
    
    def _create_context_menu(self):
        self.video_menu = tk.Menu(self.video_tree, tearoff=0)
        self.video_menu.add_command(label="▶ 播放", command=self._play_selected)
        self.video_menu.add_command(label="📂 打开文件夹", command=self._open_folder)
        self.video_menu.add_separator()
        self.video_menu.add_command(label="复制路径", command=self._copy_path)
        self.video_menu.add_command(label="移除标记", command=self._remove_video_tag)
        
        self.video_tree.bind("<Button-3>", self._show_context_menu)
    
    def _show_context_menu(self, event):
        try:
            item = self.video_tree.identify_row(event.y)
            if item:
                self.video_tree.selection_set(item)
                self.video_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.video_menu.grab_release()
    
    def _get_tag_display_text(self, tag_name, count, device_count=1, selected_device=None):
        """获取标签显示文本"""
        if selected_device is None and device_count > 1:
            return f"{tag_name} ({count}) [{device_count}设备]"
        else:
            return f"{tag_name} ({count})"
    
    def refresh(self):
        """从配置刷新标签列表（支持显示无视频的标签）"""
        self.tag_listbox.delete(0, tk.END)
        
        current_dev = self.config.device_id
        selected_device = self.device_map.get(self.device_filter.get(), None)
        devices = self.config.get_all_devices()
        
        # 获取所有定义的标签（保持用户定义的顺序）
        defined_tags = self.config._config.get('tags', [])
        
        # 收集标签统计（实际使用的标签）
        tag_counts = {}
        tag_devices = {}  # 记录每个标签出现的设备
        
        for dev_id, dev_data in getattr(self.config, 'tags_v2', {}).items():
            # 如果筛选了特定设备，跳过其他设备
            if selected_device is not None and dev_id != selected_device:
                continue
                
            device_name = devices.get(dev_id, {}).get('display_name', dev_id)
            
            for bm_id, files in dev_data.items():
                for rel_path, tags in files.items():
                    for tag in tags:
                        if tag not in tag_counts:
                            tag_counts[tag] = 0
                            tag_devices[tag] = set()
                        tag_counts[tag] += 1
                        tag_devices[tag].add(device_name)
        
        # 构建标签列表：先按定义顺序添加定义过的标签，再补充其他实际使用的标签
        tags_to_display = []
        processed_tags = set()
        
        # 1. 按定义顺序添加所有定义的标签
        for tag in defined_tags:
            if tag:  # 确保不是空字符串
                tags_to_display.append(tag)
                processed_tags.add(tag)
        
        # 2. 补充只在实际使用中但不在定义列表中的标签（按字母顺序）
        remaining = set(tag_counts.keys()) - processed_tags
        for tag in sorted(remaining):
            tags_to_display.append(tag)
        
        # 插入列表（使用格式化的显示文本）
        for tag_name in tags_to_display:
            count = tag_counts.get(tag_name, 0)
            device_count = len(tag_devices.get(tag_name, set())) if tag_name in tag_devices else 0
            
            display_text = self._get_tag_display_text(
                tag_name, count, device_count, selected_device
            )
            self.tag_listbox.insert(tk.END, display_text)
        
        # 更新统计标签
        total_tags = len(tags_to_display)
        total_files = sum(tag_counts.values()) if tag_counts else 0
        filter_text = ""
        if selected_device:
            dev_name = devices.get(selected_device, {}).get('display_name', selected_device)
            filter_text = f" [{dev_name}]"
        
        self.stats_label.config(
            text=f"共 {total_tags} 个标签{filter_text}，{total_files} 个已标记视频",
            foreground="blue"
        )
    
    def _get_tag_name_from_display(self, display_text):
        """从显示文本中提取标签名"""
        # 格式: "标签名 (数量)" 或 "标签名 (数量) [N设备]"
        if " (" in display_text:
            return display_text.split(" (")[0]
        return display_text
    
    def _move_tag_up(self):
        """上移选中的标签"""
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移动的标签")
            return
        
        idx = selection[0]
        if idx == 0:
            messagebox.showinfo("提示", "已在最顶部")
            return
        
        # 获取当前标签列表
        display_text = self.tag_listbox.get(idx)
        tag_name = self._get_tag_name_from_display(display_text)
        
        # 获取当前定义顺序
        defined_tags = self.config._config.get('tags', [])
        
        # 确保标签在定义列表中
        if tag_name not in defined_tags:
            # 如果不在定义列表中，先添加
            defined_tags.append(tag_name)
        
        # 找到位置并交换
        pos = defined_tags.index(tag_name)
        defined_tags[pos], defined_tags[pos-1] = defined_tags[pos-1], defined_tags[pos]
        
        # 保存并刷新
        self.config._config['tags'] = defined_tags
        self.config.save_config()
        
        if self.on_tags_changed:
            self.on_tags_changed()
        
        self.refresh()
        
        # 重新选中移动后的标签
        for i, item in enumerate(self.tag_listbox.get(0, tk.END)):
            if self._get_tag_name_from_display(item) == tag_name:
                self.tag_listbox.selection_set(i)
                self.tag_listbox.see(i)
                break
    
    def _move_tag_down(self):
        """下移选中的标签"""
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移动的标签")
            return
        
        idx = selection[0]
        last_idx = self.tag_listbox.size() - 1
        if idx == last_idx:
            messagebox.showinfo("提示", "已在最底部")
            return
        
        # 获取当前标签列表
        display_text = self.tag_listbox.get(idx)
        tag_name = self._get_tag_name_from_display(display_text)
        
        # 获取当前定义顺序
        defined_tags = self.config._config.get('tags', [])
        
        # 确保标签在定义列表中
        if tag_name not in defined_tags:
            defined_tags.append(tag_name)
        
        # 找到位置并交换
        pos = defined_tags.index(tag_name)
        defined_tags[pos], defined_tags[pos+1] = defined_tags[pos+1], defined_tags[pos]
        
        # 保存并刷新
        self.config._config['tags'] = defined_tags
        self.config.save_config()
        
        if self.on_tags_changed:
            self.on_tags_changed()
        
        self.refresh()
        
        # 重新选中移动后的标签
        for i, item in enumerate(self.tag_listbox.get(0, tk.END)):
            if self._get_tag_name_from_display(item) == tag_name:
                self.tag_listbox.selection_set(i)
                self.tag_listbox.see(i)
                break
    
    def _move_tag_top(self):
        """将选中标签移到顶部"""
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移动的标签")
            return
        
        idx = selection[0]
        if idx == 0:
            messagebox.showinfo("提示", "已在最顶部")
            return
        
        display_text = self.tag_listbox.get(idx)
        tag_name = self._get_tag_name_from_display(display_text)
        
        defined_tags = self.config._config.get('tags', [])
        
        # 确保标签在定义列表中
        if tag_name not in defined_tags:
            defined_tags.append(tag_name)
        
        # 移除并插入到开头
        defined_tags.remove(tag_name)
        defined_tags.insert(0, tag_name)
        
        self.config._config['tags'] = defined_tags
        self.config.save_config()
        
        if self.on_tags_changed:
            self.on_tags_changed()
        
        self.refresh()
        self.tag_listbox.selection_set(0)
        self.tag_listbox.see(0)
    
    def _move_tag_bottom(self):
        """将选中标签移到底部"""
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要移动的标签")
            return
        
        idx = selection[0]
        last_idx = self.tag_listbox.size() - 1
        if idx == last_idx:
            messagebox.showinfo("提示", "已在最底部")
            return
        
        display_text = self.tag_listbox.get(idx)
        tag_name = self._get_tag_name_from_display(display_text)
        
        defined_tags = self.config._config.get('tags', [])
        
        # 确保标签在定义列表中
        if tag_name not in defined_tags:
            defined_tags.append(tag_name)
        
        # 移除并添加到末尾
        defined_tags.remove(tag_name)
        defined_tags.append(tag_name)
        
        self.config._config['tags'] = defined_tags
        self.config.save_config()
        
        if self.on_tags_changed:
            self.on_tags_changed()
        
        self.refresh()
        self.tag_listbox.selection_set(last_idx)
        self.tag_listbox.see(last_idx)
    
    def _on_tag_select(self, event):
        selection = self.tag_listbox.curselection()
        if not selection:
            return
        
        idx = selection[0]
        tag_text = self.tag_listbox.get(idx)
        # 解析标签名（去掉数量）
        self.current_tag = self._get_tag_name_from_display(tag_text)
        
        self._refresh_video_list(self.current_tag)
    
    def _refresh_video_list(self, tag_name):
        for item in self.video_tree.get_children():
            self.video_tree.delete(item)
        
        selected_device = self.device_map.get(self.device_filter.get(), None)
        devices = self.config.get_all_devices()
        matching_videos = []
        
        # 从所有设备的标签中查找
        for dev_id, dev_data in getattr(self.config, 'tags_v2', {}).items():
            if selected_device is not None and dev_id != selected_device:
                continue
            
            # 获取该书签的映射
            device_bookmarks = {}
            if dev_id in devices:
                for bm in devices[dev_id].get('bookmarks', []):
                    device_bookmarks[bm['id']] = bm['path']
            
            device_name = devices.get(dev_id, {}).get('display_name', dev_id)
            
            for bm_id, files in dev_data.items():
                bm_path = device_bookmarks.get(bm_id)
                if not bm_path:
                    continue
                
                for rel_path, tags in files.items():
                    if tag_name in tags:
                        abs_path = os.path.normpath(os.path.join(bm_path, rel_path))
                        if os.path.exists(abs_path):
                            folder = os.path.basename(os.path.dirname(abs_path))
                            filename = os.path.basename(abs_path)
                            tags_str = ", ".join(tags)
                            matching_videos.append((folder, filename, tags_str, device_name, abs_path))
        
        # 插入树形控件
        for folder, filename, tags_str, device, path in matching_videos:
            self.video_tree.insert("", "end", values=(folder, filename, tags_str, device, path))
        
        self.filter_label.config(
            text=f"标签 '{tag_name}' 共 {len(matching_videos)} 个视频",
            foreground="blue"
        )
    
    def _show_multi_tag(self):
        for item in self.video_tree.get_children():
            self.video_tree.delete(item)
        
        selected_device = self.device_map.get(self.device_filter.get(), None)
        devices = self.config.get_all_devices()
        multi_videos = []
        
        for dev_id, dev_data in getattr(self.config, 'tags_v2', {}).items():
            if selected_device is not None and dev_id != selected_device:
                continue
            
            # 获取书签映射
            device_bookmarks = {}
            if dev_id in devices:
                for bm in devices[dev_id].get('bookmarks', []):
                    device_bookmarks[bm['id']] = bm['path']
            
            device_name = devices.get(dev_id, {}).get('display_name', dev_id)
            
            for bm_id, files in dev_data.items():
                bm_path = device_bookmarks.get(bm_id)
                if not bm_path:
                    continue
                
                for rel_path, tags in files.items():
                    if len(tags) > 1:
                        abs_path = os.path.normpath(os.path.join(bm_path, rel_path))
                        if os.path.exists(abs_path):
                            folder = os.path.basename(os.path.dirname(abs_path))
                            filename = os.path.basename(abs_path)
                            multi_videos.append((len(tags), folder, filename, ", ".join(tags), device_name, abs_path))
        
        multi_videos.sort(reverse=True)
        
        for _, folder, filename, tags, device, path in multi_videos:
            self.video_tree.insert("", "end", values=(folder, filename, tags, device, path))
        
        self.filter_label.config(text=f"多标签视频: {len(multi_videos)} 个", foreground="purple")
        self.current_tag = None
    
    def _add_tag(self):
        name = simpledialog.askstring("添加标签", "请输入标签名称:", parent=self.parent)
        if name and name.strip():
            success = self.config.add_tag(name.strip())
            if success:
                if self.on_tags_changed:
                    self.on_tags_changed()
                self.refresh()
                messagebox.showinfo("成功", f"标签 '{name.strip()}' 已添加")
            else:
                messagebox.showwarning("提示", "标签已存在")
    
    def _rename_tag(self):
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要修改的标签")
            return
        
        idx = selection[0]
        tag_text = self.tag_listbox.get(idx)
        old_name = self._get_tag_name_from_display(tag_text)
        
        new_name = simpledialog.askstring("修改标签", f"将 '{old_name}' 修改为:", 
                                         parent=self.parent, initialvalue=old_name)
        
        if new_name and new_name.strip() and new_name.strip() != old_name:
            success = self.config.rename_tag(old_name, new_name.strip())
            if success:
                if self.on_tags_changed:
                    self.on_tags_changed()
                self.refresh()
                messagebox.showinfo("成功", f"标签已重命名为 '{new_name.strip()}'")
            else:
                messagebox.showwarning("提示", "重命名失败，可能是新名称已存在")
    
    def _delete_tag(self):
        selection = self.tag_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的标签")
            return
        
        idx = selection[0]
        tag_text = self.tag_listbox.get(idx)
        tag_name = self._get_tag_name_from_display(tag_text)
        
        # 统计使用数量（所有设备）
        count = 0
        for dev_data in getattr(self.config, 'tags_v2', {}).values():
            for files in dev_data.values():
                for tags in files.values():
                    if tag_name in tags:
                        count += 1
        
        msg = f"确定删除标签 '{tag_name}' 吗？"
        if count > 0:
            msg += f"\n该标签已绑定 {count} 个视频（跨设备）。"
        
        if messagebox.askyesno("确认删除", msg):
            success = self.config.remove_tag(tag_name)
            if success:
                if self.on_tags_changed:
                    self.on_tags_changed()
                self.refresh()
                
                for item in self.video_tree.get_children():
                    self.video_tree.delete(item)
                self.filter_label.config(text="选择标签查看视频", foreground="gray")
                messagebox.showinfo("成功", f"标签 '{tag_name}' 已删除")
    
    def _remove_video_tag(self):
        selection = self.video_tree.selection()
        if not selection:
            return
        
        removed = 0
        for item in selection:
            values = self.video_tree.item(item, "values")
            if values:
                video_path = values[4]  # path列是第5列（索引4）
                if self.current_tag:
                    # 移除特定标签
                    current_tags = self.config.get_video_tags(video_path)
                    if self.current_tag in current_tags:
                        current_tags.remove(self.current_tag)
                        self.config.set_video_tags(video_path, current_tags)
                        removed += 1
                else:
                    # 移除所有标签
                    self.config.set_video_tags(video_path, [])
                    removed += 1
        
        if removed > 0:
            self.config.save_config()  # 确保保存
            if self.on_tags_changed:
                self.on_tags_changed()
            if self.current_tag:
                self._refresh_video_list(self.current_tag)
            else:
                self._show_multi_tag()
            self.refresh()
            messagebox.showinfo("成功", f"已移除 {removed} 个视频的标记")
    
    def _play_selected(self):
        selection = self.video_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.video_tree.item(item, "values")
        if values and self.on_play_video:
            video_path = values[4]  # path列
            self.on_play_video(video_path)
    
    def _open_folder(self):
        selection = self.video_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.video_tree.item(item, "values")
        if values:
            video_path = values[4]
            from core import PathUtils
            folder = os.path.dirname(video_path)
            PathUtils.open_folder_in_explorer(folder)
    
    def _copy_path(self):
        selection = self.video_tree.selection()
        if not selection:
            return
        
        item = selection[0]
        values = self.video_tree.item(item, "values")
        if values:
            video_path = values[4]
            self.parent.clipboard_clear()
            self.parent.clipboard_append(video_path)
            messagebox.showinfo("成功", "路径已复制到剪贴板")
    
    def set_play_callback(self, callback):
        self.on_play_video = callback
    
    def set_tags_changed_callback(self, callback):
        self.on_tags_changed = callback


class TagSelectorDialog:
    """标签选择对话框 - 配置文件版本（适配新配置结构）"""
    
    def __init__(self, parent, config_manager, video_paths, debug_utils=None, db_manager=None):
        self.parent = parent
        self.config = config_manager
        self.db = db_manager  # 仅用于更新数据库中的tag_summary字段
        self.video_paths = video_paths if isinstance(video_paths, list) else [video_paths]
        self.debug = debug_utils
        self.result = False
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"设置标签 ({len(self.video_paths)} 个视频)")
        self.dialog.geometry("400x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self._create_ui()
        
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - 200
        y = (self.dialog.winfo_screenheight() // 2) - 250
        self.dialog.geometry(f"+{x}+{y}")
    
    def _create_ui(self):
        ttk.Label(self.dialog, text=f"为 {len(self.video_paths)} 个视频设置标签：", 
                 font=("微软雅黑", 10, "bold")).pack(padx=10, pady=10)
        
        frame = ttk.LabelFrame(self.dialog, text="选中的视频")
        frame.pack(fill=tk.X, padx=10, pady=5)
        
        for i, path in enumerate(self.video_paths[:3]):
            ttk.Label(frame, text=f"• {os.path.basename(path)}", font=("微软雅黑", 9)).pack(anchor=tk.W, padx=5)
        if len(self.video_paths) > 3:
            ttk.Label(frame, text=f"... 还有 {len(self.video_paths)-3} 个", 
                     font=("微软雅黑", 9, "italic"), foreground="gray").pack(anchor=tk.W, padx=5)
        
        ttk.Label(self.dialog, text="选择标签（多选）：", font=("微软雅黑", 10)).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        list_frame = ttk.Frame(self.dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        scroll = ttk.Scrollbar(list_frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tag_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, font=("微软雅黑", 10), 
                                     yscrollcommand=scroll.set)
        self.tag_listbox.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.tag_listbox.yview)
        
        # 从配置加载标签（保持定义顺序）
        defined_tags = self.config._config.get('tags', [])
        tags = defined_tags if defined_tags else self.config.get_all_tags()
        
        for tag_name in tags:
            # 检查是否所有视频都有此标签
            has_all = all(tag_name in self.config.get_video_tags(vp) for vp in self.video_paths)
            prefix = "✓ " if has_all else "  "
            self.tag_listbox.insert(tk.END, f"{prefix}{tag_name}")
        
        if not tags:
            self.tag_listbox.insert(tk.END, "（暂无标签）")
            self.tag_listbox.config(state="disabled")
        
        ttk.Label(self.dialog, text="或输入新标签：", font=("微软雅黑", 10)).pack(anchor=tk.W, padx=10, pady=(10, 5))
        self.new_tag_entry = ttk.Entry(self.dialog, font=("微软雅黑", 10))
        self.new_tag_entry.pack(fill=tk.X, padx=10, pady=5)
        
        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=20)
        
        ttk.Button(btn_frame, text="确定", command=self._confirm).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="+ 新建", command=self._quick_add).pack(side=tk.LEFT, padx=5)
    
    def _quick_add(self):
        new_tag = self.new_tag_entry.get().strip()
        if not new_tag:
            return
        
        if self.config.add_tag(new_tag):
            self.tag_listbox.config(state="normal")
            self.tag_listbox.insert(tk.END, f"  {new_tag}")
            self.new_tag_entry.delete(0, tk.END)
            messagebox.showinfo("成功", f"标签 '{new_tag}' 已添加，请在列表中勾选")
    
    def _confirm(self):
        selected_indices = self.tag_listbox.curselection()
        selected_tags = []
        
        for idx in selected_indices:
            tag_text = self.tag_listbox.get(idx)
            tag_name = tag_text.replace("✓ ", "").replace("  ", "")
            selected_tags.append(tag_name)
        
        new_tag = self.new_tag_entry.get().strip()
        if new_tag:
            if new_tag not in self.config.get_all_tags():
                self.config.add_tag(new_tag)
            if new_tag not in selected_tags:
                selected_tags.append(new_tag)
        
        if not selected_tags:
            messagebox.showwarning("提示", "请至少选择一个标签")
            return
        
        # 为所有视频设置标签（追加模式）
        for video_path in self.video_paths:
            current_tags = set(self.config.get_video_tags(video_path))
            current_tags.update(selected_tags)
            self.config.set_video_tags(video_path, list(current_tags))
            
            # 同步更新数据库中的tag_summary字段（作为标记）
            if self.db and self.db.is_ready:
                try:
                    tag_summary = ", ".join(sorted(current_tags))
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE media_files SET tag_summary = ? WHERE file_path = ?",
                        (tag_summary, video_path)
                    )
                    conn.commit()
                    cursor.close()
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"更新数据库tag_summary失败: {e}")
        
        self.config.save_config()
        self.result = True
        self.dialog.destroy()
    
    def show(self):
        self.parent.wait_window(self.dialog)
        return self.result