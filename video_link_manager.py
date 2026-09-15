# -*- coding: utf-8 -*-
"""视频关联模块（对应设计文档第24章）。

包含四个类：
- VideoLinkManager：视频跨文件夹关联的 CRUD、智能扫描建议、批量路径替换，
  负责与数据库（db_manager 的 video_links 专用方法）和事件总线交互；
- VideoLinkManagerDialog：已有链接管理对话框（查看 / 删除 / 打开主视频所在文件夹）；
- BatchPathReplaceDialog：批量路径前缀替换对话框；
- LinkSuggestionDialog：扫描建议确认对话框（勾选后批量创建关联）。

数据类 VideoLink / LinkSuggestion 从契约文件 models.py 导入；
事件常量从契约文件 events.py 导入；
全局常量从契约文件 constants.py 导入。
仅使用标准库 + tkinter。
"""
import os
import re
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import constants
import events
from models import LinkSuggestion, VideoLink


class VideoLinkManager:
    """视频关联管理器：负责视频跨文件夹关联的创建、删除、查询与智能建议。

    属性:
        db_manager:    DatabaseManager       数据库管理器实例（video_links 专用方法）
        event_bus:     EventBus              事件总线实例
        config_manager: ConfigManager        配置管理器实例（预留，供路径解析等使用）
        _pattern_engine: object | False      番号识别引擎（延迟导入，不可用时为 False）
    """

    # 内置番号回退正则（设计 24 章：PatternRecognitionEngine 不可用时的兜底）
    FALLBACK_CODE_PATTERN = r'([A-Za-z]{2,6}[-_]?\d{2,5})'

    def __init__(self, db_manager, event_bus, config_manager):
        """初始化视频关联管理器。

        参数:
            db_manager:     数据库管理器实例（需有 current_device_id 及 video_links 专用方法）
            event_bus:      事件总线实例
            config_manager: 配置管理器实例
        """
        self.db_manager = db_manager
        self.event_bus = event_bus
        self.config_manager = config_manager
        self._pattern_engine = None  # None=未尝试加载, False=不可用, 其他=引擎实例

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------
    def _execute(self, sql, params=()):
        """执行写SQL，经 db_manager 提供的方法执行（优先通用 execute）。"""
        execute_fn = getattr(self.db_manager, 'execute', None)
        if callable(execute_fn):
            return execute_fn(sql, params)
        cursor = self.db_manager.connection.execute(sql, params)
        self.db_manager.connection.commit()
        return cursor

    def _query(self, sql, params=()):
        """执行读SQL并返回全部行，经 db_manager 提供的方法执行（优先通用 query）。"""
        query_fn = getattr(self.db_manager, 'query', None)
        if callable(query_fn):
            return query_fn(sql, params)
        return self.db_manager.connection.execute(sql, params).fetchall()

    @staticmethod
    def _normalize_path(path):
        """路径统一为 POSIX 风格（数据库中存储的相对路径格式），去除首尾空白。"""
        return (path or "").replace("\\", "/").strip()

    @staticmethod
    def _escape_like(text):
        """转义 SQL LIKE 中的通配符（配合 ESCAPE '\\' 使用）。"""
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _publish_link_changed(self, action, **kwargs):
        """发布视频关联变更事件。"""
        if self.event_bus is None:
            return
        self.event_bus.publish(
            events.EVENT_VIDEO_LINK_CHANGED, action=action, **kwargs)

    def _get_pattern_engine(self):
        """延迟导入并缓存番号识别引擎（PatternRecognitionEngine）。

        引擎模块（24号）可能尚未实现或依赖缺失，任何异常都回退到内置正则。

        返回:
            引擎实例；不可用时返回 None。
        """
        if self._pattern_engine is None:
            try:
                from pattern_recognition_engine import PatternRecognitionEngine
                self._pattern_engine = PatternRecognitionEngine(self.db_manager)
            except Exception:
                self._pattern_engine = False
        return self._pattern_engine or None

    def _extract_codes(self, file_name):
        """从文件名提取番号候选列表（大写去重）。

        优先使用 PatternRecognitionEngine；引擎不可用时回退内置正则。
        引擎结果与正则结果合并去重，引擎命中的置信度更高。

        参数:
            file_name: 文件名（不含目录）
        返回:
            list[tuple[str, float]]，元素为 (番号, 提取置信度)
        """
        codes = {}

        engine = self._get_pattern_engine()
        if engine is not None:
            try:
                result = engine.extract_code(file_name)
                code = ""
                if isinstance(result, str):
                    code = result
                elif result is not None:
                    code = (getattr(result, 'code', "") or
                            getattr(result, 'text', "") or
                            getattr(result, 'best_code', ""))
                code = (code or "").strip().upper()
                if code:
                    codes[code] = 0.95
            except Exception:
                pass  # 引擎提取失败时静默回退到正则

        for match in re.findall(self.FALLBACK_CODE_PATTERN, file_name or ""):
            code = match.upper()
            if code and code not in codes:
                codes[code] = 0.70

        return sorted(codes.items(), key=lambda kv: kv[1], reverse=True)

    def _find_candidate_videos(self, code, exclude_folder=""):
        """按番号在数据库 files 表中查找候选主视频（相对路径列表）。

        参数:
            code:          番号（大写）
            exclude_folder: 需要排除的文件夹相对路径（图片自身所在文件夹）
        返回:
            候选主视频相对路径列表（按路径排序）
        """
        device_id = getattr(self.db_manager, 'current_device_id', "")
        pattern = "%" + self._escape_like(code) + "%"
        rows = self._query(
            "SELECT relative_path FROM files WHERE device_id=? AND "
            "(file_name LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\')",
            (device_id, pattern, pattern),
        )
        candidates = []
        exclude_folder = self._normalize_path(exclude_folder)
        for row in rows:
            relative_path = row[0] if not isinstance(row, dict) else row.get("relative_path", "")
            relative_path = self._normalize_path(relative_path)
            ext = os.path.splitext(relative_path)[1].lower()
            if ext not in constants.VIDEO_EXTENSIONS:
                continue
            if exclude_folder:
                folder = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
                if folder == exclude_folder:
                    continue  # 排除图片所在文件夹自身的视频
            candidates.append(relative_path)
        return sorted(candidates)

    # ------------------------------------------------------------------
    # 关联 CRUD（设计 24.3 节）
    # ------------------------------------------------------------------
    def create_link(self, primary_path: str, linked_folder_path: str,
                    match_code: str = '') -> bool:
        """创建视频关联（主视频 <-> 图片文件夹）。

        幂等：同主视频 + 同文件夹已存在时直接返回成功。
        成功后发布 EVENT_VIDEO_LINK_CHANGED 事件。

        参数:
            primary_path:        主视频相对路径
            linked_folder_path:  被关联的图片文件夹相对路径
            match_code:          匹配番号（可为空）
        返回:
            是否创建成功（或已存在）
        """
        primary_path = self._normalize_path(primary_path)
        linked_folder_path = self._normalize_path(linked_folder_path)
        if not primary_path or not linked_folder_path:
            return False
        link_id = self.db_manager.create_link(
            primary_path, linked_folder_path, match_code or "")
        if not link_id:
            return False
        self._publish_link_changed("created", link_id=link_id,
                                   primary_video_path=primary_path,
                                   linked_folder_path=linked_folder_path)
        return True

    def remove_link(self, link_id: int) -> bool:
        """删除单条视频关联。

        成功后发布 EVENT_VIDEO_LINK_CHANGED 事件。

        参数:
            link_id: 关联ID
        返回:
            是否删除了记录
        """
        ok = self.db_manager.remove_link(link_id)
        if ok:
            self._publish_link_changed("removed", link_id=link_id)
        return ok

    def remove_links_by_folder(self, folder_path: str) -> int:
        """删除指定图片文件夹的全部视频关联。

        删除条数大于 0 时发布 EVENT_VIDEO_LINK_CHANGED 事件。

        参数:
            folder_path: 图片文件夹相对路径
        返回:
            删除的条数
        """
        count = self.db_manager.remove_links_by_folder(
            self._normalize_path(folder_path))
        if count > 0:
            self._publish_link_changed("removed_by_folder", count=count,
                                       linked_folder_path=folder_path)
        return count

    def get_linked_videos(self, folder_path: str) -> list:
        """查询指定图片文件夹关联到的全部视频。

        参数:
            folder_path: 图片文件夹相对路径
        返回:
            VideoLink 列表
        """
        return self.db_manager.get_linked_videos(self._normalize_path(folder_path))

    def get_primary_videos(self, folder_path: str) -> list:
        """查询指定文件夹作为主视频所在方的全部关联记录。

        即：其他哪些图片文件夹关联到了本文件夹（或其子路径）下的主视频。

        参数:
            folder_path: 文件夹相对路径
        返回:
            VideoLink 列表（primary_video_path 位于该文件夹下）
        """
        folder_path = self._normalize_path(folder_path).rstrip("/")
        links = self.db_manager.get_all_links()
        if not folder_path:
            return links
        prefix = folder_path + "/"
        return [link for link in links
                if link.primary_video_path == folder_path
                or link.primary_video_path.startswith(prefix)]

    def is_linked_folder(self, folder_path: str) -> bool:
        """判断指定文件夹是否已被关联到某个主视频。

        参数:
            folder_path: 文件夹相对路径
        返回:
            True=已关联
        """
        return bool(self.get_linked_videos(folder_path))

    def get_primary_path(self, folder_path: str):
        """查询指定图片文件夹关联到的主视频路径。

        参数:
            folder_path: 图片文件夹相对路径
        返回:
            主视频相对路径；未关联时返回 None
        """
        folder_path = self._normalize_path(folder_path).rstrip("/")
        # db_manager.get_primary_path 按“文件相对路径”推导所在文件夹，
        # 此处追加占位文件名使其推导结果恰为 folder_path 本身
        probe = (folder_path + "/.link_probe") if folder_path else ".link_probe"
        return self.db_manager.get_primary_path(probe)

    def batch_update_paths(self, old_prefix: str, new_prefix: str,
                           path_type: str = 'both') -> int:
        """批量替换路径前缀（设备根目录迁移 / 盘符变更时使用）。

        path_type 取值：
            'both'    —— 同时替换主视频路径与关联文件夹路径（委托 db_manager 专用方法）
            'primary' —— 仅替换主视频路径
            'linked'  —— 仅替换关联文件夹路径

        参数:
            old_prefix: 旧路径前缀
            new_prefix: 新路径前缀
            path_type:  替换范围（'both' / 'primary' / 'linked'）
        返回:
            更新的关联条数
        """
        old_norm = self._normalize_path(old_prefix).rstrip("/")
        new_norm = self._normalize_path(new_prefix).rstrip("/")
        if not old_norm or new_norm == "" and new_prefix == "":
            return 0
        device_id = getattr(self.db_manager, 'current_device_id', "")

        if path_type == 'both':
            count = self.db_manager.batch_update_paths(old_norm, new_norm)
        else:
            column = 'primary_video_path' if path_type == 'primary' else 'linked_folder_path'
            cursor = self._execute(
                "UPDATE video_links SET {0}=? || SUBSTR({0}, ?) "
                "WHERE device_id=? AND {0} LIKE ?".format(column),
                (new_norm, len(old_norm) + 1, device_id, old_norm + "%"),
            )
            count = cursor.rowcount

        if count > 0:
            self._publish_link_changed("batch_updated", count=count,
                                       path_type=path_type,
                                       old_prefix=old_norm, new_prefix=new_norm)
        return count

    def scan_for_links(self, root_path: str) -> list:
        """扫描根目录，为“只有图片没有视频”的文件夹生成关联建议。

        逻辑：
            1. 遍历 root_path 下所有文件夹；
            2. 跳过含视频文件或不含图片文件的文件夹；
            3. 跳过已建立关联的文件夹；
            4. 从图片文件名提取番号（优先 PatternRecognitionEngine，失败回退内置正则）；
            5. 按番号在数据库中查找候选主视频；
            6. 为每个命中生成 LinkSuggestion（含置信度与原因）。

        参数:
            root_path: 待扫描的根目录绝对路径
        返回:
            LinkSuggestion 列表
        """
        suggestions = []
        if not root_path or not os.path.isdir(root_path):
            return suggestions

        seen_folders = set()
        for dir_path, _dir_names, file_names in os.walk(root_path):
            has_video = any(
                os.path.splitext(name)[1].lower() in constants.VIDEO_EXTENSIONS
                for name in file_names)
            if has_video:
                continue
            image_names = [
                name for name in file_names
                if os.path.splitext(name)[1].lower() in constants.IMAGE_EXTENSIONS]
            if not image_names:
                continue

            rel_folder = self._normalize_path(os.path.relpath(dir_path, root_path))
            if rel_folder == ".":
                rel_folder = ""
            if rel_folder in seen_folders:
                continue
            seen_folders.add(rel_folder)

            # 已关联的文件夹不再建议
            if self.is_linked_folder(rel_folder):
                continue

            # 汇总该文件夹全部图片的番号候选（按置信度排序）
            code_candidates = []
            for image_name in sorted(image_names):
                for code, confidence in self._extract_codes(
                        os.path.splitext(image_name)[0]):
                    code_candidates.append((code, confidence, image_name))
            if not code_candidates:
                continue

            # 每个番号取置信度最高、路径最短的一个候选主视频
            used_codes = set()
            for code, confidence, image_name in code_candidates:
                if code in used_codes:
                    continue
                used_codes.add(code)
                candidates = self._find_candidate_videos(code, rel_folder)
                if not candidates:
                    continue
                # 文件名中完整包含番号的候选优先，其次路径最短者
                best = sorted(
                    candidates,
                    key=lambda p: (0 if code.lower() in os.path.basename(p).lower() else 1,
                                   len(p)))[0]
                if confidence >= 0.95:
                    reason = "番号引擎识别: {0}（来自 {1}）".format(code, image_name)
                else:
                    reason = "文件名番号匹配: {0}（来自 {1}）".format(code, image_name)
                suggestions.append(LinkSuggestion(
                    primary_path=best,
                    linked_folder_path=rel_folder,
                    match_code=code,
                    match_confidence=round(confidence, 2),
                    match_reason=reason,
                ))

        suggestions.sort(key=lambda s: (s.linked_folder_path, s.match_code))
        return suggestions

    def get_all_links(self) -> list:
        """查询当前设备的全部视频关联记录。

        返回:
            VideoLink 列表
        """
        return self.db_manager.get_all_links()

    def get_link_stats(self) -> dict:
        """统计当前设备的视频关联情况。

        返回:
            dict，含 total_links（总条数）、linked_folders（被关联文件夹数）、
            primary_videos（主视频数）
        """
        links = self.get_all_links()
        return {
            "total_links": len(links),
            "linked_folders": len({link.linked_folder_path for link in links}),
            "primary_videos": len({link.primary_video_path for link in links}),
        }


class _BaseDialog(tk.Toplevel):
    """模态对话框基类：统一窗口协议、模态行为与通用控件创建。"""

    def __init__(self, parent, title, width=760, height=520):
        """初始化模态对话框。

        参数:
            parent: 父窗口（tk.Tk 或 tk.Toplevel）
            title:  窗口标题
            width:  窗口宽度
            height: 窗口高度
        """
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.minsize(480, 320)
        self.transient(parent.winfo_toplevel())
        self.result = None
        # 居中显示
        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.geometry("{0}x{1}+{2}+{3}".format(width, height, max(x, 0), max(y, 0)))
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_cancel(self):
        """关闭对话框（子类可覆盖以自定义取消行为）。"""
        self.destroy()

    def show_modal(self):
        """以模态方式显示对话框，等待关闭后返回 self.result。"""
        self.grab_set()
        self.focus_set()
        self.wait_window(self)
        return self.result


class VideoLinkManagerDialog(_BaseDialog):
    """视频关联管理对话框：查看已有链接、删除链接、打开主视频所在文件夹。"""

    def __init__(self, parent, link_manager: VideoLinkManager, root_path: str = ""):
        """初始化管理对话框。

        参数:
            parent:        父窗口
            link_manager:  VideoLinkManager 实例
            root_path:     设备根目录绝对路径（用于把相对路径解析为磁盘路径，可为空）
        """
        super().__init__(parent, "视频关联管理", 820, 560)
        self.link_manager = link_manager
        self.root_path = root_path or ""
        self._links = {}      # iid -> VideoLink
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        """构建界面：Treeview 列表 + 操作按钮。"""
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.BOTH, expand=True)

        columns = ("linked_folder", "primary_video", "match_code", "created_at")
        self.tree = ttk.Treeview(top, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("linked_folder", text="图片文件夹")
        self.tree.heading("primary_video", text="主视频")
        self.tree.heading("match_code", text="匹配番号")
        self.tree.heading("created_at", text="创建时间")
        self.tree.column("linked_folder", width=220, anchor=tk.W)
        self.tree.column("primary_video", width=260, anchor=tk.W)
        self.tree.column("match_code", width=100, anchor=tk.W)
        self.tree.column("created_at", width=140, anchor=tk.W)
        self.tree.bind("<Double-1>", lambda _e: self._open_primary_folder())
        yscroll = ttk.Scrollbar(top, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        btns = ttk.Frame(self, padding=(8, 0, 8, 8))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="打开主视频所在文件夹",
                   command=self._open_primary_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="删除选中",
                   command=self._remove_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="刷新", command=self._refresh).pack(side=tk.LEFT, padx=4)
        self.count_label = ttk.Label(btns, text="")
        self.count_label.pack(side=tk.LEFT, padx=12)
        ttk.Button(btns, text="关闭", command=self._on_cancel).pack(side=tk.RIGHT, padx=4)

    def _refresh(self):
        """从数据库重新加载全部链接到列表。"""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._links.clear()
        links = self.link_manager.get_all_links()
        for link in links:
            created = ""
            if isinstance(link.created_at, datetime):
                created = link.created_at.strftime("%Y-%m-%d %H:%M")
            iid = self.tree.insert(
                "", tk.END,
                values=(link.linked_folder_path, link.primary_video_path,
                        link.match_code, created))
            self._links[iid] = link
        self.count_label.config(text="共 {0} 条关联".format(len(links)))

    def _selected_links(self):
        """获取当前选中的 VideoLink 列表。"""
        return [self._links[iid] for iid in self.tree.selection() if iid in self._links]

    def _resolve_absolute(self, relative_path):
        """把数据库中的相对路径解析为磁盘绝对路径（无法解析时返回原值）。"""
        if not relative_path:
            return ""
        if os.path.isabs(relative_path):
            return relative_path
        if self.root_path:
            return os.path.join(self.root_path, relative_path.replace("/", os.sep))
        return relative_path

    def _open_primary_folder(self):
        """在系统资源管理器中打开选中链接的主视频所在文件夹。"""
        selected = self._selected_links()
        if not selected:
            messagebox.showinfo("提示", "请先选中一条关联记录。", parent=self)
            return
        link = selected[0]
        absolute = self._resolve_absolute(link.primary_video_path)
        folder = os.path.dirname(absolute)
        if not os.path.isdir(folder):
            messagebox.showwarning(
                "路径不存在", "主视频所在文件夹不存在：\n{0}".format(folder), parent=self)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _remove_selected(self):
        """删除选中的关联记录。"""
        selected = self._selected_links()
        if not selected:
            messagebox.showinfo("提示", "请先选中要删除的关联记录。", parent=self)
            return
        if not messagebox.askyesno(
                "确认删除", "确定删除选中的 {0} 条关联吗？".format(len(selected)),
                parent=self):
            return
        removed = 0
        for link in selected:
            if self.link_manager.remove_link(link.link_id):
                removed += 1
        self._refresh()
        messagebox.showinfo("完成", "已删除 {0} 条关联。".format(removed), parent=self)


class BatchPathReplaceDialog(_BaseDialog):
    """批量路径替换对话框：输入旧前缀 / 新前缀 / 替换范围，执行批量更新。"""

    PATH_TYPES = (("both", "两者都替换"), ("primary", "仅主视频路径"), ("linked", "仅关联文件夹路径"))

    def __init__(self, parent, link_manager: VideoLinkManager):
        """初始化批量路径替换对话框。

        参数:
            parent:       父窗口
            link_manager: VideoLinkManager 实例
        """
        super().__init__(parent, "批量路径替换", 560, 220)
        self.link_manager = link_manager
        self.result = 0
        self._build_ui()

    def _build_ui(self):
        """构建表单界面。"""
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="旧路径前缀：").grid(row=0, column=0, sticky=tk.W, pady=6)
        self.old_entry = ttk.Entry(frame, width=50)
        self.old_entry.grid(row=0, column=1, sticky=tk.EW, pady=6)

        ttk.Label(frame, text="新路径前缀：").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.new_entry = ttk.Entry(frame, width=50)
        self.new_entry.grid(row=1, column=1, sticky=tk.EW, pady=6)

        ttk.Label(frame, text="替换范围：").grid(row=2, column=0, sticky=tk.W, pady=6)
        self.type_combo = ttk.Combobox(
            frame, state="readonly", width=20,
            values=[label for _key, label in self.PATH_TYPES])
        self.type_combo.current(0)
        self.type_combo.grid(row=2, column=1, sticky=tk.W, pady=6)

        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(btns, text="执行替换", command=self._do_replace).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="取消", command=self._on_cancel).pack(side=tk.LEFT, padx=4)

    def _do_replace(self):
        """执行批量前缀替换并显示更新条数。"""
        old_prefix = self.old_entry.get().strip()
        new_prefix = self.new_entry.get().strip()
        if not old_prefix:
            messagebox.showwarning("输入无效", "请填写旧路径前缀。", parent=self)
            return
        label = self.type_combo.get()
        path_type = next((key for key, text in self.PATH_TYPES if text == label), 'both')
        try:
            count = self.link_manager.batch_update_paths(old_prefix, new_prefix, path_type)
        except Exception as exc:
            messagebox.showerror("替换失败", "批量替换出错：\n{0}".format(exc), parent=self)
            return
        self.result = count
        messagebox.showinfo(
            "替换完成", "已更新 {0} 条关联记录。".format(count), parent=self)
        self.destroy()


class LinkSuggestionDialog(_BaseDialog):
    """扫描建议对话框：展示 scan_for_links 结果，勾选后批量创建关联。"""

    CHECK_ON, CHECK_OFF = "☑", "☐"

    def __init__(self, parent, link_manager: VideoLinkManager, suggestions: list):
        """初始化建议确认对话框。

        参数:
            parent:        父窗口
            link_manager:  VideoLinkManager 实例
            suggestions:   LinkSuggestion 列表
        """
        super().__init__(parent, "视频关联建议", 860, 560)
        self.link_manager = link_manager
        self.suggestions = list(suggestions or [])
        self.result = []  # 已创建的 LinkSuggestion 列表
        self._checked = {}  # iid -> bool
        self._build_ui()

    def _build_ui(self):
        """构建界面：可勾选建议列表 + 批量操作按钮。"""
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.BOTH, expand=True)

        columns = ("checked", "folder", "primary", "code", "confidence", "reason")
        self.tree = ttk.Treeview(top, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("checked", text="选择")
        self.tree.heading("folder", text="图片文件夹")
        self.tree.heading("primary", text="主视频")
        self.tree.heading("code", text="番号")
        self.tree.heading("confidence", text="置信度")
        self.tree.heading("reason", text="匹配原因")
        self.tree.column("checked", width=50, anchor=tk.CENTER)
        self.tree.column("folder", width=200, anchor=tk.W)
        self.tree.column("primary", width=220, anchor=tk.W)
        self.tree.column("code", width=90, anchor=tk.W)
        self.tree.column("confidence", width=60, anchor=tk.CENTER)
        self.tree.column("reason", width=220, anchor=tk.W)
        self.tree.bind("<Button-1>", self._on_tree_click)
        yscroll = ttk.Scrollbar(top, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        for index, suggestion in enumerate(self.suggestions):
            iid = self.tree.insert(
                "", tk.END,
                values=(self.CHECK_ON, suggestion.linked_folder_path,
                        suggestion.primary_path, suggestion.match_code,
                        "{0:.2f}".format(suggestion.match_confidence),
                        suggestion.match_reason))
            self._checked[iid] = True
            self.tree.selection_add(iid)

        btns = ttk.Frame(self, padding=(8, 0, 8, 8))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="全选", command=lambda: self._set_all(True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="全不选", command=lambda: self._set_all(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="反选", command=self._invert).pack(side=tk.LEFT, padx=2)
        self.count_label = ttk.Label(btns, text="")
        self.count_label.pack(side=tk.LEFT, padx=12)
        ttk.Button(btns, text="创建选中关联", command=self._create_selected).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="取消", command=self._on_cancel).pack(side=tk.RIGHT, padx=4)
        self._update_count()

    def _update_count(self):
        """更新已勾选数量显示。"""
        total = len(self._checked)
        checked = sum(1 for flag in self._checked.values() if flag)
        self.count_label.config(text="已选 {0} / {1}".format(checked, total))

    def _set_all(self, flag):
        """全选 / 全不选。"""
        for iid in self._checked:
            self._checked[iid] = flag
            values = list(self.tree.item(iid, "values"))
            values[0] = self.CHECK_ON if flag else self.CHECK_OFF
            self.tree.item(iid, values=values)
            if flag:
                self.tree.selection_add(iid)
            else:
                self.tree.selection_remove(iid)
        self._update_count()

    def _invert(self):
        """反选。"""
        for iid, flag in list(self._checked.items()):
            self._set_checked(iid, not flag)
        self._update_count()

    def _set_checked(self, iid, flag):
        """设置单条建议的勾选状态并同步 Treeview 显示与选中态。"""
        if iid not in self._checked:
            return
        self._checked[iid] = flag
        values = list(self.tree.item(iid, "values"))
        values[0] = self.CHECK_ON if flag else self.CHECK_OFF
        self.tree.item(iid, values=values)
        if flag:
            self.tree.selection_add(iid)
        else:
            self.tree.selection_remove(iid)

    def _on_tree_click(self, event):
        """点击“选择”列时切换勾选状态。"""
        region = self.tree.identify("region", event.x, event.y)
        column = self.tree.identify("column", event.x, event.y)
        if region == "cell" and column == "#1":
            iid = self.tree.identify_row(event.y)
            if iid in self._checked:
                self._set_checked(iid, not self._checked[iid])
                self._update_count()
                return "break"
        return None

    def _create_selected(self):
        """为勾选的Suggestion批量创建关联。"""
        checked_iids = [iid for iid, flag in self._checked.items() if flag]
        if not checked_iids:
            messagebox.showinfo("提示", "请先勾选要创建的关联建议。", parent=self)
            return
        created, failed = [], 0
        index_map = {self.tree.index(iid): iid for iid in self.tree.get_children("")}
        for iid in checked_iids:
            index = self.tree.index(iid)
            if index < len(self.suggestions):
                suggestion = self.suggestions[index]
                if self.link_manager.create_link(
                        suggestion.primary_path, suggestion.linked_folder_path,
                        suggestion.match_code):
                    created.append(suggestion)
                else:
                    failed += 1
        self.result = created
        messagebox.showinfo(
            "创建完成",
            "成功创建 {0} 条关联{1}。".format(
                len(created), "，失败 {0} 条".format(failed) if failed else ""),
            parent=self)
        self.destroy()
