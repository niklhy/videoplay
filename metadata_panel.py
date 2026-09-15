# -*- coding: utf-8 -*-
"""视频元数据面板模块（对应设计文档第22章）。

显示当前选中视频/图片的详细元数据信息。
默认只显示 2-3 行关键信息（标题/演员/发行日期/时长），
点击展开按钮可显示完整元数据（含同步状态、关联视频等）。

仅使用标准库 + tkinter，契约（数据类、事件常量）一律从契约文件导入。
"""
import tkinter as tk

from events import EVENT_ACTOR_SELECTED, EVENT_METADATA_UPDATED
from models import FileMetadata


class MetadataPanel:
    """视频元数据面板。

    属性:
        parent:           父控件
        db_manager:       数据库管理器
        event_bus:        事件总线
        actor_manager:    演员管理器
        container:        面板容器
        summary_frame:    摘要区域（默认高度约60px）
        detail_frame:     详情区域（展开时约200px）
        expand_btn:       展开/收起按钮
        is_expanded:      当前是否展开详情
        current_metadata: 当前显示的元数据
    """

    # 面板高度常量（像素）
    SUMMARY_HEIGHT = 60      # 摘要区默认高度
    DETAIL_HEIGHT = 200      # 详情区展开高度

    def __init__(self, parent: tk.Widget, db_manager, event_bus, actor_manager):
        """初始化元数据面板。

        :param parent:        父控件
        :param db_manager:    数据库管理器（DatabaseManager）
        :param event_bus:     事件总线（EventBus）
        :param actor_manager: 演员管理器（ActorManager）
        """
        self.parent = parent
        self.db_manager = db_manager
        self.event_bus = event_bus
        self.actor_manager = actor_manager

        self.container = None
        self.summary_frame = None
        self.detail_frame = None
        self.expand_btn = None

        self.is_expanded = False
        self.current_metadata = None

        # 摘要/详情内容控件引用（重建时替换）
        self._summary_content = None
        self._detail_content = None

        self._create_ui()

        # 订阅元数据更新事件，自动刷新显示
        if self.event_bus is not None:
            self.event_bus.subscribe(EVENT_METADATA_UPDATED, self._on_metadata_updated)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _create_ui(self) -> None:
        """创建面板 UI：容器 + 摘要区 + 展开按钮 + 详情区。"""
        self.container = tk.Frame(self.parent, bd=1, relief=tk.GROOVE)
        self.container.pack(fill=tk.X, side=tk.TOP)
        # 容器高度由内部区域控制，禁止自动传播尺寸
        self.container.pack_propagate(False)
        self.container.config(height=self.SUMMARY_HEIGHT)

        # 摘要区：默认显示 2-3 行关键信息
        self.summary_frame = tk.Frame(self.container)
        self.summary_frame.pack(fill=tk.X, side=tk.TOP)
        self.summary_frame.pack_propagate(False)
        self.summary_frame.config(height=self.SUMMARY_HEIGHT)

        # 展开/收起按钮
        self.expand_btn = tk.Button(
            self.container, text="展开▼", command=self._on_expand_toggle,
            relief=tk.FLAT, padx=4,
        )
        self.expand_btn.pack(fill=tk.X, side=tk.TOP)

        # 详情区：展开时显示完整字段
        self.detail_frame = tk.Frame(self.container)
        self.detail_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        self.detail_frame.pack_propagate(False)
        self.detail_frame.config(height=0)

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    def show_metadata(self, metadata: FileMetadata) -> None:
        """显示指定元数据（摘要 + 详情）。

        演员信息优先通过 actor_manager.get_video_actors 查询，
        失败时回退到 metadata.actor_names。

        :param metadata: 文件元数据（FileMetadata）
        """
        self.current_metadata = metadata

        # 刷新摘要区
        if self._summary_content is not None:
            self._summary_content.destroy()
        self._summary_content = tk.Frame(self.summary_frame)
        self._summary_content.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        summary_text = self._format_metadata_summary(metadata)
        summary_label = tk.Label(
            self._summary_content, text=summary_text,
            justify=tk.LEFT, anchor=tk.W,
        )
        summary_label.pack(fill=tk.X, side=tk.TOP)

        # 摘要区第二行：可点击演员名（点击发布 EVENT_ACTOR_SELECTED）
        actor_ids = self._resolve_actor_ids(metadata)
        if actor_ids:
            actor_row = tk.Frame(self._summary_content)
            actor_row.pack(fill=tk.X, side=tk.TOP)
            tk.Label(actor_row, text="演员:", anchor=tk.W).pack(side=tk.LEFT)
            self._build_actor_labels(actor_row, actor_ids)

        # 刷新详情区
        if self._detail_content is not None:
            self._detail_content.destroy()
        self._detail_content = tk.Frame(self.detail_frame)
        self._detail_content.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        fields = self._format_metadata_detail(metadata)
        for row_idx, (field_name, field_value) in enumerate(fields):
            name_label = tk.Label(
                self._detail_content, text=f"{field_name}:",
                justify=tk.LEFT, anchor=tk.W,
            )
            name_label.grid(row=row_idx, column=0, sticky=tk.W, padx=(0, 6))

            if field_name == "演员" and actor_ids:
                # 演员字段：显示为可点击标签
                value_frame = tk.Frame(self._detail_content)
                value_frame.grid(row=row_idx, column=1, sticky=tk.W)
                self._build_actor_labels(value_frame, actor_ids)
            else:
                value_label = tk.Label(
                    self._detail_content, text=field_value,
                    justify=tk.LEFT, anchor=tk.W,
                )
                value_label.grid(row=row_idx, column=1, sticky=tk.W)

        self.detail_frame.grid_columnconfigure(1, weight=1)

    def clear(self) -> None:
        """清空面板显示，恢复初始状态。"""
        self.current_metadata = None
        if self._summary_content is not None:
            self._summary_content.destroy()
            self._summary_content = None
        if self._detail_content is not None:
            self._detail_content.destroy()
            self._detail_content = None
        if self.is_expanded:
            self.is_expanded = False
            self.expand_btn.config(text="展开▼")
            self.container.config(height=self.SUMMARY_HEIGHT)
            self.detail_frame.config(height=0)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_expand_toggle(self) -> None:
        """展开/收起按钮回调：切换容器与详情区高度。"""
        if self.is_expanded:
            # 收起：只保留摘要区高度
            self.is_expanded = False
            self.expand_btn.config(text="展开▼")
            self.detail_frame.config(height=0)
            self.container.config(height=self.SUMMARY_HEIGHT)
        else:
            # 展开：摘要区 + 详情区
            self.is_expanded = True
            self.expand_btn.config(text="收起▶")
            self.detail_frame.config(height=self.DETAIL_HEIGHT)
            self.container.config(height=self.SUMMARY_HEIGHT + self.DETAIL_HEIGHT)

    def _on_metadata_updated(self, relative_path: str, metadata: FileMetadata) -> None:
        """元数据更新事件回调：若更新的是当前显示项则自动刷新。

        :param relative_path: 更新的文件相对路径
        :param metadata:      更新后的元数据
        """
        if self.current_metadata is None:
            return
        if self.current_metadata.relative_path == relative_path:
            self.show_metadata(metadata)

    def _on_actor_click(self, actor_id: int) -> None:
        """演员名点击回调：发布 EVENT_ACTOR_SELECTED 供演员页跳转。

        :param actor_id: 演员ID
        """
        if self.event_bus is not None:
            self.event_bus.publish(EVENT_ACTOR_SELECTED, actor_id=actor_id)

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------
    def _format_metadata_summary(self, metadata: FileMetadata) -> str:
        """格式化摘要信息（2-3 行关键信息：标题/发行日期/时长）。

        :param metadata: 文件元数据
        :return: 摘要文本
        """
        lines = []
        title = metadata.title or metadata.file_name or "（无标题）"
        lines.append(f"标题: {title}")

        date_str = metadata.release_date or "未知"
        duration_str = metadata.duration or "未知"
        lines.append(f"发行日期: {date_str}    时长: {duration_str}")

        return "\n".join(lines)

    def _format_metadata_detail(self, metadata: FileMetadata) -> list:
        """格式化详情字段列表（按设计22.3节字段映射表）。

        :param metadata: 文件元数据
        :return: [(字段名, 字段值), ...]
        """
        sync_map = {
            "pending": "待同步",
            "synced": "已同步",
            "failed": "同步失败",
        }
        sync_status = sync_map.get(metadata.sync_status, metadata.sync_status or "未知")
        video_link = metadata.video_link or "无"
        duration_str = metadata.duration or "未知"
        date_str = metadata.release_date or "未知"

        return [
            ("标题", metadata.title or metadata.file_name or "（无标题）"),
            ("演员", metadata.get_actors_string() or "未知"),
            ("发行日期", date_str),
            ("时长", duration_str),
            ("同步状态", sync_status),
            ("关联视频", video_link),
        ]

    # ------------------------------------------------------------------
    # 演员名辅助
    # ------------------------------------------------------------------
    def _resolve_actor_ids(self, metadata: FileMetadata) -> list:
        """解析当前元数据对应的演员ID列表。

        优先使用 metadata.actor_ids（运行时属性），
        否则通过 actor_manager.get_video_actors 查询。

        :param metadata: 文件元数据
        :return: 演员ID列表
        """
        if metadata.actor_ids:
            return list(metadata.actor_ids)
        if self.actor_manager is not None and metadata.relative_path:
            try:
                actors = self.actor_manager.get_video_actors(metadata.relative_path)
            except Exception:
                return []
            return [a.actor_id for a in actors if getattr(a, "actor_id", None) is not None]
        return []

    def _build_actor_labels(self, parent: tk.Widget, actor_ids: list) -> None:
        """在父控件中构建可点击的演员名标签。

        每个演员名显示为蓝色可点击文本，点击后发布
        EVENT_ACTOR_SELECTED 事件供演员页跳转。

        :param parent:    父控件
        :param actor_ids: 演员ID列表
        """
        for actor_id in actor_ids:
            display_name = self._get_actor_display_name(actor_id)
            if not display_name:
                continue
            lbl = tk.Label(
                parent, text=display_name, fg="#1a5276",
                cursor="hand2", padx=2,
            )
            lbl.pack(side=tk.LEFT)
            lbl.bind("<Button-1>", lambda e, aid=actor_id: self._on_actor_click(aid))
            # 分隔符
            tk.Label(parent, text="/", fg="#808080").pack(side=tk.LEFT)

    def _get_actor_display_name(self, actor_id: int) -> str:
        """获取演员的显示名称。

        :param actor_id: 演员ID
        :return: 显示名称（查不到时返回空字符串）
        """
        if self.actor_manager is None:
            return ""
        try:
            actor = self.actor_manager.get_actor_by_id(actor_id)
        except Exception:
            return ""
        if actor is None:
            return ""
        if hasattr(actor, "get_display_name"):
            return actor.get_display_name()
        return getattr(actor, "japanese_name", "") or str(actor_id)
