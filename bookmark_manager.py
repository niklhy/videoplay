# -*- coding: utf-8 -*-
"""书签管理模块（对应设计文档第二十六章）。

包含三个部分：
- ``Bookmark``：书签数据类（设计 26.3 节），定义书签字段并检查路径可达性；
- ``BookmarkManager``：书签业务管理器（设计 26.2 节），负责书签的增删改查、
  设备重命名与全部书签路径同步检查，持久化在 config_manager 的
  ``data["devices"][device_id]["bookmarks"]`` 结构中；
- ``BookmarkTree``：两级书签树组件（设计 26.1 节），设备节点 -> 书签名节点，
  支持双击跳转、右键菜单与当前设备置顶；
- ``AddBookmarkDialog``：添加书签对话框，输入书签名、路径并选择所属根目录。

仅使用标准库与 tkinter。
"""
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk

from events import EVENT_FOLDER_SELECTED


# ---------------------------------------------------------------------------
# 数据类：Bookmark（设计文档 26.3 节）
# ---------------------------------------------------------------------------
@dataclass
class Bookmark:
    """书签数据类。

    属性:
        bookmark_id: 书签唯一标识（uuid 短码，如 "bm_a1b2c3d4"）
        device_id:   所属设备ID
        name:        书签名（显示名称）
        path:        书签对应的文件夹绝对路径
        root_id:     所属根目录ID（可为 None，表示不关联根目录）
        created_at:  创建时间（datetime，可能为 None）
    """
    bookmark_id: str
    device_id: str
    name: str
    path: str
    root_id: str = None
    created_at: datetime = None

    def exists(self) -> bool:
        """检查书签路径当前是否存在（可达）。

        返回:
            路径存在且为目录时返回 True，否则返回 False。
        """
        try:
            return bool(self.path) and os.path.isdir(self.path)
        except OSError:
            return False


# ---------------------------------------------------------------------------
# 书签管理器（设计文档 26.2 节）
# ---------------------------------------------------------------------------
class BookmarkManager:
    """书签管理器：书签的增删改查、设备重命名与同步检查。

    书签持久化结构（config.json）::

        devices[device_id]["bookmarks"] = [
            {"bookmark_id": "bm_001", "name": "群晖918 Other",
             "path": "\\\\NAS-SERVER\\Share\\Other", "root_id": "D2",
             "created_at": "2026-01-15T10:00:00"},
            ...
        ]

    所有写操作完成后都会调用 ``config_manager.save()`` 落盘。
    """

    def __init__(self, config_manager, event_bus):
        """初始化书签管理器。

        参数:
            config_manager: 配置管理器实例（提供 data / save() /
                            get_current_device_id() / get_device_roots() 等接口）
            event_bus:      事件总线实例（预留，供后续书签变更事件使用）
        """
        self.config_manager = config_manager
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------
    def _get_device_entry(self, device_id: str, create: bool = False) -> dict:
        """获取设备的配置条目。

        参数:
            device_id: 设备ID
            create:    条目不存在时是否创建（含 "bookmarks" 空列表）

        返回:
            设备配置字典；不存在且 create=False 时返回空字典。
        """
        devices = self.config_manager.data.get("devices", {})
        entry = devices.get(device_id)
        if entry is None:
            if not create:
                return {}
            devices[device_id] = entry = {}
        if not isinstance(entry, dict):
            return {}
        return entry

    def _get_raw_bookmarks(self, device_id: str) -> list:
        """读取设备的原始书签字典列表（不做解析）。"""
        entry = self._get_device_entry(device_id)
        bookmarks = entry.get("bookmarks", [])
        return list(bookmarks) if isinstance(bookmarks, list) else []

    @staticmethod
    def _raw_to_bookmark(raw: dict, device_id: str) -> Bookmark:
        """将持久化的字典记录转换为 Bookmark 对象。"""
        created_at = raw.get("created_at")
        if isinstance(created_at, str) and created_at:
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None
        elif not isinstance(created_at, datetime):
            created_at = None
        return Bookmark(
            bookmark_id=str(raw.get("bookmark_id", "")),
            device_id=device_id,
            name=str(raw.get("name", "")),
            path=str(raw.get("path", "")),
            root_id=raw.get("root_id") or None,
            created_at=created_at,
        )

    @staticmethod
    def _bookmark_to_raw(bookmark: Bookmark) -> dict:
        """将 Bookmark 对象转换为可 JSON 序列化的字典记录。"""
        created_at = bookmark.created_at
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat(timespec="seconds")
        else:
            created_at = ""
        return {
            "bookmark_id": bookmark.bookmark_id,
            "name": bookmark.name,
            "path": bookmark.path,
            "root_id": bookmark.root_id or "",
            "created_at": created_at,
        }

    def _save_bookmarks(self, device_id: str, bookmarks: list) -> None:
        """写回设备书签列表并保存配置。"""
        entry = self._get_device_entry(device_id, create=True)
        entry["bookmarks"] = bookmarks
        self.config_manager.save()

    # ------------------------------------------------------------------
    # 公开接口（对应设计文档 26.2 节）
    # ------------------------------------------------------------------
    def get_device_bookmarks(self, device_id: str) -> list:
        """获取指定设备的全部书签。

        参数:
            device_id: 设备ID

        返回:
            list[Bookmark]：书签对象列表（按配置中的存储顺序）。
        """
        return [self._raw_to_bookmark(raw, device_id)
                for raw in self._get_raw_bookmarks(device_id)]

    def add_bookmark(self, device_id: str, name: str, path: str,
                     root_id: str = None) -> Bookmark:
        """为指定设备添加书签。

        参数:
            device_id:  设备ID
            name:       书签名
            path:       书签对应的文件夹绝对路径
            root_id:    所属根目录ID（可选）

        返回:
            新创建的 Bookmark 对象（bookmark_id 为 uuid 短码）。
        """
        bookmark = Bookmark(
            bookmark_id="bm_" + uuid.uuid4().hex[:8],
            device_id=device_id,
            name=name.strip(),
            path=path.strip(),
            root_id=root_id or None,
            created_at=datetime.now(),
        )
        raws = self._get_raw_bookmarks(device_id)
        raws.append(self._bookmark_to_raw(bookmark))
        self._save_bookmarks(device_id, raws)
        return bookmark

    def remove_bookmark(self, device_id: str, bookmark_id: str) -> bool:
        """删除指定书签。

        参数:
            device_id:   设备ID
            bookmark_id: 书签ID

        返回:
            找到并删除返回 True；书签不存在返回 False。
        """
        raws = self._get_raw_bookmarks(device_id)
        remaining = [raw for raw in raws
                     if str(raw.get("bookmark_id", "")) != bookmark_id]
        if len(remaining) == len(raws):
            return False
        self._save_bookmarks(device_id, remaining)
        return True

    def rename_bookmark(self, device_id: str, bookmark_id: str,
                        new_name: str) -> bool:
        """重命名指定书签。

        参数:
            device_id:   设备ID
            bookmark_id: 书签ID
            new_name:    新的书签名

        返回:
            找到并重命名返回 True；书签不存在或新名称为空返回 False。
        """
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        raws = self._get_raw_bookmarks(device_id)
        changed = False
        for raw in raws:
            if str(raw.get("bookmark_id", "")) == bookmark_id:
                raw["name"] = new_name
                changed = True
                break
        if not changed:
            return False
        self._save_bookmarks(device_id, raws)
        return True

    def rename_device(self, device_id: str, new_name: str) -> bool:
        """重命名设备显示名称。

        参数:
            device_id: 设备ID
            new_name:  新的设备显示名称

        返回:
            找到设备并重命名返回 True；设备不存在或新名称为空返回 False。
        """
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        devices = self.config_manager.data.get("devices", {})
        entry = devices.get(device_id)
        if not isinstance(entry, dict):
            return False
        entry["name"] = new_name
        self.config_manager.save()
        return True

    def open_bookmark(self, device_id: str, bookmark_id: str) -> str:
        """获取书签对应的文件夹路径（供跳转使用）。

        参数:
            device_id:   设备ID
            bookmark_id: 书签ID

        返回:
            书签路径字符串；书签不存在时返回空字符串。
        """
        for raw in self._get_raw_bookmarks(device_id):
            if str(raw.get("bookmark_id", "")) == bookmark_id:
                return str(raw.get("path", ""))
        return ""

    def open_in_explorer(self, device_id: str, bookmark_id: str) -> None:
        """在系统资源管理器中打开书签所在文件夹。

        参数:
            device_id:   设备ID
            bookmark_id: 书签ID

        书签路径不存在时不做任何操作。
        """
        path = self.open_bookmark(device_id, bookmark_id)
        if path and os.path.isdir(path):
            subprocess.Popen(["explorer", path])

    def sync_all_bookmarks(self, device_id: str = None) -> dict:
        """检查全部书签路径的可达性。

        参数:
            device_id: 仅检查指定设备；为 None 时检查所有设备。

        返回:
            统计字典，结构::

                {
                    "ok": 路径可达的书签数量,
                    "missing": 路径不可达的书签数量,
                    "missing_items": [
                        {"device_id": ..., "bookmark_id": ...,
                         "name": ..., "path": ...}, ...
                    ]
                }
        """
        data = self.config_manager.data
        devices = data.get("devices", {})
        device_ids = ([device_id] if device_id else list(devices.keys()))
        ok = 0
        missing = 0
        missing_items = []
        for dev_id in device_ids:
            for raw in self._get_raw_bookmarks(dev_id):
                path = str(raw.get("path", ""))
                if path and os.path.isdir(path):
                    ok += 1
                else:
                    missing += 1
                    missing_items.append({
                        "device_id": dev_id,
                        "bookmark_id": str(raw.get("bookmark_id", "")),
                        "name": str(raw.get("name", "")),
                        "path": path,
                    })
        return {"ok": ok, "missing": missing, "missing_items": missing_items}


# ---------------------------------------------------------------------------
# 两级书签树组件（设计文档 26.1 节）
# ---------------------------------------------------------------------------
class BookmarkTree(tk.Frame):
    """两级书签树：设备节点 -> 书签名节点（无第三级）。

    交互说明：
    - 当前设备节点置顶显示，其余设备按名称排序；
    - 双击书签名节点发布 ``EVENT_FOLDER_SELECTED`` 事件，请求主程序跳转到
      对应文件夹（携带 folder_path / root_id / relative_path）；
    - 右键菜单提供：打开、在资源管理器中显示、重命名、删除、添加书签。
    """

    def __init__(self, parent, bookmark_manager: BookmarkManager,
                 config_manager, event_bus):
        """初始化书签树组件。

        参数:
            parent:           父容器（tkinter 组件）
            bookmark_manager: BookmarkManager 实例
            config_manager:   配置管理器实例（读取设备信息与根目录）
            event_bus:        事件总线实例
        """
        super().__init__(parent)
        self.bookmark_manager = bookmark_manager
        self.config_manager = config_manager
        self.event_bus = event_bus

        # 记录节点标识 -> (device_id, Bookmark) 的映射，None 表示设备节点
        self._item_map = {}

        self._build_widgets()
        self.refresh()

    def _build_widgets(self) -> None:
        """构建树形控件与滚动条。"""
        self.tree = ttk.Treeview(self, columns=("path",), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="设备 / 书签")
        self.tree.heading("path", text="路径")
        self.tree.column("#0", width=180, minwidth=120)
        self.tree.column("path", width=260, minwidth=160)

        scrollbar = ttk.Scrollbar(self, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        if os.name == "nt":
            # Windows 上右键同时可能是 <Button-2>（视鼠标设置而定）
            self.tree.bind("<Button-2>", self._on_right_click)

    # ------------------------------------------------------------------
    # 数据与刷新
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """从配置重新加载设备与书签，重建整棵树。"""
        self.tree.delete(*self.tree.get_children())
        self._item_map = {}

        devices = self.config_manager.data.get("devices", {})
        if not isinstance(devices, dict):
            return
        current_id = self.config_manager.get_current_device_id()

        # 当前设备置顶，其余设备按显示名称排序
        def sort_key(dev_id):
            entry = devices.get(dev_id) or {}
            return str(entry.get("name", dev_id))

        other_ids = sorted((d for d in devices.keys() if d != current_id),
                           key=sort_key)
        ordered = ([current_id] if current_id in devices else []) + other_ids

        for dev_id in ordered:
            entry = devices.get(dev_id) or {}
            name = str(entry.get("name") or dev_id)
            label = str(entry.get("device_label") or "")
            display = f"{name}（{label}）" if label else name
            if dev_id == current_id:
                display += " ★"
            dev_item = self.tree.insert("", "end", text=display, values=("",),
                                        open=True)
            self._item_map[dev_item] = (dev_id, None)

            for bm in self.bookmark_manager.get_device_bookmarks(dev_id):
                suffix = "" if bm.exists() else "（丢失）"
                bm_item = self.tree.insert(
                    dev_item, "end", text=bm.name + suffix,
                    values=(bm.path,))
                self._item_map[bm_item] = (dev_id, bm)

    def _get_selection(self):
        """获取当前选中项对应的 (device_id, Bookmark 或 None)。

        返回:
            (device_id, Bookmark) 元组；未选中或未知节点返回 (None, None)。
        """
        selection = self.tree.selection()
        if not selection:
            return None, None
        return self._item_map.get(selection[0], (None, None))

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_double_click(self, event) -> None:
        """双击书签名节点：发布文件夹选中事件请求跳转。"""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        device_id, bookmark = self._item_map.get(item_id, (None, None))
        if bookmark is None:
            return
        relative_path = ""
        if bookmark.root_id:
            try:
                relative_path = self.config_manager.get_relative_path(
                    bookmark.path, bookmark.root_id)
            except Exception:
                relative_path = ""
        self.event_bus.publish(
            EVENT_FOLDER_SELECTED,
            folder_path=bookmark.path,
            root_id=bookmark.root_id or "",
            relative_path=relative_path,
        )

    def _on_right_click(self, event) -> None:
        """右键弹出上下文菜单（打开/资源管理器/重命名/删除/添加书签）。"""
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self.tree.selection_set(item_id)
        device_id, bookmark = self._get_selection()

        menu = tk.Menu(self, tearoff=0)
        if bookmark is not None:
            menu.add_command(label="打开", command=self._open_selected)
            menu.add_command(label="在资源管理器中显示",
                             command=self._explore_selected)
            menu.add_separator()
            menu.add_command(label="重命名", command=self._rename_selected)
            menu.add_command(label="删除", command=self._delete_selected)
        elif device_id is not None:
            menu.add_command(label="添加书签", command=self._add_bookmark)
            menu.add_separator()
            menu.add_command(label="重命名设备", command=self._rename_device)
        else:
            menu.add_command(label="添加书签", command=self._add_bookmark)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # 菜单动作
    # ------------------------------------------------------------------
    def _open_selected(self) -> None:
        """打开选中的书签（模拟双击行为）。"""
        device_id, bookmark = self._get_selection()
        if bookmark is None:
            return
        relative_path = ""
        if bookmark.root_id:
            try:
                relative_path = self.config_manager.get_relative_path(
                    bookmark.path, bookmark.root_id)
            except Exception:
                relative_path = ""
        self.event_bus.publish(
            EVENT_FOLDER_SELECTED,
            folder_path=bookmark.path,
            root_id=bookmark.root_id or "",
            relative_path=relative_path,
        )

    def _explore_selected(self) -> None:
        """在系统资源管理器中显示选中的书签。"""
        device_id, bookmark = self._get_selection()
        if bookmark is None:
            return
        self.bookmark_manager.open_in_explorer(device_id, bookmark.bookmark_id)
        self.refresh()

    def _rename_selected(self) -> None:
        """重命名选中的书签。"""
        device_id, bookmark = self._get_selection()
        if bookmark is None:
            return
        new_name = simpledialog.askstring(
            "重命名书签", "请输入新的书签名：",
            initialvalue=bookmark.name, parent=self.winfo_toplevel())
        if not new_name or not new_name.strip():
            return
        if self.bookmark_manager.rename_bookmark(
                device_id, bookmark.bookmark_id, new_name):
            self.refresh()

    def _rename_device(self) -> None:
        """重命名选中的设备。"""
        device_id, _ = self._get_selection()
        if device_id is None:
            return
        entry = self.config_manager.data.get("devices", {}).get(device_id, {})
        old_name = str(entry.get("name") or device_id)
        new_name = simpledialog.askstring(
            "重命名设备", "请输入新的设备名称：",
            initialvalue=old_name, parent=self.winfo_toplevel())
        if not new_name or not new_name.strip():
            return
        if self.bookmark_manager.rename_device(device_id, new_name):
            self.refresh()

    def _delete_selected(self) -> None:
        """删除选中的书签（需确认）。"""
        device_id, bookmark = self._get_selection()
        if bookmark is None:
            return
        if not messagebox.askyesno(
                "删除书签",
                f"确定要删除书签「{bookmark.name}」吗？\n（仅删除书签记录，"
                "不会删除实际文件夹）",
                parent=self.winfo_toplevel()):
            return
        self.bookmark_manager.remove_bookmark(device_id, bookmark.bookmark_id)
        self.refresh()

    def _add_bookmark(self) -> None:
        """弹出添加书签对话框，确认后刷新树。"""
        device_id, _ = self._get_selection()
        dialog = AddBookmarkDialog(self.winfo_toplevel(),
                                   self.config_manager,
                                   device_id=device_id)
        if dialog.result is not None:
            self.bookmark_manager.add_bookmark(
                dialog.result.device_id,
                dialog.result.name,
                dialog.result.path,
                root_id=dialog.result.root_id,
            )
            self.refresh()


# ---------------------------------------------------------------------------
# 添加书签对话框
# ---------------------------------------------------------------------------
class AddBookmarkDialog(tk.Toplevel):
    """添加书签对话框。

    界面包含：书签名输入框、路径输入框（带浏览按钮）、
    所属根目录下拉框（从当前设备的根目录中选择，可为空）。

    确认后 ``self.result`` 为填写的 Bookmark 对象（未创建则为 None），
    由调用方负责调用 BookmarkManager.add_bookmark 完成持久化。
    """

    def __init__(self, parent, config_manager, device_id: str = None):
        """初始化对话框并模态等待用户操作。

        参数:
            parent:         父窗口
            config_manager: 配置管理器实例（读取当前设备与根目录列表）
            device_id:      目标设备ID；为 None 时取当前设备。
        """
        super().__init__(parent)
        self.config_manager = config_manager
        self.device_id = device_id or config_manager.get_current_device_id()
        self.result = None

        self.title("添加书签")
        self.transient(parent)
        self.resizable(False, False)
        self.grab_set()

        # 根目录选项：(显示文本, root_id)
        self._roots = [("", "")]
        for root in config_manager.get_device_roots(self.device_id):
            text = f"{root.name}（{root.path}）"
            self._roots.append((text, root.root_id))

        self._build_widgets()
        self._center_on_parent(parent)
        self.wait_window(self)

    def _build_widgets(self) -> None:
        """构建对话框内的表单控件。"""
        pad = {"padx": 8, "pady": 4}
        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(frame, text="书签名：").grid(row=0, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.name_var, width=40).grid(
            row=0, column=1, columnspan=2, sticky="we", **pad)

        tk.Label(frame, text="路径：").grid(row=1, column=0, sticky="e", **pad)
        self.path_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.path_var, width=40).grid(
            row=1, column=1, sticky="we", **pad)
        tk.Button(frame, text="浏览…", command=self._on_browse).grid(
            row=1, column=2, **pad)

        tk.Label(frame, text="所属根目录：").grid(row=2, column=0, sticky="e", **pad)
        self.root_var = tk.StringVar(value=self._roots[0][0])
        self.root_combo = ttk.Combobox(
            frame, textvariable=self.root_var, state="readonly", width=37,
            values=[text for text, _ in self._roots])
        self.root_combo.grid(row=2, column=1, columnspan=2, sticky="we", **pad)

        button_frame = tk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=(8, 0))
        tk.Button(button_frame, text="确定", width=10,
                  command=self._on_confirm).pack(side="left", padx=8)
        tk.Button(button_frame, text="取消", width=10,
                  command=self._on_cancel).pack(side="left", padx=8)

    def _center_on_parent(self, parent) -> None:
        """将对话框居中于父窗口。"""
        try:
            self.update_idletasks()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + max((pw - w) // 2, 0)
            y = py + max((ph - h) // 2, 0)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _on_browse(self) -> None:
        """打开目录选择对话框填充路径。"""
        initial = self.path_var.get().strip() or None
        directory = filedialog.askdirectory(parent=self, initialdir=initial)
        if directory:
            self.path_var.set(directory)
            # 路径选择后，若书签名仍为空则自动填充文件夹名
            if not self.name_var.get().strip():
                self.name_var.set(os.path.basename(directory) or directory)

    def _selected_root_id(self) -> str:
        """根据下拉框当前文本返回对应的 root_id（无匹配返回空）。"""
        text = self.root_var.get()
        for item_text, root_id in self._roots:
            if item_text == text:
                return root_id
        return ""

    def _on_confirm(self) -> None:
        """校验输入并生成结果书签，随后关闭对话框。"""
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        if not name:
            messagebox.showwarning("添加书签", "书签名不能为空！", parent=self)
            return
        if not path:
            messagebox.showwarning("添加书签", "路径不能为空！", parent=self)
            return
        self.result = Bookmark(
            bookmark_id="",
            device_id=self.device_id,
            name=name,
            path=path,
            root_id=self._selected_root_id() or None,
            created_at=datetime.now(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        """取消添加，直接关闭对话框。"""
        self.result = None
        self.destroy()
