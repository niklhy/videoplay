# -*- coding: utf-8 -*-
"""界面布局模块（对应设计文档第20章）。

职责：整体布局管理、PanedWindow 分割条、布局状态存取。

布局结构：
    主窗口 (root)
    └── main_paned (tk.PanedWindow, horizontal)
        ├── left_frame (tk.Frame, 左侧整体，默认 280px)
        │   └── left_paned (tk.PanedWindow, vertical)
        │       ├── tree1_frame (Tree1 设备树，占 35%)
        │       └── tree2_frame (Tree2 文件夹树，占 65%)
        └── right_frame (tk.Frame, 右侧整体)
            ├── tag_frame     (标签区域，固定 40px)
            ├── metadata_frame(元数据区域，60px 折叠 / 200px 展开，
            │                  由 17 号元数据面板模块自行控制，本模块只提供容器)
            ├── gallery_frame (图库浏览区，自适应剩余高度)
            └── info_frame    (信息显示区，固定 60px)

本模块不创建任何具体业务 UI（设备树、图库等），只提供容器 Frame，
由 main.py 将各模块的 UI 放置到对应容器中。
"""

import tkinter as tk

# 区域默认尺寸（设计文档 20.2 节）
LEFT_DEFAULT_WIDTH = 280      # 左侧整体默认宽度（像素）
TAG_AREA_HEIGHT = 40          # 标签区域固定高度（像素）
METADATA_COLLAPSED_HEIGHT = 60    # 元数据区域折叠高度（像素）
METADATA_EXPANDED_HEIGHT = 200    # 元数据区域展开高度（像素）
INFO_AREA_HEIGHT = 60         # 信息显示区固定高度（像素）

# 分割条位置在状态字典中的键名
KEY_MAIN_SASH = "main_sash"    # 主分割条（左/右宽度）
KEY_LEFT_SASH = "left_sash"    # 左侧面板分割条（Tree1/Tree2 高度）


class LayoutManager:
    """界面布局管理器。

    管理整体布局的创建、四个功能区域容器的提供，
    以及 PanedWindow 分割条位置的保存与恢复。

    属性：
        root: tk.Tk 主窗口
        main_paned: tk.PanedWindow 主水平分割条
        left_paned: tk.PanedWindow 左侧垂直分割条
        right_frame: tk.Frame 右侧整体容器
    """

    def __init__(self, root: tk.Tk):
        """初始化布局管理器。

        参数：
            root: tk.Tk 主窗口实例
        """
        self.root = root
        self.main_paned = None
        self.left_paned = None
        self.right_frame = None
        # 四个功能区域容器（由 create_layout 创建）
        self.tag_frame = None
        self.metadata_frame = None
        self.gallery_frame = None
        self.info_frame = None
        # 左右树容器（由 create_layout 创建）
        self.tree1_frame = None
        self.tree2_frame = None

    def create_layout(self) -> None:
        """创建整体布局。

        依次创建：主水平 PanedWindow、左侧垂直 PanedWindow（Tree1/Tree2）、
        右侧 Frame（内含标签区/元数据区/图库区/信息区四个容器）。
        所有组件 pack 到 root 并随窗口缩放。
        """
        # 主水平分割条：左侧整体 | 右侧整体
        self.main_paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            sashwidth=5, bd=0, opaqueresize=True,
        )
        self.main_paned.pack(fill=tk.BOTH, expand=True)

        # ---- 左侧整体 ----
        left_frame = tk.Frame(self.main_paned, width=LEFT_DEFAULT_WIDTH)
        self.main_paned.add(left_frame, width=LEFT_DEFAULT_WIDTH, minsize=160)

        # 左侧垂直分割条：Tree1(35%) / Tree2(65%)
        self.left_paned = tk.PanedWindow(
            left_frame, orient=tk.VERTICAL,
            sashwidth=5, bd=0, opaqueresize=True,
        )
        self.left_paned.pack(fill=tk.BOTH, expand=True)

        self.tree1_frame = tk.Frame(self.left_paned)
        self.tree2_frame = tk.Frame(self.left_paned)
        self.left_paned.add(self.tree1_frame, height=200, minsize=60)
        self.left_paned.add(self.tree2_frame, height=380, minsize=60)

        # ---- 右侧整体 ----
        self.right_frame = tk.Frame(self.main_paned)
        self.main_paned.add(self.right_frame, minsize=400)

        # 标签区域：固定高度 40px，顶部
        self.tag_frame = tk.Frame(self.right_frame, height=TAG_AREA_HEIGHT)
        self.tag_frame.pack(side=tk.TOP, fill=tk.X)
        self.tag_frame.pack_propagate(False)

        # 元数据区域：默认折叠 60px，可由 17 号模块自行调整高度
        self.metadata_frame = tk.Frame(
            self.right_frame, height=METADATA_COLLAPSED_HEIGHT)
        self.metadata_frame.pack(side=tk.TOP, fill=tk.X)
        self.metadata_frame.pack_propagate(False)

        # 信息显示区：固定高度 60px，底部
        self.info_frame = tk.Frame(self.right_frame, height=INFO_AREA_HEIGHT)
        self.info_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.info_frame.pack_propagate(False)

        # 图库浏览区：填充剩余空间
        self.gallery_frame = tk.Frame(self.right_frame)
        self.gallery_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def get_left_frame(self) -> tk.Frame:
        """获取左侧整体容器 Frame。

        返回：
            tk.Frame，内部已包含垂直分割的 Tree1/Tree2 两个容器，
            供设备树模块与文件夹树模块放置各自 UI。
        """
        return self.left_paned

    def get_right_frame(self) -> tk.Frame:
        """获取右侧整体容器 Frame。

        返回：
            tk.Frame，内部已包含标签区/元数据区/图库区/信息区四个容器。
        """
        return self.right_frame

    def get_tag_frame(self) -> tk.Frame:
        """获取标签区域容器 Frame（固定高度 40px）。"""
        return self.tag_frame

    def get_metadata_frame(self) -> tk.Frame:
        """获取元数据区域容器 Frame。

        本模块只提供容器，折叠(60px)/展开(200px)由 17 号元数据面板
        模块通过调整该 Frame 高度自行控制。
        """
        return self.metadata_frame

    def get_gallery_frame(self) -> tk.Frame:
        """获取图库浏览区容器 Frame（自适应剩余高度）。"""
        return self.gallery_frame

    def get_info_frame(self) -> tk.Frame:
        """获取信息显示区容器 Frame（固定高度 60px）。"""
        return self.info_frame

    def save_layout_state(self) -> dict:
        """保存当前布局状态（分割条位置）。

        返回：
            dict，键值说明：
                "main_sash": [x]  主分割条位置（左/右宽度分界）
                "left_sash": [y]  左侧分割条位置（Tree1/Tree2 高度分界）
            尚未创建布局时返回空字典。
        """
        state = {}
        if self.main_paned is not None:
            try:
                coords = [self.main_paned.sash_coord(i)
                          for i in range(len(self.main_paned.panes()) - 1)]
                state[KEY_MAIN_SASH] = [int(c[0]) for c in coords]
            except tk.TclError:
                pass
        if self.left_paned is not None:
            try:
                coords = [self.left_paned.sash_coord(i)
                          for i in range(len(self.left_paned.panes()) - 1)]
                state[KEY_LEFT_SASH] = [int(c[1]) for c in coords]
            except tk.TclError:
                pass
        return state

    def restore_layout_state(self, state: dict) -> None:
        """恢复布局状态（分割条位置）。

        参数：
            state: save_layout_state() 返回的状态字典；
                   缺失或非法的键会被安全忽略。
        """
        if not isinstance(state, dict):
            return

        # 恢复主分割条（水平方向，使用 x 坐标）
        main_sash = state.get(KEY_MAIN_SASH)
        if (main_sash and self.main_paned is not None
                and len(self.main_paned.panes()) >= 2):
            try:
                self.main_paned.sash_place(0, int(main_sash[0]), 0)
            except (tk.TclError, ValueError, IndexError):
                pass

        # 恢复左侧分割条（垂直方向，使用 y 坐标）
        left_sash = state.get(KEY_LEFT_SASH)
        if (left_sash and self.left_paned is not None
                and len(self.left_paned.panes()) >= 2):
            try:
                self.left_paned.sash_place(0, 0, int(left_sash[0]))
            except (tk.TclError, ValueError, IndexError):
                pass
