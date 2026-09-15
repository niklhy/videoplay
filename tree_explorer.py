#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资源管理器模块 - 基于内存缓存或文件系统扫描构建树状文件夹结构
版本：4.0 - 支持外部缓存传入、文件系统扫描后备、启动自动恢复
"""
import tkinter as tk
from tkinter import ttk
import os
import json
import time
import hashlib
from datetime import datetime


class TreeExplorer:
    """树形资源管理器 - 支持外部缓存直接构建或文件系统扫描构建"""

    def __init__(self, parent_frame, on_select_callback, debug_utils=None, db_manager=None):
        self.parent = parent_frame
        self.on_select = on_select_callback
        self.debug = debug_utils
        self.db = db_manager

        # 状态变量
        self.root_path = None
        self.current_folder = None
        self._selected_path = None
        self._path_to_node = {}          # 路径 -> treeview节点ID的映射
        self._folder_cache = {}           # 文件夹信息缓存 {path: folder_info}
        self._tree_ready = False          # 树是否已生成完成
        self._device_id = None
        self._bookmark_id = None
        self._on_loaded_callback = None    # 树加载完成回调函数

        # 防重复标志
        self._load_generation = 0
        self._programmatic_selecting = False
        self._suppress_select_event = False   # 启动恢复期间屏蔽TreeviewSelect事件

        # 创建UI
        self._create_ui()

    def set_db(self, db_manager):
        """设置数据库管理器"""
        self.db = db_manager
        if self.debug:
            self.debug.print("[TreeExplorer] 数据库管理器已设置")

    def _create_ui(self):
        """创建UI组件"""
        self.frame = ttk.Frame(self.parent)
        self.frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # 创建Treeview
        self.tree = ttk.Treeview(self.frame, selectmode="browse")
        self.tree.heading("#0", text="文件夹", anchor=tk.W)
        self.tree.column("#0", width=280, minwidth=200)

        # 滚动条
        scroll_y = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # 绑定事件
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    # ==================== 缓存文件操作（保留用于后备）====================

    def _get_cache_dir(self):
        """获取缓存目录"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(base_dir, "data")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _get_cache_file_path(self, device_id, bookmark_id):
        """生成缓存文件路径: data/tree_{device_id}_{hash}.json"""
        if not device_id:
            device_id = "default"
        if not bookmark_id:
            bookmark_id = "default"
        short_id = hashlib.md5(str(bookmark_id).encode()).hexdigest()[:12]
        cache_name = f"tree_{device_id}_{short_id}.json"
        return os.path.join(self._get_cache_dir(), cache_name)

    def _load_cache(self, device_id, bookmark_id):
        """从JSON文件加载缓存（后备方案）"""
        cache_path = self._get_cache_file_path(device_id, bookmark_id)

        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # 验证缓存时效（24小时内有效）
            cache_time = cache_data.get('cache_time', 0)
            if time.time() - cache_time > 86400:
                if self.debug:
                    self.debug.print("[TreeExplorer] 缓存已过期，将重新扫描")
                return None

            # 转换为内部缓存格式
            nodes = cache_data.get('nodes', {})
            folder_cache = {}
            for path, node in nodes.items():
                path_norm = os.path.normpath(path)
                folder_cache[path_norm] = {
                    'id': node.get('id'),
                    'name': node.get('name', os.path.basename(path_norm)),
                    'parent': os.path.normpath(node['parent']) if node.get('parent') else None,
                    'is_bookmark': node.get('is_bookmark', False),
                    'file_count': node.get('file_count', 0),
                    'children': [os.path.normpath(c) for c in node.get('children', [])]
                }

            if self.debug:
                self.debug.print(f"[TreeExplorer] 从JSON缓存加载: {len(folder_cache)} 个文件夹")
            return folder_cache

        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 加载JSON缓存失败: {e}")
            return None

    def _save_cache(self, device_id, bookmark_id, root_path):
        """保存缓存到JSON文件"""
        cache_path = self._get_cache_file_path(device_id, bookmark_id)

        # 转换为nodes格式
        nodes = {}
        for path, info in self._folder_cache.items():
            nodes[path] = {
                'id': info.get('id'),
                'name': info['name'],
                'parent': info['parent'],
                'is_bookmark': info['is_bookmark'],
                'file_count': info['file_count'],
                'children': info.get('children', [])
            }

        cache_data = {
            'device_id': device_id,
            'bookmark_id': bookmark_id,
            'root_path': root_path,
            'nodes': nodes,
            'cache_time': time.time(),
            'created_at': datetime.now().isoformat()
        }

        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            if self.debug:
                self.debug.print(f"[TreeExplorer] 缓存已保存: {os.path.basename(cache_path)}")
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 保存缓存失败: {e}")
            return False

    # ==================== 核心加载逻辑 ====================

    def load_bookmark(self, root_path, device_id=None, bookmark_id=None, 
                      folder_cache=None, last_folder=None, on_loaded=None):
        """
        加载书签根路径，构建文件夹树

        优先级：
        1. 使用传入的 folder_cache（外部缓存）
        2. 使用 JSON 缓存文件
        3. 扫描文件系统构建缓存

        Args:
            root_path: 书签根路径
            device_id: 设备ID
            bookmark_id: 书签ID
            folder_cache: 外部传入的文件夹缓存字典 {path: folder_info}
            last_folder: 上次打开的文件夹路径（从config.json读取）
            on_loaded: 加载完成回调函数，签名: on_loaded(success: bool, selected_path: str)
        """
        # 清空现有树
        self.clear()

        self.root_path = os.path.normpath(root_path) if root_path else None
        self._device_id = device_id
        self._bookmark_id = bookmark_id
        self._on_loaded_callback = on_loaded
        self._tree_ready = False

        # 递增加载世代
        self._load_generation += 1
        current_gen = self._load_generation

        if self.debug:
            self.debug.print(f"[TreeExplorer] 开始加载书签: {self.root_path}")

        # ========== 步骤1: 获取文件夹缓存 ==========
        cache_source = "unknown"

        if folder_cache is not None and len(folder_cache) > 0:
            # 使用外部传入的缓存
            self._folder_cache = self._normalize_cache(folder_cache)
            cache_source = "external"
            if self.debug:
                self.debug.print(f"[TreeExplorer] 使用外部缓存: {len(self._folder_cache)} 个文件夹")
        else:
            # 尝试从JSON缓存加载
            json_cache = self._load_cache(device_id, bookmark_id)
            if json_cache and len(json_cache) > 0:
                self._folder_cache = json_cache
                cache_source = "json"
                if self.debug:
                    self.debug.print(f"[TreeExplorer] 使用JSON缓存: {len(self._folder_cache)} 个文件夹")
            else:
                # 扫描文件系统构建缓存
                if self.root_path and os.path.exists(self.root_path):
                    self._folder_cache = self._scan_folder_tree(self.root_path)
                    cache_source = "scan"
                    if self.debug:
                        self.debug.print(f"[TreeExplorer] 使用文件系统扫描: {len(self._folder_cache)} 个文件夹")
                else:
                    if self.debug:
                        self.debug.print(f"[TreeExplorer] 错误: 根路径不存在且没有缓存: {self.root_path}")
                    self._tree_ready = True
                    if on_loaded:
                        on_loaded(False, None)
                    return

        if not self._folder_cache or len(self._folder_cache) == 0:
            if self.debug:
                self.debug.print("[TreeExplorer] 错误: 无法获取任何缓存数据")
            self._tree_ready = True
            if on_loaded:
                on_loaded(False, None)
            return

        if self.debug:
            self.debug.print(f"[TreeExplorer] 缓存来源: {cache_source}, 共 {len(self._folder_cache)} 个文件夹")

        # ========== 步骤2: 在主线程构建树 ==========
        self._build_tree_from_cache(current_gen)

        # ========== 步骤3: 恢复上次打开的文件夹 ==========
        actual_selected = None
        if last_folder:
            last_folder_norm = os.path.normpath(last_folder)
            # 验证last_folder是否在缓存中
            if last_folder_norm in self._folder_cache:
                success = self._restore_last_folder(last_folder_norm, current_gen)
                if success:
                    actual_selected = last_folder_norm
                    if self.debug:
                        self.debug.print(f"[TreeExplorer] 已恢复上次文件夹: {last_folder_norm}")
            else:
                if self.debug:
                    self.debug.print(f"[TreeExplorer] last_folder不在缓存中: {last_folder_norm}")
                # 尝试使用根路径
                if self.root_path in self._folder_cache:
                    actual_selected = self.root_path
                    self._restore_last_folder(self.root_path, current_gen)
        else:
            # 没有last_folder，默认选中根节点
            if self.root_path in self._folder_cache:
                actual_selected = self.root_path
                self._restore_last_folder(self.root_path, current_gen)

        # ========== 步骤4: 标记就绪并回调 ==========
        self._tree_ready = True

        # 保存缓存到JSON（如果是扫描构建的）
        if cache_source == "scan" and device_id and bookmark_id:
            self._save_cache(device_id, bookmark_id, self.root_path)

        if self.debug:
            self.debug.print(f"[TreeExplorer] 树构建完成，选中: {actual_selected}")

        # 【关键修复】延迟到Treeview视觉渲染完成后再回调，确保图库在树展开/选中渲染后加载
        if self._on_loaded_callback:
            self._notify_loaded(True, actual_selected)

    def _notify_loaded(self, success, selected_path):
        """
        确保Treeview视觉渲染（展开/选中/滚动）完成后再通知主程序加载图库。

        使用 after_idle 将回调排入事件循环空闲队列，并在回调内调用
        update_idletasks() 强制处理Treeview重绘等挂起的idle任务，
        随后解除选择事件屏蔽，最后触发主程序回调。
        """
        def _do_notify():
            try:
                # 强制处理Treeview重绘、滚动等挂起的idle任务
                self.parent.update_idletasks()
            except Exception:
                pass
            # 渲染完成，解除选择事件屏蔽，恢复用户交互
            self._suppress_select_event = False
            # 通知主程序（图库加载的唯一入口）
            if self._on_loaded_callback:
                cb = self._on_loaded_callback
                self._on_loaded_callback = None  # 防止重复调用
                cb(success, selected_path)

        # 排入空闲队列：当前事件处理完毕 + Treeview重绘idle任务之后执行
        self.parent.after_idle(_do_notify)

    def _normalize_cache(self, cache):
        """规范化缓存中的所有路径"""
        normalized = {}
        for path, info in cache.items():
            path_norm = os.path.normpath(path)
            parent = info.get('parent')
            if parent:
                parent = os.path.normpath(parent)

            children = info.get('children', [])
            if isinstance(children, list):
                children = [os.path.normpath(c) for c in children if c]

            normalized[path_norm] = {
                'id': info.get('id'),
                'name': info.get('name', os.path.basename(path_norm)),
                'parent': parent,
                'is_bookmark': info.get('is_bookmark', False),
                'file_count': info.get('file_count', 0),
                'children': children
            }
        return normalized

    def _scan_folder_tree(self, root_path):
        """
        扫描文件系统构建文件夹树缓存

        Returns:
            dict: 文件夹缓存 {path: folder_info}
        """
        cache = {}
        root_norm = os.path.normpath(root_path)

        try:
            # 添加根节点
            cache[root_norm] = {
                'id': None,
                'name': os.path.basename(root_norm) or root_norm,
                'parent': None,
                'is_bookmark': True,
                'file_count': 0,
                'children': []
            }

            # 递归扫描所有子文件夹
            for dirpath, dirnames, filenames in os.walk(root_norm):
                # 跳过隐藏文件夹
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]

                dirpath_norm = os.path.normpath(dirpath)

                # 统计文件数量（视频+图片）
                file_count = 0
                for f in filenames:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.ts',
                               '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}:
                        file_count += 1

                # 更新当前文件夹的文件数量
                if dirpath_norm in cache:
                    cache[dirpath_norm]['file_count'] = file_count
                else:
                    # 如果不在缓存中（可能是通过os.walk访问到的），添加它
                    parent = os.path.dirname(dirpath_norm)
                    parent = os.path.normpath(parent) if parent != dirpath_norm else None
                    cache[dirpath_norm] = {
                        'id': None,
                        'name': os.path.basename(dirpath_norm),
                        'parent': parent,
                        'is_bookmark': False,
                        'file_count': file_count,
                        'children': []
                    }
                    # 确保父节点的children包含此路径
                    if parent and parent in cache:
                        if dirpath_norm not in cache[parent]['children']:
                            cache[parent]['children'].append(dirpath_norm)

                # 添加子文件夹到缓存和父节点的children
                for dirname in dirnames:
                    child_path = os.path.normpath(os.path.join(dirpath_norm, dirname))
                    if child_path not in cache:
                        cache[child_path] = {
                            'id': None,
                            'name': dirname,
                            'parent': dirpath_norm,
                            'is_bookmark': False,
                            'file_count': 0,
                            'children': []
                        }

                    # 确保父节点包含此子节点
                    if dirpath_norm in cache:
                        if child_path not in cache[dirpath_norm]['children']:
                            cache[dirpath_norm]['children'].append(child_path)

            if self.debug:
                self.debug.print(f"[TreeExplorer] 扫描完成: {len(cache)} 个文件夹")

            return cache

        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 扫描文件系统失败: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _build_tree_from_cache(self, expected_gen):
        """
        根据缓存数据构建完整树形结构

        算法：
        1. 找到根节点（is_bookmark=True 或 parent为None）
        2. 按路径深度排序，确保父节点先于子节点插入
        3. 递归构建整个树
        """
        if self._load_generation != expected_gen:
            if self.debug:
                self.debug.print("[TreeExplorer] 构建世代过期，丢弃")
            return

        if not self._folder_cache:
            if self.debug:
                self.debug.print("[TreeExplorer] 缓存为空，无法构建树")
            return

        # 临时禁用选择事件，避免构建过程中触发保存
        original_on_select = self.on_select
        self.on_select = None

        try:
            # 1. 确定根节点
            root_path_norm = os.path.normpath(self.root_path) if self.root_path else None
            root_entry = None

            # 首先查找标记为书签的节点
            for path, info in self._folder_cache.items():
                if info.get('is_bookmark'):
                    root_entry = (path, info)
                    break

            # 如果没找到，查找parent为None的节点
            if not root_entry:
                for path, info in self._folder_cache.items():
                    if info.get('parent') is None:
                        root_entry = (path, info)
                        break

            # 如果还没找到，使用root_path对应的项
            if not root_entry and root_path_norm and root_path_norm in self._folder_cache:
                root_entry = (root_path_norm, self._folder_cache[root_path_norm])

            # 如果仍然没找到，使用第一个节点作为根
            if not root_entry:
                first_path = next(iter(self._folder_cache.keys()))
                root_entry = (first_path, self._folder_cache[first_path])
                if self.debug:
                    self.debug.print(f"[TreeExplorer] 警告: 未找到根节点，使用第一个节点: {first_path}")

            if not root_entry:
                if self.debug:
                    self.debug.print("[TreeExplorer] 错误: 无法确定根节点")
                return

            # 2. 插入根节点
            root_path, root_info = root_entry
            root_name = root_info.get('name') or os.path.basename(root_path) or root_path
            root_display = f"{root_name} ({root_info.get('file_count', 0)})" if root_info.get('file_count', 0) > 0 else root_name

            root_node = self.tree.insert("", "end", text=root_display, values=[root_path], open=True)
            self._path_to_node[root_path] = root_node

            if self.debug:
                self.debug.print(f"[TreeExplorer] 插入根节点: {root_name} ({root_path})")

            # 3. 按路径深度排序所有非根节点，确保先父后子
            all_paths = []
            for path, info in self._folder_cache.items():
                path_norm = os.path.normpath(path)
                if path_norm == os.path.normpath(root_path):
                    continue  # 跳过根节点

                # 计算深度（通过路径分隔符数量）
                depth = path_norm.count(os.sep)
                all_paths.append((depth, path_norm, info))

            # 按深度升序排序（先父后子）
            all_paths.sort(key=lambda x: x[0])

            # 4. 逐级插入节点
            inserted_count = 0
            failed_paths = []

            for depth, path, info in all_paths:
                # 查找父节点
                parent_path = info.get('parent')
                parent_node = None

                if parent_path:
                    parent_path_norm = os.path.normpath(parent_path)
                    parent_node = self._path_to_node.get(parent_path_norm)

                # 如果直接parent找不到，尝试查找最接近的祖先
                if not parent_node:
                    parent_node = self._find_nearest_ancestor(path, root_node, root_path)

                if parent_node:
                    node_name = info.get('name') or os.path.basename(path) or path
                    file_count = info.get('file_count', 0)
                    display_text = f"{node_name} ({file_count})" if file_count > 0 else node_name

                    node_id = self.tree.insert(parent_node, "end", text=display_text, values=[path])
                    self._path_to_node[path] = node_id
                    inserted_count += 1
                else:
                    failed_paths.append(path)

            if failed_paths and self.debug:
                self.debug.print(f"[TreeExplorer] 警告: {len(failed_paths)} 个节点无法插入（找不到父节点）")
                for p in failed_paths[:5]:
                    self.debug.print(f"  - {p}")

            if self.debug:
                self.debug.print(f"[TreeExplorer] 树构建完成: {inserted_count + 1} 个节点 (根+{inserted_count})")

        finally:
            # 恢复选择事件回调
            self.on_select = original_on_select

    def _find_nearest_ancestor(self, path, root_node, root_path):
        """
        查找路径最接近的祖先节点

        从path的父目录开始向上遍历，直到找到存在于树中的节点
        """
        path_norm = os.path.normpath(path)
        root_path_norm = os.path.normpath(root_path)

        # 如果path是root_path的子目录，返回root_node
        if path_norm.startswith(root_path_norm + os.sep):
            return root_node

        # 向上遍历查找
        current = os.path.dirname(path_norm)
        while current and current != path_norm:
            current_norm = os.path.normpath(current)
            if current_norm in self._path_to_node:
                return self._path_to_node[current_norm]

            # 检查是否到达根
            if current_norm == root_path_norm:
                return root_node

            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return root_node  # 默认返回根节点

    def _restore_last_folder(self, last_folder, expected_gen):
        """
        恢复上次打开的文件夹：展开路径并选中节点

        Returns:
            bool: 是否成功恢复
        """
        if self._load_generation != expected_gen:
            return False

        if not last_folder:
            return False

        last_folder = os.path.normpath(last_folder)

        # 检查路径是否在树中
        if last_folder not in self._path_to_node:
            if self.debug:
                self.debug.print(f"[TreeExplorer] last_folder不在树节点中: {last_folder}")
                # 打印所有节点路径用于调试
                self.debug.print(f"[TreeExplorer] 现有节点: {list(self._path_to_node.keys())[:10]}...")
            return False

        # 展开路径上的所有父节点
        self.expand_to_path(last_folder)

        # 屏蔽选择事件 + 程序操作标记
        self._suppress_select_event = True
        self._programmatic_selecting = True
        try:
            node_id = self._path_to_node[last_folder]
            self.tree.selection_set(node_id)
            self.tree.focus(node_id)
            self.tree.see(node_id)
            self._selected_path = last_folder
            self.current_folder = last_folder

            if self.debug:
                self.debug.print(f"[TreeExplorer] 已恢复选中: {last_folder}")

            # 【已删除】after(100, _trigger_select_callback) —— 图库加载改由 on_loaded_callback 统一负责
            return True

        finally:
            self._programmatic_selecting = False
            # 注意：_suppress_select_event 不在此处重置，待渲染完成回调 _notify_loaded 中重置

    def _trigger_select_callback(self, folder_path):
        """触发选择回调，用于加载右侧图库"""
        if self.on_select and self._tree_ready:
            folder_name = os.path.basename(folder_path)
            # 标记为程序恢复，避免触发保存
            self._programmatic_selecting = True
            try:
                self.on_select(folder_path, folder_name, click_source='restore')
            finally:
                self._programmatic_selecting = False

    # ==================== 树操作方法 ====================

    def _on_tree_select(self, event=None):
        """树节点选中事件"""
        # 启动恢复期间，屏蔽TreeviewSelect事件，防止意外触发on_select导致图库重复加载
        if self._suppress_select_event:
            return
        selection = self.tree.selection()
        if not selection:
            return

        item = selection[0]
        item_data = self.tree.item(item)
        values = item_data.get('values', [])

        if values and len(values) > 0:
            folder_path = values[0]
            if folder_path and os.path.isdir(folder_path):
                folder_path = os.path.normpath(folder_path)
                self._selected_path = folder_path
                self.current_folder = folder_path

                # 如果是程序自动选中，不触发回调
                if self._programmatic_selecting:
                    return

                # 用户点击，触发回调
                if self.on_select:
                    self.on_select(folder_path, item_data['text'], click_source='user')

    def select_path(self, path, click_source=None):
        """
        根据路径选中节点

        Args:
            path: 文件夹路径
            click_source: 触发来源，'restore'表示程序恢复，不保存状态
        """
        # 如果树尚未就绪，延迟执行
        if not self._tree_ready:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 树未就绪，延迟选中: {path}")
            self.parent.after(100, lambda: self.select_path(path, click_source))
            return False

        try:
            self._programmatic_selecting = True

            if not path:
                return False

            path = os.path.normpath(path)

            # 检查节点是否存在
            node_id = self._path_to_node.get(path)
            if node_id:
                # 确保父节点展开
                self.expand_to_path(path)

                # 选中节点
                self.tree.selection_set(node_id)
                self.tree.focus(node_id)
                self.tree.see(node_id)
                self._selected_path = path
                self.current_folder = path

                # 只有用户主动点击时才触发回调
                if click_source == 'user' and self.on_select:
                    folder_name = self.tree.item(node_id, 'text')
                    self.on_select(path, folder_name, click_source='user')

                return True

            # 节点不存在
            if self.debug:
                self.debug.print(f"[TreeExplorer] 路径不在树中: {path}")
                self.debug.print(f"[TreeExplorer] 现有节点: {list(self._path_to_node.keys())[:10]}...")
            return False

        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 选中路径异常: {e}")
            return False
        finally:
            self._programmatic_selecting = False

    def expand_to_path(self, path):
        """展开到指定路径的所有父节点"""
        if not path or not self.root_path:
            return False

        path = os.path.normpath(path)
        root_path_norm = os.path.normpath(self.root_path)

        # 检查路径是否在根节点下
        if not path.startswith(root_path_norm):
            return False

        # 逐级展开
        current = path
        while current and current != root_path_norm:
            node = self._path_to_node.get(current)
            if node:
                self.tree.item(node, open=True)

            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        # 展开根节点
        root_node = self._path_to_node.get(root_path_norm)
        if root_node:
            self.tree.item(root_node, open=True)

        return True

    def get_selected_path(self):
        """获取当前选中的路径"""
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            item_data = self.tree.item(item)
            values = item_data.get('values', [])
            if values:
                return values[0]
        return self._selected_path

    def get_state(self):
        """获取树状态（用于保存）"""
        try:
            expanded = []
            for path, node in self._path_to_node.items():
                if self.tree.item(node, 'open'):
                    expanded.append(path)

            return {
                'expanded_paths': expanded,
                'selected_path': self.get_selected_path(),
                'scroll_position': self.get_scroll_position(),
                'root': self.root_path
            }
        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 获取树状态失败: {e}")
            return {
                'expanded_paths': [],
                'selected_path': None,
                'scroll_position': 0.0,
                'root': self.root_path
            }

    def restore_state(self, expanded_paths=None, selected_path=None, scroll_position=0.0):
        """
        恢复树状态（用于启动时恢复）

        Args:
            expanded_paths: 需要展开的路径列表
            selected_path: 需要选中的路径
            scroll_position: 滚动位置
        """
        if not self._tree_ready:
            if self.debug:
                self.debug.print("[TreeExplorer] 树未就绪，延迟恢复状态")
            self.parent.after(100, lambda: self.restore_state(expanded_paths, selected_path, scroll_position))
            return

        try:
            # 展开节点
            if expanded_paths:
                for path in expanded_paths:
                    if not path:
                        continue
                    path = os.path.normpath(path)
                    node = self._path_to_node.get(path)
                    if node:
                        self.tree.item(node, open=True)

            # 选中路径（程序恢复，不触发保存）
            if selected_path:
                restored_path = os.path.normpath(selected_path)
                self.select_path(restored_path, click_source='restore')

            # 恢复滚动位置
            if scroll_position:
                self.set_scroll_position(scroll_position)

        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 恢复状态失败: {e}")

    def get_scroll_position(self):
        """获取滚动位置"""
        try:
            yview = self.tree.yview()
            if yview and len(yview) == 2:
                return float(yview[0])
        except:
            pass
        return 0.0

    def set_scroll_position(self, position):
        """设置滚动位置"""
        try:
            if isinstance(position, (int, float)) and 0 <= float(position) <= 1:
                self.tree.yview_moveto(float(position))
        except Exception as e:
            if self.debug:
                self.debug.print(f"[TreeExplorer] 设置滚动位置失败: {e}")

    def is_ready(self):
        """返回树是否已就绪"""
        return self._tree_ready

    def get_root_path(self):
        """获取根路径"""
        return self.root_path

    def get_cache(self):
        """获取文件夹缓存（供外部使用）"""
        return self._folder_cache

    def clear(self):
        """清空树"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._path_to_node.clear()
        self._folder_cache = {}
        self._selected_path = None
        self._tree_ready = False

    def refresh_from_cache(self, folder_cache=None):
        """
        从外部缓存刷新树结构（用于缓存更新后重建）

        Args:
            folder_cache: 新的文件夹缓存字典，None则使用现有缓存
        """
        if folder_cache is not None:
            self._folder_cache = self._normalize_cache(folder_cache)

        if not self._folder_cache:
            if self.debug:
                self.debug.print("[TreeExplorer] 缓存为空，无法刷新")
            return

        # 保存当前状态
        current_state = self.get_state()

        # 重新加载
        self.load_bookmark(
            root_path=self.root_path,
            device_id=self._device_id,
            bookmark_id=self._bookmark_id,
            folder_cache=self._folder_cache,
            last_folder=current_state.get('selected_path'),
            on_loaded=None
        )

        # 恢复展开状态
        self.restore_state(
            expanded_paths=current_state.get('expanded_paths', []),
            selected_path=current_state.get('selected_path'),
            scroll_position=current_state.get('scroll_position', 0.0)
        )

        if self.debug:
            self.debug.print("[TreeExplorer] 已从缓存刷新树结构")