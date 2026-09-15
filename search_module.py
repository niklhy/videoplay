# -*- coding: utf-8 -*-
"""搜索模块（设计文档第十九章）。

模块职责：跨文件夹搜索（数据库 + 文件系统双路）、关键词高亮、搜索面板 UI。

组成：
    - SearchManager：搜索管理器，负责双路搜索（数据库经 ``db_manager.search_files``，
      文件系统遍历当前设备启用根目录）、结果合并去重（按 relative_path）、
      小缓存防重复搜索、关键词高亮；
    - SearchPanel：搜索面板 UI（输入框 + 按钮 + 范围下拉 + 结果 Treeview +
      结果计数标签），后台线程执行搜索，经 ``after(0)`` 回填 UI；
    - 依赖番号识别引擎 ``PatternRecognitionEngine``（24 号模块，可能并行开发中），
      采用延迟导入，导入失败时回退到内置正则提取。

仅使用标准库 + tkinter；数据类与常量一律从契约文件（models.py / constants.py /
events.py）导入。
"""
import os
import re
import time
import threading
import tkinter as tk
from tkinter import ttk

from constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from events import EVENT_FOLDER_SELECTED
from models import SearchResult, SearchResultItem

# ---------------------------------------------------------------------------
# 番号识别引擎（24 号模块）可选延迟导入：该模块可能并行开发中，导入失败时
# 本模块回退到内置正则提取，不影响搜索功能可用。
# ---------------------------------------------------------------------------
try:
    from pattern_recognition_engine import PatternRecognitionEngine
    _HAS_PATTERN_ENGINE = True
except ImportError:
    PatternRecognitionEngine = None
    _HAS_PATTERN_ENGINE = False

# 搜索范围常量（设计 19.1 节）
SCOPE_CURRENT_DEVICE = "current_device"
SCOPE_ALL_DEVICES = "all_devices"
SCOPE_CURRENT_FOLDER = "current_folder"

# 缓存上限（防止缓存无限增长）
_CACHE_MAX_SIZE = 32

# 内置番号回退正则（PatternRecognitionEngine 不可用时的兜底方案）
_FALLBACK_CODE_RE = re.compile(
    r'[A-Za-z]{2,6}[-_]?\d{2,5}(?:[A-Za-z])?', re.IGNORECASE)

# 匹配字段显示名（中文）
_FIELD_LABELS = {
    "file_name": "文件名",
    "title": "标题",
    "video_code": "番号",
    "actors": "演员",
    "folder_name": "文件夹",
}

# 结果类型显示名（中文）
_TYPE_LABELS = {
    "video": "视频",
    "image": "图片",
    "folder": "文件夹",
}


def _norm_key(path):
    """规范化路径用于比较（统一大小写与分隔符）。

    :param path: 原始路径
    :return: 规范化后的比较键
    """
    return os.path.normcase(os.path.normpath(path or ""))


class SearchManager:
    """搜索管理器（设计文档 19.1 节）。

    双路搜索：数据库（已扫描入库的元数据）+ 文件系统（实时遍历启用根目录），
    结果按 relative_path 合并去重，并统计搜索耗时。

    属性:
        db_manager: DatabaseManager 数据库管理器实例
        async_scanner: AsyncScanner 异步扫描器实例（预留）
        folder_index: FolderIndex 内存文件夹索引实例（预留）
        config_manager: ConfigManager 配置管理器实例
        _search_cache: dict 小缓存，键为 (keyword, scope, fields)，防重复搜索
    """

    def __init__(self, db_manager, async_scanner, folder_index, config_manager):
        """初始化搜索管理器。

        :param db_manager: 数据库管理器（使用其 search_files 接口）
        :param async_scanner: 异步扫描器实例（预留，当前未直接使用）
        :param folder_index: 内存文件夹索引实例（预留，当前未直接使用）
        :param config_manager: 配置管理器（读取设备根目录与上次状态）
        """
        self.db_manager = db_manager
        self.async_scanner = async_scanner
        self.folder_index = folder_index
        self.config_manager = config_manager
        self._search_cache = {}
        self._cache_lock = threading.Lock()
        self._pattern_engine = None  # 番号识别引擎（懒加载单例）

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def search(self, keyword, search_scope="current_device", search_fields=None):
        """执行搜索（设计 19.1 节）。

        双路搜索：数据库（_search_in_database）+ 文件系统
        （_search_in_filesystem），结果按 relative_path 合并去重，
        统计 search_time_ms；带小缓存防重复搜索。

        :param keyword: 搜索关键词（大小写不敏感）
        :param search_scope: 搜索范围，"current_device"（当前设备）/
                             "all_devices"（全部设备）/"current_folder"（当前文件夹）
        :param search_fields: 限定搜索字段列表（如 ["file_name", "title"]），
                              None 表示不限制
        :return: SearchResult 搜索结果对象
        """
        keyword = (keyword or "").strip()
        result = SearchResult(keyword=keyword, search_scope=search_scope)
        if not keyword:
            return result

        fields_key = tuple(sorted(search_fields)) if search_fields else ()
        cache_key = (keyword, search_scope, fields_key)

        # 命中缓存则直接返回（浅拷贝结果，避免调用方改动污染缓存）
        with self._cache_lock:
            cached = self._search_cache.get(cache_key)
        if cached is not None:
            result = SearchResult(
                keyword=cached.keyword,
                total_count=cached.total_count,
                items=list(cached.items),
                search_time_ms=cached.search_time_ms,
                search_scope=cached.search_scope,
            )
            return result

        start = time.time()
        try:
            device_ids = self._resolve_scope_devices(search_scope)
            folder_prefix = self._get_current_folder_prefix(
                search_scope, device_ids)

            # 第一路：数据库搜索（当前设备数据库实例只覆盖本机数据）
            db_items = []
            current_device = self._get_current_device_id()
            if self.db_manager is not None and current_device in device_ids:
                try:
                    db_items = self._search_in_database(keyword, current_device)
                except Exception:
                    db_items = []

            # 第二路：文件系统搜索（遍历各设备启用根目录）
            fs_items = []
            for device_id in device_ids:
                roots = self._get_enabled_roots(device_id)
                if roots:
                    try:
                        fs_items.extend(self._search_in_filesystem(keyword, roots))
                    except Exception:
                        pass

            # 合并去重（按 relative_path）
            merged = {}
            for item in db_items + fs_items:
                key = _norm_key(item.relative_path) or _norm_key(item.file_path)
                if not key:
                    continue
                if key not in merged:
                    merged[key] = item
                else:
                    # 数据库结果优先保留（元数据更完整）
                    old = merged[key]
                    if old.metadata is None and item.metadata is not None:
                        merged[key] = item

            items = list(merged.values())

            # "current_folder" 范围：按当前选中文件夹前缀过滤
            if folder_prefix:
                prefix = _norm_key(folder_prefix)
                items = [
                    it for it in items
                    if _norm_key(it.relative_path).startswith(prefix)
                    or _norm_key(it.parent_folder).startswith(prefix)
                ]

            # 字段限定过滤（对文件系统搜索结果二次确认）
            if fields_key:
                items = [it for it in items if it.matched_field in fields_key]

            result.items = items
            result.total_count = len(items)
        except Exception:
            result.items = []
            result.total_count = 0
        result.search_time_ms = int((time.time() - start) * 1000)

        # 写入缓存（超过上限时按插入顺序淘汰最旧项）
        with self._cache_lock:
            if len(self._search_cache) >= _CACHE_MAX_SIZE:
                oldest = next(iter(self._search_cache), None)
                if oldest is not None:
                    self._search_cache.pop(oldest, None)
            self._search_cache[cache_key] = SearchResult(
                keyword=result.keyword,
                total_count=result.total_count,
                items=list(result.items),
                search_time_ms=result.search_time_ms,
                search_scope=result.search_scope,
            )
        return result

    def highlight_keyword(self, text, keyword):
        """对文本中的关键词加【】高亮标记。

        大小写不敏感；关键词为空或未命中时返回原文。

        :param text: 原始文本
        :param keyword: 关键词
        :return: 带【】标记的文本
        """
        if not text or not keyword:
            return text or ""
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        return pattern.sub(lambda m: "【" + m.group(0) + "】", text)

    def clear_cache(self):
        """清空搜索缓存。"""
        with self._cache_lock:
            self._search_cache.clear()

    # ------------------------------------------------------------------
    # 内部方法：双路搜索
    # ------------------------------------------------------------------
    def _search_in_database(self, keyword, device_id):
        """在数据库中搜索（经 db_manager.search_files）。

        :param keyword: 搜索关键词
        :param device_id: 设备 ID（用于填充结果项的 root_id 归属）
        :return: list[SearchResultItem] 数据库命中结果列表
        """
        if self.db_manager is None:
            return []
        rows = self.db_manager.search_files(keyword) or []
        items = []
        for meta in rows:
            try:
                rel_path = getattr(meta, "relative_path", "") or ""
                file_name = getattr(meta, "file_name", "") or os.path.basename(
                    rel_path.replace("/", os.sep))
                item_type = self._guess_type(file_name)
                matched_field, matched_text = self._pick_matched_field(
                    meta, keyword)
                abs_path = self._resolve_absolute_path(device_id, rel_path)
                parent_rel = os.path.dirname(rel_path.replace("/", os.sep))
                items.append(SearchResultItem(
                    item_id="db:" + rel_path,
                    item_type=item_type,
                    file_path=abs_path,
                    relative_path=rel_path,
                    root_id=(rel_path.split("/", 1)[0] if "/" in rel_path else ""),
                    file_name=file_name,
                    matched_field=matched_field,
                    matched_text=matched_text,
                    thumbnail_path=getattr(meta, "cover_path", "") or "",
                    metadata=meta,
                    parent_folder=os.path.dirname(abs_path) if abs_path else parent_rel,
                ))
            except Exception:
                continue
        return items

    def _search_in_filesystem(self, keyword, root_paths):
        """在文件系统中搜索（遍历启用根目录，文件名/番号匹配）。

        根目录不可访问、遍历中出错等情况一律捕获并跳过，不影响其他根目录。

        :param keyword: 搜索关键词
        :param root_paths: 根目录列表（元素为 RootConfig 对象或路径字符串）
        :return: list[SearchResultItem] 文件系统命中结果列表
        """
        items = []
        keyword_lower = keyword.lower()
        for root in root_paths or []:
            root_id = ""
            if isinstance(root, str):
                root_path = root
            else:
                root_path = getattr(root, "path", "") or ""
                root_id = getattr(root, "root_id", "") or ""
            if not root_path or not os.path.isdir(root_path):
                continue  # 根目录不可访问，跳过

            def _on_walk_error(exc):
                # 遍历过程中单个目录出错（权限/拔出等），跳过继续
                pass

            try:
                for dirpath, dirnames, filenames in os.walk(
                        root_path, onerror=_on_walk_error):
                    # 文件夹名匹配
                    for dirname in dirnames:
                        if keyword_lower in dirname.lower():
                            full = os.path.join(dirpath, dirname)
                            items.append(self._make_fs_item(
                                root_path, root_id, full, dirname,
                                "folder", "folder_name", dirname))
                    # 文件名/番号匹配
                    for filename in filenames:
                        lower_name = filename.lower()
                        matched_field = None
                        matched_text = ""
                        if keyword_lower in lower_name:
                            matched_field = "file_name"
                            matched_text = filename
                        else:
                            code = self._extract_video_code(filename)
                            if code and keyword_lower in code.lower():
                                matched_field = "video_code"
                                matched_text = code
                        if matched_field:
                            full = os.path.join(dirpath, filename)
                            items.append(self._make_fs_item(
                                root_path, root_id, full, filename,
                                self._guess_type(filename),
                                matched_field, matched_text))
            except Exception:
                continue  # 单个根目录整体失败，跳过
        return items

    def _extract_video_code(self, filename):
        """从文件名中提取番号/编号。

        优先使用番号识别引擎 PatternRecognitionEngine.extract_code
        （24 号模块，延迟导入）；不可用时回退到内置正则。

        :param filename: 文件名
        :return: 提取到的番号字符串，失败返回空字符串
        """
        if not filename:
            return ""
        if _HAS_PATTERN_ENGINE:
            try:
                if self._pattern_engine is None:
                    self._pattern_engine = PatternRecognitionEngine(self.db_manager)
                recognition = self._pattern_engine.extract_code(
                    filename, context="search")
                code = getattr(recognition, "code", "") or ""
                return str(code).strip()
            except Exception:
                pass  # 引擎异常时回退到内置正则
        base = os.path.splitext(os.path.basename(filename))[0]
        match = _FALLBACK_CODE_RE.search(base)
        return match.group(0) if match else ""

    # ------------------------------------------------------------------
    # 内部方法：辅助
    # ------------------------------------------------------------------
    def _resolve_scope_devices(self, search_scope):
        """根据搜索范围解析需要检索的设备 ID 列表。

        :param search_scope: 搜索范围
        :return: list[str] 设备 ID 列表
        """
        current = self._get_current_device_id()
        if search_scope == SCOPE_ALL_DEVICES:
            devices = []
            try:
                data = getattr(self.config_manager, "data", {}) or {}
                all_devices = data.get("devices", {}) or {}
                if isinstance(all_devices, dict):
                    devices = [str(d) for d in all_devices.keys()]
            except Exception:
                devices = []
            if current and current not in devices:
                devices.append(current)
            return devices
        # current_device / current_folder / 未知范围均按当前设备处理
        return [current] if current else []

    def _get_current_device_id(self):
        """获取当前设备 ID（经配置管理器）。"""
        try:
            if self.config_manager is not None:
                return str(self.config_manager.get_current_device_id() or "")
        except Exception:
            pass
        return ""

    def _get_enabled_roots(self, device_id):
        """获取指定设备已启用的根目录列表。

        :param device_id: 设备 ID
        :return: list[RootConfig] 启用的根目录对象列表
        """
        if self.config_manager is None:
            return []
        try:
            roots = self.config_manager.get_device_roots(device_id) or []
        except Exception:
            return []
        return [r for r in roots if getattr(r, "enabled", True)]

    def _get_current_folder_prefix(self, search_scope, device_ids):
        """获取"当前文件夹"范围的相对路径前缀。

        :param search_scope: 搜索范围
        :param device_ids: 已解析的设备列表
        :return: 相对路径前缀（非 current_folder 范围返回空字符串）
        """
        if search_scope != SCOPE_CURRENT_FOLDER or not device_ids:
            return ""
        try:
            state = self.config_manager.get_last_state(device_ids[0])
            return (getattr(state, "selected_folder", "") or "").strip()
        except Exception:
            return ""

    def _resolve_absolute_path(self, device_id, relative_path):
        """将相对路径（首段为根目录 ID）解析为绝对路径。

        :param device_id: 设备 ID
        :param relative_path: 相对路径，如 "D1/D11/xxx.mp4"
        :return: 绝对路径；无法解析时返回空字符串
        """
        if not relative_path:
            return ""
        parts = relative_path.split("/", 1)
        if len(parts) < 2:
            return ""
        root_id, rest = parts[0], parts[1]
        try:
            root = self.config_manager.get_root_by_id(root_id, device_id)
        except Exception:
            root = None
        if root is None or not getattr(root, "path", ""):
            return ""
        return os.path.join(root.path, rest.replace("/", os.sep))

    def _make_fs_item(self, root_path, root_id, full_path, name,
                      item_type, matched_field, matched_text):
        """构造一个文件系统搜索结果项。

        :param root_path: 所属根目录绝对路径
        :param root_id: 根目录 ID
        :param full_path: 文件/文件夹绝对路径
        :param name: 文件/文件夹名
        :param item_type: 结果类型（video/image/folder）
        :param matched_field: 匹配字段标识
        :param matched_text: 匹配到的文本
        :return: SearchResultItem
        """
        try:
            rel = os.path.relpath(full_path, root_path)
            rel = rel.replace(os.sep, "/")
            relative_path = (root_id + "/" + rel) if root_id else rel
        except Exception:
            relative_path = name
        parent_rel = os.path.dirname(relative_path.replace("/", os.sep))
        return SearchResultItem(
            item_id="fs:" + relative_path,
            item_type=item_type,
            file_path=full_path,
            relative_path=relative_path,
            root_id=root_id,
            file_name=name,
            matched_field=matched_field,
            matched_text=matched_text,
            thumbnail_path="",
            metadata=None,
            parent_folder=parent_rel,
        )

    @staticmethod
    def _guess_type(file_name):
        """根据扩展名猜测结果类型（video/image/folder）。"""
        ext = os.path.splitext(file_name or "")[1].lower()
        if ext in VIDEO_EXTENSIONS:
            return "video"
        if ext in IMAGE_EXTENSIONS:
            return "image"
        return "folder"

    @staticmethod
    def _pick_matched_field(meta, keyword):
        """从文件元数据中挑出包含关键词的字段及值。

        依次检查：文件名、标题、番号、演员列表。

        :param meta: FileMetadata 对象
        :param keyword: 搜索关键词
        :return: (matched_field, matched_text) 元组
        """
        kw = (keyword or "").lower()

        def _hit(value):
            return bool(value) and kw in str(value).lower()

        file_name = getattr(meta, "file_name", "") or ""
        if _hit(file_name):
            return "file_name", file_name
        title = getattr(meta, "title", "") or ""
        if _hit(title):
            return "title", title
        code = getattr(meta, "video_code", "") or ""
        if _hit(code):
            return "video_code", code
        actors = getattr(meta, "actor_names", None) or []
        for actor in actors:
            if _hit(actor):
                return "actors", str(actor)
        # 均未命中（可能由数据库内部其他字段匹配），兜底用文件名
        return "file_name", file_name or getattr(meta, "relative_path", "")


class SearchPanel:
    """搜索面板 UI 组件（设计文档 19.3 节）。

    布局：顶部搜索栏（输入框 + 按钮 + 范围下拉），中部结果 Treeview
    （列：文件名/匹配字段/所在文件夹/类型），底部结果计数标签。
    搜索在后台线程执行，完成后经 ``after(0)`` 回到主线程回填结果。
    双击结果项经 event_bus 发布 EVENT_FOLDER_SELECTED 请求跳转到所在文件夹。

    快捷键（设计 19.4 节）：
        Enter 执行搜索；Ctrl+Enter 在当前文件夹内搜索；
        Escape 关闭面板；F3 查找下一个；Shift+F3 查找上一个。
        Ctrl+F 由主程序绑定到 focus_search()。

    属性:
        parent: tk.Widget 父容器
        search_manager: SearchManager 搜索管理器
        event_bus: EventBus 事件总线
        search_entry: ttk.Entry 搜索输入框
        search_btn: ttk.Button 搜索按钮
        scope_combo: ttk.Combobox 范围下拉框
        result_tree: ttk.Treeview 结果列表
        result_count_label: tk.Label 结果计数标签
    """

    # 范围下拉选项（值 -> 显示文本）
    _SCOPE_OPTIONS = (
        (SCOPE_CURRENT_DEVICE, "当前设备"),
        (SCOPE_ALL_DEVICES, "全部设备"),
    )

    def __init__(self, parent, search_manager, event_bus):
        """初始化搜索面板（创建 UI 后默认隐藏）。

        :param parent: 父容器控件
        :param search_manager: SearchManager 实例
        :param event_bus: EventBus 实例
        """
        self.parent = parent
        self.search_manager = search_manager
        self.event_bus = event_bus
        self.frame = ttk.Frame(parent)
        self._visible = False
        self._searching = False
        self._item_map = {}       # tree iid -> SearchResultItem
        self._tree_iids = []      # 结果 iid 顺序列表
        self._current_index = -1  # F3 导航当前位置
        self._create_ui()
        self._bind_shortcuts()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _create_ui(self):
        """创建面板 UI：搜索栏 + 结果 Treeview + 计数标签。"""
        # 顶部搜索栏
        top_bar = ttk.Frame(self.frame)
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)

        self.search_entry = ttk.Entry(top_bar)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind("<Return>", lambda e: self._on_search())

        self.search_btn = ttk.Button(
            top_bar, text="搜索", command=self._on_search)
        self.search_btn.pack(side=tk.LEFT, padx=(6, 0))

        scope_values = [label for _, label in self._SCOPE_OPTIONS]
        self.scope_combo = ttk.Combobox(
            top_bar, values=scope_values, state="readonly", width=10)
        self.scope_combo.current(0)
        self.scope_combo.pack(side=tk.LEFT, padx=(6, 0))

        # 结果 Treeview（带滚动条）
        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                        padx=6, pady=(0, 4))
        columns = ("file_name", "matched_field", "parent_folder", "item_type")
        self.result_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse")
        self.result_tree.heading("file_name", text="文件名")
        self.result_tree.heading("matched_field", text="匹配字段")
        self.result_tree.heading("parent_folder", text="所在文件夹")
        self.result_tree.heading("item_type", text="类型")
        self.result_tree.column("file_name", width=220, anchor=tk.W)
        self.result_tree.column("matched_field", width=70, anchor=tk.CENTER)
        self.result_tree.column("parent_folder", width=260, anchor=tk.W)
        self.result_tree.column("item_type", width=50, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_tree.bind("<<TreeviewSelect>>", self._on_result_select)
        self.result_tree.bind("<Double-1>", self._on_result_double_click)
        self.result_tree.bind("<Return>", self._on_result_double_click)

        # 底部计数标签
        self.result_count_label = tk.Label(
            self.frame, text="输入关键词后按 Enter 搜索", anchor=tk.W)
        self.result_count_label.pack(side=tk.BOTTOM, fill=tk.X,
                                     padx=6, pady=(0, 4))

    def _bind_shortcuts(self):
        """绑定面板内快捷键（设计 19.4 节）。

        Enter 执行搜索（在输入框上绑定）；Ctrl+Enter 在当前文件夹内搜索；
        Escape 关闭面板；F3 查找下一个；Shift+F3 查找上一个。
        """
        self.search_entry.bind("<Control-Return>",
                               lambda e: self._on_search(SCOPE_CURRENT_FOLDER))
        for widget in (self.search_entry, self.result_tree):
            widget.bind("<Escape>", lambda e: self.hide())
            widget.bind("<F3>", lambda e: self._find_next())
            widget.bind("<Shift-F3>", lambda e: self._find_previous())

    # ------------------------------------------------------------------
    # 搜索执行与结果回填
    # ------------------------------------------------------------------
    def _on_search(self, override_scope=None):
        """执行搜索（后台线程），完成后经 after(0) 回填结果。

        :param override_scope: 覆盖范围下拉的搜索范围（如 Ctrl+Enter
                               传入 SCOPE_CURRENT_FOLDER），None 表示用下拉框值
        """
        if self._searching:
            return
        keyword = self.search_entry.get().strip()
        if not keyword:
            self.result_count_label.config(text="请输入搜索关键词")
            return
        if override_scope is not None:
            search_scope = override_scope
        else:
            display = self.scope_combo.get()
            search_scope = SCOPE_CURRENT_DEVICE
            for value, label in self._SCOPE_OPTIONS:
                if label == display:
                    search_scope = value
                    break

        self._searching = True
        self.search_btn.config(state=tk.DISABLED)
        self.result_count_label.config(text="搜索中...")

        def _worker():
            try:
                result = self.search_manager.search(keyword, search_scope)
            except Exception as exc:
                result = None
                error = str(exc)
            else:
                error = ""
            # 回到主线程回填 UI
            self.frame.after(0, lambda: self._display_results(
                result, error, search_scope))

        threading.Thread(target=_worker, daemon=True).start()

    def _display_results(self, result, error, search_scope):
        """在主线程回填搜索结果到 Treeview（含高亮与计数）。

        :param result: SearchResult 结果对象（失败时为 None）
        :param error: 错误信息（成功时为空字符串）
        :param search_scope: 本次搜索范围
        """
        self._searching = False
        self.search_btn.config(state=tk.NORMAL)
        self.result_tree.delete(*self.result_tree.get_children())
        self._item_map.clear()
        self._tree_iids = []
        self._current_index = -1

        if result is None:
            self.result_count_label.config(text="搜索失败：" + (error or "未知错误"))
            return

        keyword = result.keyword
        for index, item in enumerate(result.items):
            iid = str(index)
            display_name = self.search_manager.highlight_keyword(
                item.file_name, keyword)
            field_label = _FIELD_LABELS.get(item.matched_field,
                                            item.matched_field)
            type_label = _TYPE_LABELS.get(item.item_type, item.item_type)
            self.result_tree.insert(
                "", tk.END, iid=iid,
                values=(display_name, field_label,
                        item.parent_folder, type_label))
            self._item_map[iid] = item
            self._tree_iids.append(iid)

        scope_label = dict((v, l) for v, l in self._SCOPE_OPTIONS).get(
            search_scope, search_scope)
        self.result_count_label.config(text="共 {} 条结果（{}，耗时 {} ms）".format(
            result.total_count, scope_label, result.search_time_ms))

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_result_select(self, event):
        """结果项选中事件：更新计数标签显示当前选中项。"""
        selection = self.result_tree.selection()
        if not selection:
            return
        iid = selection[0]
        if iid in self._item_map:
            item = self._item_map[iid]
            self.result_count_label.config(text="选中：{}（{}）".format(
                item.file_name,
                _TYPE_LABELS.get(item.item_type, item.item_type)))

    def _on_result_double_click(self, event):
        """结果项双击：经 event_bus 发布 EVENT_FOLDER_SELECTED 请求跳转。

        payload 含 folder_path（结果项所在文件夹绝对路径，缺失时用
        file_path 的目录名）。
        """
        iid = self.result_tree.focus()
        if not iid and hasattr(event, "widget"):
            row_id = event.widget.identify_row(event.y)
            iid = row_id or ""
        item = self._item_map.get(iid)
        if item is None:
            return
        folder_path = item.parent_folder
        if not folder_path and item.file_path:
            folder_path = os.path.dirname(item.file_path)
        if not folder_path:
            return
        self._publish_folder_selected(folder_path, item)

    def _publish_folder_selected(self, folder_path, item):
        """发布文件夹选中事件（优先异步 publish，退化为 publish_sync）。

        :param folder_path: 目标文件夹路径
        :param item: 触发跳转的 SearchResultItem
        """
        if self.event_bus is None:
            return
        payload = {
            "folder_path": folder_path,
            "relative_path": item.relative_path,
            "root_id": item.root_id,
        }
        try:
            if hasattr(self.event_bus, "publish"):
                self.event_bus.publish(EVENT_FOLDER_SELECTED, **payload)
            elif hasattr(self.event_bus, "publish_sync"):
                self.event_bus.publish_sync(EVENT_FOLDER_SELECTED, **payload)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # F3 / Shift+F3 结果导航
    # ------------------------------------------------------------------
    def _find_next(self):
        """查找下一个：选中结果列表中的下一项。"""
        self._navigate_results(1)

    def _find_previous(self):
        """查找上一个：选中结果列表中的上一项。"""
        self._navigate_results(-1)

    def _navigate_results(self, step):
        """在结果列表中按步长移动选中项。

        :param step: 步长（+1 下一个，-1 上一个）
        """
        total = len(self._tree_iids)
        if total == 0:
            return
        self._current_index = (self._current_index + step) % total
        iid = self._tree_iids[self._current_index]
        self.result_tree.selection_set(iid)
        self.result_tree.focus(iid)
        self.result_tree.see(iid)

    # ------------------------------------------------------------------
    # 显示控制
    # ------------------------------------------------------------------
    def show(self):
        """显示搜索面板。"""
        if self._visible:
            return
        self._visible = True
        self.frame.pack(fill=tk.BOTH, expand=True)

    def hide(self):
        """隐藏搜索面板。"""
        if not self._visible:
            return
        self._visible = False
        self.frame.pack_forget()

    def toggle(self):
        """切换搜索面板显示/隐藏。"""
        if self._visible:
            self.hide()
        else:
            self.show()

    def focus_search(self):
        """聚焦搜索输入框（供主程序绑定 Ctrl+F）。

        面板隐藏时先显示，然后聚焦输入框并全选已有内容。
        """
        self.show()
        self.search_entry.focus_set()
        try:
            self.search_entry.selection_range(0, tk.END)
        except tk.TclError:
            pass
