#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能图片浏览器 - 主程序（跨设备同步版）
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import webbrowser
import threading
import json
import time
import hashlib
import platform
from datetime import datetime

from core import ConfigManager, DebugUtils, ImageViewer, VideoPlayer, PathUtils, extract_code_from_text, get_pattern_matcher

# 导入SQLite数据库模块
try:
    from database_manager import DatabaseManager
    DB_AVAILABLE = True
except ImportError as e:
    DB_AVAILABLE = False
    print(f"警告: 数据库模块导入失败: {e}")

from bookmark_manager import BookmarkManager
from tree_explorer import TreeExplorer
from tag_manager import TagManager, TagSelectorDialog
from gallery_viewer import GalleryViewer
from config_manager import ExtendedConfigManager
from ui_components import ToolTip, DynamicToolTip
from video_relation_manager import RelationManager, VideoRelationDialog


class ImageBrowserApp:
    """智能图片浏览器主应用 - 跨设备版"""

    def __init__(self, root):
        self.root = root
        self.root.title("智能图片浏览器 - 跨设备版")
        self.root.geometry("1600x1000")  # 扩大窗口
        self.root.minsize(1200, 700)

        # 使用扩展配置管理器
        self.config = ExtendedConfigManager()
        self.debug = DebugUtils(self.config.debug_mode)

        # 启动时间记录
        self._start_time = time.time()
        self.debug.print(f"程序启动时间: {self._start_time}")
        self.debug.print(f"当前设备: {self.config.device_id}")

        # 初始化状态标志
        self._ui_ready = False
        self._db_ready = False
        self._db_loading = False
        
        # 数据库对象
        self.db = None
        
        # 初始化文件夹缓存管理器（程序启动时自动清空）
        from core import FolderCacheManager
        self.folder_cache = FolderCacheManager(self.debug)
        cleared = self.folder_cache.clear_all_cache()  # 每次启动清空缓存
        if self.debug:
            self.debug.print(f"文件夹缓存管理器初始化完成，清空结果: {cleared}")
        
        # 媒体播放器
        self.video_player = VideoPlayer(self.config, self.debug)
        self.image_viewer = ImageViewer(self.config, self.debug)

        # 业务状态
        self.current_root = None
        self.current_folder = None
        self.multi_select_mode = False

        # 管理器引用
        self.tag_manager = None
        self.actor_manager = None
        self.relation_manager = None
        self.gallery_loader = None

        # 初始化标签选择状态
        self._selected_tag_links = set()
        self._current_bookmark_id = None

        # ========== 创建UI ==========
        self._create_menu()
        self._create_ui()
        self._ui_ready = True
        
        self.status_label.config(text="正在恢复会话...", foreground="blue")
        self.root.update_idletasks()
        
        # 绑定标签切换事件
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)        
               
        # 延迟启动
        self.root.after(100, self._quick_startup)

    def _quick_startup(self):
        """快速启动流程"""
        try:
            # 修复：确保缓存目录在启动时创建（不只是清空）
            if hasattr(self, 'folder_cache') and self.folder_cache:
                try:
                    cache_dir = self.folder_cache.cache_dir
                    os.makedirs(cache_dir, exist_ok=True)
                    # 测试写入权限
                    test_file = os.path.join(cache_dir, ".write_test")
                    with open(test_file, 'w') as f:
                        f.write("test")
                    os.remove(test_file)
                    if self.debug:
                        self.debug.print(f"✓ 缓存目录就绪: {cache_dir}")
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"✗ 缓存目录准备失败: {e}")            
            
            # 新增：确保数据库就绪
            from init_database import ensure_database_ready
            db_success, db_msg = ensure_database_ready()
            if not db_success:
                self.debug.print(f"数据库初始化失败: {db_msg}")
            else:
                self.debug.print("✓ 数据库检查通过")
            
            self._restore_ui_from_cache_fast()
            startup_time = time.time() - self._start_time
            self.status_label.config(text=f"就绪 (启动: {startup_time:.2f}s)", foreground="green")
            
            # 延迟初始化数据库（确保UI已完全渲染）
            self.root.after(500, self._init_database_async_safe)
        except Exception as e:
            self.debug.print(f"快速启动失败: {e}")
            self.status_label.config(text="启动失败", foreground="red")
            import traceback
            traceback.print_exc()

    def _restore_ui_from_cache_fast(self):
        """从配置快速恢复UI - 修复：确保资源管理器正确选中上次文件夹"""
        cache = self._load_ui_cache()

        # 1. 恢复书签
        try:
            self.bookmark_manager.refresh_from_config()
        except Exception as e:
            self.debug.print(f"恢复书签失败: {e}")

        # 2. 获取上次文件夹（规范化路径）
        last_folder = cache.get('last_folder')
        if last_folder:
            last_folder = os.path.normpath(last_folder)
        
        expanded_paths = [os.path.normpath(p) for p in cache.get('expanded_paths', []) if p]
        tree_scroll = cache.get('tree_scroll_position', 0.0)
        gallery_scroll = cache.get('last_scroll_y', 0)
        
        # 3. 确定上次使用的书签（通过规范化路径匹配）
        target_bm = None
        if last_folder:
            for dev_id, device in self.config.devices.items():
                for bm in device.get('bookmarks', []):
                    bm_path = os.path.normpath(bm['path'])
                    if last_folder == bm_path or last_folder.startswith(bm_path + os.sep):
                        target_bm = bm
                        break
                if target_bm:
                    break
        
        if target_bm and os.path.exists(target_bm['path']):
            # 恢复该书签的状态（优先使用 bookmark_states 中的详细状态）
            bm_state = self.config.get_bookmark_state(target_bm['id'])
            
            if bm_state and bm_state.get('last_folder'):
                state_folder = os.path.normpath(bm_state['last_folder'])
                if os.path.exists(state_folder):
                    target_folder = state_folder
                else:
                    target_folder = os.path.normpath(target_bm['path'])
            else:
                target_folder = os.path.normpath(target_bm['path'])
            
            tree_state = bm_state.get('tree_state', {}) if bm_state else {}
            tree_selected = tree_state.get('selected_path')
            if tree_selected:
                tree_selected = os.path.normpath(tree_selected)
            
            # 最终目标文件夹：优先使用 tree_state 中的选中路径，其次是 last_folder
            final_target = tree_selected if tree_selected and os.path.exists(tree_selected) else target_folder
            
            self.current_root = os.path.normpath(target_bm['path'])
            self.current_folder = final_target
            self._current_bookmark_id = target_bm['id']
            
            try:
                # 加载树根
                self.tree_explorer.load_root(self.current_root)
                
                # 直接使用 restore_state 恢复（不传入 expanded_paths）
                self.tree_explorer.restore_state(
                    expanded_paths=[],  # 不保存展开节点，所以恢复时也不使用
                    selected_path=final_target,
                    scroll_position=tree_scroll
                )
                
                # 加载图库
                self._load_gallery_with_cache(final_target, gallery_scroll)
                self.path_label.config(text=f"浏览: {os.path.basename(final_target)}")
                self.copy_btn.config(state="normal")
                
            except Exception as e:
                self.debug.print(f"恢复浏览位置失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            # 没有找到有效书签，但有上次文件夹记录
            if last_folder and os.path.exists(last_folder):
                self.debug.print(f"未找到书签，尝试直接加载: {last_folder}")
                # 这里可以添加直接加载逻辑，或提示用户选择书签

    def _on_gallery_loaded(self, folder_path):
        count = self.gallery.get_image_count()
        self.count_label.config(text=f"图片: {count}")
        self.status_label.config(text=f"就绪 ({count}项)", foreground="green")

    def _init_database_async_safe(self):
        """异步初始化数据库（极速版）"""
        if self._db_loading or self._db_ready or not DB_AVAILABLE:
            if not DB_AVAILABLE:
                self.status_label.config(text="数据库模块不可用", foreground="orange")
            return

        self._db_loading = True
        self.status_label.config(text="连接数据库...", foreground="orange")

        def db_thread():
            try:
                # 关键优化：使用 fast_mode=True 快速初始化，跳过重复的结构检查
                self.db = DatabaseManager(debug_utils=self.debug, fast_mode=True)
                if self.db.is_ready:
                    self._db_ready = True
                    self.debug.print("数据库快速初始化成功")
                    
                    # 关键优化：启动时不执行全量同步，延迟到后台执行
                    self.root.after(0, self._on_db_ui_update)
                    
                    # 延迟3秒后执行轻量级同步（只在空闲时扫描当前书签）
                    if self._current_bookmark_id:
                        self.root.after(3000, self._delayed_background_sync)
                else:
                    error_msg = "数据库未就绪（缺少必要表或连接失败）"
                    self.debug.print(error_msg)
                    self.root.after(0, lambda: self.status_label.config(
                        text=error_msg, foreground="red"))
            except Exception as e:
                error_msg = f"数据库初始化失败: {e}"
                self.debug.print(error_msg)
                self.root.after(0, lambda: self.status_label.config(
                    text="数据库连接失败", foreground="red"))
            finally:
                self._db_loading = False

        threading.Thread(target=db_thread, daemon=True).start()

    def _delayed_background_sync(self):
        """延迟后台同步 - 只在需要时扫描"""
        if not self._db_ready or not self._current_bookmark_id:
            return
        
        # 只同步当前书签，而非所有设备的所有书签
        bm_state = self.config.get_bookmark_state(self._current_bookmark_id)
        current_folder = bm_state.get('last_folder') if bm_state else None
        
        if current_folder and os.path.exists(current_folder):
            self.status_label.config(text="后台同步当前文件夹...", foreground="blue")
            threading.Thread(
                target=self._sync_single_folder_lightweight, 
                args=(current_folder,), 
                daemon=True
            ).start()

    def _sync_single_folder_lightweight(self, folder_path):
        """轻量级同步单个文件夹"""
        try:
            if self.db and self.db.is_ready:
                changes = self.db._sync_folder(folder_path)
                self.root.after(0, lambda: self.status_label.config(
                    text=f"数据库就绪 ({changes} 更新)", foreground="green"))
                
                # 如果有变更，刷新缓存
                if changes > 0:
                    self.db._load_all_to_cache_async()
        except Exception as e:
            if self.debug:
                self.debug.print(f"轻量级同步失败: {e}")
            self.root.after(0, lambda: self.status_label.config(
                text="数据库就绪", foreground="green"))

    def _on_db_ui_update(self):
        """数据库就绪后更新UI - 强制刷新当前图库"""
        if not self._db_ready:
            self.status_label.config(text="数据库未连接", foreground="orange")
            return

        self.status_label.config(text="数据库已连接", foreground="green")
        
        # 更新各组件的数据库引用
        if hasattr(self, 'bookmark_manager'):
            self.bookmark_manager.db = self.db
        if hasattr(self, 'tree_explorer'):
            self.tree_explorer.db = self.db
        if hasattr(self, 'gallery'):
            self.gallery.db = self.db
        
        # 初始化关联管理器和图库加载器
        if self.relation_manager is None:
            self.relation_manager = RelationManager(self.root, self.db, self.config, self.debug)
        if self.gallery_loader is None:
            from gallery_loader import GalleryLoader
            self.gallery_loader = GalleryLoader(self.db, self.folder_cache, self.debug)
        
        # 延迟刷新演员管理器
        current_tab = self.notebook.select()
        if current_tab:
            tab_text = self.notebook.tab(current_tab, "text")
            if tab_text == "演员管理" and hasattr(self, 'actor_manager') and self.actor_manager:
                self.root.after(500, lambda: self.actor_manager.refresh() if self.actor_manager else None)
        
        # 刷新标签超链接
        self._refresh_tag_links()
        
        # 关键修复：数据库就绪后，如果当前图库显示的是普通文件夹，强制清除缓存并重新扫描
        if self.current_folder and not str(self.current_folder).startswith('['):
            # 清除该文件夹的缓存，确保重新扫描
            if hasattr(self, 'folder_cache'):
                self.folder_cache.clear_cache_for_folder(self.current_folder)
            # 延迟刷新，避免阻塞UI
            self.root.after(500, lambda: self._load_gallery_realtime_and_cache(self.current_folder, self.gallery.get_scroll_y()))

    def clear_cache_for_folder(self, folder_path):
        """清除指定文件夹的缓存"""
        import hashlib
        cache_key = hashlib.md5(folder_path.encode('utf-8')).hexdigest()
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        if os.path.exists(cache_file):
            try:
                os.remove(cache_file)
                return True
            except Exception as e:
                if self.debug:
                    self.debug.print(f"清除缓存失败 {cache_file}: {e}")
        return False

    # 可选：添加手动完整同步方法（当用户需要时执行）
    def _manual_full_sync(self):
        """手动执行完整同步（扫描所有书签）"""
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库尚未就绪")
            return
        
        self.status_label.config(text="完整同步中...", foreground="blue")
        
        def sync_thread():
            try:
                total_changes = 0
                # 只同步当前设备的书签，而非所有设备
                device = self.config.devices.get(self.config.device_id, {})
                bookmarks = device.get('bookmarks', [])
                
                for idx, bm in enumerate(bookmarks):
                    folder = bm['path']
                    try:
                        self.root.after(0, lambda f=os.path.basename(folder): 
                            self.status_label.config(text=f"同步: {f}"))
                        changes = self.db._sync_folder(folder)
                        total_changes += changes
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"同步失败 {folder}: {e}")
                
                # 刷新缓存
                self.db._load_all_to_cache_async()
                
                self.root.after(0, lambda: self.status_label.config(
                    text=f"同步完成 ({total_changes} 更新)", foreground="green"))
                
            except Exception as e:
                self.root.after(0, lambda: self.status_label.config(
                    text=f"同步失败: {e}", foreground="red"))
        
        threading.Thread(target=sync_thread, daemon=True).start()

    def _background_sync_safe(self):
        """后台同步"""
        if not self._db_ready:
            return
        try:
            # 同步所有设备的书签
            for dev_id, device in self.config.devices.items():
                for bm in device.get('bookmarks', []):
                    folder = bm['path']
                    try:
                        self.db._sync_folder(folder)
                        conn = self.db._get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE folders SET is_bookmark = 1 WHERE folder_path = ?", 
                            (folder,)
                        )
                        conn.commit()
                        cursor.close()
                    except Exception as e:
                        self.debug.print(f"同步失败 {folder}: {e}")
            self.db._load_all_to_cache()
        except Exception as e:
            self.debug.print(f"后台同步失败: {e}")

    def _refresh_tag_links(self):
        """刷新标签超链接显示 - 修复：保持标签管理器中的自定义顺序"""
        if not hasattr(self, 'tag_links_container'):
            return
            
        # 清空现有标签
        for widget in self.tag_links_container.winfo_children():
            widget.destroy()
        
        # 获取标签数据 - 使用配置中定义的顺序（tags列表定义的顺序）
        tags = self.config.get_all_tags()  # 此方法已确保按config.tags顺序返回
        video_tags = self.config.get_all_video_tags()
        
        # 统计标签使用数量
        tag_counts = {}
        for path, tags_list in video_tags.items():
            for tag in tags_list:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        if not tags:
            tk.Label(self.tag_links_container, text="（暂无标签）", 
                    font=("微软雅黑", 9), fg="gray").pack(side=tk.LEFT, padx=10)
            return
        
        # 关键修复：移除 sorted() ，直接按 config.tags 的顺序遍历
        # 原代码：for tag in sorted(tags):  # 这会导致按字母排序，破坏自定义顺序
        for tag in tags:  # 保持标签管理器中的自定义顺序
            count = tag_counts.get(tag, 0)
            display_text = f"{tag}({count})"
            is_selected = tag in self._selected_tag_links
            
            if is_selected:
                lbl = tk.Label(
                    self.tag_links_container, 
                    text=display_text, 
                    cursor="hand2",
                    fg="white", 
                    bg="#9370DB",
                    font=("微软雅黑", 9, "bold"),
                    relief=tk.RAISED,
                    bd=2,
                    padx=8,
                    pady=2
                )
            else:
                lbl = tk.Label(
                    self.tag_links_container, 
                    text=display_text, 
                    cursor="hand2",
                    fg="#0066CC",
                    font=("微软雅黑", 9, "underline"),
                    relief=tk.FLAT,
                    padx=8,
                    pady=2
                )
                lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg="#FF1493", bg="#F0F0F0"))
                lbl.bind("<Leave>", lambda e, l=lbl, t=tag: l.config(
                    fg="#0066CC", bg="SystemButtonFace" if t not in self._selected_tag_links else "#9370DB"
                ))
            
            lbl.pack(side=tk.LEFT, padx=3, pady=3)
            lbl.bind("<Button-1>", lambda e, t=tag: self._on_tag_link_click(t))
        
        # 清除按钮（如果有选中）- 保持原有逻辑
        if self._selected_tag_links:
            clear_btn = tk.Label(
                self.tag_links_container,
                text="[清除筛选]",
                cursor="hand2",
                fg="red",
                font=("微软雅黑", 9, "bold"),
                padx=8,
                pady=2
            )
            clear_btn.pack(side=tk.LEFT, padx=10)
            clear_btn.bind("<Button-1>", lambda e: self._clear_tag_filter_links())
            clear_btn.bind("<Enter>", lambda e, l=clear_btn: l.config(bg="#FFE4E1"))
            clear_btn.bind("<Leave>", lambda e, l=clear_btn: l.config(bg="SystemButtonFace"))


    def _on_tag_link_click(self, tag):
        """点击标签超链接"""
        if tag in self._selected_tag_links:
            self._selected_tag_links.remove(tag)
        else:
            self._selected_tag_links.add(tag)
        
        # 刷新显示状态
        self._refresh_tag_links()
        
        # 应用筛选
        if self._selected_tag_links:
            self._apply_tag_filter_links()
        else:
            self._clear_tag_filter_links()

    def _clear_tag_filter_links(self):
        """清除所有标签筛选 - 修复：直接恢复状态，不依赖书签选中"""
        self._selected_tag_links.clear()
        self._refresh_tag_links()
        
        # 恢复到当前书签的最后状态
        if self._current_bookmark_id:
            bm_state = self.config.get_bookmark_state(self._current_bookmark_id)
            last_folder = bm_state.get('last_folder')
            tree_state = bm_state.get('tree_state', {})
            gallery_scroll = bm_state.get('gallery_scroll_y', 0)
            
            # 验证路径有效性（不能是筛选标记，必须存在）
            if last_folder and os.path.exists(last_folder) and not str(last_folder).startswith('['):
                # 更新当前文件夹
                self.current_folder = last_folder
                
                # 恢复树形状态（延迟执行确保UI就绪）
                expanded_paths = tree_state.get('expanded_paths', [])
                selected_path = tree_state.get('selected_path', last_folder)
                scroll_position = tree_state.get('scroll_position', 0.0)
                
                # 先恢复树形结构展开状态
                if expanded_paths:
                    self.root.after(100, lambda: self._restore_bookmark_tree_state(
                        expanded_paths, selected_path, scroll_position
                    ))
                
                # 恢复图库显示（带滚动位置）
                self._load_gallery_with_cache(last_folder, gallery_scroll)
                
                # 更新UI状态
                self.path_label.config(text=f"浏览: {os.path.basename(last_folder)}")
                self.copy_btn.config(state="normal")
                
                # 保存全局配置
                self._save_current_location(folder=last_folder)
                
                if self.debug:
                    self.debug.print(f"清除筛选后恢复到: {last_folder}")
            else:
                # 如果没有有效的last_folder，回退到书签根目录
                for dev_id, device in self.config.devices.items():
                    for bm in device.get('bookmarks', []):
                        if bm['id'] == self._current_bookmark_id:
                            self._on_bookmark_select(bm['path'])
                            break
        else:
            # 没有当前书签，清空图库
            self.gallery.clear()
            self.current_folder = None

    def _apply_tag_filter_links(self):
        """应用标签筛选（交集逻辑）"""
        if not self._selected_tag_links:
            return
        
        # 关键修复：在进入标签筛选前，先保存当前书签的状态
        if self._current_bookmark_id and self.tree_explorer:
            try:
                tree_state = self.tree_explorer.get_state()
                gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    self.current_folder,
                    tree_state,
                    gallery_scroll
                )
                if self.debug:
                    self.debug.print(f"标签筛选前保存书签状态: {self._current_bookmark_id}")
            except Exception as e:
                if self.debug:
                    self.debug.print(f"标签筛选前保存状态失败: {e}")
        
        # 从配置获取标签数据
        video_tags = self.config.get_all_video_tags()
        matching_files = []
        
        # 交集逻辑：同时满足所有选中的标签
        for file_path, file_tags in video_tags.items():
            if all(tag in file_tags for tag in self._selected_tag_links):
                if os.path.exists(file_path):
                    matching_files.append(file_path)
        
        if not matching_files:
            messagebox.showinfo("提示", 
                f"标签 '{' + '.join(sorted(self._selected_tag_links))}' 下没有视频")
            return
        
        # 清除图库并加载筛选结果
        self.gallery.clear()
        
        # 关键修复：不清除_current_bookmark_id，只是标记当前处于筛选模式
        # self._current_bookmark_id 保持原值，这样返回时能知道是哪个书签
        self.current_folder = f"[标签筛选: {' + '.join(sorted(self._selected_tag_links))}]"
        self.path_label.config(text=f"标签筛选 ({len(matching_files)}个)")
        self.copy_btn.config(state="disabled")
        
        self.gallery.load_files_by_paths(matching_files, self._update_status)

    def _sync_tags_to_db(self):
        """同步标签到数据库"""
        if not self._db_ready:
            return
        try:
            video_tags = self.config.get_all_video_tags()
            for video_path, tags in video_tags.items():
                tag_summary = ", ".join(sorted(tags)) if tags else None
                self.db.set_file_tag_summary(video_path, tag_summary)
        except Exception as e:
            self.debug.print(f"同步标签失败: {e}")

    def _create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 在 _create_menu 方法中修改 file_menu：
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件夹", menu=file_menu)
        file_menu.add_command(label="添加书签", command=self._add_bookmark)
        file_menu.add_command(label="移除书签", command=self._remove_bookmark)
        file_menu.add_separator()
        # 修改这里：使用新的完整同步方法
        file_menu.add_command(label="完整同步所有书签", command=self._manual_full_sync)
        file_menu.add_separator()
        file_menu.add_command(label="刷新", command=self._refresh)
        file_menu.add_command(label="清空JSON缓存", command=self._clear_json_cache)

        # 新增：视频关联菜单
        relation_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视频关联", menu=relation_menu)
        relation_menu.add_command(label="扫描并建议关联", command=self._on_scan_relations)
        relation_menu.add_separator()
        relation_menu.add_command(label="管理关联关系", command=self._on_manage_relations)

        tag_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="标签", menu=tag_menu)
        tag_menu.add_command(label="标签管理页", command=lambda: self.notebook.select(self.tag_tab))
        tag_menu.add_command(label="设置标签", command=self._set_tags_for_selected)
        tag_menu.add_separator()
        tag_menu.add_checkbutton(label="多选模式", command=self._toggle_multi_select)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视图", menu=view_menu)

        self.size_var = tk.StringVar(value=self.config.thumbnail_size)
        for size_name in ["小", "中", "大", "最大"]:
            view_menu.add_radiobutton(
                label=f"{size_name} ({self.config.THUMBNAIL_SIZES[size_name]}像素)",
                variable=self.size_var, value=size_name,
                command=self._change_size
            )

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="设置", menu=settings_menu)

        self.remember_var = tk.BooleanVar(value=getattr(self.config, 'remember_location', True))
        settings_menu.add_checkbutton(
            label="记住浏览位置",
            variable=self.remember_var,
            command=self._toggle_remember_location
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="修改本机显示名称", command=self._rename_current_device)
        settings_menu.add_separator()
        settings_menu.add_command(label="播放器路径", command=self._set_player)
        settings_menu.add_command(label="检查播放器", command=lambda: self.video_player.check_player(silent=False))
        settings_menu.add_command(label="下载PotPlayer", command=lambda: webbrowser.open("https://potplayer.daum.net/"))
        settings_menu.add_separator()
        settings_menu.add_command(label="重置查看窗口", command=self.image_viewer.reset_position)

        self.debug_var = tk.BooleanVar(value=self.config.debug_mode)
        settings_menu.add_checkbutton(label="调试模式", variable=self.debug_var, command=self._toggle_debug)
        settings_menu.add_separator()
        settings_menu.add_command(label="保存配置", command=self._save_config)

    def _rename_current_device(self):
        """修改当前设备显示名称"""
        from tkinter import simpledialog
        current = getattr(self.config, 'current_device_name', self.config.device_id)
        new_name = simpledialog.askstring("修改设备名称", 
                                          "请输入本机显示名称（仅本地显示，便于区分不同电脑）:",
                                          initialvalue=current,
                                          parent=self.root)
        if new_name:
            self.config.set_device_display_name(new_name.strip())
            self.bookmark_manager.refresh_from_config()
            messagebox.showinfo("成功", f"设备显示名称已修改为: {new_name.strip()}")

    def _clear_json_cache(self):
        """清空所有JSON缓存文件"""
        try:
            cleared = self.folder_cache.clear_all_cache()
            messagebox.showinfo("完成", f"已清空 {cleared} 个缓存文件")
            if self.debug:
                self.debug.print(f"用户手动清空缓存，共 {cleared} 个文件")
        except Exception as e:
            messagebox.showerror("错误", f"清空缓存失败: {e}")

    def _create_ui(self):
        """创建主UI（添加日志标签页）"""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 1. 视频浏览标签页
        self.main_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.main_tab, text="视频浏览")

        # 2. 标签管理标签页
        self.tag_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.tag_tab, text="标签管理")

        # 3. 演员管理标签页
        self.actor_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.actor_tab, text="演员管理")
        
        # 4. 新增：操作日志标签页
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="操作日志")

        # 创建浏览器界面
        self._create_browser_ui(self.main_tab)
        
        # 创建操作日志界面
        self._create_log_ui(self.log_tab)
        
        # 绑定标签切换事件
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _create_browser_ui(self, parent):
        """创建浏览器界面（调整书签/资源管理器高度比例）"""
        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 左侧栏 - 使用Frame容器精确控制高度分配
        left_frame = ttk.Frame(paned, width=320)
        left_frame.pack_propagate(False)  # 防止子控件撑开父容器

        # 1. 书签管理器 - 固定较小高度（约25%），使用LabelFrame带标题
        bm_frame = ttk.LabelFrame(left_frame, text="书签", height=180)
        bm_frame.pack_propagate(False)  # 固定高度
        bm_frame.pack(fill=tk.X, padx=3, pady=(3, 0))
        
        self.bookmark_manager = BookmarkManager(
            bm_frame, self.config, None, self._on_bookmark_select, self.debug
        )
        
        # 2. 资源管理器 - 占据剩余所有高度（约75%）
        tree_frame = ttk.LabelFrame(left_frame, text="资源管理器")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        
        self.tree_explorer = TreeExplorer(
            tree_frame, None, self._on_tree_select, self.debug
        )

        paned.add(left_frame, weight=0)

        # 右侧区域 - 保持原有结构
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        # 从上到下：工具栏 -> 标签超链接 -> 图库 -> 状态栏
        self._create_toolbar(right_frame)
        self._create_tag_links_area(right_frame)
        self._create_gallery(right_frame)
        self._create_status_bar(right_frame)

    def _create_tag_links_area(self, parent):
        """创建标签超链接区域（横向排列在浏览框上方）"""
        # 使用LabelFrame作为容器
        self.tag_links_frame = ttk.LabelFrame(parent, text="标签筛选（多选交集）", padding=2)
        self.tag_links_frame.pack(fill=tk.X, padx=5, pady=(5, 0), expand=False)
        
        # 使用Canvas实现横向滚动
        self.tag_links_canvas = tk.Canvas(self.tag_links_frame, height=32, highlightthickness=0)
        scroll_x = ttk.Scrollbar(self.tag_links_frame, orient=tk.HORIZONTAL, command=self.tag_links_canvas.xview)
        self.tag_links_canvas.configure(xscrollcommand=scroll_x.set)
        
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tag_links_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 内部Frame用于放置标签
        self.tag_links_container = ttk.Frame(self.tag_links_canvas)
        self.tag_links_canvas_window = self.tag_links_canvas.create_window((0, 0), window=self.tag_links_container, anchor="nw")
        
        def configure_canvas(event):
            self.tag_links_canvas.configure(scrollregion=self.tag_links_canvas.bbox("all"))
            self.tag_links_canvas.itemconfig(self.tag_links_canvas_window, height=event.height)
        
        self.tag_links_container.bind("<Configure>", configure_canvas)
        self.tag_links_canvas.bind("<Configure>", lambda e: self.tag_links_canvas.itemconfig(self.tag_links_canvas_window, width=e.width))
        
        # 绑定鼠标滚轮到横向滚动
        def on_mousewheel(event):
            if event.delta > 0:
                self.tag_links_canvas.xview_scroll(-3, "units")
            else:
                self.tag_links_canvas.xview_scroll(3, "units")
            return "break"
        
        self.tag_links_canvas.bind("<MouseWheel>", on_mousewheel)
        
        # 初始刷新
        self._refresh_tag_links()

    def _create_gallery(self, parent):
        """创建图库查看器"""
        self.gallery = GalleryViewer(
            parent, self.config, None, self.video_player, self.image_viewer,
            self._on_image_select, self._on_image_double_click, self.debug,
            on_tags_changed=self._refresh_tag_links,
            get_browser_folder=lambda: self.current_folder,
            on_set_tag=self._set_tags_for_selected,
            on_relation_request=self._show_relation_suggestion_for_image,
            on_relation_removed=self._on_relation_removed  # 新增：解除关联回调
        )
        
        # 绑定滚动保存（原有代码保持不变）
        self.gallery.bind_scroll_save(
            lambda y: self._save_current_location(scroll_y=y)
        )

    def _create_toolbar(self, parent):
        """创建工具栏"""
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        self.path_label = ttk.Label(toolbar, text="请选择左侧文件夹", font=("微软雅黑", 9))
        self.path_label.pack(side=tk.LEFT, padx=5)

        self.copy_btn = ttk.Button(toolbar, text="📋 复制文件夹名", 
                                  command=self._copy_folder_name, state="disabled")
        self.copy_btn.pack(side=tk.LEFT, padx=5)

        self.tag_btn = ttk.Button(toolbar, text="🏷️ 设置标签", 
                                 command=self._set_tags_for_selected, state="disabled")
        self.tag_btn.pack(side=tk.LEFT, padx=5)

        self.count_label = ttk.Label(toolbar, text="图片: 0")
        self.count_label.pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(toolbar, text="初始化...", foreground="orange")
        self.status_label.pack(side=tk.RIGHT, padx=5)

    def _create_status_bar(self, parent):
        """创建状态栏"""
        bottom = ttk.LabelFrame(parent, text="路径信息")
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=5)

        img_frame = ttk.Frame(bottom)
        img_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(img_frame, text="图片:", width=8).pack(side=tk.LEFT)
        self.img_path_entry = tk.Entry(img_frame, state="readonly", font=("Consolas", 9))
        self.img_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        vid_frame = ttk.Frame(bottom)
        vid_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(vid_frame, text="视频:", width=8).pack(side=tk.LEFT)
        self.vid_path_entry = tk.Entry(vid_frame, state="readonly", font=("Consolas", 9))
        self.vid_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

    def _create_log_ui(self, parent):
        """创建操作日志界面"""
        # 主框架
        main_frame = ttk.Frame(parent, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 工具栏
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        
        # 清空按钮
        ttk.Button(toolbar, text="清空日志", command=self._clear_log).pack(side=tk.LEFT, padx=2)
        
        # 导出按钮
        ttk.Button(toolbar, text="导出日志...", command=self._export_log).pack(side=tk.LEFT, padx=2)
        
        # 复制按钮
        ttk.Button(toolbar, text="复制全部", command=self._copy_all_logs).pack(side=tk.LEFT, padx=2)
        
        # 自动滚动复选框
        self.auto_scroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="自动滚动", variable=self.auto_scroll_var).pack(side=tk.LEFT, padx=10)
        
        # 日志级别过滤
        ttk.Label(toolbar, text="过滤:").pack(side=tk.LEFT, padx=(10, 2))
        self.log_filter_var = tk.StringVar(value="全部")
        filter_combo = ttk.Combobox(toolbar, textvariable=self.log_filter_var, 
                                    values=["全部", "扫描", "匹配", "标签", "错误", "其他"],
                                    width=10, state="readonly")
        filter_combo.pack(side=tk.LEFT, padx=2)
        filter_combo.bind("<<ComboboxSelected>>", self._on_log_filter_change)
        
        # 日志统计
        self.log_stats_label = ttk.Label(toolbar, text="日志: 0 条", foreground="gray")
        self.log_stats_label.pack(side=tk.RIGHT, padx=5)
        
        # 日志显示区域 - 使用 Text 控件
        log_frame = ttk.Frame(main_frame)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建 Text 和 Scrollbar
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9), 
                            bg="#1e1e1e", fg="#d4d4d4", 
                            insertbackground="white",
                            selectbackground="#264f78",
                            relief=tk.FLAT, bd=0)
        
        scrollbar_y = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar_x = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        # 配置标签样式
        self.log_text.tag_configure("error", foreground="#f48771")
        self.log_text.tag_configure("warning", foreground="#dcdcaa")
        self.log_text.tag_configure("success", foreground="#6a9955")
        self.log_text.tag_configure("info", foreground="#9cdcfe")
        self.log_text.tag_configure("scan", foreground="#ce9178")
        self.log_text.tag_configure("match", foreground="#4ec9b0")
        self.log_text.tag_configure("timestamp", foreground="#858585")
        
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 右键菜单
        self.log_context_menu = tk.Menu(self.log_text, tearoff=0)
        self.log_context_menu.add_command(label="复制", command=self._copy_selected_log)
        self.log_context_menu.add_command(label="复制全部", command=self._copy_all_logs)
        self.log_context_menu.add_separator()
        self.log_context_menu.add_command(label="清空", command=self._clear_log)
        self.log_text.bind("<Button-3>", self._show_log_context_menu)
        
        # 设置日志回调
        self.debug.set_log_callback(self._on_log_message)

    # ==================== 标签页切换处理 ====================

    def _on_tab_changed(self, event):
        """标签页切换 - 确保切换到视频浏览时刷新标签"""
        try:
            current_tab = self.notebook.select()
            tab_text = self.notebook.tab(current_tab, "text")

            if tab_text == "视频浏览":
                self._refresh_tag_links()  # 切换回来时刷新，显示最新顺序
            elif tab_text == "标签管理":
                self._init_tag_manager()
            elif tab_text == "演员管理":
                self._init_actor_manager()
        except Exception as e:
            self.debug.print(f"标签切换错误: {e}")

    def _init_tag_manager(self):
        """初始化标签管理器（修复控件失效问题）"""
        try:
            # 如果已存在实例，检查控件是否仍然有效
            if self.tag_manager is not None:
                try:
                    # 测试关键控件是否仍然存在（winfo_exists 返回 0 或 1，抛出异常表示已销毁）
                    if self.tag_manager.tag_listbox.winfo_exists():
                        self.tag_manager.refresh()
                        return
                    else:
                        # 控件ID存在但已被标记为销毁
                        self.tag_manager = None
                except tk.TclError:
                    # 控件已完全销毁（TclError: invalid command name）
                    self.tag_manager = None
            
            # 需要重新创建
            if self.tag_manager is None:
                # 确保标签页是空的（清除之前的错误信息）
                for widget in self.tag_tab.winfo_children():
                    widget.destroy()
                
                from tag_manager import TagManager
                self.tag_manager = TagManager(self.tag_tab, self.config, self.debug)
                self.tag_manager.set_play_callback(self._play_video_by_path)
                self.tag_manager.set_tags_changed_callback(self._refresh_tag_links)
                self.tag_manager.refresh()
                        
        except Exception as e:
            self.debug.print(f"标签管理器初始化失败: {e}")
            import traceback
            traceback.print_exc()
            # 清理并显示错误
            try:
                for widget in self.tag_tab.winfo_children():
                    widget.destroy()
            except:
                pass
            error_frame = ttk.Frame(self.tag_tab)
            error_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
            tk.Label(error_frame, text="标签管理初始化失败", 
                    fg="red", font=("微软雅黑", 12, "bold")).pack(pady=10)
            tk.Label(error_frame, text=str(e), 
                    fg="red", wraplength=500, justify=tk.LEFT).pack(pady=5)
            tk.Button(error_frame, text="重试", 
                    command=self._init_tag_manager).pack(pady=10)

    def _init_actor_manager(self):
        """初始化演员管理器（强制创建UI即使数据库未连接）"""
        try:
            need_refresh = False
            
            # 检查现有实例的控件是否有效
            if self.actor_manager is not None:
                try:
                    if hasattr(self.actor_manager, 'actor_tree') and self.actor_manager.actor_tree.winfo_exists():
                        if self._db_ready and self.actor_manager.db != self.db:
                            self.actor_manager.db = self.db
                            need_refresh = True
                        if need_refresh:
                            self.actor_manager.refresh()
                        return
                    else:
                        self.actor_manager = None
                except tk.TclError:
                    self.actor_manager = None
            
            # 需要创建或重建
            if self.actor_manager is None:
                from actor_manager import ActorManager
                # 确保标签页是空的
                for widget in self.actor_tab.winfo_children():
                    widget.destroy()
                    
                self.actor_manager = ActorManager(self.actor_tab, self.db, self.config, self.debug)
                self.actor_manager.refresh()
                            
        except Exception as e:
            self.debug.print(f"演员管理器初始化失败: {e}")
            try:
                for widget in self.actor_tab.winfo_children():
                    widget.destroy()
                error_frame = ttk.Frame(self.actor_tab)
                error_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
                tk.Label(error_frame, text="演员管理初始化失败", 
                        fg="red", font=("微软雅黑", 12, "bold")).pack(pady=10)
                tk.Label(error_frame, text=str(e), 
                        fg="red", wraplength=500, justify=tk.LEFT).pack(pady=5)
                tk.Button(error_frame, text="重试", 
                        command=self._init_actor_manager).pack(pady=10)
            except:
                pass

    # ==================== 事件处理 ====================

    def _on_bookmark_select(self, folder_path):
        """书签选择时恢复该书签的最后状态"""
        # 清除标签筛选状态
        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()
        
        # 获取新书签信息
        selected_bm = self.bookmark_manager.get_selected_bookmark_id()
        if not selected_bm:
            return
        
        new_bm_id, new_bm_path, new_bm_name = selected_bm
        
        # 如果不是同一个书签，保存旧书签状态
        if self._current_bookmark_id and self._current_bookmark_id != new_bm_id:
            try:
                old_tree_state = self.tree_explorer.get_state() if self.tree_explorer else {}
                old_gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    self.current_folder,
                    old_tree_state,
                    old_gallery_scroll
                )
            except Exception as e:
                if self.debug:
                    self.debug.print(f"保存旧书签状态失败: {e}")
        
        # 更新当前书签ID
        self._current_bookmark_id = new_bm_id
        
        # 恢复新书签状态
        bm_state = self.config.get_bookmark_state(new_bm_id)
        target_folder = bm_state.get('last_folder', new_bm_path)
        tree_state = bm_state.get('tree_state', {})
        
        # 验证目标文件夹是否存在，如果不存在则回退到根目录
        if not os.path.exists(target_folder):
            target_folder = new_bm_path
            tree_state = {}  # 状态无效，清空
        
        # 清空路径显示
        self._update_path_entries(img_path="", video_path="")
        
        self.current_root = new_bm_path
        self.current_folder = target_folder
        self.path_label.config(text=f"根目录: {new_bm_name}")
        self.copy_btn.config(state="normal")
        
        # 关键修复：先加载树形结构，确保数据加载完成后再恢复状态
        def after_tree_load():
            """树加载完成后的回调"""
            # 如果有保存的状态且目标不是根目录，恢复树状态
            if target_folder != new_bm_path and tree_state:
                self.root.after(100, lambda: self._restore_bookmark_tree_state(
                    tree_state.get('expanded_paths', []),
                    tree_state.get('selected_path', target_folder),
                    tree_state.get('scroll_position', 0.0)
                ))
            else:
                self.root.after(100, lambda: self.tree_explorer.expand_to_path(new_bm_path))
            
            # 加载图库（使用缓存或异步加载）- 关键修复
            gallery_scroll = bm_state.get('gallery_scroll_y', 0)
            self._load_gallery_with_cache(target_folder, gallery_scroll)
        
        # 加载树形结构
        if self._db_ready:
            try:
                sub_folders = self.db.get_folders_by_parent(new_bm_path)
                if sub_folders:
                    self.tree_explorer.load_from_cache(new_bm_path, sub_folders)
                    # 关键：使用after确保load_from_cache完成渲染
                    self.root.after(50, after_tree_load)
                else:
                    self.tree_explorer.load_root(new_bm_path)
                    self.root.after(50, after_tree_load)
            except Exception as e:
                if self.debug:
                    self.debug.print(f"书签选择处理失败: {e}")
                self.tree_explorer.load_root(new_bm_path)
                self.root.after(50, after_tree_load)
        else:
            self.tree_explorer.load_root(new_bm_path)
            self.root.after(50, after_tree_load)
        
        # 保存全局配置
        self._save_current_location(folder=target_folder)

    def _on_tree_select(self, folder_path, folder_name):
        """树节点选择时更新当前书签状态"""
        # 清除标签筛选状态
        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()
        
        # 清空路径显示
        self._update_path_entries(img_path="", video_path="")
        
        # 保存当前书签的最新状态（关键修复：在变更current_folder前保存）
        if self._current_bookmark_id and self.tree_explorer:
            try:
                # 获取当前完整状态（变更前）
                current_state = self.tree_explorer.get_state()
                current_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                
                # 更新状态中的选中路径为新的路径
                current_state['selected_path'] = folder_path
                
                # 保存到配置
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    folder_path,  # 新的当前文件夹
                    current_state,
                    current_scroll
                )
                
                if self.debug:
                    self.debug.print(f"保存书签状态: {self._current_bookmark_id} -> {folder_path}")
            except Exception as e:
                if self.debug:
                    self.debug.print(f"保存书签树状态失败: {e}")
        
        # 更新当前文件夹
        self.current_folder = folder_path
        self.path_label.config(text=f"浏览: {folder_name}")
        self.copy_btn.config(state="normal")

        # 加载图库内容（使用缓存）- 关键修复
        self._load_gallery_with_cache(folder_path, None)
        
        self._save_current_location(folder=folder_path)

    def _load_gallery_with_cache(self, folder_path, scroll_y=None):
        """使用缓存加载图库"""
        if self.gallery_loader:
            self.gallery_loader.load_gallery(
                folder_path, self.gallery, self._update_status, scroll_y, self._db_ready
            )

    def _load_gallery_realtime_and_cache(self, folder_path, scroll_y=None):
        """实时扫描文件夹，查询数据库关联信息，并保存缓存 - 支持附加缩略图识别"""
        self._is_scanning_folder = folder_path
        
        try:
            from core import PathUtils
            import re
            
            all_files = []
            sub_folders = []
            
            # 扫描当前文件夹
            try:
                entries = list(os.scandir(folder_path))
            except Exception as e:
                if self.debug:
                    self.debug.print(f"扫描文件夹失败: {e}")
                entries = []
            
            # 处理每个文件夹（包括子文件夹）
            folders_to_scan = [(folder_path, ".")]
            
            # 收集子文件夹
            for entry in entries:
                if entry.is_dir():
                    sub_folders.append(entry.path)
                    folders_to_scan.append((entry.path, entry.name))
            
            # 用于存储所有文件信息，key为文件夹路径
            folder_data_map = {}
            
            # 扫描每个文件夹
            for scan_path, display_name in folders_to_scan:
                try:
                    scan_entries = list(os.scandir(scan_path))
                except:
                    continue
                
                videos = {}  # basename -> {path, size, mtime}
                images = {}  # basename -> {path, size, mtime}
                image_list = []  # 保持顺序
                
                for entry in scan_entries:
                    if not entry.is_file():
                        continue
                        
                    ext = os.path.splitext(entry.name)[1].lower()
                    basename = os.path.splitext(entry.name)[0].lower()
                    stat = entry.stat()
                    size_mb = round(stat.st_size / (1024 * 1024), 1)
                    mtime = stat.st_mtime
                    
                    file_info = {
                        'path': entry.path,
                        'name': entry.name,
                        'basename': basename,
                        'ext': ext,
                        'size_mb': size_mb,
                        'mtime': mtime
                    }
                    
                    if PathUtils.is_video_file(entry.path):
                        videos[basename] = file_info
                    elif PathUtils.is_image_file(entry.path):
                        images[basename] = file_info
                        image_list.append(file_info)
                
                folder_data_map[scan_path] = {
                    'display_name': display_name,
                    'videos': videos,
                    'images': images,
                    'image_list': image_list
                }
            
            # 处理匹配逻辑
            processed_items = []
            db_updates_needed = []  # 需要更新的数据库记录
            
            for scan_path, data in folder_data_map.items():
                videos = data['videos']
                images = data['images']
                image_list = data['image_list']
                display_name = data['display_name']
                
                # 构建视频到图片的映射（支持附加缩略图）
                video_to_images = {}  # video_basename -> {main: img_info, alternates: []}
                
                # 1. 处理完全同名匹配（一对一）
                matched_images = set()
                for v_basename, v_info in videos.items():
                    if v_basename in images:
                        video_to_images[v_basename] = {
                            'main': images[v_basename],
                            'alternates': [],
                            'video': v_info
                        }
                        matched_images.add(v_basename)
                
                # 2. 处理多图对一视频（附加缩略图识别）
                # 正则：匹配 basename_01, basename-1, basename01, basename 01 等
                alternate_pattern = re.compile(r'^(.*?)[_\s\-]?(\d+)$')
                
                for img_basename, img_info in images.items():
                    if img_basename in matched_images:
                        continue
                    
                    # 检查是否是某个视频的附加缩略图
                    for v_basename in videos.keys():
                        if img_basename == v_basename:
                            continue  # 已处理
                        
                        # 检查是否是主缩略图的变体
                        match = alternate_pattern.match(img_basename)
                        if match:
                            base_part = match.group(1)
                            if base_part == v_basename:
                                if v_basename in video_to_images:
                                    video_to_images[v_basename]['alternates'].append(img_info)
                                else:
                                    # 创建新条目，但标记为alternate模式
                                    video_to_images[v_basename] = {
                                        'main': img_info,  # 第一个遇到的作为主图
                                        'alternates': [],
                                        'video': videos[v_basename],
                                        'is_alternate_mode': True
                                    }
                                matched_images.add(img_basename)
                                break
                
                # 3. 处理通用封面（cover/folder/poster等）
                cover_names = ['cover', 'folder', 'poster', 'thumb', 'preview', 'default']
                for cover_name in cover_names:
                    if cover_name in images:
                        # 分配给没有缩略图的视频
                        for v_basename, v_info in videos.items():
                            if v_basename not in video_to_images:
                                video_to_images[v_basename] = {
                                    'main': images[cover_name],
                                    'alternates': [],
                                    'video': v_info,
                                    'is_cover': True
                                }
                                matched_images.add(cover_name)
                                break
                
                # 4. 生成最终的项目列表
                # 先处理有视频的
                for v_basename, mapping in video_to_images.items():
                    item = {
                        'folder_name': display_name,
                        'folder_path': scan_path,
                        'video_path': mapping['video']['path'],
                        'video_size': mapping['video']['size_mb'],
                        'video_mtime': mapping['video']['mtime'],
                        'main_image': mapping['main']['path'],
                        'alternates': [img['path'] for img in mapping.get('alternates', [])],
                        'match_type': 'dedicated' if not mapping.get('is_cover') else 'cover',
                        'file_type': 'image',
                        'is_alias': False,  # 本地视频不是alias
                        'primary_video_path': None
                    }
                    processed_items.append(item)
                    
                    # 检查数据库是否需要更新
                    if self._db_ready and self.db:
                        db_file = self.db.get_file_by_path(mapping['video']['path'])
                        if db_file:
                            db_size = round((db_file.get('size') or 0) / (1024 * 1024), 1)
                            db_modified = db_file.get('modified', 0)
                            if isinstance(db_modified, datetime):
                                db_modified_ts = db_modified.timestamp()
                            else:
                                db_modified_ts = db_modified or 0
                            
                            if db_size != mapping['video']['size_mb'] or \
                            abs(db_modified_ts - mapping['video']['mtime']) > 1:
                                db_updates_needed.append({
                                    'path': mapping['video']['path'],
                                    'size': mapping['video']['size_mb'] * 1024 * 1024,
                                    'mtime': mapping['video']['mtime']
                                })
                
                # 5. 处理没有视频匹配的占位符视频
                for v_basename, v_info in videos.items():
                    if v_basename not in video_to_images:
                        item = {
                            'folder_name': display_name,
                            'folder_path': scan_path,
                            'video_path': v_info['path'],
                            'video_size': v_info['size_mb'],
                            'video_mtime': v_info['mtime'],
                            'main_image': "__VIDEO_PLACEHOLDER__",
                            'alternates': [],
                            'match_type': 'placeholder',
                            'file_type': 'video_placeholder',
                            'file_name': v_info['name'],
                            'is_alias': False,
                            'primary_video_path': None
                        }
                        processed_items.append(item)
                        
                        # 检查数据库更新
                        if self._db_ready and self.db:
                            db_file = self.db.get_file_by_path(v_info['path'])
                            if db_file:
                                db_size = round((db_file.get('size') or 0) / (1024 * 1024), 1)
                                db_modified = db_file.get('modified', 0)
                                if isinstance(db_modified, datetime):
                                    db_modified_ts = db_modified.timestamp()
                                else:
                                    db_modified_ts = db_modified or 0
                                    
                                if db_size != v_info['size_mb'] or \
                                abs(db_modified_ts - v_info['mtime']) > 1:
                                    db_updates_needed.append({
                                        'path': v_info['path'],
                                        'size': v_info['size_mb'] * 1024 * 1024,
                                        'mtime': v_info['mtime']
                                    })
                
                # 6. 处理仅图片（无视频匹配）
                standalone_images = [img for img in image_list if img['basename'] not in matched_images]
                for img in standalone_images:
                    # 检查数据库中是否是关联图片（alias）
                    is_alias = False
                    primary_video_path = None
                    
                    if self._db_ready and self.db:
                        db_file = self.db.get_file_by_path(img['path'])
                        if db_file and db_file.get('is_alias'):
                            is_alias = True
                            primary_video_path = self.db.get_primary_video_path(db_file.get('id'))
                    
                    item = {
                        'folder_name': display_name,
                        'folder_path': scan_path,
                        'video_path': primary_video_path if is_alias else None,
                        'main_image': img['path'],
                        'alternates': [],
                        'match_type': 'linked' if is_alias else 'standalone',
                        'file_type': 'image',
                        'is_alias': is_alias,
                        'primary_video_path': primary_video_path
                    }
                    processed_items.append(item)
                    
                    # 检查数据库更新（仅非alias图片）
                    if self._db_ready and self.db and not is_alias:
                        db_file = self.db.get_file_by_path(img['path'])
                        if db_file:
                            db_size = round((db_file.get('size') or 0) / (1024 * 1024), 1)
                            db_modified = db_file.get('modified', 0)
                            if isinstance(db_modified, datetime):
                                db_modified_ts = db_modified.timestamp()
                            else:
                                db_modified_ts = db_modified or 0
                                
                            if db_size != img['size_mb'] or \
                            abs(db_modified_ts - img['mtime']) > 1:
                                db_updates_needed.append({
                                    'path': img['path'],
                                    'size': img['size_mb'] * 1024 * 1024,
                                    'mtime': img['mtime']
                                })
            
            # 6. 更新数据库中变化的文件信息
            if db_updates_needed and self._db_ready and self.db:
                threading.Thread(
                    target=self._update_db_files_info,
                    args=(db_updates_needed,),
                    daemon=True
                ).start()
            
            # 7. 补充数据库中的关联信息（覆盖本地数据）
            if self._db_ready and self.db:
                for item in processed_items:
                    if item.get('is_alias') and item.get('primary_video_path'):
                        continue  # 已处理
                    
                    img_path = item.get('main_image')
                    if img_path and img_path != "__VIDEO_PLACEHOLDER__":
                        db_file = self.db.get_file_by_path(img_path)
                        if db_file and db_file.get('is_alias'):
                            primary = self.db.get_primary_video_path(db_file.get('id'))
                            if primary:
                                item['primary_video_path'] = primary
                                item['video_path'] = primary
                                item['is_alias'] = True
                                item['match_type'] = 'linked'
                                if self.debug:
                                    self.debug.print(f"数据库关联覆盖: {os.path.basename(img_path)}")
            
            # 8. 保存到缓存
            if processed_items:
                tree_state = self.tree_explorer.get_state() if self.tree_explorer else {}
                cache_files_info = []
                for item in processed_items:
                    cache_info = {
                        'file_path': item['main_image'],
                        'file_name': os.path.basename(item['main_image']) if item['main_image'] != "__VIDEO_PLACEHOLDER__" else item.get('file_name', 'video'),
                        'file_type': item['file_type'],
                        'folder_path': item['folder_path'],
                        'folder_name': item['folder_name'],
                        'video_path': item.get('video_path'),
                        'primary_video_path': item.get('primary_video_path'),
                        'is_alias': 1 if item.get('is_alias') else 0,
                        'alternates': item.get('alternates', []),
                        'match_type': item['match_type']
                    }
                    cache_files_info.append(cache_info)
                
                self.folder_cache.save_folder_cache(
                    folder_path, 
                    sub_folders=sub_folders,
                    files_info=cache_files_info,
                    tree_state=tree_state
                )
            
            # 9. 加载到UI
            if processed_items:
                self.gallery.load_items(processed_items, self._update_status, scroll_y=scroll_y)
                self._on_gallery_loaded(folder_path)
            else:
                self.gallery.clear()
                self._update_status("文件夹为空", 100)
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"实时扫描失败: {e}")
                import traceback
                traceback.print_exc()
        finally:
            self._is_scanning_folder = None

    def _update_db_files_info(self, updates):
        """后台更新数据库文件信息"""
        try:
            if not self.db or not self._db_ready:
                return
            
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            for update in updates:
                try:
                    from datetime import datetime
                    mtime_dt = datetime.fromtimestamp(update['mtime'])
                    cursor.execute("""
                        UPDATE media_files 
                        SET file_size = ?, last_modified = ?
                        WHERE file_path = ?
                    """, (update['size'], mtime_dt, update['path']))
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"更新文件信息失败 {update['path']}: {e}")
            
            conn.commit()
            cursor.close()
            
            if self.debug:
                self.debug.print(f"已更新 {len(updates)} 个文件的数据库信息")
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"批量更新数据库失败: {e}")

    def _restore_bookmark_tree_state(self, expanded_paths, selected_path, scroll_pos, retry_count=0):
        """恢复书签的树展开和选中状态（增强版带重试机制）"""
        try:
            # 验证路径有效性
            if not selected_path or not os.path.exists(selected_path):
                if self.debug:
                    self.debug.print(f"恢复树状态失败：路径不存在 {selected_path}")
                return
            
            # 确保根节点已展开
            root_children = self.tree_explorer.tree.get_children()
            if not root_children:
                if retry_count < 5:  # 增加重试次数
                    # 树尚未加载，延迟重试
                    self.root.after(200, lambda: self._restore_bookmark_tree_state(
                        expanded_paths, selected_path, scroll_pos, retry_count + 1
                    ))
                    return
                else:
                    if self.debug:
                        self.debug.print("树加载超时，放弃恢复状态")
                    return
            
            # 关键修复：先展开所有需要展开的路径（按深度排序，先展开浅的）
            if expanded_paths:
                # 按路径深度排序，确保先展开父级
                sorted_paths = sorted(expanded_paths, key=lambda p: p.count(os.sep))
                for path in sorted_paths:
                    if os.path.exists(path):
                        try:
                            self.tree_explorer.expand_to_path(path)
                        except Exception as e:
                            if self.debug:
                                self.debug.print(f"展开失败 {path}: {e}")
            
            # 强制展开到选中路径（确保所有父级都展开）
            try:
                self.tree_explorer.expand_to_path(selected_path)
            except Exception as e:
                if self.debug:
                    self.debug.print(f"展开选中路径失败: {e}")
            
            # 执行选中（增加延迟确保展开完成）
            def do_select():
                try:
                    if os.path.exists(selected_path):
                        success = self.tree_explorer.select_path(selected_path)
                        if success:
                            self.tree_explorer.set_scroll_position(scroll_pos)
                            self.current_folder = selected_path
                            if self.debug:
                                self.debug.print(f"树状态恢复成功: {os.path.basename(selected_path)}")
                        else:
                            # 选中失败，可能是路径还未完全展开，尝试展开后重选
                            if retry_count < 3:
                                self.tree_explorer.expand_to_path(selected_path)
                                self.root.after(200, lambda: self._restore_bookmark_tree_state(
                                    expanded_paths, selected_path, scroll_pos, retry_count + 1
                                ))
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"选中路径失败: {e}")
            
            # 延迟执行选中，确保展开操作完成
            self.root.after(200, do_select)
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"恢复书签树状态失败: {e}")
                
    def _on_image_select(self, img_path, frame, is_selected):
        """图片选中事件 - 关键：正确显示关联后的主视频地址，无视频时清空"""
        # 从 frame 获取 video_path（关联后已自动更新为主视频路径）
        video_path = getattr(frame, 'video_path', None)
        
        # 关键修复：同时更新图片路径和视频路径显示
        # 当 video_path 为 None 时，传递空字符串以清空视频路径栏
        self._update_path_entries(
            img_path=img_path, 
            video_path=video_path if video_path else ""
        )
        
        selected = self.gallery.get_selected_images()
        if selected:
            self.tag_btn.config(state="normal")
            self.status_label.config(text=f"选中 {len(selected)} 个")
        else:
            self.tag_btn.config(state="disabled")
            # 无选中项时，清空所有路径显示
            if not selected:
                self._update_path_entries(img_path="", video_path="")

    def _on_image_double_click(self, img_path):
        self.image_viewer.view(img_path, self.root)

    def _update_status(self, message, progress):
        self.status_label.config(text=message)
        self.gallery.progress['value'] = progress
        self.count_label.config(text=f"图片: {self.gallery.get_image_count()}")
        self.root.update_idletasks()

    def _update_path_entries(self, img_path=None, video_path=None):
        """更新路径显示 - 修复：使用 is not None 判断以支持空字符串清空"""
        if img_path is not None:  # 使用 is not None 而不是 if img_path，以支持传递空字符串清空
            self.img_path_entry.config(state="normal")
            self.img_path_entry.delete(0, tk.END)
            self.img_path_entry.insert(0, img_path)
            self.img_path_entry.config(state="readonly")

        if video_path is not None:  # 同样处理视频路径
            self.vid_path_entry.config(state="normal")
            self.vid_path_entry.delete(0, tk.END)
            self.vid_path_entry.insert(0, video_path)
            self.vid_path_entry.config(state="readonly")

    def _set_tags_for_selected(self):
        """设置标签对话框"""
        selected = self.gallery.get_selected_images()
        if not selected:
            messagebox.showwarning("提示", "请先选择视频")
            return

        video_paths = []
        for img_path in selected:
            frame = self.gallery.get_selected_frames().get(img_path)
            video_path = getattr(frame, 'video_path', None) if frame else None
            if video_path:
                video_paths.append(video_path)
        
        if not video_paths:
            messagebox.showwarning("提示", "未找到对应的视频文件")
            return

        dialog = TagSelectorDialog(
            self.root, self.config, video_paths, self.debug, 
            db_manager=self.db if self._db_ready else None
        )
        
        if dialog.show():
            self.config.save_config()
            self._sync_tags_to_db()
            # 刷新超链接区域
            self._refresh_tag_links()
            # 如果当前在标签筛选模式，也刷新筛选结果
            if self._selected_tag_links:
                self._apply_tag_filter_links()
            elif self.current_folder and not str(self.current_folder).startswith("["):
                self.gallery.refresh(keep_scroll=True)
            messagebox.showinfo("成功", "标签已更新")

    def _play_video_by_path(self, video_path):
        if not os.path.exists(video_path):
            messagebox.showerror("错误", f"视频不存在:\n{video_path}")
            return
        success, msg = self.video_player.play_video(video_path)
        if success:
            self.status_label.config(text=msg)

    def _add_bookmark(self):
        folder = filedialog.askdirectory(title="选择要添加的文件夹")
        if folder:
            folder = os.path.normpath(folder.strip())
            # 询问自定义显示名称
            from tkinter import simpledialog
            default_name = os.path.basename(folder)
            display_name = simpledialog.askstring("书签名称", 
                                                  "请输入显示名称（留空使用默认）:",
                                                  initialvalue=default_name,
                                                  parent=self.root)
            if display_name is None:  # 用户取消
                return
            
            display_name = display_name.strip() or default_name
            
            if self.config.add_bookmark(folder, display_name):
                self.config.save_config()
                self.bookmark_manager.refresh_from_config()
                messagebox.showinfo("成功", f"已添加书签:\n{display_name}")
            else:
                messagebox.showwarning("提示", "该文件夹已在当前设备的书签中，或路径无效")

    def _remove_bookmark(self):
        """从当前设备移除书签"""
        selected = self.bookmark_manager.get_selected_bookmark_id()
        if not selected:
            messagebox.showwarning("提示", "请先选择要移除的书签")
            return
        
        bm_id, bm_path, bm_name = selected
        if messagebox.askyesno("确认删除", f"确定从书签中移除 '{bm_name}' 吗？\n\n路径: {bm_path}"):
            # 找到索引并移除
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = device.get('bookmarks', [])
            for idx, bm in enumerate(bookmarks):
                if bm['id'] == bm_id:
                    if self.config.remove_bookmark(idx):
                        self.bookmark_manager.refresh_from_config()
                        messagebox.showinfo("成功", f"书签 '{bm_name}' 已移除")
                    break

    def _manual_sync(self):
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库尚未就绪")
            return
        self.status_label.config(text="同步中...", foreground="blue")
        def sync_thread():
            self._background_sync_safe()
            self.root.after(0, self._on_db_ui_update)
        threading.Thread(target=sync_thread, daemon=True).start()

    def _refresh(self):
        if str(self.current_folder).startswith("["):
            self._apply_tag_filter()
        elif self.current_folder and os.path.exists(self.current_folder):
            self.gallery.load_folder(self.current_folder, self._update_status)

    def _change_size(self):
        new_size = self.size_var.get()
        self.config.thumbnail_size = new_size
        self.config.save_config()
        self._refresh()

    def _toggle_multi_select(self):
        self.multi_select_mode = not self.multi_select_mode
        self.gallery.set_multi_select_mode(self.multi_select_mode)

    def _copy_folder_name(self):
        if self.current_folder and os.path.exists(self.current_folder):
            name = os.path.basename(self.current_folder)
            self.root.clipboard_clear()
            self.root.clipboard_append(name)
            self.status_label.config(text=f"已复制: {name}")

    def _toggle_remember_location(self):
        self.config.remember_location = self.remember_var.get()
        self.config.save_config()
        status = "开启" if self.config.remember_location else "关闭"
        self.status_label.config(text=f"记住位置: {status}")

    def _set_player(self):
        path = filedialog.askopenfilename(title="选择播放器", filetypes=[("可执行文件", "*.exe")])
        if path:
            self.video_player.set_player_path(path)
            messagebox.showinfo("成功", f"播放器已设置为:\n{path}")

    def _toggle_debug(self):
        """切换调试模式"""
        mode = self.debug_var.get()
        self.config.debug_mode = mode
        self.debug.set_debug_mode(mode)
        self.config.save_config()
        
        # 更新状态
        status = "开启" if mode else "关闭"
        self.status_label.config(text=f"调试模式: {status}")
        
        # 如果开启调试模式，在日志页显示提示
        if mode and hasattr(self, 'log_tab'):
            self.debug.print("=" * 50)
            self.debug.print("调试模式已开启")
            self.debug.print("所有操作日志将显示在此窗口")
            self.debug.print("=" * 50)

    def _save_config(self):
        if self.config.save_config():
            messagebox.showinfo("成功", "配置已保存")
        else:
            messagebox.showerror("错误", "保存失败")

    def _save_current_location(self, folder=None, scroll_y=None):
        if not getattr(self.config, 'remember_location', True):
            return
        if folder:
            self.config.last_folder = folder
        if scroll_y is not None:
            self.config.last_scroll_y = scroll_y
        self.config.save_config()

    def _load_ui_cache(self):
        default = {
            'bookmarks': [], 'last_folder': None, 'last_root': None,
            'expanded_paths': [], 'tree_scroll_position': 0.0, 'last_scroll_y': 0, 'timestamp': 0
        }
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'config.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    full_config = json.load(f)
                    cache = full_config.get('ui_cache', {})
                    if time.time() - cache.get('timestamp', 0) < 86400:
                        return {**default, **cache}
        except Exception as e:
            self.debug.print(f"加载缓存失败: {e}")
        return default

    def _save_ui_cache(self):
        try:
            tree_state = self.tree_explorer.get_state() if self.tree_explorer else {}
            cache = {
                'bookmarks': list(self.config.devices.get(self.config.device_id, {}).get('bookmarks', [])),
                'last_folder': self.current_folder,
                'last_root': self.current_root,
                'expanded_paths': tree_state.get('expanded_paths', []),
                'tree_selected_path': tree_state.get('selected_path'),
                'tree_scroll_position': tree_state.get('scroll_position', 0.0),
                'last_scroll_y': self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0,
                'timestamp': time.time()
            }
            config_path = os.path.join(os.path.dirname(__file__), 'config.json')
            full_config = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    full_config = json.load(f)
            full_config['ui_cache'] = cache
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(full_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.debug.print(f"保存缓存失败: {e}")

    def _restore_tree_state(self, expanded_paths=None, selected_path=None, scroll_position=0.0):
        try:
            if not self.tree_explorer:
                return
            if expanded_paths:
                for path in expanded_paths:
                    if os.path.exists(path):
                        self.tree_explorer.expand_to_path(path)
            if selected_path and os.path.exists(selected_path):
                self.tree_explorer.expand_to_path(selected_path)
                self.tree_explorer.select_path(selected_path)
            self.root.after(200, lambda: self.tree_explorer.set_scroll_position(scroll_position))
        except Exception as e:
            self.debug.print(f"恢复树状态失败: {e}")

    def _on_closing(self):
        """窗口关闭时保存状态"""
        # 保存当前书签状态
        if self._current_bookmark_id and self.tree_explorer:
            try:
                tree_state = self.tree_explorer.get_state()
                gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    self.current_folder,
                    tree_state,
                    gallery_scroll
                )
            except Exception as e:
                if self.debug:
                    self.debug.print(f"关闭时保存书签状态失败: {e}")
        
        self._save_ui_cache()
        if hasattr(self, 'db') and self.db:
            self.db.close()
        self.root.destroy()

    # ==================== 关联管理委托给 RelationManager ====================
    
    def _open_folder_from_relation_dialog(self, title_label, listbox):
        """从关联对话框的超链接标题打开文件夹"""
        folder_path = getattr(title_label, 'folder_path', None)
        if folder_path and os.path.exists(folder_path):
            try:
                import subprocess
                import platform
                if platform.system() == 'Windows':
                    subprocess.Popen(['explorer', folder_path], shell=True)
                elif platform.system() == 'Darwin':
                    subprocess.Popen(['open', folder_path])
                else:
                    subprocess.Popen(['xdg-open', folder_path])
            except Exception as e:
                messagebox.showerror("错误", f"无法打开文件夹: {e}")
        else:
            messagebox.showwarning("提示", "文件夹路径无效或不存在")

    # ==================== 需求2：右键关联对话框（增强版）====================
    
    def _on_scan_relations(self):
        """扫描关联建议"""
        if self.relation_manager:
            self.relation_manager.scan_for_relation_suggestions(
                on_refresh_callback=self._refresh,
                status_callback=lambda msg, color: self.status_label.config(text=msg, foreground=color)
            )
    
    def _on_manage_relations(self):
        """管理关联关系"""
        self._manage_video_relations()
    
    def _show_relation_suggestion_for_image(self, img_path, frame):
        """为单张图片打开增强版关联建议对话框 - 支持手动选择视频和自动搜索"""
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库未就绪")
            return

        # 获取图片信息
        folder = os.path.dirname(img_path)
        folder_name = os.path.basename(folder)
        
        # 从图片名或文件夹名提取番号
        from core import extract_code_from_text
        folder_code = extract_code_from_text(folder_name)
        img_code = extract_code_from_text(os.path.basename(img_path))
        search_code = folder_code or img_code
        
        dialog = tk.Toplevel(self.root)
        dialog.title(f"关联建议 - {os.path.basename(img_path)[:30]}")
        dialog.geometry("1400x800")
        dialog.minsize(1100, 700)
        dialog.transient(self.root)
        dialog.grab_set()

        # ===== 需求2a：顶部手动选择视频区域 =====
        manual_frame = ttk.LabelFrame(dialog, text="手动选择主视频（可选）", padding=5)
        manual_frame.pack(fill=tk.X, padx=10, pady=5)
        
        manual_path_var = tk.StringVar()
        ttk.Entry(manual_frame, textvariable=manual_path_var, width=80).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        def browse_video():
            path = filedialog.askopenfilename(
                title="选择主视频文件",
                filetypes=[("视频文件", "*.mp4;*.avi;*.mkv;*.mov;*.wmv"), ("所有文件", "*.*")]
            )
            if path:
                manual_path_var.set(path)
        
        ttk.Button(manual_frame, text="浏览...", command=browse_video).pack(side=tk.LEFT, padx=5)
        
        # 手动关联按钮
        def apply_manual():
            manual_path = manual_path_var.get().strip()
            if not manual_path or not os.path.exists(manual_path):
                messagebox.showwarning("提示", "请先选择有效的视频文件")
                return
            
            # 建立关联
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                # 确保图片在数据库中有记录
                cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (img_path,))
                img_result = cursor.fetchone()
                
                if not img_result:
                    # 需要先创建图片记录
                    cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder,))
                    folder_result = cursor.fetchone()
                    if not folder_result:
                        # 创建文件夹记录
                        parent = os.path.dirname(folder) if folder != os.path.dirname(folder) else None
                        cursor.execute("""
                            INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                            VALUES (?, ?, ?, datetime('now'))
                        """, (folder, folder_name, parent))
                        folder_id = cursor.lastrowid
                    else:
                        folder_id = folder_result['folder_id']
                    
                    cursor.execute("""
                        INSERT INTO media_files (folder_id, file_path, file_name, file_type, last_modified)
                        VALUES (?, ?, ?, 'image', datetime('now'))
                    """, (folder_id, img_path, os.path.basename(img_path)))
                    img_id = cursor.lastrowid
                else:
                    img_id = img_result['file_id']
                
                # 获取或创建视频记录
                cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (manual_path,))
                vid_result = cursor.fetchone()
                if not vid_result:
                    # 视频不在数据库中，自动插入
                    vid_folder = os.path.dirname(manual_path)
                    cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (vid_folder,))
                    vid_folder_result = cursor.fetchone()
                    if not vid_folder_result:
                        # 创建视频所在文件夹记录
                        vid_parent = os.path.dirname(vid_folder) if vid_folder != os.path.dirname(vid_folder) else None
                        cursor.execute("""
                            INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                            VALUES (?, ?, ?, datetime('now'))
                        """, (vid_folder, os.path.basename(vid_folder), vid_parent))
                        vid_folder_id = cursor.lastrowid
                    else:
                        vid_folder_id = vid_folder_result['folder_id']
                    
                    # 插入视频记录
                    cursor.execute("""
                        INSERT INTO media_files (folder_id, file_path, file_name, file_type, file_size, last_modified)
                        VALUES (?, ?, ?, 'video', ?, datetime('now'))
                    """, (vid_folder_id, manual_path, os.path.basename(manual_path), 
                          os.path.getsize(manual_path)))
                    vid_id = cursor.lastrowid
                else:
                    vid_id = vid_result['file_id']
                
                # 检查是否已关联
                cursor.execute("SELECT 1 FROM video_relations WHERE alias_file_id = ?", (img_id,))
                if cursor.fetchone():
                    if not messagebox.askyesno("确认", "该图片已有关联关系，确定要重新关联吗？"):
                        cursor.close()
                        conn.close()
                        return
                    cursor.execute("DELETE FROM video_relations WHERE alias_file_id = ?", (img_id,))
                
                # 创建关联
                cursor.execute("""
                    INSERT INTO video_relations (primary_file_id, alias_file_id, alias_folder_path, created_date)
                    VALUES (?, ?, ?, datetime('now'))
                """, (vid_id, img_id, folder))
                cursor.execute("UPDATE media_files SET is_alias = 1 WHERE file_id = ?", (img_id,))
                
                conn.commit()
                cursor.close()
                conn.close()
                
                self.db._load_all_to_cache_async()
                messagebox.showinfo("成功", "手动关联建立成功！")
                dialog.destroy()
                self._refresh()
                
            except Exception as e:
                messagebox.showerror("错误", f"手动关联失败: {e}")
                import traceback
                traceback.print_exc()
        
        ttk.Button(manual_frame, text="手动关联", command=apply_manual, 
                  style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        
        # 分隔线
        ttk.Separator(dialog, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=5)
        
        # ===== 中间：自动搜索匹配结果 =====
        auto_frame = ttk.LabelFrame(dialog, text=f"自动搜索结果 (番号: {search_code['canonical'] if search_code else '无'})", padding=5)
        auto_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 树形列表（同菜单栏打开的对话框）
        columns = ('primary_path', 'alias_path', 'code')
        tree = ttk.Treeview(auto_frame, columns=columns, show='headings', height=8, selectmode='extended')
        tree.heading('primary_path', text='主视频路径')
        tree.heading('alias_path', text='关联图片/文件夹')
        tree.heading('code', text='匹配番号')
        tree.column('primary_path', width=500)
        tree.column('alias_path', width=500)
        tree.column('code', width=120)
        
        scrollbar_y = ttk.Scrollbar(auto_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar_y.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 需求2b：底部文件列表预览（与菜单栏版一致，但总是显示当前图片文件夹）
        preview_frame = ttk.LabelFrame(dialog, text="文件列表预览", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        paned = ttk.PanedWindow(preview_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # 创建LabelFrame并保存引用以便动态更新标题
        left_frame = ttk.LabelFrame(paned, text="主视频所在文件夹")
        right_frame = ttk.LabelFrame(paned, text=f"当前图片文件夹 ({folder_name})")
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=1)
        
        left_listbox = tk.Listbox(left_frame, font=("Consolas", 9))
        right_listbox = tk.Listbox(right_frame, font=("Consolas", 9))
        left_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=left_listbox.yview)
        right_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=right_listbox.yview)
        left_listbox.configure(yscrollcommand=left_scroll.set)
        right_listbox.configure(yscrollcommand=right_scroll.set)
        left_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        right_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 右侧默认显示当前图片文件夹的内容
        try:
            for f in sorted(os.listdir(folder)):
                right_listbox.insert(tk.END, f)
        except Exception as e:
            right_listbox.insert(tk.END, f"无法读取: {e}")
        
        # 点击搜索结果时更新左侧（主视频文件夹）和右侧（保持不变或根据选中项更新）
        def on_tree_select(event):
            selected = tree.selection()
            if not selected:
                left_frame.config(text="主视频所在文件夹")
                left_listbox.delete(0, tk.END)
                return
            
            item = selected[0]
            tags = tree.item(item, 'tags')
            if not tags:
                return
            
            import json
            try:
                sug = json.loads(tags[0])
            except:
                return
            
            # 更新主视频文件夹显示
            try:
                primary_path = sug['primary_video']['file_path']
                primary_dir = os.path.dirname(primary_path)
                primary_name = os.path.basename(primary_dir)
                left_frame.config(text=f"主视频所在文件夹 ({primary_name})")
                
                left_listbox.delete(0, tk.END)
                for f in sorted(os.listdir(primary_dir)):
                    left_listbox.insert(tk.END, f)
            except Exception as e:
                left_listbox.delete(0, tk.END)
                left_listbox.insert(tk.END, f"读取失败: {e}")
                left_frame.config(text="主视频所在文件夹 (读取失败)")
            
            # 如果是文件夹类型，更新右侧显示关联文件夹
            if sug.get('type') == 'folder':
                try:
                    alias_dir = sug['folder_path']
                    alias_name = os.path.basename(alias_dir)
                    right_frame.config(text=f"关联文件夹 ({alias_name})")
                    
                    right_listbox.delete(0, tk.END)
                    for f in sorted(os.listdir(alias_dir)):
                        right_listbox.insert(tk.END, f)
                except Exception as e:
                    right_listbox.delete(0, tk.END)
                    right_listbox.insert(tk.END, f"读取失败: {e}")
        
        tree.bind('<<TreeviewSelect>>', on_tree_select)
        
        # 需求3：动态Tooltip（同菜单栏版）
        def get_tooltip_text(event):
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            if not row_id:
                return None
            
            item = tree.item(row_id)
            values = item.get('values', [])
            if not values or len(values) < 2:
                return None
            
            if col_id == '#1':  # 主视频列
                primary_path = values[0] if len(values) > 0 else ""
                if primary_path:
                    return f"完整路径: {os.path.dirname(primary_path)}"
            elif col_id == '#2':  # 关联列
                tags = item.get('tags', [])
                if tags:
                    try:
                        import json
                        sug = json.loads(tags[0])
                        if sug.get('type') == 'folder':
                            return f"完整路径: {sug.get('folder_path', '')}"
                        else:
                            img_path = sug.get('image', {}).get('file_path', '')
                            return f"完整路径: {os.path.dirname(img_path)}" if img_path else values[1]
                    except:
                        pass
            return None
        
        tooltip = DynamicToolTip(tree, get_tooltip_text)
        
        # 按钮区域
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=10, padx=10)
        
        def apply_selected():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请至少选择一行要关联的建议")
                return
            if not messagebox.askyesno("确认关联", f"确定要为选中的 {len(selected)} 个建议建立关联关系吗？"):
                return
            
            count = 0
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                for item in selected:
                    tags = tree.item(item, 'tags')
                    if not tags:
                        continue
                    
                    import json
                    try:
                        sug = json.loads(tags[0])
                    except:
                        continue
                    
                    primary_id = sug['primary_video']['file_id']
                    
                    # 获取当前图片的ID（可能需要创建记录）
                    cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (img_path,))
                    img_result = cursor.fetchone()
                    
                    if not img_result:
                        # 创建记录
                        cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder,))
                        folder_result = cursor.fetchone()
                        if not folder_result:
                            parent = os.path.dirname(folder) if folder != os.path.dirname(folder) else None
                            cursor.execute("""
                                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                                VALUES (?, ?, ?, datetime('now'))
                            """, (folder, folder_name, parent))
                            folder_id = cursor.lastrowid
                        else:
                            folder_id = folder_result['folder_id']
                        
                        cursor.execute("""
                            INSERT INTO media_files (folder_id, file_path, file_name, file_type, last_modified)
                            VALUES (?, ?, ?, 'image', datetime('now'))
                        """, (folder_id, img_path, os.path.basename(img_path)))
                        img_id = cursor.lastrowid
                    else:
                        img_id = img_result['file_id']
                    
                    # 检查是否已关联
                    cursor.execute("SELECT 1 FROM video_relations WHERE alias_file_id = ?", (img_id,))
                    if cursor.fetchone():
                        cursor.execute("DELETE FROM video_relations WHERE alias_file_id = ?", (img_id,))
                    
                    # 建立关联
                    cursor.execute("""
                        INSERT INTO video_relations (primary_file_id, alias_file_id, alias_folder_path, created_date)
                        VALUES (?, ?, ?, datetime('now'))
                    """, (primary_id, img_id, folder))
                    cursor.execute("UPDATE media_files SET is_alias = 1 WHERE file_id = ?", (img_id,))
                    count += 1
                
                conn.commit()
                cursor.close()
                # 关键：不要在这里关闭 conn，因为后面可能还需要使用
                # conn.close()  # 删除或注释掉这行，让连接保持开放或由垃圾回收处理
                
                self.db._load_all_to_cache_async()
                messagebox.showinfo("完成", f"成功建立了 {count} 个关联关系")
                dialog.destroy()
                
                # 关键：强制刷新图库以显示新的关联状态
                if hasattr(self, 'gallery') and self.current_folder:
                    self.gallery.refresh(keep_scroll=True)
                self._refresh()  # 同时刷新主窗口状态
                
            except Exception as e:
                messagebox.showerror("错误", f"关联失败: {e}")
                import traceback
                traceback.print_exc()
       
        ttk.Button(btn_frame, text="应用选中关联", command=apply_selected, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10).pack(side=tk.RIGHT, padx=5)
        
        # 启动后台搜索（与菜单栏类似的逻辑，但只搜索当前番号）
        def search_matches():
            if not search_code:
                return
            
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                # 获取当前设备书签
                device = self.config.devices.get(self.config.device_id, {})
                bookmarks = [os.path.normpath(bm['path']) for bm in device.get('bookmarks', [])]
                
                # 搜索匹配的视频
                cursor.execute("""
                    SELECT mf.file_id, mf.file_path, mf.file_name, f.folder_path
                    FROM media_files mf
                    JOIN folders f ON mf.folder_id = f.folder_id
                    WHERE mf.file_type = 'video'
                """)
                
                from core import get_pattern_matcher
                matcher = get_pattern_matcher()
                
                for row in cursor.fetchall():
                    v_code = extract_code_from_text(row['file_name'])
                    if v_code and matcher.is_strict_match(search_code, v_code) >= 1:
                        suggestion = {
                            'type': 'image',
                            'image': {'file_path': img_path, 'file_id': None},  # 临时占位
                            'primary_video': dict(row),
                            'code': v_code['canonical']
                        }
                        
                        # 插入到树中
                        def add_suggestion(s=suggestion):
                            if dialog.winfo_exists():
                                primary_path = s['primary_video']['file_path']
                                alias_info = f"[当前图片] {os.path.basename(img_path)}"
                                code = s['code']
                                import json
                                sug_json = json.dumps(s, ensure_ascii=False)
                                tree.insert('', 'end', values=(primary_path, alias_info, code), tags=(sug_json,))
                        
                        self.root.after(0, add_suggestion)
                
                cursor.close()
                conn.close()
                
            except Exception as e:
                if self.debug:
                    self.debug.print(f"搜索匹配失败: {e}")
        
        threading.Thread(target=search_matches, daemon=True).start()

    def _on_relation_removed(self, img_path=None):
        """图片关联解除后的回调 - 立即刷新图库显示"""
        if self.debug:
            self.debug.print(f"关联已解除，刷新图库: {img_path}")
        
        # 1. 刷新数据库缓存（确保关联状态已更新）
        if self._db_ready and self.db:
            self.db._load_all_to_cache_async()
        
        # 2. 关键：强制刷新图库以移除关联标识（保持当前滚动位置）
        if hasattr(self, 'gallery') and self.gallery:
            self.gallery.refresh(keep_scroll=True)
        
        # 3. 刷新标签超链接区域（如果有关联相关的标签统计）
        self._refresh_tag_links()
        
        # 4. 更新状态栏提示
        self.status_label.config(text="关联已解除", foreground="green")
        self.root.after(2000, lambda: self.status_label.config(
            text=f"就绪 ({self.gallery.get_image_count()}项)", 
            foreground="green"
        ))

    def _manage_video_relations(self):
        """管理关联关系（修复数据库连接问题）"""
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库未就绪")
            return
        
        # 关键：确保数据库连接池是活跃的
        try:
            # 测试连接是否有效
            test_conn = self.db._get_connection()
            test_cursor = test_conn.cursor()
            test_cursor.execute("SELECT 1")
            test_cursor.close()
            # 不要关闭 test_conn，只是测试
        except Exception as e:
            messagebox.showerror("错误", f"数据库连接异常: {e}")
            return
        
        dialog = tk.Toplevel(self.root)
        dialog.title("管理视频关联")
        dialog.geometry("1000x500")
        dialog.transient(self.root)
        
        ttk.Label(dialog, text="当前关联关系列表", font=("微软雅黑", 10, "bold")).pack(pady=5)
        
        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tree = ttk.Treeview(frame, columns=('primary', 'alias', 'folder'), show='headings', height=15)
        tree.heading('primary', text='主视频路径')
        tree.heading('alias', text='关联视频路径')
        tree.heading('folder', text='关联目录')
        
        tree.column('primary', width=450)
        tree.column('alias', width=450)
        tree.column('folder', width=100)
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 加载数据 - 使用独立的连接和游标
        conn = None
        cursor = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT vr.alias_file_id, vr.primary_file_id, vr.alias_folder_path,
                    mf1.file_path as alias_path, mf2.file_path as primary_path
                FROM video_relations vr
                JOIN media_files mf1 ON vr.alias_file_id = mf1.file_id
                JOIN media_files mf2 ON vr.primary_file_id = mf2.file_id
            """)
            for row in cursor.fetchall():
                tree.insert('', 'end', values=(
                    row['primary_path'],
                    row['alias_path'],
                    os.path.basename(row['alias_folder_path'])
                ), tags=(row['alias_file_id'],))
            cursor.close()
            cursor = None
        except Exception as e:
            error_msg = str(e)
            messagebox.showerror("错误", f"加载失败: {error_msg}")
            if cursor:
                cursor.close()
            if conn:
                # 不要关闭 conn，让连接池管理
                pass
            return
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
        
        def remove_selected():
            selection = tree.selection()
            if not selection:
                return
            if messagebox.askyesno("确认", f"确定解除 {len(selection)} 个关联关系吗？"):
                remove_conn = None
                remove_cursor = None
                try:
                    remove_conn = self.db._get_connection()
                    remove_cursor = remove_conn.cursor()
                    for item in selection:
                        alias_id = tree.item(item, 'tags')[0]
                        # 使用参数化查询
                        remove_cursor.execute("DELETE FROM video_relations WHERE alias_file_id = ?", (alias_id,))
                        remove_cursor.execute("UPDATE media_files SET is_alias = 0 WHERE file_id = ?", (alias_id,))
                    remove_conn.commit()
                    tree.delete(*selection)
                    self._refresh()
                    # 刷新图库
                    if hasattr(self, 'gallery') and self.current_folder:
                        self.gallery.refresh(keep_scroll=True)
                except Exception as e:
                    messagebox.showerror("错误", f"解除关联失败: {e}")
                finally:
                    if remove_cursor:
                        try:
                            remove_cursor.close()
                        except:
                            pass
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="解除选中关联", command=remove_selected).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="刷新", command=lambda: [dialog.destroy(), self._manage_video_relations()]).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="批量修改路径...", 
                  command=lambda: self._batch_replace_paths(dialog)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=10)

    def _batch_replace_paths(self, parent_dialog):
        """批量替换关联视频路径对话框 - 支持单独修改主视频或关联视频路径"""
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库未就绪")
            return
        
        dialog = tk.Toplevel(parent_dialog)
        dialog.title("批量修改关联视频路径")
        # 增加窗口高度从 650 到 800，确保按钮区域可见
        dialog.geometry("750x800")
        dialog.minsize(700, 700)  # 设置最小尺寸防止用户缩得太小
        dialog.transient(parent_dialog)
        dialog.grab_set()
        
        # 主框架 - 使用可滚动的 Canvas 以防内容超出
        main_container = ttk.Frame(dialog)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 创建 Canvas 和滚动条以支持内容超出时滚动
        canvas = tk.Canvas(main_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 主框架放在 Canvas 中
        main_frame = ttk.Frame(canvas, padding=10)
        canvas_window = canvas.create_window((0, 0), window=main_frame, anchor="nw")
        
        def configure_canvas(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=event.width)
        
        main_frame.bind("<Configure>", configure_canvas)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        
        # 绑定鼠标滚轮
        def on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
            return "break"
        canvas.bind("<MouseWheel>", on_mousewheel)
        main_frame.bind("<MouseWheel>", on_mousewheel)
        
        # 说明标签 - 明确提示可以单独修改
        ttk.Label(main_frame, 
                 text="批量替换路径前缀（可单独修改主视频路径或关联视频路径）",
                 font=("微软雅黑", 10, "bold")).pack(pady=(0, 5))
        
        info_text = ("使用说明：\n"
                    "• 您可以只修改主视频路径，或只修改关联视频路径，或同时修改两者\n"
                    "• 填写'原路径前缀'和'新路径前缀'后点击预览查看效果\n"
                    "• 留空一组表示不修改该类型路径")
        ttk.Label(main_frame, text=info_text, foreground="blue", 
                 wraplength=680, justify=tk.LEFT).pack(pady=(0, 10))
        
        ttk.Label(main_frame, 
                 text="⚠️ 警告：此操作将直接修改数据库，请谨慎操作！建议先备份 database.db 文件",
                 foreground="red", wraplength=680, font=("微软雅黑", 9, "bold")).pack(pady=(0, 10))
        
        # === 主视频路径替换区域 ===
        primary_frame = ttk.LabelFrame(main_frame, text="主视频路径替换（可选）", padding=5)
        primary_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(primary_frame, text="原路径前缀:", foreground="gray").grid(row=0, column=0, sticky=tk.W, pady=5)
        primary_old_var = tk.StringVar()
        ttk.Entry(primary_frame, textvariable=primary_old_var, width=65).grid(row=0, column=1, sticky=tk.EW, padx=5)
        
        ttk.Label(primary_frame, text="新路径前缀:", foreground="green").grid(row=1, column=0, sticky=tk.W, pady=5)
        primary_new_var = tk.StringVar()
        ttk.Entry(primary_frame, textvariable=primary_new_var, width=65).grid(row=1, column=1, sticky=tk.EW, padx=5)
        
        # 示例标签
        ttk.Label(primary_frame, text="示例: \\\\server\\share\\ → D:\\Videos\\", 
                 foreground="gray", font=("微软雅黑", 8)).grid(row=2, column=1, sticky=tk.W, padx=5)
        
        primary_frame.columnconfigure(1, weight=1)
        
        # === 关联视频路径替换区域 ===
        alias_frame = ttk.LabelFrame(main_frame, text="关联视频路径替换（可选）", padding=5)
        alias_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(alias_frame, text="原路径前缀:", foreground="gray").grid(row=0, column=0, sticky=tk.W, pady=5)
        alias_old_var = tk.StringVar()
        ttk.Entry(alias_frame, textvariable=alias_old_var, width=65).grid(row=0, column=1, sticky=tk.EW, padx=5)
        
        ttk.Label(alias_frame, text="新路径前缀:", foreground="green").grid(row=1, column=0, sticky=tk.W, pady=5)
        alias_new_var = tk.StringVar()
        ttk.Entry(alias_frame, textvariable=alias_new_var, width=65).grid(row=1, column=1, sticky=tk.EW, padx=5)
        
        # 示例标签
        ttk.Label(alias_frame, text="示例: \\\\nas\\thumbs\\ → E:\\Thumbs\\", 
                 foreground="gray", font=("微软雅黑", 8)).grid(row=2, column=1, sticky=tk.W, padx=5)
        
        alias_frame.columnconfigure(1, weight=1)
        
        # === 预览区域 - 增加高度 ===
        preview_frame = ttk.LabelFrame(main_frame, text="影响记录预览", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 创建树形预览列表 - 增加高度到 12
        columns = ('type', 'old_path', 'new_path')
        preview_tree = ttk.Treeview(preview_frame, columns=columns, show='headings', height=12)
        preview_tree.heading('type', text='类型')
        preview_tree.heading('old_path', text='原路径')
        preview_tree.heading('new_path', text='新路径')
        
        preview_tree.column('type', width=80)
        preview_tree.column('old_path', width=300)
        preview_tree.column('new_path', width=300)
        
        scrollbar_y = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=preview_tree.yview)
        preview_tree.configure(yscrollcommand=scrollbar_y.set)
        
        preview_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 状态标签
        status_label = ttk.Label(main_frame, text="", foreground="blue", wraplength=680)
        status_label.pack(fill=tk.X, pady=5)
        
        # 受影响记录数
        affected_count_label = ttk.Label(main_frame, text="受影响记录: 0", font=("微软雅黑", 9, "bold"))
        affected_count_label.pack(fill=tk.X, pady=5)
        
        # === 按钮区域 - 使用独立框架确保始终可见 ===
        btn_container = ttk.Frame(dialog)  # 放在 dialog 而不是 main_frame，确保固定底部
        btn_container.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=10)
        
        # 添加分隔线
        ttk.Separator(btn_container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))
        
        btn_frame = ttk.Frame(btn_container)
        btn_frame.pack(fill=tk.X)
        
        def validate_inputs():
            """验证输入有效性"""
            primary_old = primary_old_var.get().strip()
            primary_new = primary_new_var.get().strip()
            alias_old = alias_old_var.get().strip()
            alias_new = alias_new_var.get().strip()
            
            # 检查是否至少填写了一组
            if not primary_old and not alias_old:
                return False, "请至少填写一组路径前缀（可以只填主视频或只填关联视频）"
            
            # 检查：如果填写了旧路径，必须填写新路径
            if primary_old and not primary_new:
                return False, "主视频：填写了原路径前缀后，必须填写新路径前缀"
            
            if alias_old and not alias_new:
                return False, "关联视频：填写了原路径前缀后，必须填写新路径前缀"
            
            return True, ""
        
        def preview_changes():
            """预览即将进行的修改"""
            # 清空预览列表
            for item in preview_tree.get_children():
                preview_tree.delete(item)
            
            # 验证输入
            valid, msg = validate_inputs()
            if not valid:
                status_label.config(text=msg, foreground="red")
                return
            
            primary_old = primary_old_var.get().strip()
            primary_new = primary_new_var.get().strip()
            alias_old = alias_old_var.get().strip()
            alias_new = alias_new_var.get().strip()
            
            count = 0
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                # 获取当前所有关联关系
                cursor.execute("""
                    SELECT vr.alias_file_id, vr.primary_file_id, vr.alias_folder_path,
                        mf1.file_path as alias_path, mf2.file_path as primary_path
                    FROM video_relations vr
                    JOIN media_files mf1 ON vr.alias_file_id = mf1.file_id
                    JOIN media_files mf2 ON vr.primary_file_id = mf2.file_id
                """)
                
                for row in cursor.fetchall():
                    alias_path = row['alias_path']
                    primary_path = row['primary_path']
                    alias_folder = row['alias_folder_path']
                    
                    # 检查主视频路径是否需要替换（仅在填写了主视频前缀时）
                    if primary_old and primary_path.startswith(primary_old):
                        new_primary_path = primary_path.replace(primary_old, primary_new, 1)
                        preview_tree.insert('', 'end', values=(
                            '主视频', primary_path, new_primary_path
                        ))
                        count += 1
                    
                    # 检查关联视频路径是否需要替换（仅在填写了关联视频前缀时）
                    if alias_old:
                        if alias_path.startswith(alias_old):
                            new_alias_path = alias_path.replace(alias_old, alias_new, 1)
                            preview_tree.insert('', 'end', values=(
                                '关联视频', alias_path, new_alias_path
                            ))
                            count += 1
                        
                        if alias_folder and alias_folder.startswith(alias_old):
                            new_alias_folder = alias_folder.replace(alias_old, alias_new, 1)
                            preview_tree.insert('', 'end', values=(
                                '关联目录', alias_folder, new_alias_folder
                            ))
                            count += 1
                
                cursor.close()
                
                affected_count_label.config(text=f"受影响记录: {count}")
                
                if count > 0:
                    modify_types = []
                    if primary_old:
                        modify_types.append("主视频")
                    if alias_old:
                        modify_types.append("关联视频")
                    status_label.config(
                        text=f"✓ 预览完成，将修改 {count} 条记录（类型: {', '.join(modify_types)}）", 
                        foreground="green"
                    )
                else:
                    status_label.config(
                        text="⚠ 未找到匹配的路径记录，请检查路径前缀是否正确（注意大小写和反斜杠方向）", 
                        foreground="orange"
                    )
                    
            except Exception as e:
                status_label.config(text=f"预览失败: {e}", foreground="red")
                import traceback
                traceback.print_exc()
        
        def execute_replace():
            """执行路径替换"""
            # 验证输入
            valid, msg = validate_inputs()
            if not valid:
                messagebox.showwarning("输入验证失败", msg)
                return
            
            primary_old = primary_old_var.get().strip()
            primary_new = primary_new_var.get().strip()
            alias_old = alias_old_var.get().strip()
            alias_new = alias_new_var.get().strip()
            
            # 构建确认信息
            confirm_msg = "确定要执行以下批量路径替换吗？\n\n"
            if primary_old:
                confirm_msg += f"[主视频路径]\n{primary_old}\n→ {primary_new}\n\n"
            if alias_old:
                confirm_msg += f"[关联视频路径]\n{alias_old}\n→ {alias_new}\n\n"
            confirm_msg += "⚠️ 此操作不可撤销！建议已备份数据库。"
            
            if not messagebox.askyesno("确认执行", confirm_msg):
                return
            
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                total_changes = 0
                changes_detail = []
                
                # 1. 更新主视频路径 (media_files表) - 仅当填写了主视频前缀时
                if primary_old:
                    cursor.execute("""
                        UPDATE media_files 
                        SET file_path = REPLACE(file_path, ?, ?)
                        WHERE file_path LIKE ? 
                        AND file_id IN (SELECT primary_file_id FROM video_relations)
                    """, (primary_old, primary_new, primary_old + '%'))
                    primary_changes = cursor.rowcount
                    total_changes += primary_changes
                    changes_detail.append(f"主视频: {primary_changes}条")
                    self.debug.print(f"主视频路径更新: {primary_changes} 条")
                
                # 2. 更新关联视频路径 (media_files表) - 仅当填写了关联视频前缀时
                if alias_old:
                    cursor.execute("""
                        UPDATE media_files 
                        SET file_path = REPLACE(file_path, ?, ?)
                        WHERE file_path LIKE ? 
                        AND file_id IN (SELECT alias_file_id FROM video_relations)
                    """, (alias_old, alias_new, alias_old + '%'))
                    alias_changes = cursor.rowcount
                    total_changes += alias_changes
                    changes_detail.append(f"关联视频: {alias_changes}条")
                    self.debug.print(f"关联视频路径更新: {alias_changes} 条")
                    
                    # 3. 更新关联视频文件夹路径 (video_relations表)
                    cursor.execute("""
                        UPDATE video_relations 
                        SET alias_folder_path = REPLACE(alias_folder_path, ?, ?)
                        WHERE alias_folder_path LIKE ?
                    """, (alias_old, alias_new, alias_old + '%'))
                    folder_changes = cursor.rowcount
                    total_changes += folder_changes
                    changes_detail.append(f"关联目录: {folder_changes}条")
                    self.debug.print(f"关联文件夹路径更新: {folder_changes} 条")
                
                conn.commit()
                cursor.close()
                conn.close()
                
                # 刷新缓存
                self.db._load_all_to_cache_async()
                
                detail_str = "\n".join(changes_detail)
                messagebox.showinfo("成功", 
                    f"批量路径替换完成！\n\n共修改 {total_changes} 条记录：\n{detail_str}")
                
                # 刷新父对话框的列表
                dialog.destroy()
                parent_dialog.destroy()
                self._manage_video_relations()
                
            except Exception as e:
                messagebox.showerror("错误", f"批量替换失败: {e}")
                import traceback
                traceback.print_exc()
        
        ttk.Button(btn_frame, text="预览修改", command=preview_changes, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="执行替换", command=execute_replace, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy, width=12).pack(side=tk.RIGHT, padx=5)
        
        # 初始聚焦
        dialog.update_idletasks()
        dialog.lift()
    
    def _on_log_message(self, message):
        """接收调试日志消息"""
        # 检查过滤
        filter_type = self.log_filter_var.get()
        if filter_type != "全部":
            # 根据消息内容判断类型
            msg_lower = message.lower()
            if filter_type == "扫描" and not any(k in msg_lower for k in ['扫描', 'scan', '文件夹']):
                return
            elif filter_type == "匹配" and not any(k in msg_lower for k in ['匹配', 'match', '缩略图', 'thumbnail', '视频', '图片']):
                return
            elif filter_type == "标签" and not any(k in msg_lower for k in ['标签', 'tag']):
                return
            elif filter_type == "错误" and not any(k in msg_lower for k in ['错误', 'error', '失败', 'fail']):
                return
        
        # 确定消息类型（用于颜色）
        msg_lower = message.lower()
        if '错误' in msg_lower or 'error' in msg_lower or '失败' in msg_lower:
            tag = "error"
        elif '警告' in msg_lower or 'warning' in msg_lower:
            tag = "warning"
        elif '成功' in msg_lower or '完成' in msg_lower:
            tag = "success"
        elif '扫描' in msg_lower or 'scan' in msg_lower:
            tag = "scan"
        elif '匹配' in msg_lower or 'match' in msg_lower:
            tag = "match"
        else:
            tag = "info"
        
        # 插入到文本框
        self.log_text.insert(tk.END, message + "\n", tag)
        
        # 自动滚动
        if self.auto_scroll_var.get():
            self.log_text.see(tk.END)
        
        # 更新统计
        self._update_log_stats()


    def _update_log_stats(self):
        """更新日志统计"""
        log_count = int(self.log_text.index('end-1c').split('.')[0])
        self.log_stats_label.config(text=f"日志: {log_count} 条")


    def _clear_log(self):
        """清空日志"""
        if messagebox.askyesno("确认", "确定要清空所有日志吗？"):
            self.log_text.delete(1.0, tk.END)
            self.debug.clear_buffer()
            self._update_log_stats()
            self.status_label.config(text="日志已清空", foreground="green")


    def _export_log(self):
        """导出日志到文件"""
        from tkinter import filedialog
        
        # 获取所有日志内容
        log_content = self.log_text.get(1.0, tk.END)
        if not log_content.strip():
            messagebox.showinfo("提示", "没有日志可导出")
            return
        
        # 选择保存位置
        filepath = filedialog.asksaveasfilename(
            title="导出日志",
            defaultextension=".log",
            filetypes=[
                ("日志文件", "*.log"),
                ("文本文件", "*.txt"),
                ("所有文件", "*.*")
            ]
        )
        
        if filepath:
            success, msg = self.debug.export_logs(filepath)
            if success:
                messagebox.showinfo("成功", f"日志已导出到:\n{filepath}")
            else:
                messagebox.showerror("错误", f"导出失败: {msg}")


    def _copy_all_logs(self):
        """复制全部日志"""
        log_content = self.log_text.get(1.0, tk.END)
        if log_content.strip():
            self.root.clipboard_clear()
            self.root.clipboard_append(log_content)
            self.status_label.config(text=f"已复制 {int(log_content.count(chr(10)))} 条日志", foreground="green")


    def _copy_selected_log(self):
        """复制选中的日志"""
        try:
            selected = self.log_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if selected:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
                self.status_label.config(text=f"已复制选中内容", foreground="green")
        except tk.TclError:
            pass  # 没有选中内容


    def _show_log_context_menu(self, event):
        """显示日志右键菜单"""
        try:
            self.log_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_context_menu.grab_release()


    def _on_log_filter_change(self, event=None):
        """日志过滤器变更"""
        # 重新加载所有日志（从缓冲区）
        self.log_text.delete(1.0, tk.END)
        for msg in self.debug.get_log_buffer():
            self._on_log_message(msg)

def main():
    root = tk.Tk()
    app = ImageBrowserApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()