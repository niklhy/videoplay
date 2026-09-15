# -*- coding: utf-8 -*-
"""日志模块（对应设计文档第二十五章）。

提供内存日志单例 LogManager（级别过滤、颜色显示、导出、订阅）和
tkinter 日志面板组件 LogPanel。日志只保存在本次运行内存中，
软件关闭后自动清空，支持手动导出。

依赖契约：
- models.LogEntry（数据类，禁止重复定义）
- constants.LOG_COLORS（级别颜色映射）
- events.EVENT_LOG_APPENDED（日志追加事件）
"""
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Callable, List, Optional

from models import LogEntry
from constants import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARNING,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_CRITICAL,
    LOG_COLORS,
)
import events


# 全部日志级别（下拉框"全部"以外的选项）
_ALL_LEVELS = [
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARNING,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_CRITICAL,
]

# 下拉框"全部"选项的显示文本
_FILTER_ALL = "全部"


class LogManager:
    """内存日志管理器（单例）。

    线程安全：所有对 entries 的读写都受 _lock 保护。
    内存最多保留 max_entries 条，超出时丢弃最旧的日志。
    """

    _instance = None          # 单例实例
    _instance_lock = threading.Lock()  # 单例创建锁

    def __init__(self, event_bus=None):
        """初始化日志管理器。

        参数:
            event_bus: 可选事件总线实例（EventBus），
                       注入后 log() 会发布 EVENT_LOG_APPENDED 事件。
        """
        self.entries: List[LogEntry] = []
        self.max_entries: int = 10000
        self._lock = threading.Lock()
        self._subscribers: List[Callable] = []
        self._event_bus = event_bus

    @classmethod
    def get_instance(cls, event_bus=None) -> "LogManager":
        """获取单例实例（线程安全）。

        参数:
            event_bus: 仅首次创建实例时生效，用于注入事件总线。
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(event_bus=event_bus)
        return cls._instance

    # ------------------------------------------------------------------
    # 日志写入
    # ------------------------------------------------------------------
    def log(self, level: str, source: str, message: str, details: str = "") -> None:
        """写入一条日志，并通知订阅者与事件总线。

        参数:
            level: 日志级别（见 constants.LOG_LEVEL_*）
            source: 来源模块名
            message: 日志内容
            details: 附加详情（可选）
        """
        entry = LogEntry(
            level=level,
            source=source,
            message=message,
            details=details,
            timestamp=datetime.now(),
        )
        with self._lock:
            self.entries.append(entry)
            # 超出上限时丢弃最旧的日志
            while len(self.entries) > self.max_entries:
                self.entries.pop(0)
            subscribers = list(self._subscribers)

        # 触发订阅者回调（在锁外执行，避免回调死锁）
        for callback in subscribers:
            try:
                callback(entry)
            except Exception:
                # 订阅者回调异常不应影响日志记录本身
                pass

        # 发布事件总线事件（在锁外执行）
        if self._event_bus is not None:
            try:
                self._event_bus.publish(events.EVENT_LOG_APPENDED, entry=entry)
            except Exception:
                # 事件总线异常不应影响日志记录本身
                pass

    def debug(self, source: str, message: str, details: str = "") -> None:
        """写入 DEBUG 级别日志。"""
        self.log(LOG_LEVEL_DEBUG, source, message, details)

    def info(self, source: str, message: str, details: str = "") -> None:
        """写入 INFO 级别日志。"""
        self.log(LOG_LEVEL_INFO, source, message, details)

    def warning(self, source: str, message: str, details: str = "") -> None:
        """写入 WARNING 级别日志。"""
        self.log(LOG_LEVEL_WARNING, source, message, details)

    def error(self, source: str, message: str, details: str = "") -> None:
        """写入 ERROR 级别日志。"""
        self.log(LOG_LEVEL_ERROR, source, message, details)

    def critical(self, source: str, message: str, details: str = "") -> None:
        """写入 CRITICAL 级别日志。"""
        self.log(LOG_LEVEL_CRITICAL, source, message, details)

    # ------------------------------------------------------------------
    # 查询与维护
    # ------------------------------------------------------------------
    def get_entries(self, level_filter: str = None,
                    source_filter: str = None) -> List[LogEntry]:
        """按级别/来源过滤返回日志条目副本。

        参数:
            level_filter: 级别过滤（None 表示全部）
            source_filter: 来源过滤（None 表示全部，子串匹配）
        返回:
            符合条件的 LogEntry 列表
        """
        with self._lock:
            result = list(self.entries)
        if level_filter:
            result = [e for e in result if e.level == level_filter]
        if source_filter:
            result = [e for e in result if source_filter in (e.source or "")]
        return result

    def clear(self) -> None:
        """清空全部日志。"""
        with self._lock:
            self.entries.clear()

    def export_to_file(self, file_path: str) -> bool:
        """将全部日志导出到文本文件。

        参数:
            file_path: 目标文件路径（UTF-8 编码写入）
        返回:
            导出成功返回 True，失败返回 False
        """
        try:
            entries = self.get_entries()
            with open(file_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(entry.format() + "\n")
            return True
        except Exception:
            return False

    def subscribe(self, callback: Callable) -> None:
        """订阅日志追加事件，新日志到达时回调 callback(entry)。

        参数:
            callback: 回调函数，签名为 callback(entry: LogEntry)
        """
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """取消订阅（供调用方清理使用）。"""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)


class LogPanel:
    """日志 UI 面板（tkinter）。

    以选项卡形式集成在主界面中，包含工具栏（级别过滤、清空、
    导出、复制全部、自动滚动）和彩色日志文本区。
    """

    def __init__(self, parent: tk.Widget, log_manager: LogManager):
        """初始化日志面板。

        参数:
            parent: 父级容器控件
            log_manager: LogManager 实例（建议传入单例）
        """
        self.parent = parent
        self.log_manager = log_manager
        self.text_widget: Optional[tk.Text] = None
        self.toolbar_frame: Optional[tk.Frame] = None
        self.clear_btn: Optional[tk.Button] = None
        self.export_btn: Optional[tk.Button] = None
        self.copy_btn: Optional[tk.Button] = None
        self.auto_scroll_var: tk.BooleanVar = tk.BooleanVar(value=True)
        self.filter_combo: Optional[ttk.Combobox] = None
        self.count_label: Optional[tk.Label] = None

        self._create_ui()

        # 订阅日志追加，实时刷新显示
        self.log_manager.subscribe(self.append_log)

    def _create_ui(self) -> None:
        """创建面板 UI：工具栏 + 日志文本区。"""
        # 工具栏
        self.toolbar_frame = tk.Frame(self.parent)
        self.toolbar_frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        tk.Label(self.toolbar_frame, text="级别过滤:").pack(side=tk.LEFT)
        self.filter_combo = ttk.Combobox(
            self.toolbar_frame,
            values=[_FILTER_ALL] + _ALL_LEVELS,
            state="readonly",
            width=10,
        )
        self.filter_combo.set(_FILTER_ALL)
        self.filter_combo.pack(side=tk.LEFT, padx=(2, 8))
        self.filter_combo.bind("<<ComboboxSelected>>",
                               lambda e: self.refresh_display())

        self.clear_btn = tk.Button(self.toolbar_frame, text="清空",
                                   command=self._on_clear)
        self.clear_btn.pack(side=tk.LEFT, padx=2)

        self.export_btn = tk.Button(self.toolbar_frame, text="导出",
                                    command=self._on_export)
        self.export_btn.pack(side=tk.LEFT, padx=2)

        self.copy_btn = tk.Button(self.toolbar_frame, text="复制全部",
                                  command=self._on_copy_all)
        self.copy_btn.pack(side=tk.LEFT, padx=2)

        tk.Checkbutton(self.toolbar_frame, text="自动滚动",
                       variable=self.auto_scroll_var).pack(side=tk.LEFT, padx=6)

        self.count_label = tk.Label(self.toolbar_frame, text="共 0 条")
        self.count_label.pack(side=tk.RIGHT, padx=4)

        # 日志文本区（深色背景，级别着色）
        text_frame = tk.Frame(self.parent)
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.text_widget = tk.Text(
            text_frame,
            wrap=tk.NONE,
            state=tk.DISABLED,
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            selectbackground="#2f6f9f",
            font=("Consolas", 9),
        )
        scrollbar_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                                    command=self.text_widget.yview)
        scrollbar_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL,
                                    command=self.text_widget.xview)
        self.text_widget.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
        )
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 按 LOG_COLORS 配置各级别文字颜色标签
        for level, color in LOG_COLORS.items():
            self.text_widget.tag_configure(level, foreground=color)

        # 支持 Ctrl+C 复制选中内容（鼠标选择后可用快捷键或右键复制）
        self.text_widget.bind("<Control-c>", self._on_copy_selection)
        self.text_widget.bind("<Button-3>", self._show_context_menu)

    # ------------------------------------------------------------------
    # 日志显示
    # ------------------------------------------------------------------
    def append_log(self, entry: LogEntry) -> None:
        """向文本区追加一条日志（线程安全）。

        从工作线程调用时，通过 widget.after 调度回 UI 线程执行。

        参数:
            entry: 要显示的日志条目
        """
        widget = self.text_widget
        if widget is None:
            return
        try:
            # 线程安全：调度到 UI 线程
            widget.after(0, self._append_log_ui, entry)
        except Exception:
            # 控件已销毁等异常情况直接忽略
            pass

    def _append_log_ui(self, entry: LogEntry) -> None:
        """在 UI 线程中执行的实际追加逻辑。"""
        widget = self.text_widget
        if widget is None:
            return
        # 尊重级别过滤：与当前过滤条件不符的日志不显示
        level_filter = self.filter_combo.get() if self.filter_combo else _FILTER_ALL
        if level_filter and level_filter != _FILTER_ALL \
                and entry.level != level_filter:
            self._update_count()
            return

        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, entry.format() + "\n", entry.level)
        widget.configure(state=tk.DISABLED)
        if self.auto_scroll_var.get():
            widget.see(tk.END)
        self._update_count()

    def refresh_display(self) -> None:
        """按当前过滤条件重新渲染全部日志。"""
        if self.text_widget is None:
            return
        level_filter = self.filter_combo.get() if self.filter_combo else _FILTER_ALL
        if level_filter == _FILTER_ALL:
            level_filter = None
        entries = self.log_manager.get_entries(level_filter=level_filter)

        self.text_widget.configure(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)
        for entry in entries:
            self.text_widget.insert(tk.END, entry.format() + "\n", entry.level)
        self.text_widget.configure(state=tk.DISABLED)
        if self.auto_scroll_var.get():
            self.text_widget.see(tk.END)
        self._update_count()

    def _update_count(self) -> None:
        """更新日志条数标签。"""
        if self.count_label is None:
            return
        try:
            total = len(self.log_manager.get_entries())
            shown_lines = int(self.text_widget.index("end-1c").split(".")[0]) \
                if self.text_widget else 0
            self.count_label.config(text="共 %d 条 / 显示 %d 条" % (total, shown_lines))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 工具栏动作
    # ------------------------------------------------------------------
    def _on_clear(self) -> None:
        """清空日志（内存 + 显示区）。"""
        self.log_manager.clear()
        if self.text_widget is not None:
            self.text_widget.configure(state=tk.NORMAL)
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.configure(state=tk.DISABLED)
        self._update_count()

    def _on_export(self) -> None:
        """导出全部日志到文本文件。"""
        file_path = filedialog.asksaveasfilename(
            parent=self.parent,
            title="导出日志",
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile="app_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        if not file_path:
            return
        if self.log_manager.export_to_file(file_path):
            messagebox.showinfo("导出日志", "日志已导出到:\n%s" % file_path,
                                parent=self.parent)
        else:
            messagebox.showerror("导出日志", "日志导出失败:\n%s" % file_path,
                                 parent=self.parent)

    def _on_copy_all(self) -> None:
        """复制全部显示的日志到剪贴板。"""
        if self.text_widget is None:
            return
        content = self.text_widget.get("1.0", tk.END + "-1c")
        self.parent.clipboard_clear()
        self.parent.clipboard_append(content)

    def _on_copy_selection(self, event=None):
        """复制当前选中的文本（Ctrl+C）。"""
        if self.text_widget is None:
            return
        try:
            selection = self.text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.parent.clipboard_clear()
            self.parent.clipboard_append(selection)
        except tk.TclError:
            pass  # 无选中内容
        return "break"

    def _show_context_menu(self, event) -> None:
        """右键菜单：复制选中内容。"""
        if self.text_widget is None:
            return
        menu = tk.Menu(self.parent, tearoff=0)
        menu.add_command(label="复制选中", command=self._on_copy_selection)
        menu.add_command(label="复制全部", command=self._on_copy_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def destroy(self) -> None:
        """销毁面板并取消日志订阅。"""
        try:
            self.log_manager.unsubscribe(self.append_log)
        except Exception:
            pass
