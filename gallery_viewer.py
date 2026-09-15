# -*- coding: utf-8 -*-
"""图库浏览器模块（对应设计文档第11章）。

本模块是软件核心，包含三个类：

- ``GalleryViewer``：缩略图网格/列表渲染、视图控制、防抖动交互、
  异步缩略图加载（分批，每批20个，经 ``widget.after`` 调度避免卡顿）、
  演员过滤、纯图片文件夹图库模式；
- ``ThumbnailMatcher``：五种视频-图片匹配模式
  （完全同名 / 封面匹配 / 数据库关联 / 仅图片 / 封面优先）；
- ``ThumbnailSwitcher``：封面优先模式下的缩略图左右切换按钮。

仅使用标准库 + tkinter + Pillow；公共契约（数据类、常量、事件）一律从
``models.py`` / ``constants.py`` / ``events.py`` 导入。
"""
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk

from constants import (
    COVER_KEYWORDS,
    IMAGE_EXTENSIONS,
    MODE_COVER_MATCH,
    MODE_COVER_PRIORITY,
    MODE_EXCLUSIVE_VIDEO,
    MODE_IMAGE_ONLY,
    MODE_VIDEO_LINKED,
    THUMBNAIL_BG_HOVER,
    THUMBNAIL_BG_NORMAL,
    THUMBNAIL_BG_SELECTED,
    THUMBNAIL_BORDER_HOVER,
    THUMBNAIL_BORDER_NORMAL,
    THUMBNAIL_BORDER_SELECTED,
    THUMBNAIL_BORDER_WIDTH,
    THUMBNAIL_DEFAULT_SIZE,
    THUMBNAIL_GAP,
    THUMBNAIL_PADDING,
    THUMBNAIL_SIZES,
    VIDEO_EXTENSIONS,
)
from events import (
    EVENT_GALLERY_DATA_READY,
    EVENT_GALLERY_LOAD_REQUEST,
    EVENT_THUMBNAIL_READY,
    EVENT_THUMBNAIL_SIZE_CHANGED,
)
from models import GalleryItem

# 缩略图每批加载数量（经 widget.after 调度，避免一次性加载卡顿）
THUMBNAIL_BATCH_SIZE = 20
# 文字区域预估高度（像素）
CAPTION_HEIGHT = 36
# 上一张/下一张按钮的步进方向
SWITCH_PREVIOUS = -1
SWITCH_NEXT = 1


class GalleryViewer:
    """图库浏览器：缩略图网格/列表渲染与交互。

    属性:
        canvas: 滚动画布
        event_bus: 事件总线（events.py 契约）
        db_manager: 数据库管理器（用于视频关联查询）
        async_scanner: 异步扫描器（用于媒体文件识别）
        thumbnail_cache: 缩略图缓存（延迟导入 thumbnail_cache 模块）
        current_folder: 当前加载的文件夹路径
        items: 当前显示的图库项列表
        _load_queue: 缩略图加载队列
        _current_size_key: 当前缩略图尺寸键
        _thumbnail_widgets: item_id -> 控件引用字典
        _selected_item_id: 当前选中项ID
        _container_frame: 缩略图容器（Canvas 内窗口）
        _scrollbar: 滚动条
    """

    def __init__(self, parent_widget, event_bus, db_manager, async_scanner,
                 thumbnail_size=THUMBNAIL_DEFAULT_SIZE):
        """初始化图库浏览器。

        参数:
            parent_widget: 父控件
            event_bus: 事件总线实例
            db_manager: 数据库管理器实例
            async_scanner: 异步扫描器实例
            thumbnail_size: 缩略图尺寸键（small/medium/large/xlarge）
        """
        self.parent = parent_widget
        self.root = parent_widget.winfo_toplevel()
        self.event_bus = event_bus
        self.db_manager = db_manager
        self.async_scanner = async_scanner
        self.thumbnail_cache = None
        self.current_folder = ""
        self.items = []
        self._all_items = []          # 过滤前的完整项列表
        self._load_queue = queue.Queue()
        self._pending_items = []      # 待加载缩略图的项（分批消费）
        self._current_size_key = (
            thumbnail_size if thumbnail_size in THUMBNAIL_SIZES
            else THUMBNAIL_DEFAULT_SIZE
        )
        self._thumbnail_widgets = {}
        self._selected_item_id = None

        # --- 滚动画布 + 容器框架 ---
        self.canvas = tk.Canvas(
            parent_widget,
            bg=THUMBNAIL_BG_NORMAL,
            highlightthickness=0,
        )
        self._scrollbar = ttk.Scrollbar(
            parent_widget, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self._container_frame = tk.Frame(self.canvas, bg=THUMBNAIL_BG_NORMAL)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self._container_frame, anchor="nw"
        )
        self._container_frame.bind("<Configure>", self._on_container_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 加载中提示标签（默认隐藏）
        self._loading_label = tk.Label(
            self.canvas,
            text="加载中...",
            bg=THUMBNAIL_BG_NORMAL,
            fg="#cccccc",
            font=("Microsoft YaHei UI", 10),
        )

        # 鼠标滚轮绑定到 Canvas 及所有子控件（递归）
        self._bind_mousewheel(self.canvas)
        self._bind_mousewheel(self._container_frame)

    # ------------------------------------------------------------------
    # 视图控制
    # ------------------------------------------------------------------
    def set_thumbnail_size(self, size_key: str) -> None:
        """设置缩略图尺寸并重新渲染。

        参数:
            size_key: 尺寸键（small/medium/large/xlarge），非法键忽略
        """
        if size_key not in THUMBNAIL_SIZES:
            return
        if size_key == self._current_size_key:
            return
        self._current_size_key = size_key
        self.event_bus.publish(
            EVENT_THUMBNAIL_SIZE_CHANGED, size_key=size_key
        )
        self._display_items(self.items, layout="grid")
        self._load_thumbnails_async()

    def get_thumbnail_size(self) -> str:
        """获取当前缩略图尺寸键。"""
        return self._current_size_key

    def get_current_size_config(self) -> dict:
        """获取当前尺寸配置（THUMBNAIL_SIZES 中的子字典）。"""
        return THUMBNAIL_SIZES[self._current_size_key]

    def _calculate_thumbnail_dimensions(self, image_path: str) -> tuple:
        """根据原图比例计算缩略图实际宽高（设计 11.6.1 节）。

        规则:
            1. 宽度固定为当前设置的尺寸值（150/200/250/300）
            2. 高度根据原图宽高比计算: height = width * (orig_h / orig_w)
            3. 最小高度限制: 不低于当前尺寸配置中的 min_height
            4. 最大高度限制: 不超过 width * 2（防止极端竖图）

        参数:
            image_path: 原图路径

        返回:
            (宽度, 高度) 元组
        """
        size_config = THUMBNAIL_SIZES[self._current_size_key]
        target_width = size_config["width"]

        # 读取原图尺寸（使用缩略图缓存，失败时用 PIL，再失败按 0 处理）
        orig_w, orig_h = 0, 0
        if image_path and os.path.isfile(image_path):
            cache = self._get_thumbnail_cache()
            try:
                if cache is not None and hasattr(cache, "get_original_size"):
                    orig_w, orig_h = cache.get_original_size(image_path)
                else:
                    from PIL import Image
                    with Image.open(image_path) as im:
                        orig_w, orig_h = im.size
            except Exception:
                orig_w, orig_h = 0, 0

        if orig_w == 0:
            return target_width, size_config["min_height"]

        # 按比例计算高度
        aspect_ratio = orig_h / orig_w
        target_height = int(target_width * aspect_ratio)

        # 限制范围
        min_h = size_config["min_height"]
        max_h = target_width * 2
        target_height = max(min_h, min(target_height, max_h))

        return target_width, target_height

    def _get_container_width(self) -> int:
        """获取缩略图容器可用宽度（画布宽度减去滚动条余量）。"""
        try:
            width = self.canvas.winfo_width()
            if width <= 1:
                width = self.parent.winfo_width()
        except Exception:
            width = 800
        return max(1, width - 4)

    def _calculate_columns(self) -> int:
        """根据容器宽度和缩略图宽度计算每行列数（设计 11.6.3 节）。"""
        container_width = self._get_container_width()
        size_config = THUMBNAIL_SIZES[self._current_size_key]
        thumb_width = size_config["width"]

        # 单个缩略图占用的总宽度 = 图片宽 + 左右内边距 + 左右边框 + 间距
        item_total_width = (
            thumb_width
            + THUMBNAIL_PADDING * 2
            + THUMBNAIL_BORDER_WIDTH * 2
            + THUMBNAIL_GAP
        )

        columns = max(1, container_width // item_total_width)
        return columns

    # ------------------------------------------------------------------
    # 渲染与交互（设计 11.6.2 防抖动边框方案 / 11.6.4 状态样式表）
    # ------------------------------------------------------------------
    def _create_thumbnail_container(self, parent: tk.Widget,
                                    item: GalleryItem) -> tk.Frame:
        """创建单个缩略图容器（含防抖动边框处理）。

        防抖动原理:
            1. outer 容器固定尺寸，pack_propagate(False) 确保子控件
               不会撑大/缩小容器；
            2. inner 容器使用 place(relx=0.5, rely=0.5, anchor="center")
               居中定位；
            3. 边框通过 highlightthickness 实现，改变颜色时厚度不变，
               容器尺寸不变；
            4. 所有交互事件绑定到 outer 容器，确保鼠标移入热区足够大。

        参数:
            parent: 父控件
            item: 图库项

        返回:
            外层容器 Frame（固定尺寸，负责布局定位）
        """
        size_config = THUMBNAIL_SIZES[self._current_size_key]
        target_width = size_config["width"]
        target_height = self._calculate_thumbnail_dimensions(
            item.thumbnail_path
        )[1]
        caption_height = CAPTION_HEIGHT  # 文字区域预估高度

        # 外层容器: 固定尺寸，负责布局定位，永不改变大小
        outer = tk.Frame(
            parent,
            width=target_width + THUMBNAIL_PADDING * 2
            + THUMBNAIL_BORDER_WIDTH * 2,
            height=target_height + THUMBNAIL_PADDING * 2
            + THUMBNAIL_BORDER_WIDTH * 2 + caption_height,
            bg=THUMBNAIL_BG_NORMAL,
            padx=0, pady=0,
            highlightthickness=0,  # 禁用tkinter原生highlight边框
            bd=0,                  # 禁用tkinter原生border
        )
        outer.pack_propagate(False)  # 关键: 禁止子控件改变容器大小

        # 内层容器: 负责显示边框和背景色变化
        inner = tk.Frame(
            outer,
            bg=THUMBNAIL_BG_NORMAL,
            highlightbackground=THUMBNAIL_BORDER_NORMAL,
            highlightcolor=THUMBNAIL_BORDER_NORMAL,
            highlightthickness=THUMBNAIL_BORDER_WIDTH,
            bd=0,
        )
        inner.place(
            relx=0.5, rely=0.5,
            width=target_width + THUMBNAIL_PADDING * 2,
            height=target_height + THUMBNAIL_PADDING * 2 + caption_height,
            anchor="center",
        )

        # 图片显示区（Label）
        img_label = tk.Label(
            inner,
            bg=THUMBNAIL_BG_NORMAL,
            bd=0, highlightthickness=0,
        )
        img_label.pack(pady=(THUMBNAIL_PADDING, 0))

        # 文字信息区（文件名/演员等）
        caption_label = tk.Label(
            inner,
            text=item.display_name,
            bg=THUMBNAIL_BG_NORMAL,
            fg="#cccccc",
            font=("Microsoft YaHei UI", 8),
            wraplength=target_width,
            justify="center",
        )
        caption_label.pack(pady=(2, THUMBNAIL_PADDING))

        # 封面优先模式：叠加左右切换按钮
        if (item.match_mode == MODE_COVER_PRIORITY
                and len(item.image_paths) > 1):
            switcher = ThumbnailSwitcher.create_switcher(inner, item)
            switcher.place(
                relx=0.5, rely=1.0, anchor="s",
                y=-THUMBNAIL_PADDING - 2,
            )
            # 切换后刷新缩略图显示与按钮状态
            item._on_image_changed = (
                lambda it=item, sw=switcher: self._on_switch_image(it, sw)
            )

        # 绑定事件到外层容器（更大的热区）
        outer.bind(
            "<Enter>",
            lambda e: self._on_thumbnail_enter(item, inner, e)
        )
        outer.bind(
            "<Leave>",
            lambda e: self._on_thumbnail_leave(item, inner, e)
        )
        outer.bind(
            "<Button-1>",
            lambda e: self._on_thumbnail_click(item, e)
        )
        outer.bind(
            "<Double-Button-1>",
            lambda e: self._on_item_double_click(item)
        )
        img_label.bind(
            "<Button-1>",
            lambda e: self._on_thumbnail_click(item, e)
        )
        caption_label.bind(
            "<Button-1>",
            lambda e: self._on_thumbnail_click(item, e)
        )

        # 右键菜单：发布 "context_menu_request" 事件（由主程序订阅弹出菜单，
        # 事件方式避免本模块反向依赖主程序 / 右键菜单模块造成循环导入）
        outer.bind(
            "<Button-3>",
            lambda e: self._on_thumbnail_right_click(item, e)
        )
        img_label.bind(
            "<Button-3>",
            lambda e: self._on_thumbnail_right_click(item, e)
        )
        caption_label.bind(
            "<Button-3>",
            lambda e: self._on_thumbnail_right_click(item, e)
        )

        # 鼠标滚轮递归绑定到新容器
        self._bind_mousewheel(outer)

        self._thumbnail_widgets[item.item_id] = {
            "container": outer,
            "inner": inner,
            "img_label": img_label,
            "caption_label": caption_label,
            "photo": None,   # PhotoImage 引用，防止被垃圾回收
            "item": item,
        }
        return outer

    def _on_thumbnail_enter(self, item: GalleryItem,
                            inner_container: tk.Frame, event) -> None:
        """鼠标移入: 改变背景色和边框色（选中状态优先）。"""
        if item.item_id == self._selected_item_id:
            return  # 选中状态优先
        inner_container.config(
            bg=THUMBNAIL_BG_HOVER,
            highlightbackground=THUMBNAIL_BORDER_HOVER,
        )
        # 同步更新所有子控件背景色
        for child in inner_container.winfo_children():
            try:
                child.config(bg=THUMBNAIL_BG_HOVER)
            except Exception:
                pass
        # 悬停文字变白（11.6.4 状态样式表）
        self._set_caption_color(inner_container, "#ffffff")

    def _on_thumbnail_leave(self, item: GalleryItem,
                            inner_container: tk.Frame, event) -> None:
        """鼠标移出: 恢复默认样式（选中状态优先）。"""
        if item.item_id == self._selected_item_id:
            return  # 选中状态优先
        inner_container.config(
            bg=THUMBNAIL_BG_NORMAL,
            highlightbackground=THUMBNAIL_BORDER_NORMAL,
        )
        for child in inner_container.winfo_children():
            try:
                child.config(bg=THUMBNAIL_BG_NORMAL)
            except Exception:
                pass
        self._set_caption_color(inner_container, "#cccccc")

    @staticmethod
    def _set_caption_color(inner_container: tk.Frame, color: str) -> None:
        """设置内层容器中文字标签的前景色。"""
        children = inner_container.winfo_children()
        # 子控件顺序: [图片Label, 文字Label, (可选)切换按钮框架]
        for child in children:
            try:
                if child.winfo_class() == "Label" and child.cget("text"):
                    child.config(fg=color)
            except Exception:
                pass

    def _on_thumbnail_click(self, item: GalleryItem, event) -> None:
        """鼠标单击: 选中缩略图并同步信息面板（经事件总线）。"""
        self._selected_item_id = item.item_id
        self._update_thumbnail_selection(item.item_id)
        self.event_bus.publish(
            EVENT_GALLERY_DATA_READY,
            selected_item=item,
            items=self.items,
            folder_path=self.current_folder,
        )

    def _on_thumbnail_right_click(self, item: GalleryItem, event) -> None:
        """鼠标右键: 先选中该缩略图，再发布右键菜单请求事件。

        事件名 "context_menu_request"（契约：主程序 main.py 订阅该事件并
        调用 ContextMenuManager.show_thumbnail_menu(item, x, y) 弹出菜单）。
        """
        try:
            self._selected_item_id = item.item_id
            self._update_thumbnail_selection(item.item_id)
        except Exception:
            pass  # 选中样式更新失败不阻塞菜单弹出
        self.event_bus.publish(
            "context_menu_request",
            item=item,
            x=int(getattr(event, "x_root", 0) or 0),
            y=int(getattr(event, "y_root", 0) or 0),
        )

    def _update_thumbnail_selection(self, item_id: str) -> None:
        """更新所有缩略图的选中/默认样式（11.6.4 状态样式表）。

        选中状态边框宽度为 2px；由于内层容器使用 place 定位且外层容器
        固定尺寸，边框变宽不会导致抖动。
        """
        for current_id, entry in self._thumbnail_widgets.items():
            inner = entry.get("inner")
            caption = entry.get("caption_label")
            if inner is None or not inner.winfo_exists():
                continue
            if current_id == item_id:
                inner.config(
                    bg=THUMBNAIL_BG_SELECTED,
                    highlightbackground=THUMBNAIL_BORDER_SELECTED,
                    highlightthickness=2,
                )
                for child in inner.winfo_children():
                    try:
                        child.config(bg=THUMBNAIL_BG_SELECTED)
                    except Exception:
                        pass
                if caption is not None and caption.winfo_exists():
                    caption.config(fg="#ffffff")
            else:
                inner.config(
                    bg=THUMBNAIL_BG_NORMAL,
                    highlightbackground=THUMBNAIL_BORDER_NORMAL,
                    highlightthickness=THUMBNAIL_BORDER_WIDTH,
                )
                for child in inner.winfo_children():
                    try:
                        child.config(bg=THUMBNAIL_BG_NORMAL)
                    except Exception:
                        pass
                if caption is not None and caption.winfo_exists():
                    caption.config(fg="#cccccc")

    def _render_grid(self) -> None:
        """渲染网格布局（设计 11.6.3 节）。"""
        columns = self._calculate_columns()

        for index, item in enumerate(self.items):
            row = index // columns
            col = index % columns

            container = self._create_thumbnail_container(
                self._container_frame, item
            )
            container.grid(
                row=row, column=col,
                padx=THUMBNAIL_GAP // 2, pady=THUMBNAIL_GAP // 2,
            )

    def _render_list(self) -> None:
        """渲染列表布局（每项一行，纵向排列）。"""
        for item in self.items:
            container = self._create_thumbnail_container(
                self._container_frame, item
            )
            container.pack(fill="x", pady=THUMBNAIL_GAP // 2)

    # ------------------------------------------------------------------
    # 加载与刷新
    # ------------------------------------------------------------------
    def load_folder(self, folder_path: str) -> None:
        """加载指定文件夹到图库。

        流程: 清空 -> 异步扫描匹配 -> 渲染 -> 异步加载缩略图
        （分批，每批20个，经 widget.after 调度避免卡顿）。

        参数:
            folder_path: 文件夹绝对路径
        """
        if not folder_path or not os.path.isdir(folder_path):
            return
        self.current_folder = folder_path
        self.clear()
        self.show_loading()
        self.event_bus.publish(
            EVENT_GALLERY_LOAD_REQUEST, folder_path=folder_path
        )

        # 后台线程执行扫描匹配（工作线程不触碰UI），
        # 结果放入实例属性，由主线程经 after 轮询取回后渲染
        self._load_pending = True
        self._load_result = None

        def _worker():
            try:
                mode = self._determine_display_mode(folder_path)
                if mode == "pure_image":
                    items = self._build_pure_image_items(folder_path)
                else:
                    items = self._scan_and_match(folder_path)
            except Exception:
                items = []
            self._load_result = items
            self._load_pending = False

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_load_result()

    def _poll_load_result(self) -> None:
        """主线程轮询后台扫描结果，完成后渲染（线程安全切换）。"""
        if getattr(self, "_load_pending", False):
            self.root.after(50, self._poll_load_result)
            return
        items = getattr(self, "_load_result", None)
        self._load_result = None
        if items is not None:
            self._on_load_complete(items)

    def _on_load_complete(self, items: list) -> None:
        """加载完成（主线程）：渲染并启动异步缩略图加载。"""
        self.hide_loading()
        self._all_items = list(items)
        self._display_items(items, layout="grid")
        self._load_thumbnails_async()
        self.event_bus.publish(
            EVENT_GALLERY_DATA_READY,
            items=items,
            folder_path=self.current_folder,
            selected_item=None,
        )

    def clear(self) -> None:
        """清空图库显示。"""
        for child in self._container_frame.winfo_children():
            child.destroy()
        self._thumbnail_widgets = {}
        self.items = []
        self._all_items = []
        self._pending_items = []
        self._selected_item_id = None
        try:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
        except Exception:
            pass

    def refresh(self) -> None:
        """刷新当前文件夹。"""
        if self.current_folder:
            self.load_folder(self.current_folder)

    # ------------------------------------------------------------------
    # 演员过滤
    # ------------------------------------------------------------------
    def filter_by_actor(self, actor_id: int) -> None:
        """按单个演员过滤图库项。"""
        self.filter_by_actors([actor_id], match_mode="any")

    def filter_by_actors(self, actor_ids: list,
                         match_mode: str = "any") -> None:
        """按多个演员过滤图库项。

        参数:
            actor_ids: 演员ID列表
            match_mode: "any"（任一匹配）或 "all"（全部匹配）
        """
        if not actor_ids:
            self.clear_actor_filter()
            return
        actor_set = set(actor_ids)
        filtered = []
        for item in self._all_items:
            if item.metadata is not None:
                item_actor_ids = set(
                    getattr(item.metadata, "actor_ids", []) or []
                )
            else:
                item_actor_ids = set()
            if match_mode == "all":
                if actor_set.issubset(item_actor_ids):
                    filtered.append(item)
            else:
                if actor_set & item_actor_ids:
                    filtered.append(item)
        self._display_items(filtered, layout="grid")
        self._load_thumbnails_async()

    def clear_actor_filter(self) -> None:
        """清除演员过滤，恢复显示全部项。"""
        self._display_items(self._all_items, layout="grid")
        self._load_thumbnails_async()

    # ------------------------------------------------------------------
    # 扫描匹配与缩略图加载
    # ------------------------------------------------------------------
    def _scan_and_match(self, folder_path: str) -> list:
        """扫描文件夹并匹配视频-图片（委托 ThumbnailMatcher）。"""
        # 注入数据库管理器供关联查询使用
        ThumbnailMatcher.db_manager = self.db_manager
        return ThumbnailMatcher.match_folder(folder_path)

    def _create_thumbnail(self, item: GalleryItem) -> str:
        """生成/获取项的缩略图，返回缓存文件路径（设计接口）。

        延迟导入 thumbnail_cache 模块；失败时退回原图路径或空串。

        参数:
            item: 图库项

        返回:
            缩略图缓存文件路径（str）
        """
        if not item.thumbnail_path or not os.path.isfile(item.thumbnail_path):
            return ""
        width, height = self._calculate_thumbnail_dimensions(
            item.thumbnail_path
        )
        cache = self._get_thumbnail_cache()
        if cache is None:
            return item.thumbnail_path
        try:
            return cache.get_thumbnail(item.thumbnail_path, (width, height))
        except Exception:
            return item.thumbnail_path

    def _load_thumbnails_async(self) -> None:
        """异步加载缩略图：分批（每批20个）经 widget.after 调度，
        避免一次性加载导致UI卡顿；加载完成发布 EVENT_THUMBNAIL_READY。
        """
        self._pending_items = list(self.items)
        if not self._pending_items:
            return
        self._load_next_batch()

    def _load_next_batch(self, batch_size: int = THUMBNAIL_BATCH_SIZE) -> None:
        """加载下一批缩略图（主线程执行，批间经 after 让出事件循环）。"""
        batch = self._pending_items[:batch_size]
        self._pending_items = self._pending_items[batch_size:]
        for item in batch:
            thumb_path = self._create_thumbnail(item)
            self._apply_thumbnail(item, thumb_path)
            self.event_bus.publish(
                EVENT_THUMBNAIL_READY,
                item=item,
                thumbnail_path=thumb_path,
            )
        if self._pending_items:
            self.root.after(30, self._load_next_batch)

    def _apply_thumbnail(self, item: GalleryItem, thumb_path: str) -> None:
        """把加载完成的缩略图应用到控件上。"""
        entry = self._thumbnail_widgets.get(item.item_id)
        if not entry:
            return
        img_label = entry.get("img_label")
        if img_label is None or not img_label.winfo_exists():
            return
        if not thumb_path or not os.path.isfile(thumb_path):
            img_label.config(image="", text=self._placeholder_text(item))
            return
        try:
            from PIL import Image, ImageTk
            width, height = self._calculate_thumbnail_dimensions(
                item.thumbnail_path
            )
            with Image.open(thumb_path) as im:
                im = im.convert("RGB")
                im.thumbnail((width, height))
                photo = ImageTk.PhotoImage(im)
            img_label.config(image=photo, text="")
            entry["photo"] = photo  # 防止 PhotoImage 被垃圾回收
        except Exception:
            img_label.config(image="", text=self._placeholder_text(item))

    @staticmethod
    def _placeholder_text(item: GalleryItem) -> str:
        """无缩略图时的占位符文本。"""
        if item.is_folder:
            return "📁"
        if item.video_path:
            return "🎬"
        return "🖼"

    def _get_thumbnail_cache(self):
        """延迟初始化缩略图缓存（thumbnail_cache 模块，10号）。"""
        if self.thumbnail_cache is None:
            try:
                from thumbnail_cache import ThumbnailCache
                self.thumbnail_cache = ThumbnailCache()
            except Exception:
                self.thumbnail_cache = False
        return self.thumbnail_cache or None

    # ------------------------------------------------------------------
    # 加载提示
    # ------------------------------------------------------------------
    def show_loading(self) -> None:
        """显示加载中提示标签。"""
        self._loading_label.place(relx=0.5, rely=0.5, anchor="center")
        self._loading_label.lift()

    def hide_loading(self) -> None:
        """隐藏加载中提示标签。"""
        self._loading_label.place_forget()

    # ------------------------------------------------------------------
    # 纯图片文件夹图库模式（设计文档附录，原第十八章）
    # ------------------------------------------------------------------
    def _determine_display_mode(self, folder_path: str) -> str:
        """判定图库显示模式：无视频有图片（且非数据库关联文件夹）
        时进入 "pure_image"，否则 "video_gallery"。
        """
        videos = []
        if self.async_scanner is not None and hasattr(
                self.async_scanner, "get_media_in_folder"):
            try:
                videos = self.async_scanner.get_media_in_folder(folder_path)
            except Exception:
                videos = []
        if not videos:
            # 扫描器不可用时回退到直接目录扫描
            videos = [
                os.path.join(folder_path, name)
                for name in os.listdir(folder_path)
                if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS
                and os.path.isfile(os.path.join(folder_path, name))
            ]
        images = self._get_images_in_folder(folder_path)
        if not videos and images:
            # 数据库已关联主视频的文件夹仍走视频图库（关联模式）
            if self._get_linked_primary_path(folder_path):
                return "video_gallery"
            return "pure_image"
        return "video_gallery"

    def _load_pure_image_mode(self, folder_path: str) -> None:
        """纯图片模式加载（附录 18.3）：按文件名排序平铺显示。"""
        items = self._build_pure_image_items(folder_path)
        self._display_items(items, layout="grid")

    def _build_pure_image_items(self, folder_path: str) -> list:
        """构建纯图片模式的图库项列表（按文件名排序）。"""
        image_paths = self._get_images_in_folder(folder_path)
        image_paths.sort()
        items = []
        for i, img_path in enumerate(image_paths):
            item = GalleryItem(
                item_id=f"img_{i}",
                display_name=os.path.basename(img_path),
                thumbnail_path=img_path,
                image_paths=[img_path],
                match_mode=MODE_IMAGE_ONLY,
                is_folder=False,
            )
            items.append(item)
        return items

    def _get_linked_primary_path(self, folder_path: str):
        """查询文件夹在数据库中关联的主视频路径（无则返回 None）。"""
        db = self.db_manager
        if db is not None and hasattr(db, "get_primary_path"):
            try:
                return db.get_primary_path(folder_path)
            except Exception:
                return None
        return None

    def _display_items(self, items: list, layout: str = "grid") -> None:
        """显示图库项（layout: "grid" 网格 或 "list" 列表）。"""
        self.items = list(items)
        for child in self._container_frame.winfo_children():
            child.destroy()
        self._thumbnail_widgets = {}
        self._selected_item_id = None
        if layout == "list":
            self._render_list()
        else:
            self._render_grid()
        self._update_scrollregion()

    # ------------------------------------------------------------------
    # 图片浏览窗口集成（设计 17.2 节）
    # ------------------------------------------------------------------
    def _on_item_double_click(self, item: GalleryItem) -> None:
        """双击缩略图：打开图片浏览窗口（延迟导入 image_viewer）。"""
        if item.match_mode in (MODE_IMAGE_ONLY, MODE_VIDEO_LINKED):
            image_paths = item.image_paths
            start_index = item.current_image_index
        elif item.match_mode in (
                MODE_COVER_MATCH, MODE_EXCLUSIVE_VIDEO, MODE_COVER_PRIORITY):
            folder_path = os.path.dirname(
                item.video_path or item.thumbnail_path
            )
            image_paths = self._get_all_images_in_folder(folder_path)
            start_index = 0
            if item.thumbnail_path in image_paths:
                start_index = image_paths.index(item.thumbnail_path)
        elif item.is_folder:
            image_paths = self._get_all_images_in_folder(item.folder_path)
            start_index = 0
        else:
            return

        if image_paths:
            try:
                from image_viewer import ImageViewerWindow
            except Exception:
                return
            viewer = ImageViewerWindow(
                parent=self.root,
                image_paths=image_paths,
                start_index=start_index,
                title=f"浏览 - {item.display_name}",
            )
            viewer.show()

    def _get_images_in_folder(self, folder_path: str) -> list:
        """获取文件夹内的所有图片路径（按文件名排序）。"""
        result = []
        try:
            names = sorted(os.listdir(folder_path))
        except OSError:
            return []
        for name in names:
            full_path = os.path.join(folder_path, name)
            if (os.path.isfile(full_path)
                    and os.path.splitext(name)[1].lower()
                    in IMAGE_EXTENSIONS):
                result.append(full_path)
        return result

    def _get_all_images_in_folder(self, folder_path: str) -> list:
        """获取文件夹内的全部图片（用于图片浏览窗口切换）。"""
        return self._get_images_in_folder(folder_path)

    # ------------------------------------------------------------------
    # 切换按钮回调（封面优先模式）
    # ------------------------------------------------------------------
    def _on_switch_image(self, item: GalleryItem, switcher: tk.Frame) -> None:
        """封面优先模式切换图片后：刷新缩略图显示与按钮状态。"""
        self._apply_thumbnail(item, self._create_thumbnail(item))
        ThumbnailSwitcher.update_switcher_state(
            item, switcher.left_btn, switcher.right_btn
        )

    # ------------------------------------------------------------------
    # 滚动支持
    # ------------------------------------------------------------------
    def _on_mousewheel(self, event) -> None:
        """鼠标滚轮滚动画布内容。"""
        delta = getattr(event, "delta", 0)
        if delta:
            self.canvas.yview_scroll(int(-delta / 120), "units")

    def _bind_mousewheel(self, widget: tk.Widget) -> None:
        """递归绑定鼠标滚轮到控件及其所有子控件。"""
        try:
            widget.bind("<MouseWheel>", self._on_mousewheel)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_container_configure(self, event) -> None:
        """容器尺寸变化时更新滚动区域。"""
        self._update_scrollregion()

    def _on_canvas_configure(self, event) -> None:
        """画布尺寸变化时同步内嵌窗口宽度。"""
        try:
            self.canvas.itemconfigure(
                self._canvas_window, width=event.width
            )
        except Exception:
            pass

    def _update_scrollregion(self) -> None:
        """更新画布滚动区域。"""
        try:
            self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        except Exception:
            pass


class ThumbnailMatcher:
    """缩略图匹配器：五种视频-图片匹配模式（设计 11.3 节）。

    匹配模式优先级:
        ① 完全同名（video.mp4 + video.jpg） -> MODE_EXCLUSIVE_VIDEO
        ② 单视频 + cover 类图片 -> MODE_COVER_MATCH
        ③ 仅图片 + 数据库 video_links 关联 -> MODE_VIDEO_LINKED
        ④ 仅图片无关联 -> MODE_IMAGE_ONLY
        ⑤ 单视频 + 多图片 -> MODE_COVER_PRIORITY（封面优先，可切换）

    类属性:
        db_manager: 数据库管理器（由 GalleryViewer 注入，
                    用于 get_primary_path 关联查询）
    """

    db_manager = None

    @staticmethod
    def match_folder(folder_path: str) -> list:
        """扫描文件夹并返回匹配的图库项列表。

        参数:
            folder_path: 文件夹绝对路径

        返回:
            GalleryItem 列表（当前文件夹项 + 子文件夹项）
        """
        videos = []
        images = []
        subfolders = []
        try:
            names = sorted(os.listdir(folder_path))
        except OSError:
            return []

        for name in names:
            full_path = os.path.join(folder_path, name)
            if os.path.isdir(full_path):
                subfolders.append(full_path)
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                videos.append(full_path)
            elif ext in IMAGE_EXTENSIONS:
                images.append(full_path)

        items = ThumbnailMatcher._match_current_folder(videos, images)
        items.extend(ThumbnailMatcher._match_subfolders(subfolders))
        return items

    @staticmethod
    def _match_current_folder(videos: list, images: list) -> list:
        """对当前文件夹内的视频和图片进行匹配分类。"""
        items = []

        if len(videos) == 1:
            # 单视频场景：模式 ①②⑤
            video_path = videos[0]
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            display_name = os.path.basename(video_path)
            if not images:
                # 纯视频无图片：直接以视频路径占位
                items.append(GalleryItem(
                    item_id=video_path,
                    display_name=display_name,
                    thumbnail_path=video_path,
                    video_path=video_path,
                    image_paths=[],
                    match_mode="",
                ))
            elif len(images) == 1:
                # 模式 ①: 完全同名；模式 ②: 单视频+单图片（封面匹配）
                same_name = ThumbnailMatcher._find_matching_image(
                    video_name, images
                )
                if same_name:
                    mode = MODE_EXCLUSIVE_VIDEO
                    thumb = same_name
                else:
                    mode = MODE_COVER_MATCH
                    thumb = images[0]
                items.append(GalleryItem(
                    item_id=video_path,
                    display_name=display_name,
                    thumbnail_path=thumb,
                    video_path=video_path,
                    image_paths=list(images),
                    match_mode=mode,
                ))
            else:
                # 模式 ⑤: 单视频+多图片，封面优先，可切换
                thumb = (
                    ThumbnailMatcher._find_cover_image(images) or images[0]
                )
                items.append(GalleryItem(
                    item_id=video_path,
                    display_name=display_name,
                    thumbnail_path=thumb,
                    video_path=video_path,
                    image_paths=list(images),
                    current_image_index=(
                        images.index(thumb) if thumb in images else 0
                    ),
                    match_mode=MODE_COVER_PRIORITY,
                ))

        elif len(videos) > 1:
            # 多视频场景：每个视频优先匹配同名图片，其次封面/未占用图片
            used_images = set()
            for video_path in videos:
                video_name = os.path.splitext(
                    os.path.basename(video_path)
                )[0]
                image = ThumbnailMatcher._find_matching_image(
                    video_name, images
                )
                if image and image in used_images:
                    image = None
                mode = MODE_EXCLUSIVE_VIDEO
                if not image:
                    remaining = [
                        img for img in images if img not in used_images
                    ]
                    image = (
                        ThumbnailMatcher._find_cover_image(remaining)
                        or (remaining[0] if remaining else None)
                    )
                    mode = MODE_COVER_MATCH if image else ""
                if image:
                    used_images.add(image)
                items.append(GalleryItem(
                    item_id=video_path,
                    display_name=os.path.basename(video_path),
                    thumbnail_path=image or video_path,
                    video_path=video_path,
                    image_paths=[image] if image else [],
                    match_mode=mode,
                ))

        elif images:
            # 无视频有图片：模式 ③④
            folder_path = os.path.dirname(images[0])
            primary_path = None
            db = getattr(ThumbnailMatcher, "db_manager", None)
            if db is not None and hasattr(db, "get_primary_path"):
                try:
                    primary_path = db.get_primary_path(folder_path)
                except Exception:
                    primary_path = None

            for img_path in images:
                if primary_path:
                    # 模式 ③: 数据库关联视频
                    items.append(GalleryItem(
                        item_id=img_path,
                        display_name=os.path.basename(img_path),
                        thumbnail_path=img_path,
                        video_path=primary_path,
                        image_paths=[img_path],
                        match_mode=MODE_VIDEO_LINKED,
                        is_linked=True,
                        linked_from=primary_path,
                    ))
                else:
                    # 模式 ④: 仅图片
                    items.append(GalleryItem(
                        item_id=img_path,
                        display_name=os.path.basename(img_path),
                        thumbnail_path=img_path,
                        video_path=None,
                        image_paths=[img_path],
                        match_mode=MODE_IMAGE_ONLY,
                    ))

        return items

    @staticmethod
    def _match_subfolders(subfolders: list) -> list:
        """为每个子文件夹创建一个文件夹项（is_folder）。"""
        items = []
        for folder_path in subfolders:
            media_count = 0
            cover_path = ""
            try:
                names = sorted(os.listdir(folder_path))
            except OSError:
                names = []
            sub_images = []
            for name in names:
                full_path = os.path.join(folder_path, name)
                if not os.path.isfile(full_path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    media_count += 1
                elif ext in IMAGE_EXTENSIONS:
                    media_count += 1
                    sub_images.append(full_path)
            # 子文件夹封面：优先 cover 类图片，其次第一张图片
            cover_path = (
                ThumbnailMatcher._find_cover_image(sub_images)
                or (sub_images[0] if sub_images else "")
            )
            items.append(GalleryItem(
                item_id=folder_path,
                display_name=os.path.basename(folder_path),
                thumbnail_path=cover_path,
                video_path=None,
                image_paths=[],
                match_mode="",
                is_folder=True,
                folder_path=folder_path,
                sub_items_count=media_count,
            ))
        return items

    @staticmethod
    def _find_cover_image(image_paths: list):
        """在图片列表中查找 cover 类封面图（COVER_KEYWORDS 小写比较）。"""
        for path in image_paths:
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            for keyword in COVER_KEYWORDS:
                if stem.startswith(keyword):
                    return path
        return None

    @staticmethod
    def _find_matching_image(video_name: str, image_paths: list):
        """按文件名（不含扩展名）查找与视频同名的图片。"""
        for path in image_paths:
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem == video_name:
                return path
        return None

    @staticmethod
    def _get_video_code(filename: str) -> str:
        """从文件名提取视频番号/编号（延迟导入番号识别引擎）。

        优先调用 PatternRecognitionEngine.extract_code（24号模块），
        不可用时回退到内置正则（XXX-NNN / XXX_NNN / XXXNNN）。

        参数:
            filename: 文件名

        返回:
            番号字符串，提取失败返回空串
        """
        try:
            from pattern_recognition_engine import PatternRecognitionEngine
            engine = PatternRecognitionEngine(
                db_manager=getattr(ThumbnailMatcher, "db_manager", None)
            )
            result = engine.extract_code(filename)
            code = getattr(result, "code", "") or ""
            if code:
                return code
        except Exception:
            pass

        # 正则回退：PREFIX-NNN / PREFIX_NNN / PREFIXNNN
        match = re.search(
            r'([A-Za-z]{2,10})[-_]?(\d{2,5})', os.path.basename(filename)
        )
        if match:
            return f"{match.group(1).upper()}-{match.group(2)}"
        return ""


class ThumbnailSwitcher:
    """缩略图切换器：封面优先模式（MODE_COVER_PRIORITY）的左右切换按钮。

    在缩略图上叠加左右切换按钮，浏览同一视频对应的多张图片；
    到达边界时禁用对应方向按钮。
    """

    @staticmethod
    def create_switcher(parent: tk.Widget, item: GalleryItem) -> tk.Frame:
        """在缩略图上叠加左右切换按钮。

        参数:
            parent: 父控件（缩略图内层容器）
            item: 图库项（match_mode 应为 MODE_COVER_PRIORITY）

        返回:
            包含左右按钮的 Frame；按钮可通过
            ``frame.left_btn`` / ``frame.right_btn`` 访问
        """
        frame = tk.Frame(
            parent, bg=THUMBNAIL_BG_NORMAL, bd=0, highlightthickness=0
        )
        left_btn = tk.Button(
            frame,
            text="◀",
            font=("Microsoft YaHei UI", 9),
            bg=THUMBNAIL_BG_HOVER,
            fg="#ffffff",
            activebackground=THUMBNAIL_BORDER_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=6,
            pady=0,
            cursor="hand2",
            command=lambda: ThumbnailSwitcher.switch_image(
                item, SWITCH_PREVIOUS
            ),
        )
        right_btn = tk.Button(
            frame,
            text="▶",
            font=("Microsoft YaHei UI", 9),
            bg=THUMBNAIL_BG_HOVER,
            fg="#ffffff",
            activebackground=THUMBNAIL_BORDER_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=6,
            pady=0,
            cursor="hand2",
            command=lambda: ThumbnailSwitcher.switch_image(
                item, SWITCH_NEXT
            ),
        )
        left_btn.pack(side="left", padx=(0, 2))
        right_btn.pack(side="left")

        frame.left_btn = left_btn
        frame.right_btn = right_btn

        ThumbnailSwitcher.update_switcher_state(item, left_btn, right_btn)
        return frame

    @staticmethod
    def switch_image(item: GalleryItem, direction: int) -> None:
        """切换当前显示的图片并更新显示。

        参数:
            item: 图库项
            direction: 切换方向（-1 上一张 / 1 下一张）
        """
        if not item.image_paths:
            return
        count = len(item.image_paths)
        new_index = max(
            0, min(count - 1, item.current_image_index + direction)
        )
        if new_index == item.current_image_index:
            return
        item.current_image_index = new_index
        item.thumbnail_path = item.image_paths[new_index]
        # 通知图库刷新缩略图显示（由 GalleryViewer 注入的回调）
        callback = getattr(item, "_on_image_changed", None)
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    @staticmethod
    def update_switcher_state(item: GalleryItem, left_btn: tk.Widget,
                              right_btn: tk.Widget) -> None:
        """根据当前图片索引更新按钮启用/禁用状态（边界时禁用）。"""
        count = len(item.image_paths)
        if count <= 1:
            left_btn.config(state="disabled")
            right_btn.config(state="disabled")
            return
        left_btn.config(
            state="disabled" if item.current_image_index <= 0 else "normal"
        )
        right_btn.config(
            state="disabled"
            if item.current_image_index >= count - 1 else "normal"
        )
