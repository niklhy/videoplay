# -*- coding: utf-8 -*-
"""信息显示面板模块（对应设计文档第23章）。

底部信息区：显示当前选中缩略图对应的文件真实路径信息，
包括图片路径和视频路径，支持一键复制和打开所在文件夹。
固定高度，位于右侧最下方。

仅使用标准库 + tkinter，GalleryItem 数据契约从 models.py 导入。
"""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from models import GalleryItem


class InfoPanel:
    """信息显示面板。

    属性:
        parent:            父控件
        container:         面板容器（固定高度）
        image_path_entry:  图片路径只读输入框
        video_path_entry:  视频路径只读输入框
        file_size_label:   文件大小标签
        copy_btn:          复制按钮
        open_folder_btn:   打开所在文件夹按钮
    """

    # 面板固定高度（像素）
    PANEL_HEIGHT = 60

    def __init__(self, parent: tk.Widget):
        """初始化信息显示面板。

        :param parent: 父控件
        """
        self.parent = parent
        self.container = None
        self.image_path_entry = None
        self.video_path_entry = None
        self.file_size_label = None
        self.copy_btn = None
        self.open_folder_btn = None

        self._current_item = None

        self._create_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _create_ui(self) -> None:
        """创建面板 UI：图片路径行 + 视频路径行 + 底部操作行。"""
        self.container = tk.Frame(self.parent, bd=1, relief=tk.GROOVE)
        self.container.pack(fill=tk.X, side=tk.BOTTOM)
        # 固定高度区域
        self.container.pack_propagate(False)
        self.container.config(height=self.PANEL_HEIGHT)

        # 第一行：图片路径 + 复制按钮
        image_row = tk.Frame(self.container)
        image_row.pack(fill=tk.X, padx=4, pady=(2, 0))
        tk.Label(image_row, text="图片:", width=5, anchor=tk.W).pack(side=tk.LEFT)
        self.image_path_entry = ttk.Entry(image_row, state="readonly")
        self.image_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        image_copy_btn = tk.Button(
            image_row, text="复制", width=5,
            command=lambda: self._on_copy_path("image"),
        )
        image_copy_btn.pack(side=tk.LEFT, padx=(4, 0))

        # 第二行：视频路径 + 复制按钮 + 播放按钮
        video_row = tk.Frame(self.container)
        video_row.pack(fill=tk.X, padx=4, pady=(2, 0))
        tk.Label(video_row, text="视频:", width=5, anchor=tk.W).pack(side=tk.LEFT)
        self.video_path_entry = ttk.Entry(video_row, state="readonly")
        self.video_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        video_copy_btn = tk.Button(
            video_row, text="复制", width=5,
            command=lambda: self._on_copy_path("video"),
        )
        video_copy_btn.pack(side=tk.LEFT, padx=(4, 0))
        play_btn = tk.Button(
            video_row, text="播放", width=5,
            command=self._on_play_video,
        )
        play_btn.pack(side=tk.LEFT, padx=(4, 0))

        # 第三行：文件大小 + 打开所在文件夹
        bottom_row = tk.Frame(self.container)
        bottom_row.pack(fill=tk.X, padx=4, pady=(2, 2))
        self.file_size_label = tk.Label(bottom_row, text="文件大小: -", anchor=tk.W)
        self.file_size_label.pack(side=tk.LEFT)
        self.open_folder_btn = tk.Button(
            bottom_row, text="打开所在文件夹",
            command=lambda: self._on_open_folder(self._get_current_folder_target()),
        )
        self.open_folder_btn.pack(side=tk.RIGHT)
        self.copy_btn = video_copy_btn  # 兼容设计文档属性（复制按钮引用）

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    def show_file_info(self, item: GalleryItem) -> None:
        """显示指定图库项的文件信息。

        从 thumbnail_path / video_path 填充路径输入框，
        文件大小取视频路径优先，其次图片路径，经 _format_file_size 格式化。

        :param item: 图库项（GalleryItem）
        """
        self._current_item = item

        image_path = item.thumbnail_path or ""
        video_path = item.video_path or ""

        self._set_entry_text(self.image_path_entry, image_path)
        self._set_entry_text(self.video_path_entry, video_path)

        # 计算文件大小：优先视频文件，其次图片文件
        size_bytes = 0
        for path in (video_path, image_path):
            if path:
                try:
                    size_bytes = os.path.getsize(path)
                    break
                except OSError:
                    continue
        if size_bytes > 0:
            self.file_size_label.config(text=f"文件大小: {self._format_file_size(size_bytes)}")
        else:
            self.file_size_label.config(text="文件大小: -")

    def clear(self) -> None:
        """清空面板显示。"""
        self._current_item = None
        self._set_entry_text(self.image_path_entry, "")
        self._set_entry_text(self.video_path_entry, "")
        self.file_size_label.config(text="文件大小: -")

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_copy_path(self, path_type: str) -> None:
        """复制路径到剪贴板。

        :param path_type: "image" 复制图片路径，"video" 复制视频路径
        """
        if path_type == "image":
            path = self.image_path_entry.get()
        else:
            path = self.video_path_entry.get()
        if not path:
            return
        try:
            self.container.clipboard_clear()
            self.container.clipboard_append(path)
        except tk.TclError:
            # 剪贴板不可用时静默忽略
            pass

    def _on_open_folder(self, file_path: str) -> None:
        """在系统资源管理器中打开文件所在文件夹。

        :param file_path: 文件完整路径
        """
        if not file_path:
            return
        folder = os.path.dirname(file_path)
        if not folder or not os.path.isdir(folder):
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa: S606 - Windows 打开资源管理器
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError:
            # 打开失败时静默忽略
            pass

    def _on_play_video(self) -> None:
        """播放按钮回调：使用系统默认播放器打开当前视频。"""
        video_path = self.video_path_entry.get()
        if not video_path or not os.path.isfile(video_path):
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(video_path)  # noqa: S606 - Windows 默认播放器
            else:
                subprocess.Popen(["xdg-open", video_path])
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------
    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小为 KB / MB / GB 文本。

        :param size_bytes: 文件字节数
        :return: 格式化文本，如 "12.5 MB"
        """
        if size_bytes < 0:
            return "-"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        kb = size_bytes / 1024
        if kb < 1024:
            return f"{kb:.1f} KB"
        mb = kb / 1024
        if mb < 1024:
            return f"{mb:.1f} MB"
        gb = mb / 1024
        return f"{gb:.2f} GB"

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _set_entry_text(self, entry: ttk.Entry, text: str) -> None:
        """设置只读输入框的文本内容。

        :param entry: 输入框控件
        :param text:  要显示的文本
        """
        if entry is None:
            return
        entry.config(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, text)
        entry.config(state="readonly")

    def _get_current_folder_target(self) -> str:
        """获取用于"打开所在文件夹"的目标文件路径。

        优先视频路径，其次图片路径。

        :return: 文件完整路径（无则空字符串）
        """
        if self._current_item is None:
            return ""
        video_path = self.video_path_entry.get()
        if video_path:
            return video_path
        return self.image_path_entry.get()
