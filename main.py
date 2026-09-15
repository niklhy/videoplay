# -*- coding: utf-8 -*-
"""主程序入口（设计文档第十四章）。

MediaBrowserApp 负责全部模块的装配：
- initialize：按依赖图顺序实例化配置 / 数据库 / 事件总线 / 状态机 /
  日志及各功能模块；
- create_ui：LayoutManager 布局 + 双树导航 + 图库 + 元数据/信息面板 +
  状态栏 + 菜单栏；
- start：启动四阶段时序（IDLE -> DEVICE_LOADING -> FOLDER_LOADING -> READY）
  并恢复上次浏览状态（根目录 / 文件夹树展开路径 / 选中文件夹）；
- on_closing：保存上次状态与布局状态、配置、关闭数据库。

事件总线经 ``root.after(50)`` 轮询 ``process_events``，把后台线程产生的
事件安全切换到 UI 线程；模块间联动（根目录选中 / 文件夹选中 / 图库数据）
全部经事件订阅完成，本类是唯一的装配者与订阅方。
"""
import os
import platform
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from constants import (
    APP_NAME,
    APP_VERSION,
    DB_FILE,
    STATE_DEVICE_LOADING,
    STATE_FOLDER_LOADING,
    STATE_IDLE,
    STATE_READY,
)
from models import LastState
from events import (
    EVENT_DEVICE_SELECTED,
    EVENT_FOLDER_SELECTED,
    EVENT_GALLERY_DATA_READY,
    EVENT_ROOT_SELECTED,
)

from fonts import init_cjk_fonts
from config_manager import ConfigManager
from db_manager import DatabaseManager
from event_bus import EventBus
from state_machine import AppStateMachine
from folder_index import FolderIndex
from async_scanner import AsyncScanner
from layout_manager import LayoutManager
from device_tree import DeviceTree
from folder_tree import FolderTree
from gallery_viewer import GalleryViewer
from metadata_panel import MetadataPanel
from info_panel import InfoPanel
from tag_manager import TagManager, TagPanel
from actor_manager import ActorManager, ActorManageTab
from search_module import SearchManager, SearchPanel
from view_manager import ViewManager
from bookmark_manager import BookmarkManager, BookmarkTree
from video_link_manager import VideoLinkManager, VideoLinkManagerDialog
from player_settings import PlayerSettings
from context_menu import ContextMenuManager
from log_module import LogManager, LogPanel
from pattern_recognition_engine import PatternRecognitionEngine
from crawler_config import CrawlerConfigManager, CustomCodeManager
from crawler_module import CrawlerManager
from crawler_config_panel import CrawlerConfigPanel

# 右键菜单请求事件（gallery_viewer 发布，本模块订阅）
EVENT_CONTEXT_MENU_REQUEST = "context_menu_request"

# 事件轮询间隔（毫秒）
EVENT_POLL_INTERVAL_MS = 50


class MediaBrowserApp:
    """媒体图库浏览器主窗口（设计 14.1 节）。

    属性:
        root: tk.Tk                        主窗口
        config_manager: ConfigManager      配置管理器
        db_manager: DatabaseManager        数据库管理器
        event_bus: EventBus                事件总线
        state_machine: AppStateMachine     应用状态机
        folder_index: FolderIndex          内存文件夹索引
        async_scanner: AsyncScanner        异步扫描器
        device_tree: DeviceTree            左侧 Tree1 设备树
        folder_tree: FolderTree            左侧 Tree2 文件夹树
        gallery_viewer: GalleryViewer      图库浏览器
        info_panel: InfoPanel              信息面板
        layout_manager: LayoutManager      布局管理器
        tag_panel: TagPanel                标签面板
        search_panel: SearchPanel          搜索面板
        metadata_panel: MetadataPanel      元数据面板
        view_manager: ViewManager          视图控制
        tag_manager: TagManager            标签管理器
        actor_manager: ActorManager        演员管理器
        video_link_manager: VideoLinkManager    视频关联管理器
        player_settings: PlayerSettings    播放器设置
        context_menu: ContextMenuManager   右键菜单管理器
        log_manager: LogManager            日志管理器
        log_panel: LogPanel | None         日志面板（日志窗口打开时创建）
        pattern_engine: PatternRecognitionEngine  番号识别引擎
        crawler_config_manager: CrawlerConfigManager  爬虫配置管理器
        crawler_config_panel: CrawlerConfigPanel | None  爬虫配置面板
        crawler_manager: CrawlerManager    爬虫管理器
        custom_code_manager: CustomCodeManager  手动番号管理器
        device_id: str                     当前设备标识
        status_var: tk.StringVar           状态栏文本变量
    """

    def __init__(self, root: tk.Tk = None):
        """创建主窗口并完成初始化与 UI 装配。

        参数:
            root: 可选的外部 tk.Tk 实例（便于测试/冒烟时复用窗口）；
                  为 None 时自行创建。
        """
        self.root = root if root is not None else tk.Tk()
        self.root.title("%s v%s" % (APP_NAME, APP_VERSION))
        self.root.geometry("1280x800")
        self.root.minsize(900, 560)

        self.log_panel = None
        self.crawler_config_panel = None
        self._log_window = None
        self._crawler_window = None
        self._actor_window = None
        self._bookmark_window = None
        self._player_window = None
        self._search_visible = False
        self._closing = False

        self.initialize()
        self.create_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ------------------------------------------------------------------
    # 阶段1：初始化（设计 15.1 节）
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """按依赖图顺序初始化全部非 UI 模块。

        时序：CJK 字体 -> ConfigManager -> 确定当前设备 -> DatabaseManager
        -> FolderIndex -> AsyncScanner -> EventBus -> AppStateMachine
        -> LogManager -> 各功能模块。
        """
        # 1. CJK 字体（须在创建任何 Widget 之前设置全局默认字体）
        try:
            self.fonts_config = init_cjk_fonts(self.root)
        except Exception as exc:  # noqa: BLE001 - 字体失败不阻塞启动
            print("[main] CJK 字体初始化失败: %s" % exc)
            self.fonts_config = {}

        # 2. 配置管理器（读取 config.json）
        self.config_manager = ConfigManager()

        # 3. 确定当前设备标识（空则取本机名并写入配置）
        self.device_id = self.config_manager.get_current_device_id()
        if not self.device_id:
            self.device_id = platform.node() or "default"
            self.config_manager.set_current_device_id(self.device_id)

        # 4. 数据库管理器（连接 SQLite 并建表，幂等）
        self.db_manager = DatabaseManager(DB_FILE, self.device_id)
        self.db_manager.init_tables()

        # 5. 内存索引与异步扫描器
        self.folder_index = FolderIndex()
        self.async_scanner = AsyncScanner(self.folder_index)

        # 6. 事件总线（先于状态机/日志创建，供其发布事件）
        self.event_bus = EventBus()

        # 7. 状态机与日志管理器
        self.state_machine = AppStateMachine(self.event_bus)
        self.log_manager = LogManager(self.event_bus)

        # 8. 各功能模块（按依赖图实例化）
        self.tag_manager = TagManager(self.db_manager, self.event_bus)
        self.actor_manager = ActorManager(
            self.db_manager, self.config_manager,
            self.event_bus, self.log_manager)
        self.video_link_manager = VideoLinkManager(
            self.db_manager, self.event_bus, self.config_manager)
        self.bookmark_manager = BookmarkManager(
            self.config_manager, self.event_bus)
        self.player_settings = PlayerSettings(self.config_manager)
        self.pattern_engine = PatternRecognitionEngine(self.db_manager)
        self.crawler_config_manager = CrawlerConfigManager(
            self.config_manager, self.event_bus)
        self.custom_code_manager = CustomCodeManager(self.db_manager)
        self.crawler_manager = CrawlerManager(
            self.db_manager, self.event_bus,
            crawler_config_manager=self.crawler_config_manager,
            custom_code_manager=self.custom_code_manager)
        self.search_manager = SearchManager(
            self.db_manager, self.async_scanner,
            self.folder_index, self.config_manager)

        self.log_manager.info(
            "main", "模块初始化完成",
            "device_id=%s db=%s" % (self.device_id, DB_FILE))

    # ------------------------------------------------------------------
    # 阶段1：UI 装配
    # ------------------------------------------------------------------
    def create_ui(self) -> None:
        """创建整体布局、各区域控件、菜单栏、状态栏并订阅事件。"""
        # ---- 菜单栏 ----
        self._create_menu()

        # ---- 状态栏（先 pack 到底部，为布局留出空间） ----
        self.status_var = tk.StringVar(value="就绪")
        self.status_bar = tk.Label(
            self.root, textvariable=self.status_var,
            bd=1, relief=tk.SUNKEN, anchor=tk.W, padx=6)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # ---- 整体布局 ----
        self.layout_manager = LayoutManager(self.root)
        self.layout_manager.create_layout()
        saved_layout = self.config_manager.data.get("layout_state")
        if isinstance(saved_layout, dict):
            self.layout_manager.restore_layout_state(saved_layout)

        # ---- 左侧：Tree1 设备树 / Tree2 文件夹树 ----
        self.device_tree = DeviceTree(
            self.layout_manager.tree1_frame, self.event_bus,
            self.config_manager)
        self.folder_tree = FolderTree(
            self.layout_manager.tree2_frame, self.event_bus,
            self.async_scanner)

        # ---- 右侧：标签区 / 搜索面板 / 元数据区 / 图库区 / 信息区 ----
        self.tag_panel = TagPanel(
            self.layout_manager.tag_frame, self.tag_manager, self.event_bus)
        self.tag_panel.pack(fill=tk.BOTH, expand=True)

        # 搜索面板独立容器（默认隐藏，Ctrl+F 或视图菜单切换）
        self._search_container = tk.Frame(self.layout_manager.right_frame,
                                          height=250)
        self._search_container.pack_propagate(False)
        self.search_panel = SearchPanel(
            self._search_container, self.search_manager, self.event_bus)

        self.metadata_panel = MetadataPanel(
            self.layout_manager.metadata_frame, self.db_manager,
            self.event_bus, self.actor_manager)

        self.gallery_viewer = GalleryViewer(
            self.layout_manager.gallery_frame, self.event_bus,
            self.db_manager, self.async_scanner,
            thumbnail_size=self.config_manager.get_thumbnail_size())

        self.info_panel = InfoPanel(self.layout_manager.info_frame)

        # ---- 视图控制 ----
        self.view_manager = ViewManager(
            self.root, self.gallery_viewer, self.config_manager,
            self.event_bus)
        # 填充视图菜单（缩略图大小 / 刷新 / 自动调整列数 + 搜索面板）
        self.view_manager.create_view_menu(self.menubar)
        self.view_manager.view_menu.add_separator()
        self.view_manager.view_menu.add_command(
            label="搜索面板", accelerator="Ctrl+F",
            command=self._show_search_panel)

        # ---- 右键菜单 ----
        self.context_menu = ContextMenuManager(
            self.root, self.gallery_viewer, self.player_settings,
            self.tag_manager, self.video_link_manager, self.db_manager,
            self.crawler_manager, self.log_manager,
            actor_manager=self.actor_manager,
            pattern_engine=self.pattern_engine,
            custom_code_manager=self.custom_code_manager)

        # ---- 事件订阅（模块间联动） ----
        self._subscribe_events()

        # ---- 快捷键 ----
        self.root.bind("<Control-f>", lambda e: self._show_search_panel())
        self.root.bind("<Control-F>", lambda e: self._show_search_panel())
        self.root.bind("<Control-r>", lambda e: self.gallery_viewer.refresh())
        self.root.bind("<Control-R>", lambda e: self.gallery_viewer.refresh())

    def _create_menu(self) -> None:
        """创建菜单栏框架：文件 / 视图(占位) / 工具 / 帮助。

        视图菜单的内容由 ``ViewManager.create_view_menu`` 在
        ``create_ui`` 中填充（依赖 gallery_viewer 已创建）。
        """
        self.menubar = tk.Menu(self.root)

        # 文件
        file_menu = tk.Menu(self.menubar, tearoff=0)
        file_menu.add_command(label="退出", command=self.on_closing)
        self.menubar.add_cascade(label="文件", menu=file_menu)

        # 视图菜单由 ViewManager.create_view_menu 在 create_ui 中填充

        # 工具
        tool_menu = tk.Menu(self.menubar, tearoff=0)
        tool_menu.add_command(label="爬虫配置",
                              command=self._open_crawler_window)
        tool_menu.add_command(label="日志窗口", command=self._open_log_window)
        tool_menu.add_separator()
        tool_menu.add_command(label="视频关联管理",
                              command=self._open_video_link_dialog)
        tool_menu.add_command(label="书签管理",
                              command=self._open_bookmark_window)
        tool_menu.add_command(label="演员管理",
                              command=self._open_actor_window)
        tool_menu.add_separator()
        tool_menu.add_command(label="设置播放器",
                              command=self._open_player_settings)
        self.menubar.add_cascade(label="工具", menu=tool_menu)

        # 帮助
        help_menu = tk.Menu(self.menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._show_about)
        self.menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=self.menubar)

    def _subscribe_events(self) -> None:
        """订阅事件总线，完成模块间联动接线。"""
        self.event_bus.subscribe(
            EVENT_ROOT_SELECTED, self._on_root_selected)
        self.event_bus.subscribe(
            EVENT_FOLDER_SELECTED, self._on_folder_selected)
        self.event_bus.subscribe(
            EVENT_GALLERY_DATA_READY, self._on_gallery_data_ready)
        self.event_bus.subscribe(
            EVENT_DEVICE_SELECTED, self._on_device_selected)
        self.event_bus.subscribe(
            EVENT_CONTEXT_MENU_REQUEST, self._on_context_menu_request)

    # ------------------------------------------------------------------
    # 阶段2~4：启动四阶段时序（设计 15.1 节）
    # ------------------------------------------------------------------
    def start(self) -> None:
        """执行启动恢复：设备树加载 -> 文件夹树恢复 -> 图库加载。

        状态机：IDLE -> DEVICE_LOADING -> FOLDER_LOADING -> READY。
        """
        self.log_manager.info("main", "启动恢复开始")

        # 阶段2：设备树加载
        self.state_machine.transition(
            STATE_DEVICE_LOADING, device_id=self.device_id)
        self.device_tree.build_tree()

        # 当前设备在配置中无记录时补一个设备节点（首次运行）
        devices = self.config_manager.data.get("devices", {})
        if self.device_id not in devices:
            self.device_tree.add_device(
                self.device_id, self._device_display_name(),
                self.config_manager.get_device_roots(self.device_id))
        self.device_tree.expand_device(self.device_id)

        # 选中上次选中的根目录（无效时取第一个）
        last_state = self.config_manager.get_last_state(self.device_id)
        roots = self.config_manager.get_device_roots(self.device_id)
        selected_root_id = last_state.selected_root
        if not selected_root_id or all(
                r.root_id != selected_root_id for r in roots):
            selected_root_id = roots[0].root_id if roots else ""

        if selected_root_id:
            # 触发 EVENT_ROOT_SELECTED（同步）-> 文件夹树构建
            self.device_tree.select_root(selected_root_id)

        # 无根目录配置：状态机推进到 FOLDER_LOADING 后直接进入 READY
        if self.state_machine.get_state() == STATE_DEVICE_LOADING:
            self.state_machine.transition(
                STATE_FOLDER_LOADING, root_id=selected_root_id)

        # 阶段3：文件夹树加载（恢复上次展开路径与选中文件夹）
        if selected_root_id and self.folder_tree.current_root_path:
            if last_state.selected_folder:
                self.folder_tree.expand_path(last_state.selected_folder)
                abs_path = self.config_manager.resolve_absolute_path(
                    last_state.selected_folder, selected_root_id)
                if abs_path and os.path.isdir(abs_path):
                    # 触发 EVENT_FOLDER_SELECTED（同步）-> 图库加载
                    self.folder_tree.select_node(abs_path)
                else:
                    self.folder_tree.select_node(
                        self.folder_tree.current_root_path)
            else:
                self.folder_tree.select_node(
                    self.folder_tree.current_root_path)

        # 阶段4：图库加载由 EVENT_FOLDER_SELECTED 订阅异步触发
        self.state_machine.transition(STATE_READY)

        # 启动事件轮询（后台线程 -> UI 线程的事件切换）
        self._poll_events()

        self.log_manager.info("main", "启动恢复完成")

    def _poll_events(self) -> None:
        """经 root.after 周期轮询事件队列（UI 线程消费后台事件）。"""
        if self._closing:
            return
        try:
            self.event_bus.process_events()
        except Exception as exc:  # noqa: BLE001 - 轮询异常不中断主循环
            print("[main] 事件处理异常: %s" % exc)
        try:
            self.root.after(EVENT_POLL_INTERVAL_MS, self._poll_events)
        except tk.TclError:
            pass  # 窗口已销毁

    # ------------------------------------------------------------------
    # 事件处理（模块联动）
    # ------------------------------------------------------------------
    def _on_device_selected(self, device_id: str = "", **kwargs) -> None:
        """设备选中：更新状态栏。"""
        self._update_status(device_name=str(device_id or self.device_id))

    def _on_root_selected(self, device_id: str = "", root_id: str = "",
                          root_path: str = "", **kwargs) -> None:
        """根目录选中：状态机推进并重建文件夹树（设计 15.1 阶段2->3）。

        用户切换根目录时状态机为 READY，先复位到 IDLE 再走正常转换链。
        """
        # 状态机转换链（READY -> IDLE -> DEVICE_LOADING -> FOLDER_LOADING）
        if self.state_machine.get_state() == STATE_READY:
            self.state_machine.transition(STATE_IDLE)
        if self.state_machine.get_state() == STATE_IDLE:
            self.state_machine.transition(
                STATE_DEVICE_LOADING, device_id=device_id or self.device_id)
        if self.state_machine.get_state() == STATE_DEVICE_LOADING:
            self.state_machine.transition(
                STATE_FOLDER_LOADING, root_id=root_id)

        # 重建文件夹树（EVENT_FOLDER_SELECTED 由树自身在选中时发布）
        self.folder_tree.set_root(root_id, root_path)
        self.folder_tree.build_from_root()

        self._update_status(root_id=root_id)
        self.log_manager.info(
            "main", "根目录已切换", "root_id=%s path=%s" % (root_id, root_path))

    def _on_folder_selected(self, folder_path: str = "",
                            root_id: str = "", **kwargs) -> None:
        """文件夹选中：加载图库并清空信息/元数据面板（阶段4 入口）。"""
        self.info_panel.clear()
        if folder_path:
            self.gallery_viewer.load_folder(folder_path)
        self._update_status(
            root_id=root_id or self.folder_tree.current_root_id,
            folder_path=folder_path)

    def _on_gallery_data_ready(self, selected_item=None, items=None,
                               folder_path: str = "", **kwargs) -> None:
        """图库数据就绪：选中项 -> 信息面板 + 元数据面板；整夹加载 -> 清空。"""
        if selected_item is not None:
            try:
                self.info_panel.show_file_info(selected_item)
            except Exception as exc:  # noqa: BLE001
                print("[main] 信息面板更新失败: %s" % exc)
            metadata = getattr(selected_item, "metadata", None)
            if metadata is not None:
                try:
                    self.metadata_panel.show_metadata(metadata)
                except Exception as exc:  # noqa: BLE001
                    print("[main] 元数据面板更新失败: %s" % exc)
        elif items is not None:
            # 整夹加载完成：无选中项，清空元数据面板
            self.metadata_panel.clear()

    def _on_context_menu_request(self, item=None, x: int = 0, y: int = 0,
                                 **kwargs) -> None:
        """右键菜单请求：弹出缩略图右键菜单。"""
        if item is None:
            return
        try:
            self.context_menu.show_thumbnail_menu(item, int(x), int(y))
        except Exception as exc:  # noqa: BLE001
            self.log_manager.error(
                "main", "右键菜单弹出失败", str(exc))

    # ------------------------------------------------------------------
    # 状态栏
    # ------------------------------------------------------------------
    def _device_display_name(self) -> str:
        """获取当前设备的显示名称（配置中的 name，缺失时回退本机名）。"""
        entry = self.config_manager.data.get("devices", {}).get(
            self.device_id, {})
        if isinstance(entry, dict) and entry.get("name"):
            return str(entry["name"])
        return self.device_id

    def _update_status(self, device_name: str = "",
                       root_id: str = "", folder_path: str = "") -> None:
        """更新状态栏文本：当前设备 | 根目录 | 当前文件夹。"""
        parts = ["当前设备: %s" % (device_name or self._device_display_name())]
        root_id = root_id or self.folder_tree.current_root_id
        if root_id:
            root = self.config_manager.get_root_by_id(root_id)
            root_label = root.name if root is not None else root_id
            parts.append("根目录: %s" % root_label)
        if folder_path:
            parts.append("文件夹: %s" % os.path.basename(
                os.path.normpath(folder_path)))
        self.status_var.set("  |  ".join(parts))

    # ------------------------------------------------------------------
    # 工具菜单：各功能窗口
    # ------------------------------------------------------------------
    def _open_log_window(self) -> None:
        """打开日志窗口（Toplevel + LogPanel）。"""
        if self._log_window is not None and self._log_window.winfo_exists():
            self._log_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("日志窗口")
        window.geometry("760x480")
        self.log_panel = LogPanel(window, self.log_manager)
        self._log_window = window
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_log_window())

    def _close_log_window(self) -> None:
        """关闭日志窗口并清理引用。"""
        if self._log_window is not None and self._log_window.winfo_exists():
            self._log_window.destroy()
        self._log_window = None
        self.log_panel = None

    def _open_crawler_window(self) -> None:
        """打开爬虫配置窗口（Toplevel + CrawlerConfigPanel）。"""
        if (self._crawler_window is not None
                and self._crawler_window.winfo_exists()):
            self._crawler_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("爬虫配置")
        window.geometry("860x600")
        self.crawler_config_panel = CrawlerConfigPanel(
            window, self.crawler_config_manager)
        self.crawler_config_panel.pack(fill=tk.BOTH, expand=True)
        self._crawler_window = window
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._close_toplevel("_crawler_window",
                                         "crawler_config_panel"))

    def _open_actor_window(self) -> None:
        """打开演员管理窗口（Toplevel + ActorManageTab）。"""
        if (self._actor_window is not None
                and self._actor_window.winfo_exists()):
            self._actor_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("演员管理")
        window.geometry("900x600")
        tab = ActorManageTab(window, self.actor_manager, self.event_bus)
        tab.pack(fill=tk.BOTH, expand=True)
        self._actor_window = window
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._close_toplevel("_actor_window"))

    def _close_toplevel(self, window_attr: str, panel_attr: str = None) -> None:
        """通用 Toplevel 关闭清理。"""
        window = getattr(self, window_attr, None)
        if window is not None and window.winfo_exists():
            window.destroy()
        setattr(self, window_attr, None)
        if panel_attr:
            setattr(self, panel_attr, None)

    def _open_video_link_dialog(self) -> None:
        """打开视频关联管理对话框。"""
        try:
            dialog = VideoLinkManagerDialog(
                self.root, self.video_link_manager,
                root_path=self.folder_tree.current_root_path)
            dialog.show_modal()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("错误", "视频关联管理打开失败: %s" % exc,
                                 parent=self.root)

    # ------------------------------------------------------------------
    # 工具菜单：书签管理
    # ------------------------------------------------------------------
    def _open_bookmark_window(self) -> None:
        """打开书签管理窗口（Toplevel + BookmarkTree）。

        BookmarkTree 双击书签名节点会发布 EVENT_FOLDER_SELECTED 事件，
        由本类的订阅回调跳转到对应文件夹。
        """
        if (self._bookmark_window is not None
                and self._bookmark_window.winfo_exists()):
            self._bookmark_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("书签管理")
        window.geometry("620x440")
        tree = BookmarkTree(window, self.bookmark_manager,
                            self.config_manager, self.event_bus)
        tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._bookmark_window = window
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._close_toplevel("_bookmark_window"))

    # ------------------------------------------------------------------
    # 工具菜单：播放器设置
    # ------------------------------------------------------------------
    def _open_player_settings(self) -> None:
        """打开播放器设置窗口（默认播放器 / 路径 / 存在性检查）。"""
        if (self._player_window is not None
                and self._player_window.winfo_exists()):
            self._player_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("播放器设置")
        window.resizable(False, False)

        body = tk.Frame(window, padx=12, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        player_var = tk.StringVar(
            value=self.player_settings.get_default_player())
        tk.Label(body, text="默认播放器:").grid(row=0, column=0, sticky="w")
        player_combo = ttk.Combobox(body, textvariable=player_var,
                                    values=("potplayer", "vlc"),
                                    state="readonly", width=12)
        player_combo.grid(row=0, column=1, sticky="w", pady=4)

        path_vars = {}
        for row, (label, key) in enumerate(
                (("PotPlayer 路径:", "potplayer"),
                 ("VLC 路径:", "vlc")), start=1):
            tk.Label(body, text=label).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value=self.player_settings.get_player_path(key))
            path_vars[key] = var
            tk.Entry(body, textvariable=var, width=52).grid(
                row=row, column=1, sticky="we", pady=4)

            def on_browse(var=var):
                """浏览选择播放器可执行文件。"""
                path = filedialog.askopenfilename(
                    parent=window,
                    filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
                if path:
                    var.set(path)

            tk.Button(body, text="浏览...", width=8,
                      command=on_browse).grid(row=row, column=2, padx=(4, 0))

        check_label = tk.Label(body, text="", fg="#808080")
        check_label.grid(row=3, column=0, columnspan=3, sticky="w")

        def on_check():
            """检查当前默认播放器路径是否存在。"""
            exists = self.player_settings.check_player_exists(
                player_var.get())
            path = self.player_settings.get_player_path(player_var.get())
            if exists:
                check_label.config(
                    text="✔ 播放器可用: %s" % path, fg="#2e7d32")
            else:
                check_label.config(
                    text="✘ 未找到播放器: %s（请设置正确路径）" % path,
                    fg="#c62828")

        tk.Button(body, text="检查", width=8,
                  command=on_check).grid(row=3, column=2, sticky="e",
                                         padx=(4, 0))

        def on_save():
            """保存播放器设置。"""
            self.player_settings.set_default_player(player_var.get())
            for key, var in path_vars.items():
                self.player_settings.set_player_path(key, var.get())
            on_check()
            messagebox.showinfo("提示", "播放器设置已保存", parent=window)

        btn_frame = tk.Frame(body)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=(10, 0))
        tk.Button(btn_frame, text="保存", width=8,
                  command=on_save).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="关闭", width=8,
                  command=window.destroy).pack(side=tk.LEFT, padx=4)

        on_check()
        self._player_window = window
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._close_toplevel("_player_window"))

    # ------------------------------------------------------------------
    # 视图 / 帮助
    # ------------------------------------------------------------------
    def _show_search_panel(self) -> str:
        """显示并聚焦搜索面板（Ctrl+F）。"""
        if not self._search_visible:
            self._search_container.pack(
                side=tk.TOP, fill=tk.X,
                after=self.layout_manager.metadata_frame)
            self._search_visible = True
        self.search_panel.show()
        self.search_panel.focus_search()
        return "break"

    def _show_about(self) -> None:
        """显示关于对话框。"""
        messagebox.showinfo(
            "关于",
            "%s v%s\n\n本地视频和图片浏览管理工具。\n"
            "支持多设备根目录、双树导航、标签 / 演员管理、\n"
            "视频关联、爬虫元数据采集与番号识别。" % (APP_NAME, APP_VERSION),
            parent=self.root)

    # ------------------------------------------------------------------
    # 关闭与运行
    # ------------------------------------------------------------------
    def on_closing(self) -> None:
        """退出：保存上次状态、布局状态、配置，关闭数据库并销毁窗口。"""
        if self._closing:
            return
        self._closing = True

        try:
            self._save_last_state()
        except Exception as exc:  # noqa: BLE001
            print("[main] 保存上次状态失败: %s" % exc)

        try:
            layout_state = self.layout_manager.save_layout_state()
            if layout_state:
                self.config_manager.data["layout_state"] = layout_state
            self.config_manager.save()
        except Exception as exc:  # noqa: BLE001
            print("[main] 保存配置失败: %s" % exc)

        try:
            self.crawler_manager.stop_auto_crawl()
        except Exception:
            pass

        try:
            self.async_scanner.executor.shutdown(wait=False)
        except Exception:
            pass

        try:
            self.db_manager.close()
        except Exception as exc:  # noqa: BLE001
            print("[main] 关闭数据库失败: %s" % exc)

        self.log_manager.info("main", "应用退出")
        self.root.destroy()

    def _save_last_state(self) -> None:
        """保存上次运行状态：选中根目录 / 选中文件夹 / 文件夹树展开节点。"""
        selected_root = ""
        selected = self.device_tree.get_selected_root()
        if selected is not None:
            selected_root = selected[1]
        if not selected_root:
            selected_root = self.folder_tree.current_root_id

        selected_folder = ""
        root_id = self.folder_tree.current_root_id
        folder_abs = self.folder_tree.get_selected_folder()
        if folder_abs and root_id:
            selected_folder = self.config_manager.get_relative_path(
                folder_abs, root_id)

        state = LastState(
            selected_root=selected_root,
            selected_folder=selected_folder,
            tree2_expanded=self.folder_tree.get_expanded_nodes())
        self.config_manager.set_last_state(self.device_id, state)

    def run(self) -> None:
        """进入 tkinter 主循环。"""
        self.root.mainloop()


def main() -> None:
    """程序主入口：创建应用、执行启动恢复、进入主循环。"""
    app = MediaBrowserApp()
    app.start()
    app.run()


if __name__ == "__main__":
    main()
