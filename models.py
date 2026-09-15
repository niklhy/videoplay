# -*- coding: utf-8 -*-
"""共享数据类（契约文件，对应00号文档"共享契约清单A"）。

所有模块的数据结构一律从此文件导入，禁止各自重新定义。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# 配置相关（设计文档 3.2 节）
# ---------------------------------------------------------------------------
@dataclass
class RootConfig:
    """设备根目录配置。"""
    root_id: str                    # 根目录唯一标识，如 "D1"
    name: str                       # 显示名称，如 "本地视频"
    path: str                       # 绝对路径，如 "D:/视频" 或 "\\\\NAS\\Share"
    root_type: str = "local"        # "local" | "network"
    enabled: bool = True


@dataclass
class LastState:
    """上次运行状态。"""
    selected_root: str = ""               # 上次选中的根目录ID
    selected_folder: str = ""             # 上次选中的文件夹相对路径
    tree2_expanded: list = field(default_factory=list)  # Tree2展开的节点路径列表


# ---------------------------------------------------------------------------
# 文件元数据（设计文档 4.2 节）
# ---------------------------------------------------------------------------
@dataclass
class FileMetadata:
    file_id: Optional[int] = None
    device_id: str = ""
    relative_path: str = ""         # 相对于根目录的路径（唯一性键之一）
    file_name: str = ""
    title: str = ""
    duration: str = ""              # 文本形式时长，如 "120min"
    release_date: str = ""
    cover_path: str = ""            # 封面（缩略图）相对路径
    sync_status: str = "pending"    # pending | synced | failed
    video_link: str = ""            # 关联主视频相对路径
    video_code: str = ""            # 番号/编号
    last_scan: Optional[datetime] = None
    created_at: Optional[datetime] = None
    actor_names: list = field(default_factory=list)   # 运行时属性
    actor_ids: list = field(default_factory=list)     # 运行时属性

    def get_actors_string(self) -> str:
        return ", ".join(self.actor_names) if self.actor_names else ""


# ---------------------------------------------------------------------------
# 内存索引（设计文档第七章）
# ---------------------------------------------------------------------------
@dataclass
class FolderNode:
    path: str
    name: str
    subfolders: list = field(default_factory=list)
    media_files: list = field(default_factory=list)
    image_files: list = field(default_factory=list)
    metadata_map: dict = field(default_factory=dict)   # relative_path -> FileMetadata
    last_scan: Optional[datetime] = None
    is_expanded: bool = False


# ---------------------------------------------------------------------------
# 图库（设计文档 11.2 节）
# ---------------------------------------------------------------------------
@dataclass
class GalleryItem:
    item_id: str
    display_name: str
    thumbnail_path: str
    video_path: Optional[str] = None
    image_paths: list = field(default_factory=list)
    current_image_index: int = 0
    match_mode: str = ""
    metadata: Optional[FileMetadata] = None
    is_folder: bool = False
    folder_path: str = ""
    sub_items_count: int = 0
    is_linked: bool = False
    linked_from: str = ""
    show_filename: bool = True
    show_actors: bool = True
    show_duration: bool = True
    show_sync_badge: bool = False


# ---------------------------------------------------------------------------
# 搜索（设计文档 19.2 节）
# ---------------------------------------------------------------------------
@dataclass
class SearchResultItem:
    item_id: str
    item_type: str                  # "video" | "image" | "folder"
    file_path: str
    relative_path: str
    root_id: str
    file_name: str
    matched_field: str
    matched_text: str
    thumbnail_path: str = ""
    metadata: Optional[FileMetadata] = None
    parent_folder: str = ""

    def open_location(self) -> None:
        """在系统资源管理器中打开所在位置。"""
        import os
        import subprocess
        folder = os.path.dirname(self.file_path)
        if os.path.isdir(folder):
            subprocess.Popen(['explorer', folder])


@dataclass
class SearchResult:
    keyword: str = ""
    total_count: int = 0
    items: list = field(default_factory=list)   # list[SearchResultItem]
    search_time_ms: int = 0
    search_scope: str = "current_device"

    def get_by_type(self, item_type: str) -> list:
        return [it for it in self.items if it.item_type == item_type]

    def get_by_folder(self, folder_path: str) -> list:
        return [it for it in self.items if it.parent_folder == folder_path]

    def sort_by(self, field_name: str, reverse: bool = False) -> None:
        self.items.sort(key=lambda it: getattr(it, field_name, ""), reverse=reverse)


# ---------------------------------------------------------------------------
# 标签（设计文档 21.4 节）
# ---------------------------------------------------------------------------
@dataclass
class Tag:
    tag_id: int
    device_id: str
    tag_name: str
    tag_color: str = '#2196F3'
    file_count: int = 0
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# 视频关联（设计文档 24.4 节）
# ---------------------------------------------------------------------------
@dataclass
class VideoLink:
    link_id: int
    device_id: str
    primary_video_path: str
    linked_folder_path: str
    match_code: str = ''
    created_at: Optional[datetime] = None

    def is_valid(self) -> bool:
        import os
        return bool(self.primary_video_path) and bool(self.linked_folder_path)

    def get_display_name(self) -> str:
        import os
        return os.path.basename(self.primary_video_path) or self.primary_video_path


@dataclass
class LinkSuggestion:
    primary_path: str
    linked_folder_path: str
    match_code: str
    match_confidence: float = 0.0
    match_reason: str = ""

    def to_link(self) -> "VideoLink":
        return VideoLink(link_id=0, device_id="", primary_video_path=self.primary_path,
                         linked_folder_path=self.linked_folder_path,
                         match_code=self.match_code)


# ---------------------------------------------------------------------------
# 演员（设计文档 31.5 节）
# ---------------------------------------------------------------------------
@dataclass
class Actor:
    actor_id: Optional[int] = None
    japanese_name: str = ""         # 日文名（唯一）
    chinese_name: str = ""          # 中文名
    initial_letter: str = ""        # 首字母/声母
    alt_japanese_name: str = ""     # 关联日文名（曾用名）
    alt_chinese_name: str = ""      # 关联中文名
    profile_image: str = ""         # 头像路径
    notes: str = ""                 # 备注
    video_count: int = 0            # 关联视频数（运行时计算）
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def get_all_names(self) -> list:
        names = [self.japanese_name]
        if self.chinese_name:
            names.append(self.chinese_name)
        if self.alt_japanese_name:
            names.append(self.alt_japanese_name)
        if self.alt_chinese_name:
            names.append(self.alt_chinese_name)
        return names

    def get_display_name(self) -> str:
        if self.chinese_name:
            return f"{self.japanese_name}({self.chinese_name})"
        return self.japanese_name


@dataclass
class ActorFolderConfig:
    folder_id: Optional[int] = None
    device_id: str = ""
    folder_path: str = ""
    folder_name: str = ""
    is_enabled: bool = True
    last_scan: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class ActorNameSet:
    japanese_name: str
    chinese_name: str


@dataclass
class ActorFolderInfo:
    folder_name: str
    initial: str
    expected_initial: str
    is_initial_correct: bool
    primary: ActorNameSet
    alternate: Optional[ActorNameSet] = None


# ---------------------------------------------------------------------------
# 日志（设计文档第二十五章）
# ---------------------------------------------------------------------------
@dataclass
class LogEntry:
    level: str
    source: str
    message: str
    details: str = ""
    timestamp: Optional[datetime] = None

    def format(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else ""
        base = f"[{ts}] [{self.level}] [{self.source}] {self.message}"
        return f"{base}\n{self.details}" if self.details else base
