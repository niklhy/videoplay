# -*- coding: utf-8 -*-
"""视图控制模块（对应设计文档第34章）。

负责管理图库浏览器的显示方式：
- 顶部菜单栏"视图"菜单：缩略图尺寸切换、刷新缩略图、自动调整列数
- 缩略图尺寸持久化到配置，并在启动时恢复

仅使用标准库 + tkinter，契约（常量、事件常量）一律从契约文件导入。
"""
import tkinter as tk

from constants import THUMBNAIL_DEFAULT_SIZE, THUMBNAIL_SIZES
from events import EVENT_THUMBNAIL_SIZE_CHANGED


class ViewManager:
    """视图控制管理器。

    属性:
        root:             主窗口
        gallery_viewer:   图库查看器
        config_manager:   配置管理器
        event_bus:        事件总线
        view_menu:        视图菜单引用
        size_var:         尺寸选择变量
        auto_adjust_var:  自动调整列数勾选变量
    """

    def __init__(self, root: tk.Tk, gallery_viewer, config_manager, event_bus):
        """初始化视图控制模块，并按配置恢复上次的缩略图尺寸。

        :param root:            主窗口
        :param gallery_viewer:  图库查看器（GalleryViewer）
        :param config_manager:  配置管理器（ConfigManager）
        :param event_bus:       事件总线（EventBus）
        """
        self.root = root
        self.gallery_viewer = gallery_viewer
        self.config_manager = config_manager
        self.event_bus = event_bus

        self.view_menu = None
        self.size_var = tk.StringVar(value=THUMBNAIL_DEFAULT_SIZE)
        self.auto_adjust_var = tk.BooleanVar(value=True)

        # 启动恢复（设计34.6节）：读取上次保存的缩略图尺寸并应用
        self._restore_saved_size()

    # ------------------------------------------------------------------
    # 菜单创建
    # ------------------------------------------------------------------
    def create_view_menu(self, menubar: tk.Menu) -> tk.Menu:
        """创建视图菜单并添加到菜单栏。

        菜单结构（设计34.3节）：
            视图(V)
            ├── 缩略图大小
            │   ├── ○ 小 (150像素)
            │   ├── ● 中 (200像素)   <- 默认选中
            │   ├── ○ 大 (250像素)
            │   └── ○ 最大 (300像素)
            ├── ---------------
            ├── 刷新缩略图      Ctrl+R
            └── 自动调整列数    [x]

        :param menubar: 菜单栏
        :return: 视图菜单
        """
        # 创建视图菜单
        self.view_menu = tk.Menu(menubar, tearoff=0)

        # 缩略图大小子菜单
        size_menu = tk.Menu(self.view_menu, tearoff=0)
        self.size_var = tk.StringVar(value=self.gallery_viewer.get_thumbnail_size())

        for key, config in THUMBNAIL_SIZES.items():
            size_menu.add_radiobutton(
                label=f"{config['label']} ({config['width']}像素)",
                variable=self.size_var,
                value=key,
                command=lambda k=key: self._on_size_change(k),
            )

        self.view_menu.add_cascade(label="缩略图大小", menu=size_menu)
        self.view_menu.add_separator()

        # 刷新
        self.view_menu.add_command(
            label="刷新缩略图",
            command=self._on_refresh,
            accelerator="Ctrl+R",
        )

        # 自动调整列数
        self.auto_adjust_var = tk.BooleanVar(value=True)
        self.view_menu.add_checkbutton(
            label="自动调整列数",
            variable=self.auto_adjust_var,
            command=self._on_auto_adjust_toggle,
        )

        menubar.add_cascade(label="视图", menu=self.view_menu)
        return self.view_menu

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_size_change(self, size_key: str) -> None:
        """缩略图尺寸切换回调（设计34.4节）。

        :param size_key: 目标尺寸键名（THUMBNAIL_SIZES 的键）
        """
        # 处理缩略图尺寸切换
        old_size = self.gallery_viewer.get_thumbnail_size()
        if old_size == size_key:
            return

        # 保存到配置
        self.config_manager.set_thumbnail_size(size_key)

        # 通知图库更新
        self.gallery_viewer.set_thumbnail_size(size_key)

        # 发布事件
        self.event_bus.publish(EVENT_THUMBNAIL_SIZE_CHANGED,
                               old_size=old_size,
                               new_size=size_key)

        # 更新菜单标记
        self.size_var.set(size_key)

    def _on_refresh(self) -> None:
        """刷新缩略图菜单项回调（快捷键 Ctrl+R）。"""
        try:
            self.gallery_viewer.refresh()
        except Exception:
            # 图库查看器尚未加载内容时静默忽略
            pass

    def _on_auto_adjust_toggle(self) -> None:
        """自动调整列数勾选回调：将状态同步到图库查看器。"""
        enabled = bool(self.auto_adjust_var.get())
        try:
            self.gallery_viewer.auto_adjust_columns = enabled
        except Exception:
            # 查看器不支持该属性时静默忽略
            pass

    # ------------------------------------------------------------------
    # 菜单状态
    # ------------------------------------------------------------------
    def _update_menu_checkmarks(self) -> None:
        """更新菜单选中标记（与当前图库尺寸保持一致）。"""
        if self.size_var is not None:
            self.size_var.set(self.get_current_size())

    def get_current_size(self) -> str:
        """获取当前缩略图尺寸键名。

        :return: 尺寸键名（THUMBNAIL_SIZES 的键）
        """
        try:
            return self.gallery_viewer.get_thumbnail_size()
        except Exception:
            return THUMBNAIL_DEFAULT_SIZE

    def set_size(self, size_key: str) -> None:
        """设置缩略图尺寸（供外部调用，等效菜单选择）。

        :param size_key: 目标尺寸键名
        """
        if size_key not in THUMBNAIL_SIZES:
            return
        self._on_size_change(size_key)

    # ------------------------------------------------------------------
    # 启动恢复
    # ------------------------------------------------------------------
    def _restore_saved_size(self) -> None:
        """启动恢复（设计34.6节）：读取配置中保存的缩略图尺寸并应用。"""
        try:
            size_key = self.config_manager.get_thumbnail_size()
        except Exception:
            size_key = THUMBNAIL_DEFAULT_SIZE
        if size_key not in THUMBNAIL_SIZES:
            size_key = THUMBNAIL_DEFAULT_SIZE

        # 应用到图库查看器
        try:
            self.gallery_viewer.set_thumbnail_size(size_key)
        except Exception:
            pass

        # 同步菜单选择变量
        self.size_var.set(size_key)
