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
from crawler_manager import CrawlerManager, CrawlerInterface  # 爬虫管理器
from video_relation_manager import RelationManager, VideoRelationDialog
from contextlib import nullcontext # 添加辅助上下文管理器

# ==================== 模块调试开关 ====================
# 将对应模块设为 False 可在启动时跳过该模块，用于排查启动耗时问题
# 注意：关闭模块后，依赖该模块的功能可能无法使用，但软件不会崩溃
ENABLE_MENU = True           # 菜单栏
ENABLE_BOOKMARK = True       # 书签管理器
ENABLE_TREE_EXPLORER = True  # 资源管理器树
ENABLE_GALLERY = True        # 图库浏览器
ENABLE_TAG_MANAGER = True    # 标签管理页

class ImageBrowserApp:
    """智能图片浏览器主应用 - 跨设备版"""

    def __init__(self, root):
        self.root = root
        self.root.title("智能图片浏览器 - 跨设备版")
        self.root.geometry("1600x1000")
        self.root.minsize(1200, 700)
        # 创建图库后添加加载完成回调
        # 注意：这个回调会在每次图库加载完成后执行
        self.root.after(100, self._setup_gallery_callbacks)

        # ========== 启动时间记录系统 ==========
        self._start_time = time.time()
        self._startup_log = []  # 启动日志队列
        self._pending_startup_logs = []  # 待显示的启动日志（等UI准备好后显示）
        
        def log_stage(stage_id, message):
            """记录启动阶段 - 修复重复打印问题"""
            import time
            timestamp = time.strftime("%H:%M:%S")
            
            # 格式化日志消息
            log_content = f"[{timestamp}] [启动] {stage_id}: {message}"
            
            # 输出到终端
            print(log_content)
            
            # 关键修复：只存入待处理队列，不直接调用 debug.print
            # 避免在 debug 初始化时触发回调导致重复
            self._pending_startup_logs.append((timestamp, stage_id, message))

        self._log_stage = log_stage
        log_stage("INIT-01", "开始初始化")
        
        # [A01] 初始化配置管理器 - 插入细粒度计时诊断
        log_stage("A01", "开始加载配置管理器")
        t0 = time.time()
        
        # 新增：分段计时，定位配置加载的具体耗时点
        t_config_start = time.time()
        self.config = ExtendedConfigManager()
        t_mark = time.time()
        log_stage("A01-STEP1", f"ExtendedConfigManager() 实例化耗时 {(t_mark-t_config_start)*1000:.1f}ms")
        
        # 检查是否有隐式的书签遍历操作
        try:
            # 如果初始化时遍历了书签，记录数量和时间
            bm_count = len(self.config.devices.get(self.config.device_id, {}).get('bookmarks', []))
            t_check = time.time()
            log_stage("A01-STEP2", f"获取书签数量({bm_count}) 耗时 {(t_check-t_mark)*1000:.1f}ms")
        except Exception as e:
            log_stage("A01-STEP2-ERR", f"检查书签失败: {e}")
        
        t1 = time.time()
        log_stage("A01-OK", f"配置管理器总耗时 {(t1-t0)*1000:.1f}ms")
        
        # [A02] 初始化调试工具
        log_stage("A02", "初始化调试工具")
        t0 = time.time()
        self.debug = DebugUtils(self.config.debug_mode)
        t1 = time.time()
        log_stage("A02-OK", f"调试工具初始化完成，耗时 {(t1-t0)*1000:.1f}ms")
        
        self.debug.print(f"程序启动时间: {self._start_time}")
        self.debug.print(f"当前设备: {self.config.device_id}")

        # 初始化状态标志
        self._ui_ready = False
        self._db_ready = False
        self._db_loading = False
        self._save_debug_data_var = tk.BooleanVar(value=False)
        self._is_startup_restoring = True  # 启动恢复标志，禁止保存状态
        self._startup_gallery_loaded = False  # 启动时图库是否已加载（防重复）
        
        # 【新增】树状资源管理器全局缓存，由 tree_explorer 加载完成后同步
        self.tree = {}
        
        # 数据库对象
        self.db = None
        
        # [A03] 初始化媒体播放器
        log_stage("A03", "初始化媒体播放器")
        t0 = time.time()
        self.video_player = VideoPlayer(self.config, self.debug)
        t1 = time.time()
        log_stage("A03-OK", f"媒体播放器初始化完成，耗时 {(t1-t0)*1000:.1f}ms")
        
        # [A04] 初始化图片查看器
        log_stage("A04", "初始化图片查看器")
        t0 = time.time()
        self.image_viewer = ImageViewer(self.config, self.debug)
        t1 = time.time()
        log_stage("A04-OK", f"图片查看器初始化完成，耗时 {(t1-t0)*1000:.1f}ms")

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

        # ========== [B01] 立即创建基本UI并显示窗体 ==========
        self._log_stage("B01", "开始创建UI")
        t_ui_start = time.time()  # 新增：UI总计时
        
        t0 = time.time()
        if ENABLE_MENU:
            self._create_menu()
            t1 = time.time()
            self._log_stage("B01-1", f"菜单创建完成，耗时 {(t1-t0)*1000:.1f}ms")
        else:
            self._log_stage("B01-1", "菜单栏已跳过（调试开关）")
        
        t0 = time.time()
        self._create_ui()
        t1 = time.time()
        self._log_stage("B01-2", f"主UI创建完成，耗时 {(t1-t0)*1000:.1f}ms")
        
        # 新增：输出UI总耗时
        self._log_stage("B01-TOTAL", f"_create_ui 总耗时 {(t1-t_ui_start)*1000:.1f}ms")
        
        self._ui_ready = True
        
        # 关键：立即显示窗体，让用户看到界面
        self._log_stage("B02", "显示窗体")
        self.status_label.config(text="正在启动...", foreground="blue")
        self.root.update_idletasks()
        self.root.deiconify()
        self._log_stage("B02-OK", "窗体已显示")
        
        # 初始化爬虫接口（用于采集视频信息）
        self.crawler_interface = None
        
        # 绑定标签切换事件
        t0 = time.time()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        t1 = time.time()
        self._log_stage("B03", f"事件绑定完成，耗时 {(t1-t0)*1000:.1f}ms")
        
        # ========== [C01] 延迟执行可能阻塞的初始化 ==========
        self._log_stage("C01", "准备延迟初始化")
        self.root.after(10, self._post_ui_startup)
      
    def _post_ui_startup(self):
        """UI显示后的初始化"""
        self._log_stage("C01-START", "开始延迟初始化阶段")
        self._log_stage("C02", "启动文件夹缓存管理器初始化（后台线程）")
        threading.Thread(target=self._init_folder_cache_async, daemon=True).start()
        self._log_stage("C03", "准备开始加载内容")
        self.root.after(10, self._optimized_startup_v2)
    
    def _init_folder_cache_async(self):
        """异步初始化文件夹缓存管理器"""
        try:
            from core import FolderCacheManager
            self._log_stage("CACHE-01", "创建FolderCacheManager实例")
            t0 = time.time()
            self.folder_cache = FolderCacheManager(self.debug)
            t1 = time.time()
            self._log_stage("CACHE-02", f"FolderCacheManager创建完成，耗时 {(t1-t0)*1000:.1f}ms")
            
            # 不在启动时清空缓存：否则启动恢复书签时缓存必然未命中，
            # 大目录会回退到主线程递归扫描，导致窗口显示后长时间无响应。
            if self.debug:
                self.debug.print("文件夹缓存管理器初始化完成（保留已有缓存）")
        except Exception as e:
            self._log_stage("CACHE-ERR", f"文件夹缓存初始化失败: {e}")
            if self.debug:
                self.debug.print(f"文件夹缓存初始化失败: {e}")

    def _optimized_startup_v2(self):
        """简化版启动流程 - 使用缓存直接构建树，支持启动恢复"""
        try:
            self._log_stage("D01", "=== 开始启动流程 ===")

            # 【关键】启动恢复期间，禁止保存状态
            self._is_startup_restoring = True

            # ========== 定位目标书签并读取上次打开的文件夹 ==========
            target_bm = None
            last_folder = None

            # 从 config.json 顶层读取 last_folder
            config_last_folder = self.config.last_folder
            if config_last_folder:
                config_last_folder = os.path.normpath(config_last_folder)
                if self.debug:
                    self.debug.print(f"[启动] config.json 顶层 last_folder: {config_last_folder}")

            # 使用 last_folder 定位书签
            if config_last_folder:
                for dev_id, device in self.config.devices.items():
                    for bm in device.get('bookmarks', []):
                        bm_path = os.path.normpath(bm['path'])
                        if config_last_folder == bm_path or config_last_folder.startswith(bm_path + os.sep):
                            target_bm = bm
                            break
                    if target_bm:
                        break

            # 确定最终要恢复的文件夹
            if target_bm:
                if config_last_folder and self._quick_path_check(config_last_folder):
                    last_folder = config_last_folder
                else:
                    last_folder = os.path.normpath(target_bm['path'])

            if self.debug and last_folder:
                self.debug.print(f"[启动] 最终恢复目标文件夹: {last_folder}")

            # ========== 加载书签并构建资源管理器 ==========
            if target_bm and self._quick_path_check(target_bm['path']):
                self._log_stage("D03", f"目标书签: {target_bm.get('display_name', target_bm['path'])}")

                self.current_root = os.path.normpath(target_bm['path'])
                self.current_folder = last_folder if last_folder and self._quick_path_check(last_folder) else self.current_root
                self._current_bookmark_id = target_bm['id']

                self._log_stage("D04", "加载资源管理器")
                self.status_label.config(text="正在加载目录...", foreground="blue")

                # 树加载完成回调：同步缓存到全局变量 + 触发图库加载
                def on_tree_loaded(success, selected_path):
                    if not success or not self.tree_explorer:
                        self._log_stage("D04-ERR", "树加载失败")
                        return

                    # 将文件夹缓存同步到全局变量 self.tree
                    if hasattr(self.tree_explorer, '_folder_cache'):
                        self.tree = self.tree_explorer._folder_cache
                        node_count = len(self.tree)
                        if self.debug:
                            self.debug.print(f"[启动] 树缓存已同步到全局 self.tree: {node_count} 个文件夹")

                    # 树已构建并选中，现在加载右侧图库
                    if selected_path and self._quick_path_check(selected_path):
                        if self.debug:
                            self.debug.print(f"[启动] 准备加载图库: {selected_path}")

                        # 【补充】更新路径栏UI（原本由 _update_tree_select_ui 负责）
                        self.current_folder = selected_path
                        if hasattr(self, 'path_label') and self.path_label.winfo_exists():
                            self.path_label.config(text=f"浏览: {os.path.basename(selected_path)}")
                        if hasattr(self, 'copy_btn'):
                            self.copy_btn.config(state="normal")

                        # 获取上次滚动位置
                        cache = self._load_ui_cache()
                        gallery_scroll = cache.get('last_scroll_y', 0)

                        # 启动图库加载线程（force_db_sync=False，不清缓存）
                        threading.Thread(
                            target=self._async_load_gallery_startup,
                            args=(selected_path, gallery_scroll),
                            daemon=True
                        ).start()

                        # 【新增】标记图库已加载，防止后续select事件重复加载
                        self._startup_gallery_loaded = True

                    # 延迟关闭启动恢复标志
                    self.root.after(500, lambda: setattr(self, '_is_startup_restoring', False))

                # 检查 tree_explorer 是否已初始化
                if self.tree_explorer:
                    # 设置数据库引用
                    if self._db_ready and self.db:
                        self.tree_explorer.set_db(self.db)

                    # 获取文件夹缓存（优先使用已有缓存，否则扫描文件系统）
                    folder_cache = self._get_folder_cache_for_bookmark(target_bm['path'])

                    # 调用新的 load_bookmark 方法
                    self.tree_explorer.load_bookmark(
                        root_path=self.current_root,
                        device_id=self.config.device_id,
                        bookmark_id=target_bm['id'],
                        folder_cache=folder_cache,           # 传入缓存
                        last_folder=self.current_folder,      # 传入上次打开的文件夹
                        on_loaded=on_tree_loaded               # 加载完成回调
                    )
                else:
                    self._log_stage("D04-WARN", "资源管理器尚未初始化，延迟重试...")
                    self.root.after(100, self._optimized_startup_v2)
                    return

                # ========== 启动其余后台线程 ==========
                if self.bookmark_manager:
                    self.bookmark_manager.restore_last_bookmark(target_bm['id'], trigger_callback=False)

                self._log_stage("E02", "启动标签加载线程")
                threading.Thread(target=self._async_load_video_tags, daemon=True).start()

                self._log_stage("E04", "启动数据库连接线程")
                threading.Thread(
                    target=self._async_init_database_and_merge,
                    args=(self.current_folder,),
                    daemon=True
                ).start()
            else:
                self._log_stage("D03-NO-TARGET", "未找到有效书签")
                if self.bookmark_manager:
                    self.bookmark_manager.refresh_from_config()
                threading.Thread(target=self._async_init_database_only, daemon=True).start()
                self._is_startup_restoring = False

            ui_ready_time = time.time() - self._start_time
            self._log_stage("D05", f"启动流程调度完成，总用时: {ui_ready_time:.3f}s")

        except Exception as e:
            self._log_stage("D-ERROR", f"启动失败: {e}")
            self.status_label.config(text="启动失败", foreground="red")
            import traceback
            traceback.print_exc()
            self._is_startup_restoring = False

    def _get_folder_cache_for_bookmark(self, bookmark_path):
        """
        获取指定书签的文件夹缓存

        优先级：
        1. 全局 self.tree（已加载的缓存）
        2. 数据库缓存
        3. JSON缓存文件
        4. 扫描文件系统（后备）

        Returns:
            dict: 文件夹缓存 {path: folder_info}
        """
        bookmark_path_norm = os.path.normpath(bookmark_path)

        # 1. 检查全局缓存 self.tree
        if hasattr(self, 'tree') and isinstance(self.tree, dict) and len(self.tree) > 0:
            # 过滤出该书签下的文件夹
            filtered = {}
            for path, info in self.tree.items():
                path_norm = os.path.normpath(path)
                if path_norm.startswith(bookmark_path_norm):
                    filtered[path_norm] = info
            if filtered:
                if self.debug:
                    self.debug.print(f"[启动] 使用全局缓存: {len(filtered)} 个文件夹")
                return filtered

        # 2. 从数据库缓存读取
        if self._db_ready and self.db and hasattr(self.db, '_cache'):
            try:
                with self.db._cache_lock:
                    folders_cache = self.db._cache.get('folders', {})
                    if folders_cache:
                        db_cache = {}
                        for folder_path, folder_info in folders_cache.items():
                            path_norm = os.path.normpath(folder_path)
                            if path_norm.startswith(bookmark_path_norm):
                                db_cache[path_norm] = {
                                    'id': folder_info.get('id'),
                                    'name': folder_info.get('name', os.path.basename(path_norm)),
                                    'parent': os.path.normpath(folder_info['parent_path']) if folder_info.get('parent_path') else None,
                                    'is_bookmark': path_norm == bookmark_path_norm,
                                    'file_count': folder_info.get('file_count', 0),
                                    'children': []
                                }

                        # 构建children关系
                        for path, info in db_cache.items():
                            parent = info.get('parent')
                            if parent and parent in db_cache:
                                if path not in db_cache[parent]['children']:
                                    db_cache[parent]['children'].append(path)

                        if db_cache:
                            if self.debug:
                                self.debug.print(f"[启动] 从数据库缓存读取: {len(db_cache)} 个文件夹")
                            return db_cache
            except Exception as e:
                if self.debug:
                    self.debug.print(f"[启动] 从数据库读取缓存失败: {e}")

        # 3. 从JSON缓存文件读取
        try:
            if self.tree_explorer:
                cache_data = self.tree_explorer._load_cache(self.config.device_id, self._current_bookmark_id)
                if cache_data and cache_data.get('nodes'):
                    nodes = cache_data.get('nodes', {})
                    json_cache = {}
                    for path, node in nodes.items():
                        path_norm = os.path.normpath(path)
                        if path_norm.startswith(bookmark_path_norm):
                            json_cache[path_norm] = {
                                'id': node.get('id'),
                                'name': node.get('name', os.path.basename(path_norm)),
                                'parent': os.path.normpath(node['parent']) if node.get('parent') else None,
                                'is_bookmark': node.get('is_bookmark', False),
                                'file_count': node.get('file_count', 0),
                                'children': [os.path.normpath(c) for c in node.get('children', [])]
                            }
                    if json_cache:
                        if self.debug:
                            self.debug.print(f"[启动] 从JSON缓存读取: {len(json_cache)} 个文件夹")
                        return json_cache
        except Exception as e:
            if self.debug:
                self.debug.print(f"[启动] 从JSON缓存读取失败: {e}")

        # 4. 返回空字典，让 tree_explorer 自行扫描文件系统
        if self.debug:
            self.debug.print(f"[启动] 无可用缓存，将扫描文件系统: {bookmark_path_norm}")
        return {}

    def _async_restore_bookmark_state(self, target_bm):
        """恢复书签管理器显示并选中上次使用的书签（启动时静默恢复，不触发重新加载树和图库）"""
        try:
            bm_id = target_bm.get('id') if target_bm else None
            # 关键修复：启动时静默恢复，不触发 on_select 回调
            # 避免重复加载树和图库（树和图库已在 _on_tree_loaded 和 E01 中加载完成）
            if self.bookmark_manager:
                self.root.after(0, lambda: self.bookmark_manager.restore_last_bookmark(bm_id, trigger_callback=False))
        except Exception as e:
            if self.debug:
                self.debug.print(f"书签恢复失败: {e}")

    def _async_load_gallery_startup(self, folder_path, scroll_y=None):
        """异步加载图库（启动时使用）- 优先使用缓存，不强制数据库同步"""
        try:
            # 确保 folder_cache 存在
            if not hasattr(self, 'folder_cache') or not self.folder_cache:
                from core import FolderCacheManager
                self.folder_cache = FolderCacheManager(self.debug)
            
            # 启动时绝不强制同步数据库，优先使用JSON缓存快速显示
            # 数据库同步由 _async_init_database_and_merge 在后台处理
            self._load_gallery_unified(
                folder_path, scroll_y, 
                force_db_sync=False,  # 关键：启动时不清除缓存，避免重新扫描
                source="启动加载", 
                include_subfolders=False
            )
            
            if self.debug:
                self.debug.print(f"[启动] 图库已请求加载: {os.path.basename(folder_path)}")
        except Exception as e:
            if self.debug:
                self.debug.print(f"异步加载图库失败: {e}")
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"异步加载图库失败: {e}")

    def _update_startup_status(self, message, progress):
        """更新启动状态显示"""
        try:
            if hasattr(self, 'status_label') and self.status_label.winfo_exists():
                self.status_label.config(text=message, foreground="blue")
        except:
            pass

    def _async_load_video_tags(self):
        """异步加载视频标签信息（4A）"""
        try:
            # 从配置读取所有标签
            all_tags = self.config.get_all_tags()
            video_tags = self.config.get_all_video_tags()
            
            if self.debug:
                self.debug.print(f"  加载了 {len(all_tags)} 个标签, {len(video_tags)} 个视频标签")
            
            # 在主线程更新UI
            self.root.after(0, lambda: self._update_tag_links_ui(all_tags, video_tags))
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"  加载视频标签失败: {e}")

    def _update_tag_links_ui(self, tags, video_tags):
        """更新标签链接UI - 复用分批渲染避免阻塞"""
        try:
            # 统计标签使用数量
            tag_counts = {}
            for path, tags_list in video_tags.items():
                for tag in tags_list:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            # 复用分批渲染逻辑
            self._render_tag_links_batch(tags, tag_counts)
            
            if self.debug:
                self.debug.print("  标签UI更新完成")
        except Exception as e:
            if self.debug:
                self.debug.print(f"  更新标签UI失败: {e}")

    def _get_startup_target(self, last_folder):
        """获取启动时的目标书签和文件夹（优化：避免网络路径阻塞）"""
        target_bm = None
        target_folder = None
        final_target = None
        
        if last_folder:
            last_folder = os.path.normpath(last_folder)
        
        # 确定上次使用的书签
        if last_folder:
            for dev_id, device in self.config.devices.items():
                for bm in device.get('bookmarks', []):
                    bm_path = os.path.normpath(bm['path'])
                    if last_folder == bm_path or last_folder.startswith(bm_path + os.sep):
                        target_bm = bm
                        break
                if target_bm:
                    break
        
        if target_bm:
            # 优化：对网络路径使用快速检查，避免阻塞
            bm_path = target_bm['path']
            path_exists = self._quick_path_check(bm_path)
            
            if path_exists:
                # 获取书签状态
                bm_state = self.config.get_bookmark_state(target_bm['id'])
                
                if bm_state and bm_state.get('last_folder'):
                    state_folder = os.path.normpath(bm_state['last_folder'])
                    # 对子文件夹也使用快速检查
                    if self._quick_path_check(state_folder):
                        target_folder = state_folder
                    else:
                        target_folder = os.path.normpath(bm_path)
                else:
                    target_folder = os.path.normpath(bm_path)
                
                tree_state = bm_state.get('tree_state', {}) if bm_state else {}
                tree_selected = tree_state.get('selected_path')
                if tree_selected:
                    tree_selected = os.path.normpath(tree_selected)
                
                # 对选中路径使用快速检查
                if tree_selected and self._quick_path_check(tree_selected):
                    final_target = tree_selected
                else:
                    final_target = target_folder
        
        return target_bm, target_folder, final_target
    
    def _quick_path_check(self, path):
        """
        快速检查路径是否存在（避免网络路径超时阻塞启动）
        
        策略：
        - 本地路径：使用标准os.path.exists()
        - 网络路径：先快速ping主机（300ms超时），如果失败但ping成功，返回True不阻塞
        """
        if not path:
            return False
        
        # 本地路径使用标准检查
        if not path.startswith('\\\\'):
            try:
                return os.path.exists(path)
            except:
                return False
        
        # 网络路径：快速ping检查
        try:
            parts = path.split('\\')
            if len(parts) >= 3:
                host = parts[2]  # \\host\share\...
                import subprocess
                # 使用更短的超时（300ms）加快启动
                result = subprocess.run(
                    ['ping', '-n', '1', '-w', '300', host],
                    capture_output=True,
                    timeout=1
                )
                # 如果ping成功，假设路径可用（避免os.path.exists阻塞）
                # 如果ping失败，仍然返回True让后续处理，但可能更快失败
                return True  # 总是返回True避免阻塞，让实际文件操作检测错误
        except:
            pass
        
        # 如果检查过程中出错，返回True不阻塞启动
        return True

    # def _load_gallery_immediate(self, folder_path, scroll_y=None):
    #     """立即加载图库（仅使用文件系统数据，不等待数据库）"""
    #     try:
    #         # 使用gallery_loader立即扫描
    #         if not self.gallery_loader:
    #             from gallery_loader import GalleryLoader
    #             self.gallery_loader = GalleryLoader(None, self.folder_cache, self.debug)
            
    #         # 直接扫描文件夹，不使用数据库
    #         self.gallery_loader.load_gallery(
    #             folder_path, self.gallery, self._update_status, scroll_y, db_ready=False
    #         )
            
    #         # 保存文件夹缓存数据供后续合并使用
    #         self._folder_scanned_data = None
    #         threading.Thread(
    #             target=self._scan_folder_for_cache,
    #             args=(folder_path,),
    #             daemon=True
    #         ).start()
            
    #     except Exception as e:
    #         if self.debug:
    #             self.debug.print(f"立即加载图库失败: {e}")

    def _async_restore_ui_state(self, target_bm, final_target, cache):
        """异步恢复UI状态（书签和资源管理器）"""
        try:
            # 恢复书签管理器
            if self.bookmark_manager:
                self.root.after(0, lambda: self.bookmark_manager.refresh_from_config())
            
            # 恢复资源管理器状态
            bm_state = self.config.get_bookmark_state(target_bm['id'])
            tree_state = bm_state.get('tree_state', {}) if bm_state else {}
            
            if self.tree_explorer:
                self.root.after(100, lambda: self.tree_explorer.restore_state(
                    expanded_paths=[],
                    selected_path=final_target,
                    scroll_position=tree_state.get('scroll_position', 0.0)
                ))
            
            if self.debug:
                self.debug.print("  UI状态恢复完成")
        except Exception as e:
            if self.debug:
                self.debug.print(f"  UI状态恢复失败: {e}")

    def _async_init_database_and_merge(self, folder_path):
        """异步初始化数据库"""
        try:
            if not DB_AVAILABLE:
                self.debug.print("[步骤5] 数据库模块不可用，跳过")
                return

            from init_database import ensure_database_ready
            db_success, db_msg = ensure_database_ready()
            if not db_success:
                self.debug.print(f"[步骤5] 数据库初始化失败: {db_msg}")
                return

            self.db = DatabaseManager(debug_utils=self.debug, fast_mode=True)
            if not self.db.is_ready:
                self.debug.print("[步骤5] 数据库连接失败")
                return

            self._db_ready = True
            self.debug.print("[步骤5] 数据库连接成功")

            self.root.after(0, self._update_db_references)

            self.debug.print("[步骤6] 标记已同步视频...")
            self._mark_synced_videos()

            self.root.after(0, lambda: self._on_startup_complete_with_refresh(folder_path))

        except Exception as e:
            if self.debug:
                self.debug.print(f"[步骤5-7] 数据库处理失败: {e}")
                import traceback
                traceback.print_exc()

    def _on_startup_complete_with_refresh(self, folder_path, retry_count=0):
        """
        启动完成并刷新关联标识
        委托 GalleryViewer 统一处理数据库就绪后的图库刷新。
        """
        self._on_startup_complete()

        if not (self.current_folder and os.path.normpath(self.current_folder) == os.path.normpath(folder_path)):
            return

        gallery = getattr(self, 'gallery', None)
        if gallery and hasattr(gallery, '_pending_widgets') and gallery._pending_widgets:
            MAX_RETRIES = 15
            if retry_count < MAX_RETRIES:
                if self.debug:
                    self.debug.print(f"[启动完成] 图库待渲染 {len(gallery._pending_widgets)} 个，延迟刷新... (重试 {retry_count+1}/{MAX_RETRIES})")
                self.root.after(400, lambda: self._on_startup_complete_with_refresh(folder_path, retry_count + 1))
                return
            else:
                if self.debug:
                    self.debug.print(f"[启动完成] 重试次数用尽，执行强制增量刷新")

        if self.debug:
            self.debug.print(f"[启动完成] 执行增量刷新: {os.path.basename(folder_path)}")

        # 委托 GalleryViewer 处理同步标识和视频信息刷新
        if self.gallery and self._db_ready and self.db:
            self.gallery.on_db_ready(self.db)
        
        # 延迟再次检查确保所有视频信息都已加载
        self.root.after(500, lambda: self._ensure_all_video_info_loaded(folder_path))

    def _ensure_all_video_info_loaded(self, folder_path):
        """
        确保当前文件夹所有视频信息都已加载并显示
        委托给 GalleryViewer 的方法
        """
        if self.gallery:
            self.gallery.ensure_all_video_info_loaded(
                folder_path,
                db_ready=self._db_ready,
                db_manager=self.db,
                thumbnail_width=self.config.thumbnail_width
            )

    def _async_init_database_only(self):
        """仅初始化数据库 - 数据操作已迁移到DatabaseManager"""
        try:
            if not DB_AVAILABLE:
                self.debug.print("[步骤5] 数据库模块不可用，跳过")
                return
            
            # 关键修复：如果 db 尚未创建（无书签路径时），先创建 DatabaseManager 实例
            if self.db is None:
                self._log_stage("DB-INIT", "无书签路径，延迟创建数据库实例")
                from init_database import ensure_database_ready
                db_success, db_msg = ensure_database_ready()
                if not db_success:
                    self.debug.print(f"[步骤5] 数据库初始化失败: {db_msg}")
                    return
                
                self.db = DatabaseManager(debug_utils=self.debug, fast_mode=True)
                if not self.db.is_ready:
                    self.debug.print("[步骤5] 数据库连接失败")
                    return
                
                self.debug.print("[步骤5] 数据库实例已创建")
            
            def on_ready():
                self._db_ready = True
                self.root.after(0, self._update_db_references)
            
            self.db.init_only_async(on_ready=on_ready, debug=self.debug)
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"[步骤5] 数据库初始化失败: {e}")
                import traceback
                traceback.print_exc()

    def _update_db_references(self):
        """更新各组件的数据库引用"""
        if self.bookmark_manager:
            self.bookmark_manager.db = self.db
        if self.gallery:
            self.gallery.db = self.db

        # 初始化关联管理器和图库加载器
        if self.relation_manager is None:
            self.relation_manager = RelationManager(self.root, self.db, self.config, self.debug)
        if self.gallery_loader is None:
            from gallery_loader import GalleryLoader
            self.gallery_loader = GalleryLoader(self.db, self.folder_cache, self.debug)
        else:
            # 关键修复：启动竞态条件下 gallery_loader 可能已被提前创建（db=None），
            # 数据库就绪后必须更新其 db 引用，否则关联信息、演员信息无法查询
            if self.gallery_loader.db != self.db:
                self.gallery_loader.db = self.db
                if self.debug:
                    self.debug.print(f"[数据库引用] 更新 gallery_loader.db 引用")

        self.debug.print("  数据库引用更新完成")

    def _mark_synced_videos(self):
        """
        标记已联网同步的视频（步骤6）
        委托给 GalleryViewer 处理，减少主程序代码量。
        """
        try:
            if self.gallery and self.db:
                count = self.gallery.rebuild_synced_set(self.db)
                if self.debug:
                    self.debug.print(f"  已标记 {count} 个同步视频")
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"  标记同步视频失败: {e}")
                import traceback
                traceback.print_exc()




    def _on_startup_complete(self):
        """启动完成处理（步骤8）"""
        total_time = time.time() - self._start_time
        self.status_label.config(text=f"就绪 (总用时: {total_time:.2f}s)", foreground="green")
        self.debug.print("=" * 50)
        self.debug.print(f"[步骤8] 软件启动流程全部完成！总用时: {total_time:.2f}s")
        self.debug.print("=" * 50)
        
        # 初始化爬虫接口
        if self.crawler_interface is None:
            self.crawler_interface = CrawlerInterface(self.config, self.debug)

        # 启动完成后自动运行数据完整性检查（仅调试模式）
        if self.debug and self.debug.debug_mode:
            self.root.after(2000, self.run_data_integrity_check)

    def _restore_ui_from_cache_fast(self):
        """从配置快速恢复UI"""
        cache = self._load_ui_cache()
        last_folder = cache.get('last_folder')
        if last_folder:
            last_folder = os.path.normpath(last_folder)

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

        if target_bm and self._quick_path_check(target_bm['path']):
            self.current_root = os.path.normpath(target_bm['path'])
            # 【关键修复】使用 _quick_path_check 替代 os.path.exists，避免网络路径超时
            self.current_folder = last_folder if last_folder and self._quick_path_check(last_folder) else self.current_root
            self._current_bookmark_id = target_bm['id']

            if self.tree_explorer:
                self.tree_explorer.load_bookmark(self.current_root)
                if self.current_folder != self.current_root:
                    self.tree_explorer.select_path(self.current_folder)

            gallery_scroll = cache.get('last_scroll_y', 0)
            self._load_gallery_unified(self.current_folder, gallery_scroll, source="启动恢复")
            self.path_label.config(text=f"浏览: {os.path.basename(self.current_folder)}")
            self.copy_btn.config(state="normal")
                # 这里可以添加直接加载逻辑，或提示用户选择书签

    def _on_gallery_loaded(self, folder_path):
        if self.gallery:
            count = self.gallery.get_image_count()
            self.count_label.config(text=f"图片: {count}")
            self.status_label.config(text=f"就绪 ({count}项)", foreground="green")
        else:
            self.status_label.config(text="就绪", foreground="green")

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
        """延迟后台同步 - 委托给DatabaseManager"""
        if not self._db_ready or not self._current_bookmark_id:
            return
        
        # 只同步当前书签，而非所有设备的所有书签
        bm_state = self.config.get_bookmark_state(self._current_bookmark_id)
        current_folder = bm_state.get('last_folder') if bm_state else None
        
        if current_folder and os.path.exists(current_folder):
            self.status_label.config(text="后台同步当前文件夹...", foreground="blue")
            
            def do_sync():
                success, result = self.db.sync_folder_lightweight(current_folder, self.debug)
                if success:
                    changes = result
                    self.root.after(0, lambda: self.status_label.config(
                        text=f"数据库就绪 ({changes} 更新)", foreground="green"))
                    if changes > 0:
                        self.db._load_all_to_cache_async()
                else:
                    self.root.after(0, lambda: self.status_label.config(
                        text="数据库就绪", foreground="green"))
            
            threading.Thread(target=do_sync, daemon=True).start()

    def _sync_single_folder_lightweight(self, folder_path):
        """轻量级同步单个文件夹 - 委托给DatabaseManager"""
        try:
            if self.db and self.db.is_ready:
                # 检查缓存是否已加载，避免死锁
                if hasattr(self.db, 'is_cache_ready') and not self.db.is_cache_ready:
                    # 缓存未就绪，延迟后重试
                    self.root.after(500, lambda: self._sync_single_folder_lightweight(folder_path))
                    return
                
                success, result = self.db.sync_folder_lightweight(folder_path, self.debug)
                if success:
                    changes = result
                    self.root.after(0, lambda: self.status_label.config(
                        text=f"数据库就绪 ({changes} 更新)", foreground="green"))
                    if changes > 0:
                        self.db._load_all_to_cache_async()
                else:
                    self.root.after(0, lambda: self.status_label.config(
                        text="数据库就绪", foreground="green"))
        except Exception as e:
            if self.debug:
                self.debug.print(f"轻量级同步失败: {e}")
            self.root.after(0, lambda: self.status_label.config(
                text="数据库就绪", foreground="green"))

    def _on_db_ui_update(self):
        """数据库就绪后更新UI"""
        if not self._db_ready:
            self.status_label.config(text="数据库未连接", foreground="orange")
            return

        if self.db and hasattr(self.db, 'is_cache_ready') and not self.db.is_cache_ready:
            self.status_label.config(text="等待缓存加载...", foreground="blue")
            self.root.after(100, self._on_db_ui_update)
            return

        self.status_label.config(text="数据库已连接", foreground="green")

        if getattr(self, 'bookmark_manager', None):
            self.bookmark_manager.db = self.db
        if getattr(self, 'tree_explorer', None):
            self.tree_explorer.set_db(self.db)  # 更新数据库引用

        if self.relation_manager is None:
            self.relation_manager = RelationManager(self.root, self.db, self.config, self.debug)
        if self.gallery_loader is None:
            from gallery_loader import GalleryLoader
            self.gallery_loader = GalleryLoader(self.db, self.folder_cache, self.debug)
        else:
            if self.gallery_loader.db != self.db:
                self.gallery_loader.db = self.db
                if self.debug:
                    self.debug.print(f"[数据库引用] 更新 gallery_loader.db 引用")

        current_tab = self.notebook.select()
        if current_tab:
            tab_text = self.notebook.tab(current_tab, "text")
            if tab_text == "演员管理" and hasattr(self, 'actor_manager') and self.actor_manager:
                self.root.after(500, lambda: self.actor_manager.refresh() if self.actor_manager else None)

        self._refresh_tag_links()

        # 委托 GalleryViewer 处理数据库就绪后的图库刷新
        if self.current_folder and not str(self.current_folder).startswith('['):
            if self.debug:
                self.debug.print("[数据库就绪] 触发图库刷新...")
            if hasattr(self, 'gallery') and self.gallery:
                self.root.after(300, lambda: self.gallery.on_db_ready(self.db))

    def _refresh_gallery_for_relation_sync(self):
        """同步数据库关联信息后刷新图库 - 统一走 _load_gallery_unified 确保显示一致"""
        if hasattr(self, 'gallery') and self.current_folder:
            if self.debug:
                self.debug.print("数据库就绪，刷新图库以应用关联标识...")

            scroll_y = self.gallery.get_scroll_y() if hasattr(self.gallery, 'get_scroll_y') else 0
            self._load_gallery_unified(self.current_folder, scroll_y, force_db_sync=True, source="关联同步")
            self._refresh_tag_links()

            if self.debug:
                self.debug.print(f"已刷新图库关联标识: {os.path.basename(self.current_folder)}")

    def _incremental_refresh_gallery(self):
        """
        增量刷新图库 - 只更新数据库信息，不重新渲染缩略图，避免闪烁
        委托给 GalleryViewer 统一处理，减少主程序代码量。
        """
        if not hasattr(self, 'gallery') or not self.gallery:
            return

        # 委托给 GalleryViewer 处理同步标识和视频信息刷新
        if self.current_folder:
            self.gallery.refresh_sync_and_info(
                self.current_folder,
                db_ready=self._db_ready,
                db_manager=self.db
            )

        # 刷新标签超链接
        self._refresh_tag_links()

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


    def _refresh_tag_links(self):
        """刷新标签超链接显示 - 异步分批优化版
        
        将数据加载（含大量os.path.exists调用）移到后台线程，
        UI创建采用分批渲染，避免阻塞主线程和窗体显示。
        """
        if not hasattr(self, 'tag_links_container'):
            return
        
        # 防重入：如果正在刷新，忽略此次请求（数据会在完成后自动更新）
        if getattr(self, '_tag_links_refreshing', False):
            return
        self._tag_links_refreshing = True
        
        # 取消之前待处理的分批渲染
        if hasattr(self, '_tag_refresh_after_id') and self._tag_refresh_after_id:
            try:
                self.root.after_cancel(self._tag_refresh_after_id)
            except Exception:
                pass
            self._tag_refresh_after_id = None
        
        # 清空现有控件
        for widget in self.tag_links_container.winfo_children():
            widget.destroy()
        
        # 后台线程获取标签数据（避免主线程执行大量os.path.exists）
        def _load_tag_data_worker():
            try:
                tags = self.config.get_all_tags()
                video_tags = self.config.get_all_video_tags()
                tag_counts = {}
                for path, tags_list in video_tags.items():
                    for tag in tags_list:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                # 回到主线程渲染
                self.root.after(0, lambda: self._render_tag_links_batch(tags, tag_counts))
            except Exception as e:
                self._tag_links_refreshing = False
                if self.debug:
                    self.debug.print(f"标签数据加载失败: {e}")
        
        threading.Thread(target=_load_tag_data_worker, daemon=True).start()

    def _render_tag_links_batch(self, tags, tag_counts):
        """分批渲染标签链接（在主线程执行，避免一次性创建大量控件阻塞UI）"""
        self._tag_links_refreshing = False
        if not hasattr(self, 'tag_links_container'):
            return
        
        for widget in self.tag_links_container.winfo_children():
            widget.destroy()
        
        if not tags:
            tk.Label(self.tag_links_container, text="（暂无标签）", 
                    font=("微软雅黑", 9), fg="gray").pack(side=tk.LEFT, padx=10)
            self.tag_links_canvas.configure(scrollregion=self.tag_links_canvas.bbox("all"))
            return
        
        batch_size = 20
        total = len(tags)
        
        def create_batch(start_idx):
            if not hasattr(self, 'tag_links_container'):
                return
            end_idx = min(start_idx + batch_size, total)
            for i in range(start_idx, end_idx):
                tag = tags[i]
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
            
            if end_idx < total:
                self._tag_refresh_after_id = self.root.after(1, lambda: create_batch(end_idx))
            else:
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
                self.tag_links_canvas.configure(scrollregion=self.tag_links_canvas.bbox("all"))
                self._tag_refresh_after_id = None
        
        create_batch(0)


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
        """清除标签筛选"""
        self._selected_tag_links.clear()
        self._refresh_tag_links()

        if self._current_bookmark_id:
            bm_state = self.config.get_bookmark_state(self._current_bookmark_id)
            last_folder = bm_state.get('last_folder')
            gallery_scroll = bm_state.get('gallery_scroll_y', 0)

            if last_folder and os.path.exists(last_folder) and not str(last_folder).startswith('['):
                self.current_folder = last_folder
                self._load_gallery_unified(last_folder, gallery_scroll, source="清除筛选")
                self.path_label.config(text=f"浏览: {os.path.basename(last_folder)}")
                self.copy_btn.config(state="normal")
                self._save_current_location(folder=last_folder)
                if self.debug:
                    self.debug.print(f"清除筛选后恢复到: {last_folder}")
            else:
                for dev_id, device in self.config.devices.items():
                    for bm in device.get('bookmarks', []):
                        if bm['id'] == self._current_bookmark_id:
                            self._on_bookmark_select(bm['path'])
                            break
        else:
            if self.gallery:
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
        if not self.gallery:
            return
        
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
        
        # 新增：批量采集当前文件夹树所有视频
        file_menu.add_command(label="采集当前文件夹树所有视频信息", command=self._collect_current_tree_videos)
        file_menu.add_separator()
        
        # 修改这里：使用新的完整同步方法
        file_menu.add_command(label="完整同步所有书签", command=self._manual_full_sync)
        file_menu.add_separator()
        file_menu.add_command(label="刷新", command=self._refresh)
        file_menu.add_command(label="清空JSON缓存", command=self._clear_json_cache)
        file_menu.add_separator()
        file_menu.add_command(label="🔍 数据完整性检查", command=self.run_data_integrity_check)
        file_menu.add_separator()
        file_menu.add_command(label="🔄 更新书签内文件夹", command=self._refresh_bookmark_tree_cache)

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
        settings_menu.add_checkbutton(label="保存数据", variable=self._save_debug_data_var, command=self._toggle_save_debug_data)
        settings_menu.add_separator()
        settings_menu.add_command(label="图库颜色设置", command=self._open_color_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="保存配置", command=self._save_config)


    def _refresh_bookmark_tree_cache(self):
        """刷新当前书签下所有文件夹结构到缓存 - 委托给 TreeExplorer 处理"""
        if not self._current_bookmark_id:
            messagebox.showwarning("提示", "请先选择一个书签")
            return

        # 获取当前书签信息
        device = self.config.devices.get(self.config.device_id, {})
        target_bm = None
        for bm in device.get('bookmarks', []):
            if bm['id'] == self._current_bookmark_id:
                target_bm = bm
                break

        if not target_bm:
            messagebox.showwarning("提示", "未找到当前书签信息")
            return

        bm_path = target_bm['path']
        if not os.path.exists(bm_path):
            messagebox.showwarning("提示", f"书签路径不存在:\n{bm_path}")
            return

        # 委托给 TreeExplorer 的异步刷新方法（带数据库同步和精确进度）
        self.tree_explorer.refresh_bookmark_tree_async(
            bookmark_id=self._current_bookmark_id,
            bookmark_path=bm_path,
            bookmark_name=target_bm.get('display_name', bm_path),
            device_id=self.config.device_id,
            db_manager=self.db if self._db_ready else None,
            status_callback=self._update_bookmark_refresh_status,
            progress_callback=self._update_bookmark_refresh_progress,
            tree_refresh_callback=self._on_bookmark_tree_refreshed,
            debug=self.debug
        )

    def _update_bookmark_refresh_status(self, message, color):
        """更新书签刷新状态显示（状态栏）"""
        self.status_label.config(text=message, foreground=color)
        self.root.update_idletasks()

    def _update_bookmark_refresh_progress(self, current, total, percent):
        """更新书签刷新进度条（精确百分比）"""
        if self.gallery and hasattr(self.gallery, 'progress'):
            self.gallery.progress['value'] = percent
            self.status_label.config(
                text=f"正在处理文件夹... ({current}/{total}) {percent}%", 
                foreground="blue"
            )
            self.root.update_idletasks()

    def _on_bookmark_tree_refreshed(self, cache_data):
        """书签树刷新完成后的额外处理"""
        if self.debug:
            node_count = len(cache_data.get('nodes', {}))
            self.debug.print(f"[书签刷新] 资源管理器树已刷新，共 {node_count} 个节点")
        # 刷新完成后重置进度条
        if self.gallery and hasattr(self.gallery, 'progress'):
            self.gallery.progress['value'] = 0

    def _manual_full_sync(self):
        """手动执行完整同步（扫描所有书签）- 委托给DatabaseManager"""
        if not self._db_ready:
            messagebox.showwarning("提示", "数据库尚未就绪")
            return

        self.status_label.config(text="完整同步中...", foreground="blue")

        def do_sync():
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = device.get('bookmarks', [])
            
            def status_callback(msg):
                self.root.after(0, lambda: self.status_label.config(text=msg, foreground="blue"))
            
            success, total_changes = self.db.manual_full_sync(
                self.config.device_id, bookmarks, 
                status_callback=status_callback, debug=self.debug
            )
            
            if success:
                self.root.after(0, lambda: self.status_label.config(
                    text=f"同步完成 ({total_changes} 更新)", foreground="green"))
            else:
                self.root.after(0, lambda: self.status_label.config(
                    text="同步失败", foreground="red"))

        threading.Thread(target=do_sync, daemon=True).start()

    def _collect_current_tree_videos(self):
        """采集当前文件夹树所有视频信息（菜单调用）"""
        if not self.current_folder or not os.path.exists(self.current_folder):
            messagebox.showwarning("提示", "请先选择一个文件夹")
            return
        
        if hasattr(self, 'gallery') and self.gallery:
            target_folder = self.current_folder
            if target_folder and target_folder.startswith('['):
                messagebox.showwarning("提示", "当前处于筛选模式，请先选择一个文件夹")
                return
            
            # 菜单调用时，original_folder 为 None，表示采集当前文件夹及其子文件夹
            self.gallery._collect_folder_tree_videos_info(target_folder, original_folder=None)
        else:
            messagebox.showerror("错误", "图库未就绪")

    def _collect_current_folder_videos(self):
        """采集当前文件夹所有视频信息（菜单调用）"""
        if not self.current_folder or not os.path.exists(self.current_folder):
            messagebox.showwarning("提示", "请先选择一个文件夹")
            return
        
        # 检查是否有视频文件
        from core import PathUtils
        video_files = PathUtils.find_video_files(self.current_folder)
        
        if not video_files:
            messagebox.showinfo("提示", f"当前文件夹中没有视频文件:\n{self.current_folder}")
            return
        
        # 调用图库的批量采集方法
        if hasattr(self, 'gallery') and self.gallery:
            self.gallery._collect_folder_videos_info(self.current_folder)
        else:
            messagebox.showerror("错误", "图库未就绪")

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
            if self.bookmark_manager:
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
        """创建主UI（添加日志标签页）- 含详细分段计时"""
        # ========== 新增：详细分段计时诊断 ==========
        t_ui_start = time.time()
        
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 1. 视频浏览标签页
        t0 = time.time()
        self.main_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.main_tab, text="视频浏览")
        self._create_browser_ui(self.main_tab)
        self._log_stage("B01-UI-1", f"浏览器UI创建耗时 {(time.time()-t0)*1000:.1f}ms")
        
        # 2. 标签管理标签页
        t0 = time.time()
        if ENABLE_TAG_MANAGER:
            self.tag_tab = ttk.Frame(self.notebook)
            self.notebook.add(self.tag_tab, text="标签管理")
            self._log_stage("B01-UI-2", f"标签页创建耗时 {(time.time()-t0)*1000:.1f}ms")
        else:
            self.tag_tab = None
            self._log_stage("B01-UI-2", "标签管理页已跳过（调试开关）")
        
        # 3. 演员管理标签页
        t0 = time.time()
        self.actor_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.actor_tab, text="演员管理")
        self._log_stage("B01-UI-3", f"演员页创建耗时 {(time.time()-t0)*1000:.1f}ms")
        
        # 4. 爬虫管理标签页
        t0 = time.time()
        self.crawler_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.crawler_tab, text="爬虫配置")
        self._log_stage("B01-UI-4", f"爬虫页创建耗时 {(time.time()-t0)*1000:.1f}ms")
        
        # 5. 新增：操作日志标签页
        t0 = time.time()
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="操作日志")
        self._create_log_ui(self.log_tab)
        self._log_stage("B01-UI-5", f"日志页创建耗时 {(time.time()-t0)*1000:.1f}ms")

        # 初始化爬虫管理器（延迟到切换到该标签页时再创建）
        self.crawler_manager = None
        
        # 绑定标签切换事件
        t0 = time.time()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._log_stage("B01-UI-6", f"事件绑定耗时 {(time.time()-t0)*1000:.1f}ms")
        
        self._log_stage("B01-UI-TOTAL", f"_create_ui 总耗时 {(time.time()-t_ui_start)*1000:.1f}ms")

    def _create_browser_ui(self, parent):
        """创建浏览器界面 - 异步初始化版：先创建外壳框架，再延迟初始化内容"""
        import time
        
        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 左侧栏
        left_frame = ttk.Frame(paned, width=320)
        left_frame.pack_propagate(False)

        # 1. 书签管理器 - 仅创建外壳，内容延迟初始化
        t0 = time.time()
        if ENABLE_BOOKMARK:
            self._bm_frame = ttk.Frame(left_frame, height=180)
            self._bm_frame.pack_propagate(False)
            self._bm_frame.pack(fill=tk.X, padx=3, pady=(3, 0))
            self.bookmark_manager = None
            self._log_stage("B01-UI-1a", f"书签框架创建完成，内容将异步初始化")
        else:
            self.bookmark_manager = None
            self._log_stage("B01-UI-1a", "书签管理器已跳过（调试开关）")
        
        # 2. 资源管理器 - 仅创建外壳，内容延迟初始化
        if ENABLE_TREE_EXPLORER:
            self._tree_frame = ttk.LabelFrame(left_frame, text="资源管理器")
            self._tree_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
            self.tree_explorer = None
            self._log_stage("B01-UI-1b", f"资源管理器框架创建完成，内容将异步初始化")
        else:
            self.tree_explorer = None
            self._log_stage("B01-UI-1b", "资源管理器已跳过（调试开关）")

        paned.add(left_frame, weight=0)

        # 右侧区域 - 分段计时
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        # 工具栏
        t5 = time.time()
        self._create_toolbar(right_frame)
        t6 = time.time()
        self._log_stage("B01-UI-1c", f"工具栏创建完成 (耗时 {(t6-t5)*1000:.1f}ms)")
        
        # 标签超链接区域
        t7 = time.time()
        self._create_tag_links_area(right_frame)
        t8 = time.time()
        self._log_stage("B01-UI-1d", f"标签链接区创建完成 (耗时 {(t8-t7)*1000:.1f}ms)")
        
        # 图库 - 仅创建外壳，内容延迟初始化
        if ENABLE_GALLERY:
            self._gallery_frame = ttk.Frame(right_frame)
            self._gallery_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
            self.gallery = None
            self._log_stage("B01-UI-1e", f"图库框架创建完成，内容将异步初始化")
        else:
            self.gallery = None
            self._log_stage("B01-UI-1e", "图库浏览器已跳过（调试开关）")
        
        # 状态栏
        t11 = time.time()
        self._create_status_bar(right_frame)
        t12 = time.time()
        self._log_stage("B01-UI-1f", f"状态栏创建完成 (耗时 {(t12-t11)*1000:.1f}ms)")
        
        # 注册异步初始化任务（在UI框架创建完成后，通过消息循环逐步初始化各模块）
        # 使用 after_idle 确保在窗体显示后的第一个空闲时刻立即执行，
        # 这样模块初始化会在 _post_ui_startup (after(10)) 之前完成
        if ENABLE_BOOKMARK and self.bookmark_manager is None:
            self.root.after_idle(self._async_init_bookmark_manager)
        if ENABLE_TREE_EXPLORER and self.tree_explorer is None:
            self.root.after_idle(self._async_init_tree_explorer)
        if ENABLE_GALLERY and self.gallery is None:
            self.root.after_idle(self._async_init_gallery)

    def _async_init_bookmark_manager(self):
        """异步初始化书签管理器（在窗体显示后通过消息循环执行，避免阻塞UI）"""
        try:
            if not ENABLE_BOOKMARK or self.bookmark_manager is not None:
                return
            t0 = time.time()
            self.bookmark_manager = BookmarkManager(
                self._bm_frame, self.config, None, self._on_bookmark_select, self.debug
            )
            t1 = time.time()
            self._log_stage("B01-UI-1a-ASYNC", f"书签管理器异步初始化完成 (耗时 {(t1-t0)*1000:.1f}ms)")
            
            # 如果数据库已就绪，更新引用
            if self._db_ready and self.db:
                self.bookmark_manager.db = self.db
            
            # 如果启动流程已确定书签，恢复选中状态（不触发重新加载）
            if self._current_bookmark_id:
                self.bookmark_manager.restore_last_bookmark(
                    self._current_bookmark_id, trigger_callback=False
                )
        except Exception as e:
            self._log_stage("B01-UI-1a-ERR", f"书签管理器异步初始化失败: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()

    def _async_init_tree_explorer(self):
        """异步初始化资源管理器树"""
        try:
            if not ENABLE_TREE_EXPLORER or self.tree_explorer is not None:
                return
            t0 = time.time()
            self.tree_explorer = TreeExplorer(
                self._tree_frame, self._on_tree_select, self.debug
            )
            t1 = time.time()
            self._log_stage("B01-UI-1b-ASYNC", f"资源管理器异步初始化完成 (耗时 {(t1-t0)*1000:.1f}ms)")
            
            # 如果数据库已就绪，更新引用
            if self._db_ready and self.db:
                self.tree_explorer.set_db(self.db)
            
            # 注意：树加载和恢复统一由 _optimized_startup_v2 控制
        except Exception as e:
            self._log_stage("B01-UI-1b-ERR", f"资源管理器异步初始化失败: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()

    def _async_init_gallery(self):
        """异步初始化图库浏览器（在窗体显示后通过消息循环执行，避免阻塞UI）
        
        注意：此方法仅创建 GalleryViewer 实例和UI控件，不加载图库内容。
        图库内容的加载由启动流程中的 _async_load_gallery_startup 负责，
        避免与启动流程重复加载。
        """
        try:
            if not ENABLE_GALLERY or self.gallery is not None:
                return
            t0 = time.time()
            self._create_gallery(self._gallery_frame)
            t1 = time.time()
            self._log_stage("B01-UI-1e-ASYNC", f"图库浏览器异步初始化完成 (耗时 {(t1-t0)*1000:.1f}ms)")
            
            # 不在这里加载图库内容，由启动流程统一负责，避免重复加载
        except Exception as e:
            self._log_stage("B01-UI-1e-ERR", f"图库浏览器异步初始化失败: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()


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
        
        # 初始刷新 - 改为延迟异步执行，避免阻塞窗体显示
        self.root.after(100, self._refresh_tag_links)

    # main.py - 修改 _on_collect_video_info 方法中的回调，确保 source_url 被正确传递

    def _on_collect_video_info(self, video_path, code, callback=None):
        """采集视频信息 - 支持回调的版本，确保返回 source_url"""
        import os
        from tkinter import messagebox
        
        # 规范化路径
        video_path = os.path.normpath(video_path)

        # 初始化爬虫接口（如果尚未初始化）
        if self.crawler_interface is None:
            self.crawler_interface = CrawlerInterface(self.config, self.debug)

        # 更新状态
        self.status_label.config(text=f"正在采集: {code}...", foreground="blue")
        self.root.update_idletasks()

        def on_crawl_result(success, info):
            """爬取完成后的回调"""
            # 确保 source_url 始终存在
            source_url = info.get('source_url', '')
            
            if not success:
                error_msg = info.get('error', '未知错误')
                if callback:
                    # 确保传递 source_url
                    info['source_url'] = source_url
                    callback(False, info)
                else:
                    self.root.after(0, lambda: [
                        self.status_label.config(text=f"采集失败: {error_msg}", foreground="red"),
                        messagebox.showerror("采集失败", f"无法获取视频信息:\n{error_msg}\n网址: {source_url}" if source_url else f"无法获取视频信息:\n{error_msg}")
                    ])
                return

            # 解析爬取结果
            code = info.get('code', '')           # 番号
            title = info.get('title', '')          # 标题
            date_str = info.get('date', '')        # 发行日期
            actors = info.get('actors', [])        # 演员列表（Python list）
            # 修复：确保 duration 是整数类型
            duration = info.get('duration', 0)
            if isinstance(duration, str):
                duration = int(duration) if duration.isdigit() else 0
            elif not isinstance(duration, (int, float)):
                duration = 0
            duration = int(duration)  # 最终确保是整数           
            source_url = info.get('source_url', '')  # 来源URL

            # 获取视频文件实际大小（字节）
            try:
                file_size = os.path.getsize(video_path)
            except Exception as e:
                file_size = 0
                if self.debug:
                    self.debug.print(f"获取文件大小失败: {e}")

            self.debug.print(f"采集成功: {code}")
            self.debug.print(f"  标题: {title}")
            self.debug.print(f"  日期: {date_str}")
            self.debug.print(f"  演员: {', '.join(actors) if actors else '无'}")
            self.debug.print(f"  时长: {duration}秒")
            self.debug.print(f"  文件大小: {file_size} 字节 ({file_size/(1024*1024*1024):.2f} GB)")
            self.debug.print(f"  来源: {source_url}")

            # 保存到数据库
            if self._db_ready and self.db:
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()

                    # 1. 确保视频文件在数据库中有记录，并更新所有字段
                    cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (video_path,))
                    result = cursor.fetchone()

                    if not result:
                        # 视频不在数据库中，需要先创建记录
                        folder_path = os.path.normpath(os.path.dirname(video_path))
                        folder_name = os.path.basename(folder_path)

                        # 确保文件夹记录存在
                        cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder_path,))
                        folder_result = cursor.fetchone()
                        if folder_result:
                            folder_id = folder_result['folder_id']
                        else:
                            # 创建文件夹记录
                            parent = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
                            if parent:
                                parent = os.path.normpath(parent)
                            cursor.execute("""
                                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                                VALUES (?, ?, ?, datetime('now'))
                            """, (folder_path, folder_name, parent))
                            folder_id = cursor.lastrowid

                        # 创建视频文件记录（包含所有采集字段）
                        cursor.execute("""
                            INSERT INTO media_files 
                            (folder_id, file_path, file_name, file_type, file_size, 
                            video_code, video_title, release_date, duration, last_modified)
                            VALUES (?, ?, ?, 'video', ?, ?, ?, ?, ?, datetime('now'))
                        """, (folder_id, video_path, os.path.basename(video_path), 
                            file_size, code, title, date_str, duration))
                        file_id = cursor.lastrowid
                    else:
                        file_id = result['file_id']

                        # 更新视频信息（包含番号、标题、日期、时长、文件大小）
                        cursor.execute("""
                            UPDATE media_files 
                            SET video_code = ?, video_title = ?, release_date = ?, 
                                duration = ?, file_size = ?, last_modified = datetime('now')
                            WHERE file_id = ?
                        """, (code, title, date_str, duration, file_size, file_id))

                    # 2. 处理演员信息（核心逻辑）
                    if actors and isinstance(actors, list) and len(actors) > 0:
                        # 清除现有的演员关联
                        cursor.execute("DELETE FROM file_actors WHERE file_id = ?", (file_id,))

                        actor_names = []  # 用于日志记录成功处理的演员

                        for actor_name in actors:
                            if not actor_name or not isinstance(actor_name, str):
                                continue

                            actor_name = actor_name.strip()
                            if not actor_name:
                                continue

                            # 在演员表中查找是否已存在
                            cursor.execute("SELECT actor_id FROM actors WHERE actor_name = ?", (actor_name,))
                            actor_result = cursor.fetchone()

                            if actor_result:
                                actor_id = actor_result['actor_id']
                            else:
                                # 创建新演员记录
                                cursor.execute("""
                                    INSERT INTO actors (actor_name, created_date, updated_date)
                                    VALUES (?, datetime('now'), datetime('now'))
                                """, (actor_name,))
                                actor_id = cursor.lastrowid
                                self.debug.print(f"  创建新演员: {actor_name} (ID: {actor_id})")

                            # 建立演员与视频的关联
                            cursor.execute("""
                                INSERT OR IGNORE INTO file_actors (file_id, actor_id, bind_time)
                                VALUES (?, ?, datetime('now'))
                            """, (file_id, actor_id))
                            actor_names.append(actor_name)

                    conn.commit()
                    cursor.close()

                    # 刷新数据库缓存
                    self.db._load_all_to_cache_async()

                    # ========== 关键修复：标记为已联网同步并刷新图库标识 ==========
                    # 委托 GalleryViewer 更新同步标识
                    if hasattr(self, 'gallery') and self.gallery:
                        self.gallery.update_video_sync_status(video_path, True)
                        
                        # ========== 新增：使用番号直接更新图库中的视频信息 ==========
                        # 构建演员字符串
                        actors_str = '，'.join(actors) if actors else ''
                        
                        # 使用番号直接定位更新（O(1)复杂度）
                        if code:
                            self.gallery.update_item_by_code(code, {
                                'video_title': title,
                                'release_date': date_str,
                                'duration': duration,
                                'actors': actors_str,
                                'has_synced_video': True,
                            })
                            if self.debug:
                                self.debug.print(f"[采集更新] 已通过番号 {code} 更新图库UI")
                        else:
                            # 如果没有番号，通过路径更新
                            self.gallery.update_item_by_path(video_path, {
                                'video_title': title,
                                'release_date': date_str,
                                'duration': duration,
                                'actors': actors_str,
                                'has_synced_video': True,
                            })
                            if self.debug:
                                self.debug.print(f"[采集更新] 已通过路径更新图库UI: {os.path.basename(video_path)}")
                    
                    if self.debug:
                        self.debug.print(f"  已标记为联网同步视频: {video_path}")

                    # 如果有回调，调用回调（确保传递 source_url）
                    if callback:
                        # 确保 info 中包含 source_url
                        info['source_url'] = source_url
                        callback(True, info)
                    else:
                        # 更新UI显示
                        actor_count = len(actors) if actors else 0
                        self.root.after(0, lambda: [
                            self.status_label.config(
                                text=f"采集完成: {code} ({actor_count}位演员)", 
                                foreground="green"
                            ),
                            messagebox.showinfo(
                                "采集成功", 
                                f"视频信息已保存:\n"
                                f"番号: {code}\n"
                                f"标题: {title[:50]}{'...' if len(title) > 50 else ''}\n"
                                f"日期: {date_str}\n"
                                f"演员: {', '.join(actor_names if 'actor_names' in locals() else actors) if actors else '无'}\n"
                                f"时长: {int(duration) // 60}分{int(duration) % 60}秒\n"
                                f"大小: {file_size/(1024*1024*1024):.2f} GB\n"
                                f"来源: {source_url[:40]}..."
                            ),
                            # 刷新图库信息面板（如果当前显示的是这个视频）
                            self.gallery._update_info_panel(video_path, force_refresh=True) if hasattr(self, 'gallery') else None
                        ])

                except Exception as e:
                    error_msg = str(e)
                    self.debug.print(f"保存到数据库失败: {error_msg}")
                    import traceback
                    traceback.print_exc()
                    if callback:
                        callback(False, {'error': error_msg, 'source_url': source_url})
                    else:
                        self.root.after(0, lambda: [
                            self.status_label.config(text=f"保存失败: {error_msg}", foreground="red"),
                            messagebox.showerror("错误", f"保存视频信息失败:\n{error_msg}")
                        ])
            else:
                # 数据库未就绪，仅显示结果
                if callback:
                    callback(False, {'error': '数据库未就绪', 'source_url': source_url})
                else:
                    actors_str = '，'.join(actors) if actors else ''
                    self.root.after(0, lambda: [
                        self.status_label.config(text="数据库未就绪，无法保存", foreground="orange"),
                        messagebox.showwarning(
                            "提示", 
                            f"采集成功但数据库未连接:\n"
                            f"番号: {code}\n"
                            f"标题: {title}\n"
                            f"日期: {date_str}\n"
                            f"演员: {actors_str}\n"
                            f"时长: {duration}秒\n"
                            f"大小: {file_size/(1024*1024*1024):.2f} GB\n"
                            f"来源: {source_url}"
                        )
                    ])

        # 执行异步爬取
        self.crawler_interface.crawl_video_info(code, callback=on_crawl_result)

    def _create_gallery(self, parent):
        """创建图库查看器"""
        self.gallery = GalleryViewer(
            parent, self.config, None, self.video_player, self.image_viewer,
            self._on_image_select, self._on_image_double_click, self.debug,
            on_tags_changed=self._refresh_tag_links,
            get_browser_folder=lambda: self.current_folder,
            on_set_tag=self._set_tags_for_selected,
            on_relation_request=self._show_relation_suggestion_for_image,
            on_relation_removed=self._on_relation_removed,  # 新增：解除关联回调
            on_refresh_request=self._on_gallery_refresh_request  # 新增：统一刷新回调
        )
        
        # 设置视频信息采集回调
        self.gallery.on_collect_video_info = self._on_collect_video_info
        
        # 设置进入子文件夹回调（用于宫格双击）
        self.gallery.on_enter_sub_folder = self._enter_sub_folder

        
        # 绑定滚动保存（原有代码保持不变）
        self.gallery.bind_scroll_save(
            lambda y: self._save_current_location(scroll_y=y)
        )

    def _create_toolbar(self, parent):
        """创建工具栏 - 增加番号搜索框"""
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

        # ========== 新增：番号搜索框 ==========
        search_frame = ttk.Frame(toolbar)
        search_frame.pack(side=tk.RIGHT, padx=5)
        
        ttk.Label(search_frame, text="🔍 番号搜索:", font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=(0, 3))
        
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=25, font=("微软雅黑", 9))
        self.search_entry.pack(side=tk.LEFT, padx=(0, 3))
        self.search_entry.bind("<Return>", lambda e: self._on_search_code())
        
        self.search_btn = ttk.Button(search_frame, text="搜索", command=self._on_search_code, width=6)
        self.search_btn.pack(side=tk.LEFT, padx=(0, 3))
        
        self.search_clear_btn = ttk.Button(search_frame, text="清除", command=self._on_clear_search, width=6)
        self.search_clear_btn.pack(side=tk.LEFT)
        # =====================================

        self.status_label = ttk.Label(toolbar, text="初始化...", foreground="orange")
        self.status_label.pack(side=tk.RIGHT, padx=5)

    def _on_search_code(self):
        """番号搜索 - 使用数据库番号索引快速搜索"""
        search_text = self.search_var.get().strip()
        if not search_text:
            return
        
        if self.debug:
            self.debug.print(f"[番号搜索] 搜索: {search_text}")
        
        self.status_label.config(text=f"搜索中: {search_text}...", foreground="blue")
        
        # 在后台线程执行搜索
        threading.Thread(
            target=self._search_code_indexed,
            args=(search_text,),
            daemon=True
        ).start()

    def _search_code_indexed(self, search_text):
        """使用番号索引进行快速搜索"""
        try:
            # 获取当前设备的所有书签路径
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = device.get('bookmarks', [])
            bookmark_paths = set()
            for bm in bookmarks:
                path = bm.get('path', '')
                if path:
                    bookmark_paths.add(os.path.normpath(path))
            
            if not bookmark_paths:
                self.root.after(0, lambda: self.status_label.config(
                    text="当前设备没有书签", foreground="red"))
                return
            
            matched_items = []
            searched_folders = set()
            
            # ========== 使用数据库番号索引搜索 ==========
            if self._db_ready and self.db and self.db.is_cache_ready:
                if self.debug:
                    self.debug.print(f"[搜索] 使用番号索引...")
                
                db_results = self.db.search_by_code(search_text, device_id=self.config.device_id)
                
                for file_info in db_results:
                    # 防御性检查：确保 file_path 有效
                    video_path = file_info.get('file_path')
                    if not video_path or not isinstance(video_path, str):
                        if self.debug:
                            self.debug.print(f"[搜索] 跳过无效路径记录: {file_info.get('file_id')}")
                        continue
                    
                    folder_path = file_info.get('folder_path', '')
                    if not folder_path:
                        if self.debug:
                            self.debug.print(f"[搜索] 跳过无文件夹路径记录: {video_path}")
                        continue
                    
                    # 检查文件是否实际存在
                    if not os.path.exists(video_path):
                        if self.debug:
                            self.debug.print(f"[搜索] 跳过不存在的视频: {video_path}")
                        continue
                    
                    # 检查是否在书签范围内
                    folder_norm = os.path.normpath(folder_path)
                    in_bookmark = any(
                        folder_norm.startswith(os.path.normpath(bp))
                        for bp in bookmark_paths
                    )
                    if not in_bookmark:
                        continue
                    
                    # 去重：一个文件夹只显示一次
                    if folder_norm in searched_folders:
                        continue
                    searched_folders.add(folder_norm)
                    
                    # 获取文件夹下的图片
                    images = self.db.get_folder_images_from_cache(folder_path)
                    if not images:
                        images = self._get_images_from_fs(folder_path)
                    
                    matched_items.append({
                        'folder_path': folder_norm,
                        'folder_name': os.path.basename(folder_norm),
                        'video_path': video_path,
                        'video_name': os.path.basename(video_path),
                        'match_reason': f"番号索引: {search_text}",
                        'images': images,
                        'file_info': file_info
                    })
                
                if self.debug:
                    self.debug.print(f"[搜索] 索引找到 {len(matched_items)} 个")
            
            # ========== 后备：JSON缓存搜索 ==========
            if not matched_items and hasattr(self, 'folder_cache') and self.folder_cache:
                if self.debug:
                    self.debug.print(f"[搜索] 索引未命中，回退到JSON缓存...")
                # ... 后备逻辑 ...
            
            # ========== 最后后备：文件系统扫描 ==========
            if not matched_items:
                if self.debug:
                    self.debug.print(f"[搜索] 回退到文件系统扫描...")
                # ... 后备逻辑 ...
            
            if self.debug:
                self.debug.print(f"[番号搜索] 总计找到 {len(matched_items)} 个有效匹配")
            
            self.root.after(0, lambda: self._on_search_complete(matched_items, search_text))
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"[番号搜索失败] {e}")
                import traceback
                traceback.print_exc()
            self.root.after(0, lambda: self.status_label.config(
                text=f"搜索失败: {e}", foreground="red"))
    
    def _on_search_complete(self, matched_items, search_text):
        """搜索完成，显示结果 - 适配字典结构"""
        if not matched_items:
            self.status_label.config(text=f"未找到匹配: {search_text}", foreground="orange")
            messagebox.showinfo("搜索结果", f"未找到与 '{search_text}' 匹配的视频")
            return
        
        self.status_label.config(text=f"搜索完成: {search_text} ({len(matched_items)}个结果)", foreground="green")
        
        # 清除标签筛选状态
        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()
        
        # 构建图库加载数据
        processed_items = []
        
        for match in matched_items:
            # 防御性检查必要字段
            folder_path = match.get('folder_path')
            video_path = match.get('video_path')
            
            if not folder_path or not video_path:
                if self.debug:
                    self.debug.print(f"[搜索显示] 跳过无效匹配项: folder={folder_path}, video={video_path}")
                continue
            
            if not os.path.exists(video_path):
                if self.debug:
                    self.debug.print(f"[搜索显示] 跳过不存在的视频: {video_path}")
                continue
            
            folder_name = match.get('folder_name') or os.path.basename(folder_path)
            images = match.get('images', [])
            file_info = match.get('file_info', {})
            
            video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
            main_img = None
            alternates = []
            
            # 优先找同名图片
            for img_path in images:
                if not img_path or not os.path.exists(img_path):
                    continue
                try:
                    img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
                    if img_basename == video_basename:
                        main_img = img_path
                    else:
                        alternates.append(img_path)
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"[搜索显示] 处理图片路径出错: {img_path}, {e}")
                    continue
            
            # 找通用封面
            if not main_img:
                cover_names = ['cover', 'folder', 'poster', 'thumb', 'preview']
                for img_path in images:
                    if not img_path or not os.path.exists(img_path):
                        continue
                    try:
                        img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
                        if img_basename in cover_names:
                            main_img = img_path
                            break
                    except:
                        continue
            
            # 用第一张图
            if not main_img and images:
                for img_path in images:
                    if img_path and os.path.exists(img_path):
                        main_img = img_path
                        break
            
            # 构建item
            if main_img:
                try:
                    is_dedicated = (os.path.splitext(os.path.basename(main_img))[0].lower() == video_basename)
                except:
                    is_dedicated = False
                
                item = {
                    'folder_name': folder_name,
                    'folder_path': folder_path,
                    'main_image': main_img,
                    'video_path': video_path,
                    'alternates': alternates,
                    'match_type': 'dedicated' if is_dedicated else 'cover',
                    'file_type': 'image',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
            else:
                item = {
                    'folder_name': folder_name,
                    'folder_path': folder_path,
                    'main_image': '__VIDEO_PLACEHOLDER__',
                    'video_path': video_path,
                    'file_name': os.path.basename(video_path),
                    'match_type': 'placeholder',
                    'file_type': 'video_placeholder',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
            
            # 注入数据库采集信息
            if file_info and isinstance(file_info, dict):
                item['video_title'] = file_info.get('video_title', '')
                item['video_code'] = file_info.get('video_code', '')
                item['release_date'] = file_info.get('release_date', '')
                item['duration'] = file_info.get('duration', 0)
                item['file_size'] = file_info.get('size', 0) or file_info.get('file_size', 0)
                item['alias_count'] = file_info.get('alias_count', 0)
            
            processed_items.append(item)
        
        # ========== 关键修改：转换为字典结构 ==========
        processed_dict = {}
        for item in processed_items:
            # 生成唯一key
            video_code = item.get('video_code')
            if not video_code:
                # 从视频路径提取番号
                video_path = item.get('video_path')
                if video_path:
                    from core import extract_code_from_text
                    code_info = extract_code_from_text(os.path.basename(video_path))
                    if code_info:
                        video_code = code_info['canonical']
                    else:
                        # 使用路径hash作为备用key
                        import hashlib
                        video_code = hashlib.md5(video_path.encode()).hexdigest()[:12]
                else:
                    # 纯图片项使用图片路径hash
                    img_path = item.get('main_image', '')
                    if img_path and img_path != '__VIDEO_PLACEHOLDER__':
                        import hashlib
                        video_code = hashlib.md5(img_path.encode()).hexdigest()[:12]
                    else:
                        video_code = f"item_{len(processed_dict)}"
                item['video_code'] = video_code
            
            # 避免重复key
            if video_code in processed_dict:
                unique_key = f"{video_code}_{item.get('folder_path', '')}"
                processed_dict[unique_key] = item
            else:
                processed_dict[video_code] = item
        
        # 显示结果
        if processed_dict:
            processed_items_list = list(processed_dict.values())
            self.current_folder = f"[搜索: {search_text}]"
            self.path_label.config(text=f"搜索结果: {search_text} ({len(processed_dict)}个)")
            self.copy_btn.config(state="disabled")
            
            if self.gallery_loader:
                self.gallery_loader._last_loaded_folder = None
            
            if self.gallery:
                self.gallery.clear()
                # 使用带字典的新方法加载
                self.gallery.load_items_with_dict(
                    processed_items_list, processed_dict, self._update_status
                )
            
            if self.debug:
                self.debug.print(f"[番号搜索] 已显示 {len(processed_dict)} 个结果")
        else:
            self.status_label.config(text=f"搜索结果无效: {search_text}", foreground="red")
            messagebox.showwarning("搜索结果", 
                f"数据库找到 {len(matched_items)} 个记录，但视频文件均无效或不存在。\n"
                f"建议执行【文件夹】→【完整同步所有书签】更新数据库。")

    def _get_images_from_fs(self, folder_path):
        """从文件系统获取文件夹下的图片"""
        from core import PathUtils
        images = []
        try:
            for entry in os.scandir(folder_path):
                if entry.is_file() and PathUtils.is_image_file(entry.path):
                    images.append(entry.path)
        except:
            pass
        return images
    
    def _search_json_cache(self, search_text, bookmark_paths):
        """JSON缓存搜索（后备）"""
        # 保留原有逻辑，略...
        return []
    
    def _search_fs_for_code(self, folder_path, search_text):
        """文件系统搜索（最后后备）"""
        # 保留原有逻辑，略...
        return []

    def _search_code_thread_fast(self, search_text):
        """快速搜索 - 优先数据库缓存，回退JSON缓存"""
        try:
            from core import extract_code_from_text, get_pattern_matcher
            
            search_code = extract_code_from_text(search_text)
            matcher = get_pattern_matcher()
            search_lower = search_text.lower()
            
            # 获取当前设备的所有书签路径
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = device.get('bookmarks', [])
            bookmark_paths = set()
            for bm in bookmarks:
                path = bm.get('path', '')
                if path:
                    bookmark_paths.add(os.path.normpath(path))
            
            if not bookmark_paths:
                self.root.after(0, lambda: self.status_label.config(
                    text="当前设备没有书签", foreground="red"))
                return
            
            matched_items = []
            searched_paths = set()
            
            # ========== 第一层：数据库内存缓存搜索 ==========
            if self._db_ready and self.db and self.db.is_cache_ready:
                if self.debug:
                    self.debug.print(f"[搜索] 从数据库缓存搜索...")
                
                with self.db._cache_lock:
                    files_cache = self.db._cache.get('files', {})
                    
                    for file_path, file_info in files_cache.items():
                        if file_info.get('type') != 'video':
                            continue
                        
                        folder_path = file_info.get('folder_path', '')
                        if not folder_path:
                            continue
                        
                        # 检查是否在书签范围内
                        in_bookmark = any(
                            os.path.normpath(folder_path).startswith(os.path.normpath(bp))
                            for bp in bookmark_paths
                        )
                        if not in_bookmark:
                            continue
                        
                        is_match, match_reason = self._check_video_match(
                            file_path, file_info, search_text, search_code, matcher, search_lower
                        )
                        
                        if is_match:
                            folder_path_norm = os.path.normpath(folder_path)
                            if folder_path_norm not in searched_paths:
                                searched_paths.add(folder_path_norm)
                                images = self._get_images_from_cache(folder_path_norm)
                                matched_items.append({
                                    'folder_path': folder_path_norm,
                                    'folder_name': os.path.basename(folder_path_norm),
                                    'video_path': file_path,
                                    'video_name': os.path.basename(file_path),
                                    'match_reason': match_reason,
                                    'images': images,
                                    'source': 'db_cache'
                                })
                
                if self.debug:
                    self.debug.print(f"[搜索] 数据库缓存找到 {len(matched_items)} 个")
            
            # ========== 第二层：JSON文件夹缓存搜索 ==========
            if hasattr(self, 'folder_cache') and self.folder_cache:
                if self.debug:
                    self.debug.print(f"[搜索] 从JSON缓存搜索...")
                
                json_matches = 0
                for bm_path in bookmark_paths:
                    if not os.path.exists(bm_path):
                        continue
                    
                    for dirpath, dirnames, _ in os.walk(bm_path):
                        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                        
                        folder_norm = os.path.normpath(dirpath)
                        if folder_norm in searched_paths:
                            continue
                        
                        cache_data = self.folder_cache.load_folder_cache(folder_norm)
                        if not cache_data:
                            continue
                        
                        files_info = cache_data.get('files_info', [])
                        folder_matched = False
                        match_reason = ""
                        images = []
                        video_path = None
                        
                        for f in files_info:
                            f_type = f.get('file_type', '')
                            f_path = f.get('file_path', '')
                            
                            if f_type == 'image' and f_path and os.path.exists(f_path):
                                images.append(f_path)
                            
                            if f_type in ('video', 'video_placeholder') or f.get('video_path'):
                                vp = f.get('video_path') or f_path
                                if vp and os.path.exists(vp):
                                    is_match = False
                                    v_name = os.path.basename(vp)
                                    v_code = extract_code_from_text(v_name)
                                    f_code = f.get('video_code', '')
                                    
                                    if v_code and search_code:
                                        if matcher.is_strict_match(v_code, search_code) >= 1:
                                            is_match = True
                                            match_reason = f"缓存匹配: {v_code.get('canonical', '')}"
                                    elif f_code and search_code:
                                        f_code_parsed = extract_code_from_text(f_code)
                                        if f_code_parsed and matcher.is_strict_match(f_code_parsed, search_code) >= 1:
                                            is_match = True
                                            match_reason = f"缓存番号: {f_code}"
                                    elif search_lower in v_name.lower():
                                        is_match = True
                                        match_reason = f"文件名包含: {search_text}"
                                    
                                    if is_match:
                                        video_path = vp
                                        folder_matched = True
                        
                        if folder_matched and video_path:
                            searched_paths.add(folder_norm)
                            matched_items.append({
                                'folder_path': folder_norm,
                                'folder_name': os.path.basename(folder_norm),
                                'video_path': video_path,
                                'video_name': os.path.basename(video_path),
                                'match_reason': match_reason,
                                'images': images,
                                'source': 'json_cache'
                            })
                            json_matches += 1
                
                if self.debug:
                    self.debug.print(f"[搜索] JSON缓存新增 {json_matches} 个")
            
            # ========== 第三层：文件系统扫描（后备）==========
            if not matched_items and not (self._db_ready and self.db and self.db.is_cache_ready):
                if self.debug:
                    self.debug.print(f"[搜索] 缓存未就绪，回退到文件系统扫描...")
                
                for bm_path in bookmark_paths:
                    if not os.path.exists(bm_path):
                        continue
                    self._scan_folder_for_code_search_fs(
                        bm_path, search_text, search_code, matcher, search_lower,
                        matched_items, searched_paths
                    )
            
            if self.debug:
                self.debug.print(f"[番号搜索] 总计找到 {len(matched_items)} 个匹配")
            
            self.root.after(0, lambda: self._on_search_complete(matched_items, search_text))
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"[番号搜索失败] {e}")
                import traceback
                traceback.print_exc()
            self.root.after(0, lambda: self.status_label.config(
                text=f"搜索失败: {e}", foreground="red"))
            
    def _check_video_match(self, video_path, file_info, search_text, search_code, matcher, search_lower):
        """多维度匹配检查"""
        from core import extract_code_from_text
        
        video_name = os.path.basename(video_path)
        video_code = extract_code_from_text(video_name)
        
        # 视频文件名番号匹配
        if video_code and search_code:
            match_level = matcher.is_strict_match(video_code, search_code)
            if match_level >= 1:
                return True, f"视频名匹配: {video_code.get('canonical', '')}"
        
        # 数据库video_code匹配
        db_video_code = file_info.get('video_code', '')
        if db_video_code:
            db_code = extract_code_from_text(db_video_code)
            if db_code and search_code:
                match_level = matcher.is_strict_match(db_code, search_code)
                if match_level >= 1:
                    return True, f"数据库番号: {db_video_code}"
            elif db_video_code.lower() == search_text.lower():
                return True, f"数据库番号完全匹配"
        
        # 文件夹名匹配
        folder_path = file_info.get('folder_path', '')
        folder_name = os.path.basename(folder_path)
        folder_code = extract_code_from_text(folder_name)
        if folder_code and search_code:
            match_level = matcher.is_strict_match(folder_code, search_code)
            if match_level >= 1:
                return True, f"文件夹名匹配: {folder_code.get('canonical', '')}"
        
        # 简单包含匹配
        if search_lower in video_name.lower():
            return True, f"文件名包含: {search_text}"
        if search_lower in folder_name.lower():
            return True, f"文件夹名包含: {search_text}"
        
        return False, ""
    
    def _get_images_from_cache(self, folder_path):
        """从缓存获取文件夹下的图片列表"""
        images = []
        
        # 优先查JSON缓存
        if hasattr(self, 'folder_cache') and self.folder_cache:
            cache_data = self.folder_cache.load_folder_cache(folder_path)
            if cache_data:
                files_info = cache_data.get('files_info', [])
                for f in files_info:
                    if f.get('file_type') == 'image':
                        img_path = f.get('file_path')
                        if img_path and os.path.exists(img_path):
                            images.append(img_path)
                if images:
                    return images
        
        # 回退查数据库缓存
        if self._db_ready and self.db:
            with self.db._cache_lock:
                for fp, info in self.db._cache.get('files', {}).items():
                    if info.get('folder_path') == folder_path and info.get('type') == 'image':
                        if os.path.exists(fp):
                            images.append(fp)
        
        # 最后查文件系统
        if not images:
            from core import PathUtils
            try:
                for entry in os.scandir(folder_path):
                    if entry.is_file() and PathUtils.is_image_file(entry.path):
                        images.append(entry.path)
            except:
                pass
        
        return images
  
    def _search_code_thread(self, search_text):
        """后台搜索线程"""
        try:
            from core import extract_code_from_text, get_pattern_matcher
            
            # 解析搜索词
            search_code = extract_code_from_text(search_text)
            matcher = get_pattern_matcher()
            
            # 获取当前设备的所有书签
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = device.get('bookmarks', [])
            
            if not bookmarks:
                self.root.after(0, lambda: self.status_label.config(
                    text="当前设备没有书签", foreground="red"))
                return
            
            # 收集所有匹配结果
            matched_items = []  # [(folder_path, video_path, match_info), ...]
            
            for bm in bookmarks:
                bm_path = bm.get('path', '')
                if not bm_path or not os.path.exists(bm_path):
                    continue
                
                # 递归扫描书签下的所有文件夹
                self._scan_folder_for_code_search(
                    bm_path, search_text, search_code, matcher, matched_items
                )
            
            if self.debug:
                self.debug.print(f"[番号搜索] 找到 {len(matched_items)} 个匹配")
            
            # 回到主线程更新UI
            self.root.after(0, lambda: self._on_search_complete(matched_items, search_text))
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"[番号搜索失败] {e}")
                import traceback
                traceback.print_exc()
            self.root.after(0, lambda: self.status_label.config(
                text=f"搜索失败: {e}", foreground="red"))
    
    def _scan_folder_for_code_search(self, folder_path, search_text, search_code, matcher, matched_items):
        """递归扫描文件夹，查找匹配的视频"""
        from core import PathUtils, extract_code_from_text
        
        try:
            entries = list(os.scandir(folder_path))
        except:
            return
        
        videos = []
        images = []
        sub_folders = []
        
        for entry in entries:
            if entry.is_dir():
                sub_folders.append(entry.path)
            elif entry.is_file():
                if PathUtils.is_video_file(entry.path):
                    videos.append(entry)
                elif PathUtils.is_image_file(entry.path):
                    images.append(entry)
        
        # 检查当前文件夹是否匹配
        folder_name = os.path.basename(folder_path)
        folder_code = extract_code_from_text(folder_name)
        
        # 匹配逻辑：
        # 1. 视频文件名匹配
        # 2. 文件夹名匹配
        # 3. 数据库中的video_code匹配（如果数据库就绪）
        
        for video in videos:
            video_code = extract_code_from_text(video.name)
            
            # 检查是否匹配
            is_match = False
            match_reason = ""
            
            # 方式1：视频文件名匹配
            if video_code and search_code:
                match_level = matcher.is_strict_match(video_code, search_code)
                if match_level >= 1:
                    is_match = True
                    match_reason = f"视频名匹配: {video_code.get('canonical', '')}"
            elif video_code and video_code.get('full', '').lower() == search_text.lower():
                is_match = True
                match_reason = f"视频名完全匹配"
            
            # 方式2：文件夹名匹配
            if not is_match and folder_code and search_code:
                match_level = matcher.is_strict_match(folder_code, search_code)
                if match_level >= 1:
                    is_match = True
                    match_reason = f"文件夹名匹配: {folder_code.get('canonical', '')}"
            
            # 方式3：数据库video_code匹配
            if not is_match and self._db_ready and self.db:
                try:
                    db_file = self.db.get_file_by_path(video.path)
                    if db_file and db_file.get('video_code'):
                        db_code = extract_code_from_text(db_file['video_code'])
                        if db_code and search_code:
                            match_level = matcher.is_strict_match(db_code, search_code)
                            if match_level >= 1:
                                is_match = True
                                match_reason = f"数据库番号匹配: {db_file['video_code']}"
                        elif db_file['video_code'].lower() == search_text.lower():
                            is_match = True
                            match_reason = f"数据库番号完全匹配"
                except Exception:
                    pass
            
            # 方式4：简单包含匹配（作为后备）
            if not is_match:
                search_lower = search_text.lower()
                if search_lower in video.name.lower():
                    is_match = True
                    match_reason = f"文件名包含: {search_text}"
                elif search_lower in folder_name.lower():
                    is_match = True
                    match_reason = f"文件夹名包含: {search_text}"
            
            if is_match:
                matched_items.append({
                    'folder_path': folder_path,
                    'folder_name': folder_name,
                    'video_path': video.path,
                    'video_name': video.name,
                    'match_reason': match_reason,
                    'images': images,
                    'videos': videos
                })
        
        # 递归搜索子文件夹
        for sub_path in sub_folders:
            self._scan_folder_for_code_search(sub_path, search_text, search_code, matcher, matched_items)
    
    def _on_search_complete(self, matched_items, search_text):
        """搜索完成，显示结果"""
        if not matched_items:
            self.status_label.config(text=f"未找到匹配: {search_text}", foreground="orange")
            messagebox.showinfo("搜索结果", f"未找到与 '{search_text}' 匹配的视频")
            return
        
        self.status_label.config(text=f"搜索完成: {search_text} ({len(matched_items)}个结果)", foreground="green")
        
        # 清除标签筛选状态
        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()
        
        # 构建图库加载数据
        processed_items = []
        
        for match in matched_items:
            folder_path = match['folder_path']
            folder_name = match['folder_name']
            video_path = match['video_path']
            images = match.get('images', [])
            file_info = match.get('file_info', {})  # 数据库中的完整信息
            
            video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
            main_img = None
            alternates = []
            
            # 优先找同名图片
            for img_path in images:
                if not os.path.exists(img_path):
                    continue
                img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
                if img_basename == video_basename:
                    main_img = img_path
                else:
                    alternates.append(img_path)
            
            # 找通用封面
            if not main_img:
                cover_names = ['cover', 'folder', 'poster', 'thumb', 'preview']
                for img_path in images:
                    if not os.path.exists(img_path):
                        continue
                    img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
                    if img_basename in cover_names:
                        main_img = img_path
                        break
            
            # 用第一张图
            if not main_img and images:
                for img_path in images:
                    if os.path.exists(img_path):
                        main_img = img_path
                        break
            
            # 构建item（复用 gallery_loader 格式）
            if main_img:
                is_dedicated = (os.path.splitext(os.path.basename(main_img))[0].lower() == video_basename)
                item = {
                    'folder_name': folder_name,
                    'folder_path': folder_path,
                    'main_image': main_img,
                    'video_path': video_path,
                    'alternates': alternates,
                    'match_type': 'dedicated' if is_dedicated else 'cover',
                    'file_type': 'image',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
            else:
                item = {
                    'folder_name': folder_name,
                    'folder_path': folder_path,
                    'main_image': '__VIDEO_PLACEHOLDER__',
                    'video_path': video_path,
                    'file_name': os.path.basename(video_path),
                    'match_type': 'placeholder',
                    'file_type': 'video_placeholder',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
            
            # 直接注入数据库采集信息，避免再次查询
            if file_info:
                item['video_title'] = file_info.get('video_title', '')
                item['video_code'] = file_info.get('video_code', '')
                item['release_date'] = file_info.get('release_date', '')
                item['duration'] = file_info.get('duration', 0)
                item['file_size'] = file_info.get('size', 0)
                item['alias_count'] = file_info.get('alias_count', 0)
            
            processed_items.append(item)
        
        # 显示结果
        if processed_items:
            self.current_folder = f"[搜索: {search_text}]"
            self.path_label.config(text=f"搜索结果: {search_text} ({len(processed_items)}个)")
            self.copy_btn.config(state="disabled")
            
            if self.gallery_loader:
                self.gallery_loader._last_loaded_folder = None
            
            if self.gallery:
                self.gallery.clear()
                self.gallery.load_items_new(processed_items, self._update_status)
            
            if self.debug:
                self.debug.print(f"[番号搜索] 已显示 {len(processed_items)} 个结果")

    def _on_clear_search(self):
        """清除搜索，恢复浏览"""
        self.search_var.set("")
        
        if self._current_bookmark_id:
            bm_state = self.config.get_bookmark_state(self._current_bookmark_id)
            last_folder = bm_state.get('last_folder') if bm_state else None
            
            if last_folder and os.path.exists(last_folder) and not str(last_folder).startswith('['):
                self.current_folder = last_folder
                self.path_label.config(text=f"浏览: {os.path.basename(last_folder)}")
                self.copy_btn.config(state="normal")
                
                scroll_y = self.gallery.get_scroll_y() if hasattr(self.gallery, 'get_scroll_y') else None
                self._load_gallery_unified(last_folder, scroll_y, source="清除搜索")
                
                if self.tree_explorer:
                    self.tree_explorer.select_path(last_folder)
                self.status_label.config(text="已恢复浏览", foreground="green")
            else:
                for dev_id, device in self.config.devices.items():
                    for bm in device.get('bookmarks', []):
                        if bm['id'] == self._current_bookmark_id:
                            self._on_bookmark_select(bm['path'])
                            break
        else:
            if self.gallery:
                self.gallery.clear()
            self.current_folder = None
            self.status_label.config(text="请选择书签", foreground="orange")

    def _scan_folder_for_code_search_fs(self, folder_path, search_text, search_code, matcher,
                                        search_lower, matched_items, searched_paths):
        """文件系统扫描（后备方案）"""
        from core import PathUtils, extract_code_from_text
        
        try:
            entries = list(os.scandir(folder_path))
        except:
            return
        
        videos = []
        images = []
        sub_folders = []
        
        for entry in entries:
            if entry.is_dir():
                sub_folders.append(entry.path)
            elif entry.is_file():
                if PathUtils.is_video_file(entry.path):
                    videos.append(entry)
                elif PathUtils.is_image_file(entry.path):
                    images.append(entry.path)
        
        folder_name = os.path.basename(folder_path)
        folder_code = extract_code_from_text(folder_name)
        folder_norm = os.path.normpath(folder_path)
        
        if folder_norm not in searched_paths:
            for video in videos:
                video_code = extract_code_from_text(video.name)
                
                is_match = False
                match_reason = ""
                
                if video_code and search_code:
                    if matcher.is_strict_match(video_code, search_code) >= 1:
                        is_match = True
                        match_reason = f"FS匹配: {video_code.get('canonical', '')}"
                elif search_lower in video.name.lower():
                    is_match = True
                    match_reason = f"FS包含: {search_text}"
                elif folder_code and search_code:
                    if matcher.is_strict_match(folder_code, search_code) >= 1:
                        is_match = True
                        match_reason = f"FS文件夹: {folder_code.get('canonical', '')}"
                
                if is_match:
                    searched_paths.add(folder_norm)
                    matched_items.append({
                        'folder_path': folder_norm,
                        'folder_name': folder_name,
                        'video_path': video.path,
                        'video_name': video.name,
                        'match_reason': match_reason,
                        'images': images,
                        'source': 'fs_scan'
                    })
                    break
        
        for sub_path in sub_folders:
            self._scan_folder_for_code_search_fs(
                sub_path, search_text, search_code, matcher, search_lower,
                matched_items, searched_paths
            )
    
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
        """创建操作日志界面 - 修复日志重复输出问题"""
        # ========== 关键修复：防止重复初始化导致日志重复打印 ==========
        if hasattr(self, '_log_ui_initialized') and self._log_ui_initialized:
            if self.debug:
                self.debug.print("[日志UI] 已初始化，跳过重复创建")
            return
        self._log_ui_initialized = True
        
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
        self.log_text.tag_configure("startup", foreground="#c586c0")  # 启动日志专用颜色（紫色）
        
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
        
        # 设置日志回调（确保只设置一次，防止重复发送历史日志）
        if not hasattr(self, '_log_callback_set'):
            self.debug.set_log_callback(self._on_log_message)
            self._log_callback_set = True
            if self.debug:
                self.debug.print("[日志UI] 日志回调已设置，历史日志已发送到UI")
        else:
            if self.debug:
                self.debug.print("[日志UI] 警告：日志回调已被设置过，跳过")
        
        # ========== 关键修复：处理 debug 初始化前的日志（仅 A01 阶段）==========
        if hasattr(self, '_pending_startup_logs') and self._pending_startup_logs:
            # 注意：这里不要再次调用 debug.print，直接添加到 UI
            for timestamp, stage_id, message in self._pending_startup_logs:
                log_msg = f"[{timestamp}] [启动] {stage_id}: {message}"
                self._on_log_message(log_msg)
            self._pending_startup_logs.clear()
            if self.debug:
                self.debug.print("[日志UI] 已处理 pre-debug 阶段的启动日志")



    # ==================== 标签页切换 ====================

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
            elif tab_text == "爬虫配置":
                self._init_crawler_manager()
        except Exception as e:
            self.debug.print(f"标签切换错误: {e}")

    def _init_tag_manager(self):
        """初始化标签管理器（修复控件失效问题）"""
        if not ENABLE_TAG_MANAGER:
            return
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

    def _init_crawler_manager(self):
        """初始化爬虫管理器 - 使用 crawler_gui.py 的界面（双栏布局）"""
        try:
            # 如果已存在实例，检查控件是否仍然有效
            if self.crawler_manager is not None:
                try:
                    # 检查主框架是否仍然存在
                    if hasattr(self.crawler_manager, 'parent') and self.crawler_manager.parent.winfo_exists():
                        # 已经存在，不需要重新创建
                        return
                    else:
                        self.crawler_manager = None
                except tk.TclError:
                    self.crawler_manager = None
            
            # 需要创建或重建
            if self.crawler_manager is None:
                from crawler_gui import CrawlerConfigApp
                # 确保标签页是空的
                for widget in self.crawler_tab.winfo_children():
                    widget.destroy()
                
                # 使用 crawler_gui.py 的 CrawlerConfigApp，作为子组件嵌入标签页
                self.crawler_manager = CrawlerConfigApp(
                    self.crawler_tab, 
                    config_file=self.config.config_file,
                    is_standalone=False
                )
                
        except Exception as e:
            self.debug.print(f"爬虫管理器初始化失败: {e}")
            import traceback
            traceback.print_exc()
            try:
                for widget in self.crawler_tab.winfo_children():
                    widget.destroy()
                error_frame = ttk.Frame(self.crawler_tab)
                error_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
                tk.Label(error_frame, text="爬虫管理初始化失败", 
                        fg="red", font=("微软雅黑", 12, "bold")).pack(pady=10)
                tk.Label(error_frame, text=str(e), 
                        fg="red", wraplength=500, justify=tk.LEFT).pack(pady=5)
                tk.Button(error_frame, text="重试", 
                        command=self._init_crawler_manager).pack(pady=10)
            except:
                pass

    # ==================== 事件处理 ====================

    def _on_bookmark_select(self, folder_path):
        """书签选择处理 - 增强：保存旧书签的完整树状态"""
        # 【关键修复】启动恢复期间，禁止保存状态
        is_user_click = not self._is_startup_restoring
        
        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()

        if not self.bookmark_manager:
            return
        selected_bm = self.bookmark_manager.get_selected_bookmark_id()
        if not selected_bm:
            return

        new_bm_id, new_bm_path, new_bm_name = selected_bm

        # 保存旧书签状态（必须在 load_bookmark 清除旧树之前完成）
        # 【关键修复】只有在非启动恢复期间才保存旧书签状态
        if is_user_click and self._current_bookmark_id and self._current_bookmark_id != new_bm_id:
            try:
                gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                
                # 【关键】保存旧书签的完整树状态
                tree_state = {}
                if self.tree_explorer:
                    tree_state = self.tree_explorer.get_state()
                
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    self.current_folder,
                    tree_state,
                    gallery_scroll
                )
                if self.debug:
                    self.debug.print(f"[书签切换] 已保存旧书签状态: {self._current_bookmark_id}, "
                                   f"展开节点={len(tree_state.get('expanded_paths', []))}个")
            except Exception as e:
                if self.debug:
                    self.debug.print(f"保存旧书签状态失败: {e}")

        self._current_bookmark_id = new_bm_id
        self.current_root = new_bm_path
        self.current_folder = new_bm_path
        self.path_label.config(text=f"根目录: {new_bm_name}")
        self.copy_btn.config(state="normal")

        self._update_path_entries(img_path="", video_path="")

        # 加载新书签的树（会自动读取 tree_{device_id}_{bookmark_id}.json 或后台扫描）
        if self.tree_explorer:
            self.tree_explorer.load_bookmark(
                new_bm_path,
                device_id=self.config.device_id,
                bookmark_id=new_bm_id
            )

        self._load_gallery_unified(new_bm_path, None, source="书签选择")

        # 【关键修复】只有在非启动恢复期间才保存当前位置
        if is_user_click:
            self._save_current_location(folder=new_bm_path)

    def _delayed_bookmark_refresh(self, folder_path):
        """书签切换后的延迟刷新"""
        if self.gallery:
            self.gallery.refresh_sync_and_info(
                folder_path,
                db_ready=self._db_ready,
                db_manager=self.db
            )
            self.gallery.ensure_all_video_info_loaded(
                folder_path,
                db_ready=self._db_ready,
                db_manager=self.db,
                thumbnail_width=self.config.thumbnail_width
            )

    def _on_tree_select(self, folder_path, folder_name, click_source=None):
        """树节点选择处理"""
        # 【新增】启动恢复期间，图库已由 on_tree_loaded 唯一加载，拦截所有select事件防止重复加载
        if self._is_startup_restoring and self._startup_gallery_loaded:
            if self.debug:
                self.debug.print(f"[树选择] 启动恢复期间图库已加载，跳过重复加载: {folder_path}")
            return

        # 启动恢复期间，禁止保存状态
        if self._is_startup_restoring and click_source == 'user':
            if self.debug:
                self.debug.print(f"[树选择] 启动恢复期间，跳过保存: {folder_path}")
            # 但仍需加载图库
            self._update_tree_select_ui(folder_path, folder_name)
            return

        if self._selected_tag_links:
            self._selected_tag_links.clear()
            self._refresh_tag_links()

        # 更新UI
        self._update_tree_select_ui(folder_path, folder_name)

        # 只有用户主动点击时才保存状态到 config.json
        if click_source == 'user' and not self._is_startup_restoring:
            self._save_tree_state_on_user_click(folder_path, folder_name)


    def _save_tree_state_on_user_click(self, folder_path, folder_name):
        """用户主动点击时保存树状态和最后打开的文件夹"""
        if self._current_bookmark_id:
            try:
                gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0

                # 保存完整的树状态
                tree_state = {}
                if self.tree_explorer:
                    tree_state = self.tree_explorer.get_state()

                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    folder_path,
                    tree_state,
                    gallery_scroll
                )

                # 【关键】保存 last_folder 到 config.json 顶层
                self.config.last_folder = folder_path
                self.config.save_config()

                if self.debug:
                    self.debug.print(f"[用户点击] 已保存状态: {folder_path}, "
                                f"展开节点={len(tree_state.get('expanded_paths', []))}个")
            except Exception as e:
                if self.debug:
                    self.debug.print(f"保存书签状态失败: {e}")

    def _update_tree_select_ui(self, folder_path, folder_name):
        """树选择时的UI更新"""
        self.current_folder = folder_path
        self.path_label.config(text=f"浏览: {folder_name}")
        self.copy_btn.config(state="normal")

        if hasattr(self, 'gallery') and self.gallery:
            self.gallery._update_info_panel(None)
            self.gallery.clear_selection()
            self._update_path_entries(img_path="", video_path="")

        scroll_y = self.gallery.get_scroll_y() if hasattr(self.gallery, 'get_scroll_y') else None
        self._load_gallery_unified(folder_path, scroll_y, force_db_sync=True, source="树切换")

        if self._db_ready and self.db and self.db.is_ready:
            threading.Thread(
                target=self._sync_folder_to_db,
                args=(folder_path,),
                daemon=True
            ).start()

    def _build_tree_from_cache(self, cache_data):
        """根据缓存数据一次性构建完整树形结构（按路径深度排序，确保先父后子）"""
        nodes = cache_data.get('nodes', {})
        self.folder_cache = cache_data
        if not nodes:
            return

        # 关键修复：临时禁用选择事件回调，避免构建过程中触发保存
        original_on_select = self.on_select
        self.on_select = None

        try:
            # 按路径层级深度排序，保证父节点先于子节点插入
            sorted_paths = sorted(nodes.keys(), key=lambda p: p.count(os.sep))

            for path in sorted_paths:
                path = os.path.normpath(path)
                
                # 跳过根路径
                if path == self.root_path:
                    continue
                
                # 关键修复：若该路径已存在于树中，不再重复插入
                if path in self._path_to_node:
                    continue

                node_data = nodes.get(path, {})
                parent_path = os.path.dirname(path)
                parent_path = os.path.normpath(parent_path)

                # 防止 UNC 根目录等 parent 等于自身的情况
                if parent_path == path:
                    continue

                parent_node = self._path_to_node.get(parent_path)
                if not parent_node:
                    continue

                name = node_data.get('name', os.path.basename(path))
                node_id = self.tree.insert(parent_node, "end", text=name, values=[path])
                self._path_to_node[path] = node_id

            # 恢复展开状态
            for exp_path in cache_data.get('expanded_paths', []):
                if not exp_path:
                    continue
                exp_path = os.path.normpath(exp_path)
                node = self._path_to_node.get(exp_path)
                if node:
                    self.tree.item(node, open=True)

            # 恢复选中状态（延迟执行，确保节点可见）
            selected = cache_data.get('selected_path')
            if selected:
                def do_select():
                    self.select_path(selected)
                self.parent.after(100, do_select)
                
        finally:
            # 恢复选择事件回调
            self.on_select = original_on_select

    def _sync_folder_to_db(self, folder_path):
        """后台线程：将文件夹中的实际文件与数据库核对，以文件夹数据为准"""
        try:
            if not self._db_ready or not self.db or not self.db.is_ready:
                return

            success, changes = self.db.sync_folder_files(
                folder_path,
                device_id=getattr(self.config, 'device_id', 'unknown'),
                bookmark_id=self._current_bookmark_id,
                debug=self.debug
            )

            if success and changes > 0:
                if self.debug:
                    self.debug.print(f"[点击同步] {os.path.basename(folder_path)} 已更新 {changes} 条记录")

                # 同步完成后，增量刷新图库（更新同步标识、关联状态等）
                self.root.after(0, self._incremental_refresh_gallery)

        except Exception as e:
            if self.debug:
                self.debug.print(f"[点击同步异常] {e}")
                import traceback
                traceback.print_exc()


    def _load_gallery_unified(self, folder_path, scroll_y=None, force_db_sync=False, source="", include_subfolders=True):
        """统一的图库加载方法 - 修复：启动时不强制清除缓存"""
        # 【关键修复】使用 _quick_path_check 替代 os.path.exists，避免网络路径超时导致图库不加载
        if not folder_path or not self._quick_path_check(folder_path):
            if self.debug:
                self.debug.print(f"[统一加载] 路径无效: {folder_path}")
            return

        # 启动时不应清除缓存，force_db_sync 仅用于手动刷新场景
        if force_db_sync and source not in ("启动加载", "启动恢复") and hasattr(self, 'folder_cache'):
            self.folder_cache.clear_cache_for_folder(folder_path)
            if self.debug:
                self.debug.print(f"[统一加载] 已清除缓存: {os.path.basename(folder_path)}")

        if not self.gallery_loader:
            from gallery_loader import GalleryLoader
            self.gallery_loader = GalleryLoader(self.db, self.folder_cache, self.debug)
            if self.debug:
                self.debug.print(f"[统一加载] 来源[{source}] -> 延迟初始化 GalleryLoader")

        if not self.gallery:
            return

        if hasattr(self.gallery, '_load_generation'):
            self._startup_target_generation = self.gallery._load_generation + 1

        tree_state = self.tree_explorer.get_state() if self.tree_explorer else None
        self.gallery_loader.load_folder_unified(
            folder_path, self.gallery, self._update_status, scroll_y,
            db_ready=self._db_ready, force_reload=force_db_sync, source=source,
            on_loaded=lambda path, count: self._on_gallery_loaded(path),
            tree_state=tree_state, include_subfolders=include_subfolders
        )
        if self.debug:
            self.debug.print(f"[统一加载] 来源[{source}] -> 已加载: {os.path.basename(folder_path)}")


                
    def _on_image_select(self, img_path, frame, is_selected):
        """图片选中事件 - 关键：正确显示关联后的主视频地址，无视频时清空"""
        # 从 frame 获取 video_path（关联后已自动更新为主视频路径）
        video_path = getattr(frame, 'video_path', None)

        # 诊断：记录路径信息用于排查数据库查询失败问题
        if self.debug and video_path:
            import os
            self.debug.print(f"[_on_image_select] 缩略图: {os.path.basename(img_path)}")
            self.debug.print(f"  video_path: {video_path}")
            self.debug.print(f"  video_path_norm: {os.path.normpath(video_path)}")
            self.debug.print(f"  frame.is_alias: {getattr(frame, 'is_alias', 'N/A')}")
            self.debug.print(f"  frame.has_local_video: {getattr(frame, 'has_local_video', 'N/A')}")
            self.debug.print(f"  frame.primary_video_path: {getattr(frame, 'primary_video_path', 'N/A')}")
        
        # 关键修复：同时更新图片路径和视频路径显示
        # 当 video_path 为 None 时，传递空字符串以清空视频路径栏
        self._update_path_entries(
            img_path=img_path, 
            video_path=video_path if video_path else ""
        )
        
        if self.gallery:
            selected = self.gallery.get_selected_images()
            if selected:
                self.tag_btn.config(state="normal")
                self.status_label.config(text=f"选中 {len(selected)} 个")
            else:
                self.tag_btn.config(state="disabled")
                # 无选中项时，清空所有路径显示
                if not selected:
                    self._update_path_entries(img_path="", video_path="")

    def _on_image_double_click(self, img_path, image_list=None):
        self.image_viewer.view(img_path, self.root, image_list=image_list)
    
    def _enter_sub_folder(self, sub_folder_path):
        """进入子文件夹（用于宫格显示的双击）"""
        if not os.path.exists(sub_folder_path):
            messagebox.showwarning("提示", "文件夹不存在")
            return
        
        # 更新当前文件夹
        self.current_folder = sub_folder_path
        
        # 在资源管理器中选中该文件夹（用户点击触发保存）
        if self.tree_explorer:
            self.tree_explorer.select_path(sub_folder_path, click_source='user')
        
        # 加载图库
        self._load_gallery_unified(sub_folder_path, None, source="子文件夹")
        
        # 保存位置
        self._save_tree_state_on_user_click(sub_folder_path, os.path.basename(sub_folder_path))
        
        if self.debug:
            self.debug.print(f"进入子文件夹: {sub_folder_path}")

    def _update_status(self, message, progress):
        self.status_label.config(text=message)
        if self.gallery:
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
        if not self.gallery:
            messagebox.showwarning("提示", "图库未就绪")
            return
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
                if self.gallery:
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
                if self.bookmark_manager:
                    self.bookmark_manager.refresh_from_config()
                messagebox.showinfo("成功", f"已添加书签:\n{display_name}")
            else:
                messagebox.showwarning("提示", "该文件夹已在当前设备的书签中，或路径无效")

    def _remove_bookmark(self):
        """从当前设备移除书签"""
        if not self.bookmark_manager:
            messagebox.showwarning("提示", "书签管理器未启用")
            return
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
        """刷新当前文件夹 - 修复：使用统一加载方法确保关联标识正确"""
        if str(self.current_folder).startswith("["):
            self._apply_tag_filter_links()
        elif self.current_folder and os.path.exists(self.current_folder):
            # 获取当前滚动位置
            scroll_y = self.gallery.get_scroll_y() if hasattr(self.gallery, 'get_scroll_y') else None
            
            # ========== 关键修复：使用统一方法，强制数据库同步 ==========
            # 原代码：self._load_gallery_unified(self.current_folder, scroll_y, force_db_sync=True)
            self._load_gallery_unified(self.current_folder, scroll_y, force_db_sync=True, source="菜单刷新")
            
            if self.debug:
                self.debug.print(f"已刷新文件夹: {os.path.basename(self.current_folder)}")

    def _change_size(self):
        new_size = self.size_var.get()
        self.config.thumbnail_size = new_size
        self.config.save_config()
        self._refresh()

    def _toggle_multi_select(self):
        self.multi_select_mode = not self.multi_select_mode
        if self.gallery:
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
            path = os.path.normpath(path)
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

    def _toggle_save_debug_data(self):
        """切换保存数据模式（调试用）"""
        if self._save_debug_data_var.get():
            self._save_debug_data()

    def _save_debug_data(self):
        """
        保存调试数据到 data/ 文件夹（异步执行，不阻塞UI）
        保存三种数据：
        1. config.json 的完整内容
        2. 当前文件夹/书签内的文件列表
        3. 数据库中的关键数据（委托给 DatabaseManager）
        """
        import json
        from datetime import datetime
        import threading
        
        # 确保 data 目录存在
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 在状态栏显示开始保存
        self.status_label.config(text="正在保存调试数据...", foreground="blue")
        
        def do_save():
            saved_files = []
            
            try:
                # ========== 1. 保存 config.json ==========
                self.root.after(0, lambda: self.status_label.config(text="正在保存配置...", foreground="blue"))
                config_path = self.config.config_file if hasattr(self.config, 'config_file') else None
                if config_path and os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    config_save_path = os.path.join(data_dir, f"debug_config_{timestamp}.json")
                    with open(config_save_path, 'w', encoding='utf-8') as f:
                        json.dump(config_data, f, ensure_ascii=False, indent=2)
                    saved_files.append(f"配置: {os.path.basename(config_save_path)}")
                
                # ========== 2. 保存当前文件夹/书签的文件列表 ==========
                self.root.after(0, lambda: self.status_label.config(text="正在扫描文件列表...", foreground="blue"))
                files_data = {
                    "scan_time": timestamp,
                    "current_folder": self.current_folder,
                    "bookmarks": []
                }
                
                device = self.config.devices.get(self.config.device_id, {})
                for bm in device.get('bookmarks', []):
                    bm_path = bm.get('path', '')
                    if not bm_path or not os.path.exists(bm_path):
                        continue
                    
                    bm_files = {"bookmark_path": bm_path, "bookmark_name": bm.get('display_name', ''), "folders": []}
                    
                    for root, dirs, files in os.walk(bm_path):
                        dirs[:] = [d for d in dirs if not d.startswith('.')]
                        folder_files = []
                        for name in files:
                            if name.startswith('.'):
                                continue
                            full_path = os.path.join(root, name)
                            try:
                                stat = os.stat(full_path)
                                folder_files.append({
                                    "name": name,
                                    "path": os.path.normpath(full_path).lower(),
                                    "size": stat.st_size,
                                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat()
                                })
                            except Exception:
                                folder_files.append({
                                    "name": name,
                                    "path": os.path.normpath(full_path).lower(),
                                    "size": 0,
                                    "mtime": None
                                })
                        
                        if folder_files:
                            bm_files["folders"].append({
                                "folder_path": os.path.normpath(root).lower(),
                                "files": folder_files
                            })
                    
                    files_data["bookmarks"].append(bm_files)
                
                files_save_path = os.path.join(data_dir, f"debug_files_{timestamp}.json")
                with open(files_save_path, 'w', encoding='utf-8') as f:
                    json.dump(files_data, f, ensure_ascii=False, indent=2)
                saved_files.append(f"文件: {os.path.basename(files_save_path)}")
                
                # ========== 3. 保存数据库数据（委托给 DatabaseManager）==========
                if self.db and self.db.is_ready:
                    def on_db_status(msg):
                        self.root.after(0, lambda: self.status_label.config(text=msg, foreground="blue"))
                    
                    success, db_path, db_err = self.db.export_debug_data(data_dir, timestamp, status_callback=on_db_status)
                    if success and db_path:
                        saved_files.append(f"数据库: {os.path.basename(db_path)}")
                    elif db_err:
                        saved_files.append(f"数据库导出失败: {db_err}")
                else:
                    saved_files.append("数据库: 未就绪，跳过")
                
                # 保存完成，回主线程更新UI
                def on_done():
                    self.status_label.config(text=f"调试数据已保存 ({len([s for s in saved_files if not s.startswith('数据库导出失败')])}个文件)", foreground="green")
                    if self.debug:
                        self.debug.print("=" * 50)
                        self.debug.print("调试数据已保存:")
                        for sf in saved_files:
                            self.debug.print(f"  - {sf}")
                        self.debug.print(f"保存位置: {data_dir}")
                        self.debug.print("=" * 50)
                
                self.root.after(0, on_done)
                
            except Exception as e:
                def on_error():
                    self.status_label.config(text=f"保存调试数据失败: {e}", foreground="red")
                    if self.debug:
                        self.debug.print(f"保存调试数据失败: {e}")
                        import traceback
                        traceback.print_exc()
                
                self.root.after(0, on_error)
        
        # 启动后台线程执行保存
        threading.Thread(target=do_save, daemon=True).start()

    def _open_color_settings(self):
        """打开图库颜色设置对话框"""
        ColorSettingsDialog(self.root, self.config, self.debug)

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
        """加载UI缓存 - 委托给database_manager模块"""
        from database_manager import load_ui_cache
        return load_ui_cache(self.debug)

    def _save_ui_cache(self):
        """保存UI缓存"""
        try:
            cache_data = {
                'bookmarks': list(self.config.devices.get(self.config.device_id, {}).get('bookmarks', [])),
                'last_folder': self.current_folder,
                'last_root': self.current_root,
                'last_scroll_y': self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0,
                'timestamp': time.time()
            }
            from database_manager import save_ui_cache
            save_ui_cache(cache_data, self.debug)
        except Exception as e:
            self.debug.print(f"保存缓存失败: {e}")

    def _on_closing(self):
        """窗口关闭处理 - 增强：保存完整树状态到 bookmark_states"""
        if self._current_bookmark_id:
            try:
                gallery_scroll = self.gallery.get_scroll_y() if hasattr(self, 'gallery') else 0
                
                # 【关键】保存完整的树状态（展开路径、选中路径、滚动位置）
                tree_state = {}
                if self.tree_explorer:
                    tree_state = self.tree_explorer.get_state()
                
                self.config.save_bookmark_state(
                    self._current_bookmark_id,
                    self.current_folder,
                    tree_state,
                    gallery_scroll
                )
                if self.debug:
                    self.debug.print(f"[关闭] 已保存书签状态: {self._current_bookmark_id}, "
                                   f"展开节点={len(tree_state.get('expanded_paths', []))}个, "
                                   f"选中={tree_state.get('selected_path')}")
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

        # 规范化路径
        img_path = os.path.normpath(img_path)
        
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
                manual_path_var.set(os.path.normpath(path))
        
        ttk.Button(manual_frame, text="浏览...", command=browse_video).pack(side=tk.LEFT, padx=5)
        
        # 手动关联按钮
        def apply_manual():
            manual_path = os.path.normpath(manual_path_var.get().strip())
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
                
                # 立即更新内存缓存（不等待异步加载）
                if self.db:
                    with self.db._cache_lock:
                        self.db._cache['video_relations'][img_id] = vid_id
                        if img_path in self.db._cache['files']:
                            self.db._cache['files'][img_path]['is_alias'] = 1
                
                self.db._load_all_to_cache_async()
                messagebox.showinfo("成功", "手动关联建立成功！")
                dialog.destroy()
                
                # 使用统一刷新方法确保显示效果一致
                if self.gallery and self.current_folder:
                    self.gallery.refresh(keep_scroll=True)
                
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
                
                # 立即更新内存缓存（不等待异步加载）
                if self.db:
                    with self.db._cache_lock:
                        # 重新查询以获取本次操作创建的所有关联
                        refresh_conn = self.db._get_connection()
                        refresh_cursor = refresh_conn.cursor()
                        refresh_cursor.execute(
                            "SELECT alias_file_id, primary_file_id FROM video_relations WHERE alias_folder_path = ?",
                            (folder,)
                        )
                        for row in refresh_cursor.fetchall():
                            self.db._cache['video_relations'][row['alias_file_id']] = row['primary_file_id']
                        refresh_cursor.close()
                        # 更新图片文件的 is_alias 标记
                        if img_path in self.db._cache['files']:
                            self.db._cache['files'][img_path]['is_alias'] = 1
                
                self.db._load_all_to_cache_async()
                messagebox.showinfo("完成", f"成功建立了 {count} 个关联关系")
                dialog.destroy()
                
                # 使用统一刷新方法确保显示效果一致
                if self.gallery and self.current_folder:
                    self.gallery.refresh(keep_scroll=True)
                
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

    def _on_gallery_refresh_request(self, keep_scroll=True):
        """图库统一刷新回调 - 确保所有入口使用相同的加载方法"""
        if not self.current_folder or not os.path.exists(self.current_folder):
            return
        
        scroll_y = None
        if keep_scroll and hasattr(self, 'gallery') and self.gallery:
            scroll_y = self.gallery.get_scroll_y()
        
        # 使用统一加载方法，强制数据库同步以确保显示效果一致
        self._load_gallery_unified(self.current_folder, scroll_y, force_db_sync=True, source="统一刷新")
        
        if self.debug:
            self.debug.print(f"[统一刷新] 已刷新: {os.path.basename(self.current_folder)}")

    def _on_relation_removed(self, img_path=None):
        """图片关联解除后的回调 - 立即刷新图库显示"""
        if self.debug:
            self.debug.print(f"关联已解除，刷新图库: {img_path}")
        
        # 1. 更新内存缓存中的关联状态（立即生效，不等待异步加载）
        if self._db_ready and self.db and img_path:
            with self.db._cache_lock:
                # 查找文件ID
                file_id = None
                for path, info in self.db._cache['files'].items():
                    if path == img_path:
                        file_id = info.get('id')
                        info['is_alias'] = 0
                        break
                # 从video_relations缓存中移除
                if file_id and file_id in self.db._cache['video_relations']:
                    del self.db._cache['video_relations'][file_id]
        
        # 2. 异步刷新数据库缓存（后台补全）
        if self._db_ready and self.db:
            self.db._load_all_to_cache_async()
        
        # 3. 关键：强制刷新图库以移除关联标识（使用统一刷新方法）
        if hasattr(self, 'gallery') and self.gallery:
            self.gallery.refresh(keep_scroll=True)
        
        # 4. 刷新标签超链接区域（如果有关联相关的标签统计）
        self._refresh_tag_links()
        
        # 5. 更新状态栏提示
        self.status_label.config(text="关联已解除", foreground="green")
        self.root.after(2000, lambda: self.status_label.config(
            text=f"就绪 ({self.gallery.get_image_count() if self.gallery else 0}项)", 
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
                    if self.gallery and self.current_folder:
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
            primary_old = os.path.normpath(primary_old_var.get().strip()).lower() if primary_old_var.get().strip() else ""
            primary_new = os.path.normpath(primary_new_var.get().strip()).lower() if primary_new_var.get().strip() else ""
            alias_old = os.path.normpath(alias_old_var.get().strip()).lower() if alias_old_var.get().strip() else ""
            alias_new = os.path.normpath(alias_new_var.get().strip()).lower() if alias_new_var.get().strip() else ""
            
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
            
            primary_old = os.path.normpath(primary_old_var.get().strip()).lower() if primary_old_var.get().strip() else ""
            primary_new = os.path.normpath(primary_new_var.get().strip()).lower() if primary_new_var.get().strip() else ""
            alias_old = os.path.normpath(alias_old_var.get().strip()).lower() if alias_old_var.get().strip() else ""
            alias_new = os.path.normpath(alias_new_var.get().strip()).lower() if alias_new_var.get().strip() else ""
            
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
        
        # 启动日志特殊处理（检测[启动]标记）
        is_startup_log = '[启动]' in message or '启动阶段' in message
        
        # 如果是启动日志，移除[启动]标记以符合用户要求的显示格式（保留时间戳）
        if is_startup_log and '[启动] ' in message:
            message = message.replace('[启动] ', '')
        
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
        if is_startup_log:
            tag = "startup"  # 启动日志使用紫色
        else:
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

    def _setup_gallery_callbacks(self):
        """设置图库的回调函数"""
        if not hasattr(self, 'gallery') or not self.gallery:
            return
        
        # 添加加载完成回调，用于在加载完成后刷新同步标识
        self.gallery.add_load_complete_callback(self._on_gallery_load_complete)


    def _on_gallery_load_complete(self, folder_path):
        """
        图库加载完成后的回调
        委托 GalleryViewer 处理同步标识和视频信息刷新。
        """
        if self.debug:
            self.debug.print(f"[加载完成回调] {os.path.basename(folder_path) if folder_path else 'None'}")
        
        # 委托 GalleryViewer 统一处理数据库就绪后的刷新
        if self.gallery and self._db_ready and self.db:
            self.gallery.on_db_ready(self.db)
        
        # 刷新标签超链接
        self._refresh_tag_links()

    # ==================== 数据完整性检查 ====================

    def run_data_integrity_check(self):
        """运行数据完整性检查 - 诊断 config.json、文件夹扫描、数据库之间的数据一致性"""
        if not self.debug:
            return

        self.debug.print("\n" + "=" * 70)
        self.debug.print("【数据完整性检查】")
        self.debug.print("=" * 70)

        import os
        import json

        # 1. 检查 config.json 数据完整性
        self.debug.print("\n【1. 配置文件检查】")
        try:
            config_path = self.config.config_file if hasattr(self.config, 'config_file') else None
            if config_path and os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    raw_config = json.load(f)

                # 检查设备结构
                devices = raw_config.get('devices', {})
                self.debug.print(f"  设备数量: {len(devices)}")
                for dev_id, dev in devices.items():
                    bookmarks = dev.get('bookmarks', [])
                    self.debug.print(f"  设备 '{dev_id}': {len(bookmarks)} 个书签")
                    for bm in bookmarks:
                        bm_path = bm.get('path', '')
                        exists = os.path.exists(bm_path)
                        self.debug.print(f"    - {bm.get('display_name', '未命名')}: {bm_path} {'[可访问]' if exists else '[不可访问]'}")

                # 检查标签数据
                tags_v2 = raw_config.get('tags_v2', {})
                total_tags = 0
                for dev_id, dev_tags in tags_v2.items():
                    for bm_id, files in dev_tags.items():
                        total_tags += len(files)
                self.debug.print(f"  标签记录: {total_tags} 个文件有标签")

                # 检查书签状态
                bm_states = raw_config.get('bookmark_states', {})
                self.debug.print(f"  书签状态: {len(bm_states)} 个书签有状态记录")
            else:
                self.debug.print(f"  配置文件不存在: {config_path}")
        except Exception as e:
            self.debug.print(f"  配置文件检查失败: {e}")

        # 2. 检查数据库连接和缓存状态
        self.debug.print("\n【2. 数据库检查】")
        if self.db and self.db.is_ready:
            try:
                cache = self.db._cache
                files_cache = cache.get('files', {})
                folders_cache = cache.get('folders', {})
                actors_cache = cache.get('actors', {})
                relations_cache = cache.get('video_relations', {})
                code_index = cache.get('code_index', {})

                self.debug.print(f"  数据库状态: 已连接")
                self.debug.print(f"  缓存就绪: {self.db.is_cache_ready}")
                self.debug.print(f"  文件缓存: {len(files_cache)} 个")
                self.debug.print(f"  文件夹缓存: {len(folders_cache)} 个")
                self.debug.print(f"  演员缓存: {len(actors_cache)} 个")
                self.debug.print(f"  视频关联: {len(relations_cache)} 个")
                self.debug.print(f"  番号索引: {len(code_index)} 个唯一番号")

                # 检查视频文件类型分布
                video_count = sum(1 for info in files_cache.values() if info.get('type') == 'video')
                image_count = sum(1 for info in files_cache.values() if info.get('type') == 'image')
                self.debug.print(f"  视频文件: {video_count} 个")
                self.debug.print(f"  图片文件: {image_count} 个")

                # 检查有采集信息的视频
                collected = sum(1 for info in files_cache.values() 
                              if info.get('type') == 'video' and 
                              (info.get('video_title') or info.get('video_code') or info.get('duration')))
                self.debug.print(f"  已采集视频: {collected} 个")

                # 检查设备分布
                device_counts = {}
                for info in files_cache.values():
                    dev_id = info.get('device_id', 'unknown')
                    device_counts[dev_id] = device_counts.get(dev_id, 0) + 1
                self.debug.print(f"  设备分布:")
                for dev_id, count in device_counts.items():
                    self.debug.print(f"    - {dev_id}: {count} 个文件")

                # 检查路径样本（用于诊断路径格式问题）
                self.debug.print(f"  路径样本 (前5个):")
                for i, path in enumerate(list(files_cache.keys())[:5]):
                    info = files_cache[path]
                    self.debug.print(f"    {i+1}. {path}")
                    self.debug.print(f"       类型={info.get('type')}, 设备={info.get('device_id')}, 书签={info.get('bookmark_id')}")

            except Exception as e:
                self.debug.print(f"  数据库检查失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            self.debug.print(f"  数据库状态: {'未连接' if not self.db else '未就绪'}")

        # 3. 检查当前文件夹扫描结果与数据库的一致性
        self.debug.print("\n【3. 当前文件夹一致性检查】")
        if self.current_folder and os.path.exists(self.current_folder):
            folder_norm = os.path.normpath(self.current_folder)
            self.debug.print(f"  当前文件夹: {folder_norm}")

            # 扫描实际文件
            try:
                from core import PathUtils
                actual_videos = PathUtils.find_video_files(folder_norm)
                actual_images = PathUtils.find_image_files(folder_norm)
                self.debug.print(f"  实际视频: {len(actual_videos)} 个")
                self.debug.print(f"  实际图片: {len(actual_images)} 个")

                # 检查数据库中该文件夹的记录
                if self.db and self.db.is_cache_ready:
                    db_files_in_folder = [p for p, info in self.db._cache['files'].items() 
                                          if os.path.normpath(info.get('folder_path', '')) == folder_norm]
                    self.debug.print(f"  数据库记录: {len(db_files_in_folder)} 个文件")

                    # 检查路径匹配情况
                    matched = 0
                    unmatched = []
                    for v_path in actual_videos:
                        norm_v = os.path.normpath(v_path)
                        if norm_v in self.db._cache['files']:
                            matched += 1
                        else:
                            unmatched.append(norm_v)

                    self.debug.print(f"  路径匹配: {matched}/{len(actual_videos)} 个视频匹配")
                    if unmatched:
                        self.debug.print(f"  未匹配视频 ({len(unmatched)}个):")
                        for p in unmatched[:5]:
                            self.debug.print(f"    - {p}")
                        if len(unmatched) > 5:
                            self.debug.print(f"    ... 还有 {len(unmatched)-5} 个")

            except Exception as e:
                self.debug.print(f"  文件夹检查失败: {e}")
        else:
            self.debug.print(f"  当前文件夹无效: {self.current_folder}")

        # 4. 检查图库中的 frame 与数据库的对应关系
        self.debug.print("\n【4. 图库UI与数据库对应检查】")
        if hasattr(self, 'gallery') and self.gallery:
            frames = self.gallery.images_frame.winfo_children()
            self.debug.print(f"  图库缩略图数量: {len(frames)}")

            frames_with_video = 0
            frames_without_db = 0
            for frame in frames:
                v_path = getattr(frame, 'video_path', None)
                if v_path:
                    frames_with_video += 1
                    if self.db and self.db.is_cache_ready:
                        db_file = self.db.get_file_by_path(v_path)
                        if not db_file:
                            frames_without_db += 1
                            if frames_without_db <= 5:
                                self.debug.print(f"  无数据库记录 #{frames_without_db}:")
                                self.debug.print(f"    frame.video_path: {v_path}")
                                self.debug.print(f"    frame.is_alias: {getattr(frame, 'is_alias', 'N/A')}")
                                self.debug.print(f"    frame.img_path: {getattr(frame, 'img_path', 'N/A')}")

            self.debug.print(f"  有视频路径的frame: {frames_with_video} 个")
            self.debug.print(f"  无数据库记录的frame: {frames_without_db} 个")

        self.debug.print("\n" + "=" * 70)
        self.debug.print("【数据完整性检查完成】")
        self.debug.print("=" * 70 + "\n")


class ColorSettingsDialog:
    """图库颜色设置对话框"""
    
    def __init__(self, parent, config_manager, debug_utils=None):
        self.parent = parent
        self.config = config_manager
        self.debug = debug_utils
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("图库颜色设置")
        self.dialog.geometry("450x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self.color_vars = {}
        self.color_labels = {}
        
        self._create_ui()
        self._load_current_colors()
    
    def _create_ui(self):
        """创建UI"""
        # 标题
        title_label = ttk.Label(
            self.dialog, 
            text="设置图库浏览器中各状态标识的背景色",
            font=("微软雅黑", 10, "bold")
        )
        title_label.pack(pady=10)
        
        # 说明文字
        desc_label = ttk.Label(
            self.dialog,
            text="点击颜色按钮可更改对应状态的背景色",
            foreground="gray"
        )
        desc_label.pack(pady=(0, 10))
        
        # 颜色设置区域
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        
        # 颜色配置项
        self.color_items = [
            ("linked_video", "🔗 关联视频", "图片关联到其他位置的视频时显示"),
            ("video_placeholder", "🎬 视频文件", "无缩略图的视频占位符"),
            ("dedicated_match", "🎬 专属视频", "图片与视频完全同名匹配"),
            ("cover_match", "📁 封面匹配", "使用通用封面图片"),
            ("normal_video", "🎬 普通视频", "有其他匹配方式的视频"),
            ("image_only", "🖼️ 仅图片", "无对应视频的独立图片"),
            ("selected", "选中状态", "图片被选中时的高亮色")
        ]
        
        for key, label, desc in self.color_items:
            row = ttk.Frame(main_frame)
            row.pack(fill=tk.X, pady=8)
            
            # 状态标签
            lbl = ttk.Label(row, text=label, width=15)
            lbl.pack(side=tk.LEFT)
            
            # 颜色预览框
            preview = tk.Frame(row, width=60, height=25, relief=tk.SOLID, borderwidth=1)
            preview.pack(side=tk.LEFT, padx=10)
            preview.pack_propagate(False)
            self.color_labels[key] = preview
            
            # 颜色值输入框
            var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=var, width=10)
            entry.pack(side=tk.LEFT, padx=5)
            self.color_vars[key] = var
            
            # 颜色选择按钮
            btn = ttk.Button(
                row, 
                text="选择...",
                command=lambda k=key: self._choose_color(k)
            )
            btn.pack(side=tk.LEFT, padx=5)
            
            # 描述
            desc_lbl = ttk.Label(row, text=desc, foreground="gray", font=("微软雅黑", 8))
            desc_lbl.pack(side=tk.LEFT, padx=10)
        
        # 按钮区域
        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=15)
        
        ttk.Button(
            btn_frame, 
            text="恢复默认",
            command=self._reset_defaults
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            btn_frame,
            text="取消",
            command=self.dialog.destroy
        ).pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(
            btn_frame,
            text="保存",
            command=self._save_colors
        ).pack(side=tk.RIGHT, padx=5)
    
    def _load_current_colors(self):
        """加载当前颜色配置"""
        for key, _, _ in self.color_items:
            color = self.config.get_gallery_color(key)
            self.color_vars[key].set(color)
            self._update_preview(key, color)
    
    def _update_preview(self, key, color):
        """更新颜色预览"""
        try:
            self.color_labels[key].config(bg=color)
        except tk.TclError:
            # 无效颜色，使用默认白色
            self.color_labels[key].config(bg="white")
    
    def _choose_color(self, key):
        """打开颜色选择器"""
        from tkinter import colorchooser
        
        current_color = self.color_vars[key].get()
        color = colorchooser.askcolor(
            parent=self.dialog,
            title=f"选择颜色 - {key}",
            initialcolor=current_color
        )
        
        if color[1]:  # color[1] 是十六进制颜色值
            self.color_vars[key].set(color[1])
            self._update_preview(key, color[1])
    
    def _reset_defaults(self):
        """恢复默认颜色"""
        if messagebox.askyesno("确认", "确定要恢复默认颜色设置吗？"):
            defaults = self.config.DEFAULT_CONFIG.get("gallery_colors", {})
            for key, _, _ in self.color_items:
                default_color = defaults.get(key, "white")
                self.color_vars[key].set(default_color)
                self._update_preview(key, default_color)
    
    def _save_colors(self):
        """保存颜色设置"""
        try:
            for key, _, _ in self.color_items:
                color = self.color_vars[key].get().strip()
                # 简单验证颜色格式
                if not color.startswith("#") or len(color) != 7:
                    messagebox.showerror(
                        "错误", 
                        f"颜色格式错误: {color}\n请使用十六进制格式，如 #FFE4B5"
                    )
                    return
                self.config.set_gallery_color(key, color)
            
            self.config.save_config()
            messagebox.showinfo("成功", "颜色设置已保存\n刷新图库后生效")
            self.dialog.destroy()
            
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")




def main():
    root = tk.Tk()
    app = ImageBrowserApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()