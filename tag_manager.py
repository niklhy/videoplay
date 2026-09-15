# -*- coding: utf-8 -*-
"""标签管理模块（对应设计文档第21章）。

包含两个类：
- TagManager：标签CRUD、文件-标签关联、按标签筛选文件，负责与数据库交互；
- TagPanel：顶部标签区UI组件，展示所有标签按钮，支持点击筛选、右键管理。

数据类 Tag 从契约文件 models.py 导入；
事件常量从契约文件 events.py 导入；
全局常量从契约文件 constants.py 导入。
仅使用标准库 + tkinter。
"""
import sqlite3
import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog

import constants
import events
from models import Tag


class TagManager:
    """标签管理器：负责标签的增删改查及文件-标签关联。

    属性:
        db_manager: DatabaseManager  数据库管理器实例（提供 current_device_id 与 SQL 执行能力）
        event_bus: EventBus          事件总线实例
        _tags_cache: dict[int, Tag]  标签缓存（tag_id -> Tag），变更时刷新
    """

    def __init__(self, db_manager, event_bus):
        """初始化标签管理器。

        参数:
            db_manager: 数据库管理器实例（需有 current_device_id；SQL 经其提供的方法执行）
            event_bus:  事件总线实例
        """
        self.db_manager = db_manager
        self.event_bus = event_bus
        self._tags_cache = {}
        self._refresh_cache()

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------
    def _execute(self, sql, params=()):
        """执行写SQL，经 db_manager 提供的方法执行（优先专用/通用 execute）。"""
        execute_fn = getattr(self.db_manager, 'execute', None)
        if callable(execute_fn):
            return execute_fn(sql, params)
        cursor = self.db_manager.connection.execute(sql, params)
        self.db_manager.connection.commit()
        return cursor

    def _query(self, sql, params=()):
        """执行读SQL并返回全部行，经 db_manager 提供的方法执行（优先专用/通用 query）。"""
        query_fn = getattr(self.db_manager, 'query', None)
        if callable(query_fn):
            return query_fn(sql, params)
        return self.db_manager.connection.execute(sql, params).fetchall()

    @staticmethod
    def _row_get(row, index, default=None):
        """按索引安全地取一行中的列值（兼容 tuple / sqlite3.Row）。"""
        try:
            value = row[index]
        except (IndexError, KeyError, TypeError):
            return default
        return default if value is None else value

    def _refresh_cache(self):
        """从数据库重新加载当前设备的全部标签（含文件计数）到缓存。"""
        device_id = self.db_manager.current_device_id
        rows = self._query(
            "SELECT t.tag_id, t.device_id, t.tag_name, t.tag_color, t.created_at,"
            "       COUNT(ft.id) AS file_count"
            " FROM tags t"
            " LEFT JOIN file_tags ft ON ft.tag_id = t.tag_id AND ft.device_id = t.device_id"
            " WHERE t.device_id = ?"
            " GROUP BY t.tag_id"
            " ORDER BY t.tag_id",
            (device_id,),
        )
        cache = {}
        for row in rows:
            tag = Tag(
                tag_id=int(self._row_get(row, 0, 0)),
                device_id=str(self._row_get(row, 1, "")),
                tag_name=str(self._row_get(row, 2, "")),
                tag_color=str(self._row_get(row, 3, "#2196F3")),
                file_count=int(self._row_get(row, 5, 0) or 0),
                created_at=self._row_get(row, 4),
            )
            cache[tag.tag_id] = tag
        self._tags_cache = cache

    def _publish_tag_changed(self, action, tag_id, tag_name=""):
        """发布标签变更事件并刷新缓存。"""
        self._refresh_cache()
        self.event_bus.publish(
            events.EVENT_TAG_CHANGED,
            action=action,
            tag_id=tag_id,
            tag_name=tag_name,
        )

    def _publish_file_tags_changed(self, action, relative_path, tag_id):
        """发布文件标签变更事件并刷新缓存（文件计数可能变化）。"""
        self._refresh_cache()
        self.event_bus.publish(
            events.EVENT_FILE_TAGS_CHANGED,
            action=action,
            relative_path=relative_path,
            tag_id=tag_id,
        )

    # ------------------------------------------------------------------
    # 标签CRUD（设计文档21.3节）
    # ------------------------------------------------------------------
    def create_tag(self, tag_name, tag_color='#2196F3'):
        """创建新标签；同名标签已存在时直接返回已有标签。

        参数:
            tag_name:  标签名称（非空字符串）
            tag_color: 标签颜色（十六进制，默认 '#2196F3'）

        返回:
            Tag: 新建（或已存在的）标签对象；名称为空时返回 None
        """
        tag_name = (tag_name or "").strip()
        if not tag_name:
            return None
        device_id = self.db_manager.current_device_id
        # 先查缓存，避免依赖 UNIQUE 冲突分支
        for tag in self._tags_cache.values():
            if tag.tag_name == tag_name:
                return tag
        cursor = self._execute(
            "INSERT OR IGNORE INTO tags (device_id, tag_name, tag_color)"
            " VALUES (?, ?, ?)",
            (device_id, tag_name, tag_color),
        )
        tag_id = getattr(cursor, 'lastrowid', None) or 0
        self._publish_tag_changed("created", tag_id, tag_name)
        return self._tags_cache.get(tag_id)

    def delete_tag(self, tag_id):
        """删除标签及其全部文件关联。

        参数:
            tag_id: 标签ID

        返回:
            bool: 删除成功（存在该标签）返回 True，否则 False
        """
        device_id = self.db_manager.current_device_id
        cursor = self._execute(
            "DELETE FROM file_tags WHERE tag_id = ? AND device_id = ?",
            (tag_id, device_id),
        )
        cursor = self._execute(
            "DELETE FROM tags WHERE tag_id = ? AND device_id = ?",
            (tag_id, device_id),
        )
        if getattr(cursor, 'rowcount', 0) and cursor.rowcount > 0:
            self._publish_tag_changed("deleted", tag_id)
            return True
        # 即使标签不存在也刷新一次缓存，保证状态一致
        self._refresh_cache()
        return False

    def rename_tag(self, tag_id, new_name):
        """重命名标签。

        参数:
            tag_id:   标签ID
            new_name: 新名称（非空字符串）

        返回:
            bool: 重命名成功返回 True；名称为空、标签不存在或与其他标签重名返回 False
        """
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        device_id = self.db_manager.current_device_id
        try:
            cursor = self._execute(
                "UPDATE tags SET tag_name = ? WHERE tag_id = ? AND device_id = ?",
                (new_name, tag_id, device_id),
            )
        except sqlite3.IntegrityError:
            # 同设备下标签名唯一
            return False
        if getattr(cursor, 'rowcount', 0) and cursor.rowcount > 0:
            self._publish_tag_changed("renamed", tag_id, new_name)
            return True
        return False

    def set_tag_color(self, tag_id, tag_color):
        """修改标签颜色（TagPanel 右键菜单使用）。

        参数:
            tag_id:    标签ID
            tag_color: 新颜色（十六进制）

        返回:
            bool: 修改成功返回 True，否则 False
        """
        device_id = self.db_manager.current_device_id
        cursor = self._execute(
            "UPDATE tags SET tag_color = ? WHERE tag_id = ? AND device_id = ?",
            (tag_color, tag_id, device_id),
        )
        if getattr(cursor, 'rowcount', 0) and cursor.rowcount > 0:
            self._publish_tag_changed("color_changed", tag_id)
            return True
        return False

    def get_all_tags(self):
        """获取当前设备的全部标签（含文件计数）。

        返回:
            list[Tag]: 标签列表，按创建顺序排列
        """
        self._refresh_cache()
        return list(self._tags_cache.values())

    def get_tag_count(self, tag_id):
        """获取指定标签下的文件数量。

        参数:
            tag_id: 标签ID

        返回:
            int: 文件数量
        """
        device_id = self.db_manager.current_device_id
        rows = self._query(
            "SELECT COUNT(*) FROM file_tags WHERE tag_id = ? AND device_id = ?",
            (tag_id, device_id),
        )
        if not rows:
            return 0
        return int(self._row_get(rows[0], 0, 0) or 0)

    # ------------------------------------------------------------------
    # 文件-标签关联（设计文档21.3节）
    # ------------------------------------------------------------------
    def add_tag_to_file(self, relative_path, tag_id):
        """为文件添加标签（幂等，重复添加不报错）。

        参数:
            relative_path: 文件相对路径
            tag_id:        标签ID

        返回:
            bool: 新增成功返回 True；已存在或标签无效返回 False
        """
        device_id = self.db_manager.current_device_id
        if tag_id not in self._tags_cache:
            return False
        cursor = self._execute(
            "INSERT OR IGNORE INTO file_tags (device_id, relative_path, tag_id)"
            " VALUES (?, ?, ?)",
            (device_id, relative_path, tag_id),
        )
        if getattr(cursor, 'rowcount', 0) and cursor.rowcount > 0:
            self._publish_file_tags_changed("added", relative_path, tag_id)
            return True
        return False

    def remove_tag_from_file(self, relative_path, tag_id):
        """移除文件的指定标签。

        参数:
            relative_path: 文件相对路径
            tag_id:        标签ID

        返回:
            bool: 移除成功返回 True；关联不存在返回 False
        """
        device_id = self.db_manager.current_device_id
        cursor = self._execute(
            "DELETE FROM file_tags WHERE device_id = ? AND relative_path = ? AND tag_id = ?",
            (device_id, relative_path, tag_id),
        )
        if getattr(cursor, 'rowcount', 0) and cursor.rowcount > 0:
            self._publish_file_tags_changed("removed", relative_path, tag_id)
            return True
        return False

    def get_file_tags(self, relative_path):
        """获取文件已关联的全部标签。

        参数:
            relative_path: 文件相对路径

        返回:
            list[Tag]: 标签列表
        """
        device_id = self.db_manager.current_device_id
        rows = self._query(
            "SELECT t.tag_id, t.device_id, t.tag_name, t.tag_color, t.created_at"
            " FROM file_tags ft"
            " JOIN tags t ON t.tag_id = ft.tag_id AND t.device_id = ft.device_id"
            " WHERE ft.device_id = ? AND ft.relative_path = ?"
            " ORDER BY t.tag_id",
            (device_id, relative_path),
        )
        return [
            Tag(
                tag_id=int(self._row_get(row, 0, 0)),
                device_id=str(self._row_get(row, 1, "")),
                tag_name=str(self._row_get(row, 2, "")),
                tag_color=str(self._row_get(row, 3, "#2196F3")),
                created_at=self._row_get(row, 4),
            )
            for row in rows
        ]

    def get_files_by_tag(self, tag_id):
        """获取指定标签下的全部文件相对路径。

        参数:
            tag_id: 标签ID

        返回:
            list[str]: 文件相对路径列表
        """
        device_id = self.db_manager.current_device_id
        rows = self._query(
            "SELECT relative_path FROM file_tags"
            " WHERE tag_id = ? AND device_id = ?"
            " ORDER BY relative_path",
            (tag_id, device_id),
        )
        return [str(self._row_get(row, 0, "")) for row in rows]

    def search_files_by_tags(self, tag_ids, match_mode="any"):
        """按多个标签筛选文件。

        参数:
            tag_ids:    标签ID列表
            match_mode: "any" 匹配任一标签（并集）；"all" 匹配全部标签（交集）

        返回:
            list[str]: 符合条件的文件相对路径列表（去重）
        """
        device_id = self.db_manager.current_device_id
        tag_ids = [int(t) for t in tag_ids if t is not None]
        if not tag_ids:
            return []
        placeholders = ", ".join("?" for _ in tag_ids)
        if match_mode == "all":
            sql = (
                "SELECT relative_path FROM file_tags"
                " WHERE device_id = ? AND tag_id IN (" + placeholders + ")"
                " GROUP BY relative_path"
                " HAVING COUNT(DISTINCT tag_id) = ?"
                " ORDER BY relative_path"
            )
            rows = self._query(sql, (device_id, *tag_ids, len(tag_ids)))
        else:
            sql = (
                "SELECT DISTINCT relative_path FROM file_tags"
                " WHERE device_id = ? AND tag_id IN (" + placeholders + ")"
                " ORDER BY relative_path"
            )
            rows = self._query(sql, (device_id, *tag_ids))
        return [str(self._row_get(row, 0, "")) for row in rows]


class TagPanel(tk.Frame):
    """顶部标签区UI组件（设计文档21.5节）。

    每个标签显示为一个按钮（"名称(数量)"，背景为标签颜色）；
    左键点击选中标签并通知图库过滤；右键弹出管理菜单；
    "+"按钮新建标签；"全部"按钮显示所有文件。

    属性:
        tag_manager: TagManager          标签管理器
        event_bus: EventBus              事件总线
        tag_container: tk.Frame          标签按钮容器
        tag_buttons: dict[int, tk.Button] 标签ID -> 按钮
        add_tag_btn: tk.Button           新建标签按钮
        edit_btn: tk.Button              标签管理按钮
    """

    def __init__(self, parent, tag_manager, event_bus):
        """初始化标签面板。

        参数:
            parent:      父控件
            tag_manager: TagManager 实例
            event_bus:   EventBus 实例
        """
        self.tag_manager = tag_manager
        self.event_bus = event_bus
        self.tag_container = None
        self.tag_buttons = {}
        self.add_tag_btn = None
        self.edit_btn = None
        self._selected_tag_id = None
        super().__init__(parent)
        self._create_ui()
        self.refresh_tags()
        # 订阅标签变更事件，保持面板与数据同步
        self.event_bus.subscribe(events.EVENT_TAG_CHANGED, self._on_tag_changed_event)
        self.event_bus.subscribe(events.EVENT_FILE_TAGS_CHANGED, self._on_file_tags_changed_event)

    def _create_ui(self):
        """创建面板UI：全部按钮 + 标签容器 + 新建/管理按钮。"""
        # "全部"按钮：清除标签筛选
        self.all_btn = tk.Button(
            self, text="全部", relief=tk.RAISED, padx=8,
            command=self.show_all_files,
        )
        self.all_btn.pack(side=tk.LEFT, padx=(2, 4), pady=2)

        # 标签按钮容器（横向排列，可随窗口扩展）
        self.tag_container = tk.Frame(self)
        self.tag_container.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)

        # 新建标签按钮
        self.add_tag_btn = tk.Button(
            self, text="＋", width=3,
            command=self._on_add_tag,
        )
        self.add_tag_btn.pack(side=tk.LEFT, padx=(4, 2), pady=2)

        # 标签管理按钮：弹出管理菜单（重命名/删除/改色/新建）
        self.edit_btn = tk.Button(
            self, text="管理", padx=6,
            command=self._on_edit_click,
        )
        self.edit_btn.pack(side=tk.LEFT, padx=2, pady=2)

    def _clear_tag_buttons(self):
        """销毁容器中的全部标签按钮并清空映射。"""
        for btn in self.tag_buttons.values():
            btn.destroy()
        self.tag_buttons = {}

    def _build_tag_button(self, tag):
        """为单个标签创建按钮并登记到映射。

        参数:
            tag: Tag 对象
        """
        btn = tk.Button(
            self.tag_container,
            text=f"{tag.tag_name}({tag.file_count})",
            bg=tag.tag_color,
            activebackground=tag.tag_color,
            relief=tk.RAISED,
            padx=8,
            command=lambda tid=tag.tag_id: self._on_tag_click(tid),
        )
        btn.bind("<Button-3>", lambda event, tid=tag.tag_id: self._on_tag_right_click(tid, event))
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.tag_buttons[tag.tag_id] = btn

    def _update_selection_style(self):
        """根据当前选中标签更新按钮的选中样式。"""
        for tag_id, btn in self.tag_buttons.items():
            if tag_id == self._selected_tag_id:
                btn.config(relief=tk.SUNKEN, highlightthickness=2,
                           highlightbackground=constants.THUMBNAIL_BORDER_SELECTED)
            else:
                btn.config(relief=tk.RAISED, highlightthickness=0)
        if self._selected_tag_id is None and hasattr(self, 'all_btn'):
            self.all_btn.config(relief=tk.SUNKEN)
        elif hasattr(self, 'all_btn'):
            self.all_btn.config(relief=tk.RAISED)

    def refresh_tags(self):
        """刷新标签按钮列表（从 TagManager 重新读取）。"""
        self._clear_tag_buttons()
        for tag in self.tag_manager.get_all_tags():
            self._build_tag_button(tag)
        # 选中标签可能已被删除
        if self._selected_tag_id not in self.tag_buttons:
            self._selected_tag_id = None
        self._update_selection_style()

    def _on_tag_changed_event(self, **kwargs):
        """标签变更事件回调：刷新按钮列表。"""
        self.refresh_tags()

    def _on_file_tags_changed_event(self, **kwargs):
        """文件标签变更事件回调：刷新计数显示。"""
        self.refresh_tags()

    def _on_tag_click(self, tag_id):
        """左键点击标签：选中该标签并发布图库过滤事件。

        参数:
            tag_id: 被点击的标签ID
        """
        self._selected_tag_id = tag_id
        self._update_selection_style()
        file_list = self.tag_manager.search_files_by_tags([tag_id], match_mode="any")
        self.event_bus.publish(
            events.EVENT_GALLERY_LOAD_REQUEST,
            tag_id=tag_id,
            tag_ids=[tag_id],
            match_mode="any",
            file_list=file_list,
        )

    def _on_tag_right_click(self, tag_id, event):
        """右键点击标签：弹出管理菜单（重命名/删除/修改颜色）。

        参数:
            tag_id: 被点击的标签ID
            event:  鼠标事件对象（提供弹菜单位置）
        """
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="重命名", command=lambda: self._rename_tag(tag_id))
        menu.add_command(label="修改颜色", command=lambda: self._change_tag_color(tag_id))
        menu.add_separator()
        menu.add_command(label="删除标签", command=lambda: self._delete_tag(tag_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_add_tag(self):
        """点击"＋"按钮：弹出输入对话框新建标签。"""
        name = simpledialog.askstring("新建标签", "请输入标签名称：", parent=self.winfo_toplevel())
        if not name or not name.strip():
            return
        tag = self.tag_manager.create_tag(name.strip())
        if tag is None:
            messagebox.showwarning("新建标签", "标签创建失败。", parent=self.winfo_toplevel())

    def _rename_tag(self, tag_id):
        """重命名指定标签。

        参数:
            tag_id: 标签ID
        """
        old_tag = self.tag_manager._tags_cache.get(tag_id)
        old_name = old_tag.tag_name if old_tag else ""
        new_name = simpledialog.askstring(
            "重命名标签", "请输入新名称：", initialvalue=old_name,
            parent=self.winfo_toplevel(),
        )
        if not new_name or not new_name.strip():
            return
        if not self.tag_manager.rename_tag(tag_id, new_name.strip()):
            messagebox.showwarning(
                "重命名标签",
                "重命名失败：名称为空、标签不存在或与其他标签重名。",
                parent=self.winfo_toplevel(),
            )

    def _change_tag_color(self, tag_id):
        """为指定标签选择新颜色。

        参数:
            tag_id: 标签ID
        """
        tag = self.tag_manager._tags_cache.get(tag_id)
        initial = tag.tag_color if tag else "#2196F3"
        rgb, hex_color = colorchooser.askcolor(
            initialcolor=initial, title="选择标签颜色", parent=self.winfo_toplevel(),
        )
        if hex_color:
            self.tag_manager.set_tag_color(tag_id, hex_color)

    def _delete_tag(self, tag_id):
        """删除指定标签（先确认）。

        参数:
            tag_id: 标签ID
        """
        tag = self.tag_manager._tags_cache.get(tag_id)
        name = tag.tag_name if tag else str(tag_id)
        if not messagebox.askyesno("删除标签", f"确定删除标签“{name}”及其全部文件关联吗？",
                                   parent=self.winfo_toplevel()):
            return
        self.tag_manager.delete_tag(tag_id)
        if self._selected_tag_id == tag_id:
            self._selected_tag_id = None

    def _on_edit_click(self):
        """点击"管理"按钮：弹出管理菜单（作用于全部标签）。"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="新建标签", command=self._on_add_tag)
        menu.add_command(label="显示全部文件", command=self.show_all_files)
        menu.add_separator()
        # 为每个标签提供子菜单入口
        for tag in self.tag_manager.get_all_tags():
            submenu = tk.Menu(menu, tearoff=0)
            submenu.add_command(label="重命名", command=lambda tid=tag.tag_id: self._rename_tag(tid))
            submenu.add_command(label="修改颜色", command=lambda tid=tag.tag_id: self._change_tag_color(tid))
            submenu.add_command(label="删除", command=lambda tid=tag.tag_id: self._delete_tag(tid))
            menu.add_cascade(label=f"{tag.tag_name}({tag.file_count})", menu=submenu)
        try:
            menu.tk_popup(self.edit_btn.winfo_rootx(), self.edit_btn.winfo_rooty() + self.edit_btn.winfo_height())
        finally:
            menu.grab_release()

    def show_all_files(self):
        """清除标签筛选，显示全部文件。"""
        self._selected_tag_id = None
        self._update_selection_style()
        self.event_bus.publish(
            events.EVENT_GALLERY_LOAD_REQUEST,
            tag_id=None,
            tag_ids=[],
            match_mode="none",
            file_list=None,
        )

    def get_selected_tag(self):
        """获取当前选中的标签ID。

        返回:
            int | None: 选中标签ID；未选中（显示全部）时返回 None
        """
        return self._selected_tag_id
