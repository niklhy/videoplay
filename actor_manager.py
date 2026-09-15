# -*- coding: utf-8 -*-
"""演员管理模块（对应设计文档 26 号，31.4 / 31.7 / 31.8 节）。

包含两个类：

- ActorManager：演员数据管理器，负责演员文件夹配置、演员 CRUD、
  演员与视频关联、爬虫结果同步。数据经 db_manager 通用
  execute/query 辅助落库，变更后通过事件总线发布
  EVENT_METADATA_UPDATED / EVENT_ACTOR_SELECTED 刷新相关 UI。
- ActorManageTab：演员管理 Tab 页 UI（tk.Frame），包含演员文件夹区域、
  演员列表（点击表头排序）、搜索框、双击展开编辑区域、键盘字母快速定位。

共享数据类一律从 models.py 导入；事件常量一律从 events.py 导入。
"""
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import events
from actor_folder_parser import ActorFolderParser
from constants import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARNING,
)
from japanese_sort import JapaneseSort
from models import Actor, ActorFolderConfig, ActorNameSet

# 日志来源标识
_LOG_SOURCE = "ActorManager"

# update_actor 允许更新的字段白名单
_ACTOR_EDITABLE_FIELDS = (
    "japanese_name", "chinese_name", "initial_letter",
    "alt_japanese_name", "alt_chinese_name",
    "profile_image", "notes",
)


class ActorManager:
    """演员管理器（设计文档 31.4 节）。

    属性:
        db_manager: DatabaseManager 实例
        config_manager: ConfigManager 实例
        event_bus: EventBus 实例
        log_manager: LogManager 实例
        _actors_cache: dict[int, Actor]  演员缓存（actor_id -> Actor）
        _name_index: dict[str, int]      名称索引（全部名称 -> actor_id）
        _cache_dirty: bool               缓存脏标记
    """

    def __init__(self, db_manager, config_manager, event_bus, log_manager):
        """初始化演员管理器。

        :param db_manager: 数据库管理器（DatabaseManager）
        :param config_manager: 配置管理器（ConfigManager）
        :param event_bus: 事件总线（EventBus）
        :param log_manager: 日志管理器（LogManager）
        """
        self.db_manager = db_manager
        self.config_manager = config_manager
        self.event_bus = event_bus
        self.log_manager = log_manager
        self._actors_cache: dict[int, Actor] = {}
        self._name_index: dict[str, int] = {}
        self._cache_dirty = True
        self._cache_lock = threading.RLock()

    # ------------------------------------------------------------------
    # 内部辅助：日志 / 缓存
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str, details: str = "") -> None:
        """写一条日志（log_manager 不可用时静默跳过）。"""
        if self.log_manager is not None:
            try:
                self.log_manager.log(level, _LOG_SOURCE, message, details)
            except Exception:
                pass

    def _refresh_cache(self) -> None:
        """缓存脏时从数据库全量加载演员并重建名称索引。"""
        with self._cache_lock:
            if not self._cache_dirty:
                return
            rows = self.db_manager.query("SELECT * FROM actors ORDER BY actor_id")
            self._actors_cache = {
                row["actor_id"]: self.db_manager._row_to_actor(row)
                for row in rows
            }
            self._name_index = {}
            for actor_id, actor in self._actors_cache.items():
                for name in actor.get_all_names():
                    if name:
                        self._name_index[name] = actor_id
            self._cache_dirty = False

    def _invalidate_cache(self) -> None:
        """标记缓存失效（写操作后调用）。"""
        with self._cache_lock:
            self._cache_dirty = True

    def _notify_metadata_updated(self, **kwargs) -> None:
        """发布元数据更新事件，通知相关 UI 刷新。"""
        if self.event_bus is not None:
            try:
                self.event_bus.publish(events.EVENT_METADATA_UPDATED, **kwargs)
            except Exception:
                pass

    def _recompute_video_counts(self) -> None:
        """重算全部演员的关联视频数（actors.video_count，全表刷新）。"""
        self.db_manager.execute(
            """UPDATE actors SET video_count=COALESCE((
                   SELECT COUNT(DISTINCT va.device_id || '/' || va.relative_path)
                   FROM video_actors va WHERE va.actor_id=actors.actor_id
               ), 0)"""
        )

    # ------------------------------------------------------------------
    # 演员文件夹管理（设计 31.4 节）
    # ------------------------------------------------------------------
    def get_actor_folders(self) -> list:
        """获取当前设备的全部演员文件夹配置。

        :return: ActorFolderConfig 列表
        """
        return self.db_manager.get_actor_folders(only_enabled=False)

    def add_actor_folder(self, folder_path: str, folder_name: str = None) -> ActorFolderConfig:
        """添加演员文件夹配置（同设备同路径幂等）。

        :param folder_path: 文件夹路径
        :param folder_name: 文件夹名称（为空时取路径末段）
        :return: 对应的 ActorFolderConfig
        """
        if not folder_name:
            folder_name = folder_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        folder_id = self.db_manager.add_actor_folder(folder_path, folder_name)
        for cfg in self.db_manager.get_actor_folders(only_enabled=False):
            if cfg.folder_id == folder_id:
                self._log(LOG_LEVEL_INFO, f"添加演员文件夹: {folder_path}")
                return cfg
        return ActorFolderConfig(folder_id=folder_id, folder_path=folder_path,
                                 folder_name=folder_name)

    def remove_actor_folder(self, folder_id: int) -> bool:
        """移除演员文件夹配置。

        :param folder_id: 配置 ID
        :return: 是否删除了记录
        """
        result = self.db_manager.remove_actor_folder(folder_id)
        if result:
            self._log(LOG_LEVEL_INFO, f"移除演员文件夹配置 ID={folder_id}")
        return result

    def update_actors_from_folders(self) -> dict:
        """扫描已配置演员文件夹，解析并新增/更新演员。

        使用 ActorFolderParser 按命名规则解析文件夹名：
        文件夹中的演员不存在则创建，已存在则补全缺失的名称字段。

        :return: 统计字典 {added: N, updated: N, skipped: N, errors: N}
        """
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
        folders = self.db_manager.get_actor_folders(only_enabled=True)

        for folder in folders:
            try:
                info = ActorFolderParser.parse(folder.folder_name)
                if info is None:
                    stats["skipped"] += 1
                    self._log(LOG_LEVEL_WARNING,
                              f"文件夹名称不符合演员命名规则，已跳过: {folder.folder_name}")
                    continue

                actor = self.find_actor_by_name(info.primary.japanese_name)
                if actor is None:
                    # 不存在则创建新演员
                    new_actor = Actor(
                        japanese_name=info.primary.japanese_name,
                        chinese_name=info.primary.chinese_name,
                        initial_letter=info.expected_initial,
                        alt_japanese_name=(info.alternate.japanese_name
                                           if info.alternate else ""),
                        alt_chinese_name=(info.alternate.chinese_name
                                          if info.alternate else ""),
                    )
                    self.save_actor(new_actor)
                    stats["added"] += 1
                else:
                    # 已存在：补全缺失的名称字段
                    updates = {}
                    if not actor.chinese_name and info.primary.chinese_name:
                        updates["chinese_name"] = info.primary.chinese_name
                    if info.alternate:
                        if not actor.alt_japanese_name and info.alternate.japanese_name:
                            updates["alt_japanese_name"] = info.alternate.japanese_name
                        if not actor.alt_chinese_name and info.alternate.chinese_name:
                            updates["alt_chinese_name"] = info.alternate.chinese_name
                    if not actor.initial_letter:
                        updates["initial_letter"] = info.expected_initial
                    if updates:
                        self.update_actor(actor.actor_id, **updates)
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1

                self.db_manager.update_actor_folder_scan(folder.folder_id)
            except Exception as exc:
                stats["errors"] += 1
                self._log(LOG_LEVEL_ERROR,
                          f"解析演员文件夹失败: {folder.folder_name}",
                          details=str(exc))

        self._invalidate_cache()
        self._notify_metadata_updated(source="actor_folders", stats=stats)
        self._log(LOG_LEVEL_INFO,
                  f"演员文件夹扫描完成: 新增{stats['added']} 更新{stats['updated']} "
                  f"跳过{stats['skipped']} 错误{stats['errors']}")
        return stats

    # ------------------------------------------------------------------
    # 演员 CRUD（设计 31.4 节）
    # ------------------------------------------------------------------
    def save_actor(self, actor: Actor) -> Actor:
        """保存新演员。

        若日文名已存在则视为更新（补全字段并返回已有演员）；
        声母为空且存在中文名时自动计算。

        :param actor: 待保存的 Actor 对象
        :return: 保存后的 Actor（含 actor_id 与时间戳）
        """
        if not actor.japanese_name:
            raise ValueError("演员日文名不能为空")

        existing = self.find_actor_by_name(actor.japanese_name)
        if existing is not None and actor.chinese_name:
            # 已存在：补全缺失字段
            updates = {}
            if not existing.chinese_name:
                updates["chinese_name"] = actor.chinese_name
            if not existing.alt_japanese_name and actor.alt_japanese_name:
                updates["alt_japanese_name"] = actor.alt_japanese_name
            if not existing.alt_chinese_name and actor.alt_chinese_name:
                updates["alt_chinese_name"] = actor.alt_chinese_name
            if updates:
                self.update_actor(existing.actor_id, **updates)
            return self.get_actor_by_id(existing.actor_id)

        if not actor.initial_letter and actor.chinese_name:
            actor.initial_letter = ActorFolderParser._get_initial_letter(
                actor.chinese_name)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.db_manager.execute(
            """INSERT INTO actors
               (japanese_name, chinese_name, initial_letter,
                alt_japanese_name, alt_chinese_name,
                profile_image, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(japanese_name) DO UPDATE SET
                   chinese_name=COALESCE(NULLIF(actors.chinese_name, ''),
                                         excluded.chinese_name),
                   initial_letter=COALESCE(NULLIF(actors.initial_letter, ''),
                                           excluded.initial_letter),
                   alt_japanese_name=COALESCE(NULLIF(actors.alt_japanese_name, ''),
                                              excluded.alt_japanese_name),
                   alt_chinese_name=COALESCE(NULLIF(actors.alt_chinese_name, ''),
                                             excluded.alt_chinese_name),
                   profile_image=COALESCE(NULLIF(actors.profile_image, ''),
                                          excluded.profile_image),
                   notes=COALESCE(NULLIF(actors.notes, ''), excluded.notes),
                   updated_at=excluded.updated_at""",
            (actor.japanese_name, actor.chinese_name, actor.initial_letter,
             actor.alt_japanese_name, actor.alt_chinese_name,
             actor.profile_image, actor.notes, now_str, now_str),
        )
        self._invalidate_cache()
        self._notify_metadata_updated(action="save_actor",
                                      japanese_name=actor.japanese_name)
        self._log(LOG_LEVEL_INFO, f"保存演员: {actor.japanese_name}")
        saved = self.find_actor_by_name(actor.japanese_name)
        return saved if saved is not None else actor

    def update_actor(self, actor_id: int, **kwargs) -> bool:
        """更新演员信息。

        仅允许更新白名单字段；传入 chinese_name 且未显式传 initial_letter
        时自动重算声母。

        :param actor_id: 演员 ID
        :param kwargs: 待更新字段及值
        :return: 是否更新了记录
        """
        updates = {k: v for k, v in kwargs.items()
                   if k in _ACTOR_EDITABLE_FIELDS}
        if not updates:
            return False

        # 中文名变化时自动重算声母（未显式指定 initial_letter 的情况下）
        if "chinese_name" in updates and "initial_letter" not in updates:
            cn_name = updates.get("chinese_name") or ""
            if cn_name:
                updates["initial_letter"] = \
                    ActorFolderParser._get_initial_letter(cn_name)
            else:
                updates["initial_letter"] = ""

        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        assignments = ", ".join(f"{field}=?" for field in updates)
        params = list(updates.values()) + [actor_id]
        cursor = self.db_manager.execute(
            f"UPDATE actors SET {assignments} WHERE actor_id=?", tuple(params))

        self._invalidate_cache()
        self._notify_metadata_updated(action="update_actor", actor_id=actor_id)
        self._log(LOG_LEVEL_INFO, f"更新演员 ID={actor_id}: {list(kwargs.keys())}")
        return cursor.rowcount > 0

    def delete_actor(self, actor_id: int) -> bool:
        """删除演员（级联删除其视频关联）。

        :param actor_id: 演员 ID
        :return: 是否删除了记录
        """
        cursor = self.db_manager.execute(
            "DELETE FROM actors WHERE actor_id=?", (actor_id,))
        self._invalidate_cache()
        self._notify_metadata_updated(action="delete_actor", actor_id=actor_id)
        self._log(LOG_LEVEL_INFO, f"删除演员 ID={actor_id}")
        return cursor.rowcount > 0

    def get_actor_by_id(self, actor_id: int) -> Actor | None:
        """按 ID 查询演员（优先读缓存）。

        :param actor_id: 演员 ID
        :return: Actor 或 None
        """
        self._refresh_cache()
        with self._cache_lock:
            actor = self._actors_cache.get(actor_id)
        if actor is not None:
            return actor
        return self.db_manager.get_actor_by_id(actor_id)

    def find_actor_by_name(self, name: str) -> Actor | None:
        """根据名称精确查找演员（匹配日文名、中文名、关联/曾用名）。

        :param name: 演员名称
        :return: Actor 或 None
        """
        if not name:
            return None
        self._refresh_cache()
        with self._cache_lock:
            actor_id = self._name_index.get(name)
            if actor_id is not None:
                return self._actors_cache.get(actor_id)

        # 缓存未命中（可能由其他路径写入）：直接查库
        rows = self.db_manager.query(
            """SELECT * FROM actors
               WHERE japanese_name=? OR chinese_name=?
                  OR alt_japanese_name=? OR alt_chinese_name=?""",
            (name, name, name, name),
        )
        if rows:
            self._invalidate_cache()
            return self.db_manager._row_to_actor(rows[0])
        return None

    def search_actors(self, keyword: str) -> list:
        """按关键词模糊搜索演员（日文名/中文名/关联名）。

        :param keyword: 搜索关键词
        :return: Actor 列表（按关联视频数降序）
        """
        if not keyword:
            return self.get_all_actors()
        return self.db_manager.search_actors(keyword)

    def get_all_actors(self, sort_by: str = "japanese_name",
                       sort_order: str = "asc") -> list:
        """获取全部演员，经 JapaneseSort 排序。

        :param sort_by: 排序字段（id/japanese_name/chinese_name/
                        initial_letter/created_at）
        :param sort_order: "asc" 或 "desc"
        :return: Actor 列表
        """
        self._refresh_cache()
        with self._cache_lock:
            actors = list(self._actors_cache.values())
        return JapaneseSort.sort_actors(actors, sort_by=sort_by, order=sort_order)

    # ------------------------------------------------------------------
    # 演员与视频关联（设计 31.4 节）
    # ------------------------------------------------------------------
    def link_video_to_actor(self, relative_path: str, actor_id: int) -> None:
        """建立视频与演员的关联（幂等）。

        :param relative_path: 视频相对路径（当前设备）
        :param actor_id: 演员 ID
        """
        device_id = self.db_manager.current_device_id
        # actor_order 取当前该视频已有演员数，保证顺序追加
        rows = self.db_manager.query(
            "SELECT COUNT(*) AS c FROM video_actors "
            "WHERE device_id=? AND relative_path=?",
            (device_id, relative_path),
        )
        actor_order = rows[0]["c"] if rows else 0
        self.db_manager.execute(
            """INSERT OR IGNORE INTO video_actors
               (device_id, relative_path, actor_id, actor_order)
               VALUES (?,?,?,?)""",
            (device_id, relative_path, actor_id, actor_order),
        )
        self._recompute_video_counts()
        self._invalidate_cache()
        self._notify_metadata_updated(action="link_video",
                                      relative_path=relative_path,
                                      actor_id=actor_id)

    def get_video_actors(self, relative_path: str) -> list:
        """查询视频关联的全部演员（按 actor_order 排序）。

        :param relative_path: 视频相对路径（当前设备）
        :return: Actor 列表
        """
        rows = self.db_manager.query(
            """SELECT a.* FROM video_actors va
               JOIN actors a ON a.actor_id=va.actor_id
               WHERE va.device_id=? AND va.relative_path=?
               ORDER BY va.actor_order, va.id""",
            (self.db_manager.current_device_id, relative_path),
        )
        return [self.db_manager._row_to_actor(row) for row in rows]

    def get_actor_video_count(self, actor_id: int) -> int:
        """查询演员关联的视频数（跨设备去重计数）。

        :param actor_id: 演员 ID
        :return: 关联视频数
        """
        rows = self.db_manager.query(
            """SELECT COUNT(DISTINCT device_id || '/' || relative_path) AS c
               FROM video_actors WHERE actor_id=?""",
            (actor_id,),
        )
        return rows[0]["c"] if rows else 0

    # ------------------------------------------------------------------
    # 爬虫集成（设计 31.4 节）
    # ------------------------------------------------------------------
    def sync_actor_from_crawler(self, actor_names: list) -> list:
        """从爬虫结果同步演员。

        1. 检查每个演员名是否已存在（含曾用名）；
        2. 不存在则创建新演员（只有日文名，中文名为空）；
        3. 返回所有对应的 Actor 对象（调用方可据此用
           link_video_to_actor 建立视频关联）。

        :param actor_names: 演员名称列表
        :return: Actor 列表（与输入一一对应，顺序一致）
        """
        result = []
        for name in actor_names or []:
            name = (name or "").strip()
            if not name:
                continue
            actor = self.find_actor_by_name(name)
            if actor is None:
                actor = self.save_actor(Actor(japanese_name=name))
                self._log(LOG_LEVEL_DEBUG,
                          f"爬虫同步: 创建新演员 {name}")
            result.append(actor)
        if result:
            self._notify_metadata_updated(action="crawler_sync",
                                          count=len(result))
        return result

    def resolve_actor_name(self, name: str) -> Actor | None:
        """解析演员名称（含曾用名/别名），返回标准演员对象。

        :param name: 演员名称
        :return: Actor 或 None
        """
        return self.find_actor_by_name(name)


class ActorManageTab(tk.Frame):
    """演员管理 Tab 页（设计文档 31.7 / 31.8 节）。

    界面组成：
    - 演员文件夹区域：Listbox + 添加/移除/更新按钮
    - 演员列表：Treeview（ID / 日文名(读音A-Z) / 中文名(拼音A-Z) /
      关联名称 / 首字母 / 创建时间），点击表头切换升降序
    - 搜索框：按关键词过滤演员
    - 编辑区域：双击演员行在列表下方展开，可修改日文名/中文名/
      曾用名/备注，保存时自动重算声母
    - 键盘字母快速定位：按字母键跳转到对应声母开头的演员
    """

    # 演员列表列定义（设计 31.8 节）
    _COLUMNS = ("id", "japanese_name", "chinese_name",
                "alt_names", "initial_letter", "created_at")
    _COLUMN_HEADINGS = {
        "id": "ID",
        "japanese_name": "日文名(读音A-Z)",
        "chinese_name": "中文名(拼音A-Z)",
        "alt_names": "关联名称",
        "initial_letter": "首字母",
        "created_at": "创建时间",
    }

    def __init__(self, parent: tk.Widget, actor_manager: ActorManager,
                 event_bus):
        """初始化演员管理 Tab 页。

        :param parent: 父控件
        :param actor_manager: ActorManager 实例
        :param event_bus: EventBus 实例
        """
        super().__init__(parent)
        self.actor_manager = actor_manager
        self.event_bus = event_bus

        # 排序状态
        self.sort_column = "japanese_name"
        self.sort_order = "asc"

        # 编辑状态
        self._editing_actor_id = None

        # Listbox 条目 -> folder_id 映射
        self._folder_ids = []

        self._create_ui()
        self._load_actor_folders()
        self._load_actors()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _create_ui(self) -> None:
        """创建界面布局：文件夹区（上）、搜索条 + 演员列表（中）、编辑区（下）。"""
        # ---- 演员文件夹区域 ----
        self.folder_frame = tk.LabelFrame(self, text="演员文件夹")
        self.folder_frame.pack(fill=tk.X, padx=5, pady=5)

        folder_btn_frame = tk.Frame(self.folder_frame)
        folder_btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        self.add_folder_btn = tk.Button(folder_btn_frame, text="添加文件夹",
                                        command=self._on_add_folder)
        self.add_folder_btn.pack(fill=tk.X, pady=2)
        self.remove_folder_btn = tk.Button(folder_btn_frame, text="移除",
                                           command=self._on_remove_folder)
        self.remove_folder_btn.pack(fill=tk.X, pady=2)
        self.update_actors_btn = tk.Button(folder_btn_frame, text="更新演员",
                                           command=self._on_update_actors)
        self.update_actors_btn.pack(fill=tk.X, pady=2)

        self.folder_listbox = tk.Listbox(self.folder_frame, height=4,
                                         exportselection=False)
        self.folder_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                                 padx=5, pady=5)
        folder_scroll = tk.Scrollbar(self.folder_frame,
                                     command=self.folder_listbox.yview)
        folder_scroll.pack(side=tk.LEFT, fill=tk.Y, pady=5)
        self.folder_listbox.config(yscrollcommand=folder_scroll.set)

        # ---- 搜索条 ----
        search_frame = tk.Frame(self)
        search_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        tk.Label(search_frame, text="搜索:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(search_frame)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.search_entry.bind("<Return>", lambda e: self._on_search())
        self.search_btn = tk.Button(search_frame, text="搜索",
                                    command=self._on_search)
        self.search_btn.pack(side=tk.LEFT, padx=2)
        self.clear_search_btn = tk.Button(search_frame, text="清除",
                                          command=self._on_clear_search)
        self.clear_search_btn.pack(side=tk.LEFT, padx=2)

        # ---- 演员列表 ----
        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self.actor_tree = ttk.Treeview(tree_frame, columns=self._COLUMNS,
                                       show="headings", selectmode="browse")
        for col in self._COLUMNS:
            self.actor_tree.heading(
                col, text=self._COLUMN_HEADINGS[col],
                command=lambda c=col: self._on_sort(c))
            width = 60 if col == "id" else (140 if col == "created_at" else 160)
            self.actor_tree.column(col, width=width, anchor=tk.W)
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                    command=self.actor_tree.yview)
        self.actor_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.actor_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.actor_tree.bind("<Double-1>", self._on_actor_double_click)
        self.actor_tree.bind("<<TreeviewSelect>>", self._on_actor_select)
        # 键盘字母快速定位（声母跳转）
        self.actor_tree.bind("<KeyPress>", self._on_key_press)

        # ---- 编辑区域（初始隐藏，双击演员行后展开） ----
        self.edit_frame = tk.LabelFrame(self, text="编辑演员")
        self.jp_name_var = tk.StringVar()
        self.cn_name_var = tk.StringVar()
        self.alt_jp_name_var = tk.StringVar()
        self.alt_cn_name_var = tk.StringVar()
        self.notes_var = tk.StringVar()

        row1 = tk.Frame(self.edit_frame)
        row1.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row1, text="日文名:").pack(side=tk.LEFT)
        tk.Entry(row1, textvariable=self.jp_name_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Label(row1, text="中文名:").pack(side=tk.LEFT)
        tk.Entry(row1, textvariable=self.cn_name_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        row2 = tk.Frame(self.edit_frame)
        row2.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row2, text="曾用日文名:").pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.alt_jp_name_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Label(row2, text="曾用中文名:").pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.alt_cn_name_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        row3 = tk.Frame(self.edit_frame)
        row3.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row3, text="备注:").pack(side=tk.LEFT)
        tk.Entry(row3, textvariable=self.notes_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        row4 = tk.Frame(self.edit_frame)
        row4.pack(fill=tk.X, padx=5, pady=5)
        self.save_btn = tk.Button(row4, text="保存", command=self._on_save_edit)
        self.save_btn.pack(side=tk.RIGHT, padx=5)
        self.cancel_btn = tk.Button(row4, text="取消",
                                    command=self._on_cancel_edit)
        self.cancel_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # 演员文件夹区域
    # ------------------------------------------------------------------
    def _load_actor_folders(self) -> None:
        """加载演员文件夹配置到 Listbox。"""
        self.folder_listbox.delete(0, tk.END)
        self._folder_ids = []
        for cfg in self.actor_manager.get_actor_folders():
            label = cfg.folder_name or cfg.folder_path
            if not cfg.is_enabled:
                label += "（已停用）"
            self.folder_listbox.insert(tk.END, label)
            self._folder_ids.append(cfg.folder_id)

    def _on_add_folder(self) -> None:
        """添加演员文件夹：弹出目录选择对话框。"""
        folder_path = filedialog.askdirectory(parent=self, title="选择演员文件夹")
        if not folder_path:
            return
        if not os.path.isdir(folder_path):
            messagebox.showwarning("提示", "所选路径不是有效文件夹", parent=self)
            return
        folder_name = os.path.basename(folder_path.rstrip("\\/"))
        self.actor_manager.add_actor_folder(folder_path, folder_name)
        self._load_actor_folders()

    def _on_remove_folder(self) -> None:
        """移除选中的演员文件夹配置。"""
        selection = self.folder_listbox.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表中选择要移除的文件夹", parent=self)
            return
        index = selection[0]
        folder_id = self._folder_ids[index]
        if self.actor_manager.remove_actor_folder(folder_id):
            self._load_actor_folders()

    def _on_update_actors(self) -> None:
        """扫描全部已配置演员文件夹并更新演员库。"""
        stats = self.actor_manager.update_actors_from_folders()
        messagebox.showinfo(
            "更新完成",
            f"演员更新完成：\n新增 {stats['added']} 个\n更新 {stats['updated']} 个\n"
            f"跳过 {stats['skipped']} 个\n错误 {stats['errors']} 个",
            parent=self,
        )
        self._load_actors()

    # ------------------------------------------------------------------
    # 演员列表
    # ------------------------------------------------------------------
    def _load_actors(self, filter_keyword: str = None) -> None:
        """加载演员列表到 Treeview。

        :param filter_keyword: 过滤关键词（为空则显示全部）
        """
        for item in self.actor_tree.get_children():
            self.actor_tree.delete(item)

        if filter_keyword:
            actors = self.actor_manager.search_actors(filter_keyword)
            actors = JapaneseSort.sort_actors(actors, sort_by=self.sort_column,
                                              order=self.sort_order)
        else:
            actors = self.actor_manager.get_all_actors(
                sort_by=self.sort_column, sort_order=self.sort_order)

        for actor in actors:
            alt_parts = []
            if actor.alt_japanese_name:
                if actor.alt_chinese_name:
                    alt_parts.append(
                        f"{actor.alt_japanese_name}({actor.alt_chinese_name})")
                else:
                    alt_parts.append(actor.alt_japanese_name)
            elif actor.alt_chinese_name:
                alt_parts.append(actor.alt_chinese_name)
            created = (actor.created_at.strftime("%Y-%m-%d %H:%M:%S")
                       if actor.created_at else "")
            self.actor_tree.insert("", tk.END, iid=str(actor.actor_id), values=(
                actor.actor_id if actor.actor_id is not None else "",
                actor.japanese_name,
                actor.chinese_name,
                " / ".join(alt_parts),
                actor.initial_letter,
                created,
            ))

    def _on_sort(self, column: str) -> None:
        """点击表头排序：同列切换升降序，异列重置为升序。

        :param column: 点击的列名
        """
        if column == "alt_names":
            # 关联名称列不参与排序
            return
        if self.sort_column == column:
            self.sort_order = "desc" if self.sort_order == "asc" else "asc"
        else:
            self.sort_column = column
            self.sort_order = "asc"
        self._load_actors(self._current_keyword())

    def _current_keyword(self) -> str:
        """获取当前搜索框关键词（去除首尾空白）。"""
        return self.search_entry.get().strip() or None

    def _on_search(self) -> None:
        """按搜索框关键词过滤演员列表。"""
        self._load_actors(self._current_keyword())

    def _on_clear_search(self) -> None:
        """清除搜索条件，显示全部演员。"""
        self.search_entry.delete(0, tk.END)
        self._load_actors()

    def _on_actor_select(self, event) -> None:
        """选中演员行：发布 EVENT_ACTOR_SELECTED 事件。"""
        selection = self.actor_tree.selection()
        if not selection:
            return
        try:
            actor_id = int(selection[0])
        except (ValueError, IndexError):
            return
        actor = self.actor_manager.get_actor_by_id(actor_id)
        if actor is not None and self.event_bus is not None:
            try:
                self.event_bus.publish(events.EVENT_ACTOR_SELECTED,
                                       actor=actor, actor_id=actor_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 编辑区域
    # ------------------------------------------------------------------
    def _on_actor_double_click(self, event) -> None:
        """双击演员行：在列表下方展开编辑区域并填充当前值。

        :param event: Treeview 双击事件
        """
        region = self.actor_tree.identify_region(event.x, event.y)
        if region in ("heading", "separator"):
            # 点击表头/分隔条不触发编辑
            return
        selection = self.actor_tree.selection()
        if not selection:
            return
        try:
            actor_id = int(selection[0])
        except (ValueError, IndexError):
            return
        actor = self.actor_manager.get_actor_by_id(actor_id)
        if actor is None:
            return

        self._editing_actor_id = actor_id
        self.jp_name_var.set(actor.japanese_name)
        self.cn_name_var.set(actor.chinese_name)
        self.alt_jp_name_var.set(actor.alt_japanese_name)
        self.alt_cn_name_var.set(actor.alt_chinese_name)
        self.notes_var.set(actor.notes)
        if not self.edit_frame.winfo_ismapped():
            self.edit_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

    def _on_save_edit(self) -> None:
        """保存编辑：更新演员信息并按中文名重算声母。"""
        if self._editing_actor_id is None:
            return
        jp_name = self.jp_name_var.get().strip()
        if not jp_name:
            messagebox.showwarning("提示", "日文名不能为空", parent=self)
            return
        updates = {
            "japanese_name": jp_name,
            "chinese_name": self.cn_name_var.get().strip(),
            "alt_japanese_name": self.alt_jp_name_var.get().strip(),
            "alt_chinese_name": self.alt_cn_name_var.get().strip(),
            "notes": self.notes_var.get().strip(),
        }
        # chinese_name 已包含在 updates 中，update_actor 会自动重算声母
        self.actor_manager.update_actor(self._editing_actor_id, **updates)
        self._editing_actor_id = None
        self.edit_frame.pack_forget()
        self._load_actors(self._current_keyword())

    def _on_cancel_edit(self) -> None:
        """取消编辑：隐藏编辑区域。"""
        self._editing_actor_id = None
        self.edit_frame.pack_forget()

    # ------------------------------------------------------------------
    # 键盘快速定位
    # ------------------------------------------------------------------
    def _on_key_press(self, event) -> None:
        """键盘字母快速定位：跳到对应声母开头的演员。

        依次查找当前列表中第一个首字母匹配的演员并选中定位；
        若当前选中项已匹配，则跳到下一个匹配项（循环）。

        :param event: 键盘事件
        """
        char = (getattr(event, "char", "") or "").lower()
        if len(char) != 1 or not ("a" <= char <= "z"):
            return

        items = self.actor_tree.get_children()
        if not items:
            return

        # 收集首字母匹配项的索引
        matches = []
        for index, item in enumerate(items):
            values = self.actor_tree.item(item, "values")
            # 列顺序: ID / 日文名 / 中文名 / 关联名称 / 首字母 / 创建时间
            initial = (values[4] if len(values) > 4 else "") or ""
            if str(initial).lower() == char:
                matches.append(index)
        if not matches:
            return

        current = self.actor_tree.selection()
        target = matches[0]
        if current:
            try:
                cur_index = items.index(current[0])
            except ValueError:
                cur_index = -1
            # 从当前选中项之后找下一个匹配（循环，无更大匹配时回到首个）
            for index in matches:
                if index > cur_index:
                    target = index
                    break
        target_item = items[target]
        self.actor_tree.selection_set(target_item)
        self.actor_tree.see(target_item)
