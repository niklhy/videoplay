# -*- coding: utf-8 -*-
"""图片浏览窗口模块（对应设计文档第17章）。

独立的图片浏览窗口，支持：
- 滚轮缩放（放大/缩小，限制在 min_scale ~ max_scale）
- 左键拖拽平移
- 左右方向键/按钮切换图片
- 键盘 +/- 缩放、Home 重置、Escape 关闭
- 双击切换全屏/窗口模式
- 窗口缩放时重渲染
"""
import logging
import tkinter as tk

from PIL import Image, ImageTk

logger = logging.getLogger(__name__)


class ImageViewerWindow:
    """独立图片浏览窗口。

    属性:
        parent: 父窗口（tk.Tk 或 tk.Toplevel）
        window: 本浏览窗口（tk.Toplevel）
        canvas: 图片显示画布
        current_image: 当前显示的 PhotoImage（保持引用防GC）
        original_image: 原始 PIL 图片
        image_paths: 图片路径列表
        current_index: 当前图片索引
        scale: 当前缩放比例
        min_scale: 最小缩放比例
        max_scale: 最大缩放比例
        scale_step: 缩放步进
        offset_x / offset_y: 平移偏移
        is_dragging / drag_start_x / drag_start_y: 拖拽状态
        left_btn / right_btn: 上/下一张按钮
        info_label: 顶部信息标签（当前序号/总数）
        zoom_label: 缩放比例标签
    """

    def __init__(self, parent: tk.Tk, image_paths: list,
                 start_index: int = 0, title: str = "图片浏览"):
        """初始化图片浏览窗口（不立即显示，需调用 show()）。

        参数:
            parent: 父窗口
            image_paths: 图片路径列表
            start_index: 起始图片索引
            title: 窗口标题
        """
        self.parent = parent
        self.image_paths = list(image_paths)
        self.current_index = max(0, min(start_index, len(self.image_paths) - 1))
        self.title = title

        self.window = None
        self.canvas = None
        self.current_image = None
        self.original_image = None

        # 缩放与平移状态
        self.scale = 1.0
        self.min_scale = 0.1
        self.max_scale = 5.0
        self.scale_step = 0.1
        self.offset_x = 0
        self.offset_y = 0

        # 拖拽状态
        self.is_dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0

        # UI 组件
        self.left_btn = None
        self.right_btn = None
        self.info_label = None
        self.zoom_label = None

        self._create_window()
        self._load_image(self.current_index)

    # ------------------------------------------------------------------
    # 窗口创建
    # ------------------------------------------------------------------
    def _create_window(self) -> None:
        """创建 tk.Toplevel 窗口：Canvas + 顶部信息标签 + 缩放标签 + 上/下一张按钮。"""
        self.window = tk.Toplevel(self.parent)
        self.window.title(self.title)
        self.window.geometry("900x700")
        self.window.minsize(300, 200)
        self.window.configure(bg="#1e1e1e")

        # 顶部工具栏：信息标签 + 缩放标签 + 切换按钮
        top_frame = tk.Frame(self.window, bg="#1e1e1e")
        top_frame.pack(side=tk.TOP, fill=tk.X)

        self.info_label = tk.Label(top_frame, text="", bg="#1e1e1e",
                                   fg="#ffffff", font=("Microsoft YaHei UI", 10))
        self.info_label.pack(side=tk.LEFT, padx=8, pady=4)

        self.zoom_label = tk.Label(top_frame, text="100%", bg="#1e1e1e",
                                   fg="#aaaaaa", font=("Microsoft YaHei UI", 10))
        self.zoom_label.pack(side=tk.RIGHT, padx=8, pady=4)

        btn_frame = tk.Frame(top_frame, bg="#1e1e1e")
        btn_frame.pack(side=tk.RIGHT, padx=4)

        self.left_btn = tk.Button(btn_frame, text="◀ 上一张", width=10,
                                  command=self._on_left_click)
        self.left_btn.pack(side=tk.LEFT, padx=2)

        self.right_btn = tk.Button(btn_frame, text="下一张 ▶", width=10,
                                   command=self._on_right_click)
        self.right_btn.pack(side=tk.LEFT, padx=2)

        # 图片画布
        self.canvas = tk.Canvas(self.window, bg="#1e1e1e",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 鼠标事件（Windows 滚轮为 <MouseWheel>，delta 上正下负）
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", self._on_button_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_button_release)
        self.canvas.bind("<B1-Motion>", self._on_mouse_move)
        self.canvas.bind("<Double-Button-1>", lambda e: self._toggle_fullscreen())

        # 键盘事件绑定到 Toplevel，确保窗口获得焦点时可响应
        self.window.bind("<Key>", self._on_key_press)
        self.window.bind("<Configure>", self._on_window_resize)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        # 让窗口获得键盘焦点
        self.window.focus_set()

    # ------------------------------------------------------------------
    # 图片加载与渲染
    # ------------------------------------------------------------------
    def _load_image(self, index: int) -> bool:
        """加载指定索引的图片。

        参数:
            index: 图片索引

        返回:
            加载成功返回 True，失败返回 False
        """
        if not (0 <= index < len(self.image_paths)):
            return False
        try:
            path = self.image_paths[index]
            self.original_image = Image.open(path)
            self.original_image.load()
            self.current_index = index
            # 重置缩放与平移
            self.scale = 1.0
            self.offset_x = 0
            self.offset_y = 0
            self._render_image()
            self._update_ui_state()
            return True
        except Exception as e:
            logger.error("加载图片失败: %s (%s)",
                         self.image_paths[index] if 0 <= index < len(self.image_paths) else index,
                         e)
            return False

    def _render_image(self) -> None:
        """按当前 scale/offset 在 Canvas 上缩放并居中显示图片。"""
        if self.original_image is None or self.canvas is None:
            return
        try:
            canvas_w = max(self.canvas.winfo_width(), 1)
            canvas_h = max(self.canvas.winfo_height(), 1)

            # 缩放尺寸
            new_w = max(1, int(self.original_image.width * self.scale))
            new_h = max(1, int(self.original_image.height * self.scale))
            resized = self.original_image.resize((new_w, new_h), Image.LANCZOS)

            # 转 PhotoImage 并保持引用，防止被 GC 回收
            self.current_image = ImageTk.PhotoImage(resized)

            # 居中位置 + 平移偏移
            x = canvas_w // 2 + self.offset_x
            y = canvas_h // 2 + self.offset_y

            self.canvas.delete("all")
            self.canvas.create_image(x, y, image=self.current_image)
        except Exception as e:
            logger.error("渲染图片失败: %s", e)

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def _on_mousewheel(self, event) -> None:
        """鼠标滚轮：向上放大、向下缩小（factor 1.1 / 0.9）。"""
        if getattr(event, "delta", 0) > 0:
            factor = 1.1
        else:
            factor = 0.9
        self._apply_zoom(factor)

    def _on_button_press(self, event) -> None:
        """左键按下：开始拖拽。"""
        self.is_dragging = True
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def _on_button_release(self, event) -> None:
        """左键释放：结束拖拽。"""
        self.is_dragging = False

    def _on_mouse_move(self, event) -> None:
        """左键按住拖动：移动图片位置。"""
        if not self.is_dragging:
            return
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        self.offset_x += dx
        self.offset_y += dy
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self._render_image()

    def _on_left_click(self) -> None:
        """切换到上一张图片。"""
        if self.image_paths:
            new_index = (self.current_index - 1) % len(self.image_paths)
            self._load_image(new_index)

    def _on_right_click(self) -> None:
        """切换到下一张图片。"""
        if self.image_paths:
            new_index = (self.current_index + 1) % len(self.image_paths)
            self._load_image(new_index)

    # ------------------------------------------------------------------
    # UI 状态
    # ------------------------------------------------------------------
    def _update_ui_state(self) -> None:
        """更新信息标签、缩放标签与切换按钮状态。"""
        total = len(self.image_paths)
        if total > 0:
            self.info_label.config(
                text=f"{self.current_index + 1} / {total}   "
                     f"{self.image_paths[self.current_index]}")
        else:
            self.info_label.config(text="无图片")

        percent = int(self.scale * 100)
        self.zoom_label.config(text=f"{percent}%")

        # 只有一张图时禁用切换按钮
        state = tk.NORMAL if total > 1 else tk.DISABLED
        if self.left_btn:
            self.left_btn.config(state=state)
        if self.right_btn:
            self.right_btn.config(state=state)

    # ------------------------------------------------------------------
    # 键盘交互
    # ------------------------------------------------------------------
    def _on_key_press(self, event) -> None:
        """键盘事件：左右切换、+/- 缩放、Escape 关闭、Home 重置。"""
        keysym = event.keysym
        if keysym == "Left":
            self._on_left_click()
        elif keysym == "Right":
            self._on_right_click()
        elif keysym in ("plus", "KP_Add", "equal"):
            self._apply_zoom(1.1)
        elif keysym in ("minus", "KP_Subtract"):
            self._apply_zoom(0.9)
        elif keysym == "Escape":
            self.close()
        elif keysym == "Home":
            self._reset_view()

    def _on_window_resize(self, event) -> None:
        """窗口 resize 时重渲染图片。"""
        if event.widget is self.window and self.original_image is not None:
            self._render_image()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _apply_zoom(self, factor: float) -> None:
        """按比例缩放，限制在 min_scale ~ max_scale 之间。"""
        new_scale = self.scale * factor
        if new_scale < self.min_scale:
            new_scale = self.min_scale
        if new_scale > self.max_scale:
            new_scale = self.max_scale
        self.scale = new_scale
        self._render_image()
        self._update_ui_state()

    def _reset_view(self) -> None:
        """重置缩放和位置（Home 键）。"""
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self._render_image()
        self._update_ui_state()

    def _toggle_fullscreen(self) -> None:
        """双击切换全屏/窗口模式。"""
        if self.window is None:
            return
        is_full = bool(self.window.attributes("-fullscreen"))
        self.window.attributes("-fullscreen", not is_full)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭窗口并释放资源。"""
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
        self.current_image = None
        self.original_image = None

    def show(self) -> None:
        """显示窗口并置于前台。"""
        if self.window is not None:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
