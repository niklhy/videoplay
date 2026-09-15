# -*- coding: utf-8 -*-
"""右键菜单模块（对应设计文档第28章）。

职责：聚合缩略图右键菜单，按 GalleryItem 类型与匹配模式动态组装菜单项：
    通用项：提取主文件夹名称 / 复制图片路径 / 打开所在文件夹
    视频项：播放 / 采集本视频信息 / 采集文件夹下所有视频 / 提取番号 /
            删除视频（二次确认）/ 更换缩略图
    图片项：查看原图 / 设为以下视频的缩略图
    标签项：设置标签（子菜单勾选全部标签）

仅使用对方模块公开接口，不修改对方代码。
数据类从契约文件 models.py 导入，常量从 constants.py 导入。
仅使用标准库 + tkinter。
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import constants
from models import GalleryItem


class ContextMenuManager:
    """缩略图右键菜单管理器（设计文档28.1节）。

    属性：
        parent:             tk.Widget                 父控件（提供剪贴板与对话框宿主）
        gallery_viewer:     GalleryViewer             图库查看器（刷新/打开图片窗口）
        player_settings:    PlayerSettings            播放器设置（播放视频）
        tag_manager:        TagManager                标签管理器（文件-标签关联）
        video_link_manager: VideoLinkManager          视频关联管理器
        db_manager:         DatabaseManager           数据库管理器（删除文件记录等）
        crawler_manager:    CrawlerManager            爬虫管理器（单条/队列采集）
        log_manager:        LogManager                日志管理器
        actor_manager:      ActorManager | None       演员管理器（可选，预留）
        pattern_engine:     PatternRecognitionEngine  番号识别引擎（可选，None 时禁用提取番号）
        custom_code_manager: CustomCodeManager        手动番号管理器（可选）
        crawler_executor:   CrawlerExecutor           爬虫执行器（可选，作为采集后备）
    """

    #: 内置番号回退正则（番号识别引擎不可用时兜底，与19号模块一致）
    FALLBACK_CODE_PATTERN = r'([A-Za-z]{2,6}[-_]?\d{2,5})'

    #: 有视频可播放的匹配模式集合
    VIDEO_MATCH_MODES = (
        constants.MODE_COVER_MATCH,
        constants.MODE_VIDEO_LINKED,
        constants.MODE_EXCLUSIVE_VIDEO,
        constants.MODE_COVER_PRIORITY,
    )

    def __init__(self, parent: tk.Widget, gallery_viewer,
                 player_settings, tag_manager, video_link_manager,
                 db_manager, crawler_manager, log_manager,
                 actor_manager=None, pattern_engine=None,
                 custom_code_manager=None, crawler_executor=None):
        """初始化右键菜单管理器。

        参数：
            parent:              父控件（tk.Widget）
            gallery_viewer:      GalleryViewer 实例
            player_settings:     PlayerSettings 实例
            tag_manager:         TagManager 实例
            video_link_manager:  VideoLinkManager 实例
            db_manager:          DatabaseManager 实例
            crawler_manager:     CrawlerManager 实例
            log_manager:         LogManager 实例
            actor_manager:       ActorManager 实例（可选，默认 None）
            pattern_engine:      PatternRecognitionEngine 实例（可选，默认 None）
            custom_code_manager: CustomCodeManager 实例（可选，默认 None）
            crawler_executor:    CrawlerExecutor 实例（可选，默认 None）
        """
        self.parent = parent
        self.gallery_viewer = gallery_viewer
        self.player_settings = player_settings
        self.tag_manager = tag_manager
        self.video_link_manager = video_link_manager
        self.db_manager = db_manager
        self.crawler_manager = crawler_manager
        self.log_manager = log_manager
        self.actor_manager = actor_manager
        self.pattern_engine = pattern_engine
        self.custom_code_manager = custom_code_manager
        self.crawler_executor = crawler_executor

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str, details: str = "") -> None:
        """写入一条日志（log_manager 不可用时静默忽略）。

        参数：
            level:   日志级别（constants.LOG_LEVEL_*）
            message: 日志内容
            details: 附加详情（可选）
        """
        if self.log_manager is None:
            return
        try:
            self.log_manager.log(level, "context_menu", message, details)
        except Exception:
            pass  # 日志失败不影响菜单功能

    def _root_window(self) -> tk.Widget:
        """获取顶层窗口（作为对话框与消息框的父窗口）。"""
        try:
            return self.parent.winfo_toplevel()
        except Exception:
            return self.parent

    def _copy_to_clipboard(self, text: str) -> None:
        """把文本复制到系统剪贴板。

        参数：
            text: 待复制的文本
        """
        self.parent.clipboard_clear()
        self.parent.clipboard_append(text)

    def _get_main_path(self, item: GalleryItem) -> str:
        """获取缩略图项的主文件路径（优先视频，其次缩略图，再次图片列表）。

        参数：
            item: 图库缩略图项
        返回：
            str，主文件绝对路径（可能为空字符串）
        """
        if item.video_path:
            return item.video_path
        if item.thumbnail_path:
            return item.thumbnail_path
        if item.image_paths:
            return item.image_paths[item.current_image_index
                                    if item.current_image_index < len(item.image_paths) else 0]
        return ""

    def _get_relative_path(self, item: GalleryItem) -> str:
        """获取缩略图项对应的数据库相对路径（元数据缺失时返回空串）。

        参数：
            item: 图库缩略图项
        返回：
            str，相对路径（无元数据时为空字符串）
        """
        if item.metadata is not None and item.metadata.relative_path:
            return item.metadata.relative_path
        return ""

    def _is_video_item(self, item: GalleryItem) -> bool:
        """判断缩略图项是否带有可播放视频。"""
        return bool(item.video_path) or item.match_mode in self.VIDEO_MATCH_MODES

    def _is_image_item(self, item: GalleryItem) -> bool:
        """判断缩略图项是否带有图片（仅图片或图片+视频均算）。"""
        return bool(item.image_paths) or bool(item.thumbnail_path) \
            or item.match_mode == constants.MODE_IMAGE_ONLY

    def _extract_code(self, file_path: str) -> str:
        """提取视频番号：手动番号优先，其次番号识别引擎，最后内置正则兜底。

        参数：
            file_path: 视频文件路径（番号从文件名提取）
        返回：
            str，番号（未识别时为空字符串）
        """
        file_name = os.path.basename(file_path or "")
        # 1. 手动番号（人工校正视为权威来源）
        if self.custom_code_manager is not None:
            try:
                manual = self.custom_code_manager.get_custom_code(file_path)
                if manual:
                    return str(manual).strip()
            except Exception:
                pass  # 手动番号查询失败时继续走引擎
        # 2. 番号识别引擎
        if self.pattern_engine is not None:
            try:
                result = self.pattern_engine.extract_code(file_name, context="gallery")
                code = ""
                if isinstance(result, str):
                    code = result
                elif result is not None:
                    code = getattr(result, 'code', "") or ""
                code = (code or "").strip().upper()
                if code:
                    return code
            except Exception:
                pass  # 引擎提取失败时静默回退到正则
        # 3. 内置正则兜底
        match = re.search(self.FALLBACK_CODE_PATTERN, file_name or "")
        return match.group(1).upper() if match else ""

    def _list_videos_in_folder(self, folder_path: str) -> list:
        """列出文件夹内的全部视频文件（按文件名排序，不递归）。

        参数：
            folder_path: 文件夹绝对路径
        返回：
            list[str]，视频文件绝对路径列表
        """
        if not folder_path or not os.path.isdir(folder_path):
            return []
        try:
            names = os.listdir(folder_path)
        except OSError:
            return []
        videos = [
            os.path.join(folder_path, name)
            for name in names
            if os.path.splitext(name)[1].lower() in constants.VIDEO_EXTENSIONS
            and os.path.isfile(os.path.join(folder_path, name))
        ]
        videos.sort(key=lambda p: os.path.basename(p).lower())
        return videos

    def _open_folder(self, folder_path: str) -> None:
        """在系统文件管理器中打开文件夹（Windows 使用 explorer）。

        参数：
            folder_path: 文件夹绝对路径
        """
        if not folder_path or not os.path.isdir(folder_path):
            messagebox.showwarning("路径不存在", "所在文件夹不存在：\n%s" % folder_path,
                                   parent=self._root_window())
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(['explorer', folder_path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
            else:
                subprocess.Popen(["xdg-open", folder_path])
        except (OSError, subprocess.SubprocessError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self._root_window())
            self._log(constants.LOG_LEVEL_ERROR, "打开文件夹失败: %s" % exc)

    # ------------------------------------------------------------------
    # 菜单构建（设计文档28.1/28.2节）
    # ------------------------------------------------------------------
    def show_thumbnail_menu(self, item: GalleryItem, x: int, y: int) -> None:
        """在指定坐标弹出缩略图右键菜单（设计文档28.1节）。

        参数：
            item: 图库缩略图项
            x:    弹菜单位置横坐标（屏幕坐标，由调用方传事件坐标）
            y:    弹菜单位置纵坐标（屏幕坐标，由调用方传事件坐标）
        """
        menu = self._create_menu(item)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _create_menu(self, item: GalleryItem) -> tk.Menu:
        """按缩略图项类型与匹配模式组装右键菜单（设计文档28.1/28.2节）。

        参数：
            item: 图库缩略图项
        返回：
            tk.Menu，组装完成的菜单对象
        """
        menu = tk.Menu(self.parent, tearoff=0)

        # ---------- 视频项（带视频才显示播放/采集/删除等） ----------
        if self._is_video_item(item):
            video_path = item.video_path or self._get_main_path(item)
            video_name = os.path.basename(video_path) or item.display_name

            if self.player_settings is not None:
                menu.add_command(
                    label="播放: %s" % video_name,
                    command=lambda: self._play_video(item))
            menu.add_command(
                label="🔍 采集本视频信息",
                command=lambda: self._crawl_single_video(item),
                state=self._crawl_menu_state())
            folder_name = os.path.basename(os.path.dirname(video_path)) or video_path
            menu.add_command(
                label="📁 采集%s下所有视频" % ("「%s」" % folder_name if folder_name else "所在文件夹"),
                command=lambda: self._crawl_folder_videos(item),
                state=self._crawl_menu_state())
            menu.add_command(
                label="📋 提取番号",
                command=lambda: self._extract_code_to_clipboard(item),
                state=(tk.NORMAL if self.pattern_engine is not None else tk.DISABLED))
            menu.add_separator()
            menu.add_command(
                label="✕ 删除视频...",
                command=lambda: self._delete_video(item))
            menu.add_command(
                label="🔄 更换缩略图...",
                command=lambda: self._replace_thumbnail(item))
            menu.add_separator()

        # ---------- 图片项 ----------
        if self._is_image_item(item):
            menu.add_command(
                label="查看原图",
                command=lambda: self._view_original_image(item))
            if item.match_mode == constants.MODE_IMAGE_ONLY or not self._is_video_item(item):
                menu.add_command(
                    label="🖼 设为以下视频的缩略图...",
                    command=lambda: self._set_as_video_thumbnail(item))
            menu.add_separator()

        # ---------- 标签子菜单 ----------
        relative_path = self._get_relative_path(item)
        tag_state = tk.NORMAL if (self.tag_manager is not None and relative_path) \
            else tk.DISABLED
        tag_menu = tk.Menu(menu, tearoff=0)
        if self.tag_manager is not None and relative_path:
            self._build_tag_submenu(tag_menu, item, relative_path)
        else:
            tag_menu.add_command(label="（无可用标签）", state=tk.DISABLED)
        menu.add_cascade(label="设置标签...", menu=tag_menu, state=tag_state)
        menu.add_separator()

        # ---------- 通用项 ----------
        menu.add_command(
            label="提取主文件夹名称",
            command=lambda: self._copy_main_folder_name(item))
        menu.add_command(
            label="复制图片路径",
            command=lambda: self._copy_image_path(item))
        menu.add_command(
            label="打开所在文件夹",
            command=lambda: self._open_item_folder(item))

        return menu

    def _crawl_menu_state(self) -> str:
        """采集类菜单项的可用状态（爬虫管理器与执行器均不可用时禁用）。"""
        if self.crawler_manager is not None or self.crawler_executor is not None:
            return tk.NORMAL
        return tk.DISABLED

    def _build_tag_submenu(self, tag_menu: tk.Menu, item: GalleryItem,
                           relative_path: str) -> None:
        """构建标签勾选子菜单：列出全部标签，勾选状态与数据库同步。

        参数：
            tag_menu:      标签子菜单对象
            item:          图库缩略图项
            relative_path: 文件相对路径（标签关联键）
        """
        # 新建标签入口
        tag_menu.add_command(label="＋ 新建标签...",
                             command=lambda: self._create_tag(item, relative_path))
        tag_menu.add_separator()
        try:
            all_tags = self.tag_manager.get_all_tags() or []
            current_tags = {t.tag_id for t in self.tag_manager.get_file_tags(relative_path)}
        except Exception as exc:
            self._log(constants.LOG_LEVEL_ERROR, "读取标签列表失败: %s" % exc)
            tag_menu.add_command(label="（读取失败）", state=tk.DISABLED)
            return
        if not all_tags:
            tag_menu.add_command(label="（暂无标签，请先新建）", state=tk.DISABLED)
            return
        tag_vars = []
        for tag in all_tags:
            var = tk.BooleanVar(value=tag.tag_id in current_tags)
            tag_vars.append(var)
            tag_menu.add_checkbutton(
                label="%s(%d)" % (tag.tag_name, tag.file_count),
                variable=var,
                command=lambda tid=tag.tag_id, v=var: self._toggle_tag(
                    relative_path, tid, v.get()))
        # 防止 BooleanVar 被垃圾回收导致勾选状态丢失
        tag_menu._tag_vars = tag_vars

    # ------------------------------------------------------------------
    # 通用菜单项动作
    # ------------------------------------------------------------------
    def _copy_main_folder_name(self, item: GalleryItem) -> None:
        """复制主文件夹名称到剪贴板（设计文档28.2节「提取主文件夹名称」）。"""
        main_path = self._get_main_path(item)
        folder_name = os.path.basename(os.path.dirname(main_path)) if main_path else ""
        if not folder_name:
            messagebox.showinfo("提示", "无法确定主文件夹。", parent=self._root_window())
            return
        self._copy_to_clipboard(folder_name)
        self._log(constants.LOG_LEVEL_INFO, "已复制文件夹名: %s" % folder_name)

    def _copy_image_path(self, item: GalleryItem) -> None:
        """复制图片路径到剪贴板（设计文档28.2节「复制图片路径」）。"""
        path = self._get_main_path(item)
        if not path:
            messagebox.showinfo("提示", "该缩略图没有可用的图片路径。",
                                parent=self._root_window())
            return
        self._copy_to_clipboard(path)
        self._log(constants.LOG_LEVEL_INFO, "已复制图片路径: %s" % path)

    def _open_item_folder(self, item: GalleryItem) -> None:
        """在系统资源管理器中打开所在文件夹（设计文档28.2节「打开所在文件夹」）。"""
        main_path = self._get_main_path(item)
        folder = os.path.dirname(main_path) if main_path else (item.folder_path or "")
        self._open_folder(folder)

    # ------------------------------------------------------------------
    # 视频菜单项动作
    # ------------------------------------------------------------------
    def _play_video(self, item: GalleryItem) -> None:
        """调用外部播放器播放视频（设计文档28.2节「播放: {文件名}」）。"""
        video_path = item.video_path or self._get_main_path(item)
        if not video_path or not os.path.exists(video_path):
            messagebox.showwarning("播放失败", "视频文件不存在：\n%s" % video_path,
                                   parent=self._root_window())
            return
        ok = False
        if self.player_settings is not None:
            try:
                ok = bool(self.player_settings.play_video(video_path))
            except Exception as exc:
                self._log(constants.LOG_LEVEL_ERROR, "播放出错: %s" % exc)
        if not ok:
            self._log(constants.LOG_LEVEL_WARNING, "播放失败: %s" % video_path)

    def _crawl_single_video(self, item: GalleryItem) -> None:
        """采集当前视频元数据（设计文档28.2节「🔍 采集本视频信息」）。

        网络请求在工作线程中执行，避免阻塞 UI。
        """
        video_path = item.video_path or self._get_main_path(item)
        if not video_path:
            return
        video_code = self._extract_code(video_path)
        if not video_code:
            if not messagebox.askyesno(
                    "未识别番号",
                    "未能从文件名识别番号，仍要尝试采集吗？\n（将不带番号直接请求）",
                    parent=self._root_window()):
                return

        def worker():
            """后台采集线程：优先爬虫管理器，其次爬虫执行器。"""
            try:
                if self.crawler_manager is not None and \
                        hasattr(self.crawler_manager, "crawl_single"):
                    result = self.crawler_manager.crawl_single(video_path, video_code)
                elif self.crawler_executor is not None and \
                        hasattr(self.crawler_executor, "crawl_single"):
                    result = self.crawler_executor.crawl_single(video_path, video_code)
                else:
                    self._log(constants.LOG_LEVEL_WARNING,
                              "无可用爬虫组件，采集取消: %s" % video_path)
                    return
                if result is not None:
                    self._log(constants.LOG_LEVEL_INFO,
                              "采集完成: %s（番号 %s）" % (video_path, video_code))
                else:
                    self._log(constants.LOG_LEVEL_WARNING,
                              "采集未获得结果: %s（番号 %s）" % (video_path, video_code))
            except Exception as exc:  # noqa: BLE001 - 第三方爬虫异常类型不确定
                self._log(constants.LOG_LEVEL_ERROR,
                          "采集失败: %s" % video_path, details=str(exc))

        threading.Thread(target=worker, daemon=True,
                         name="crawl-single").start()

    def _crawl_folder_videos(self, item: GalleryItem) -> None:
        """批量采集所在文件夹下所有视频（设计文档28.2节「📁 采集文件夹下所有视频」）。

        遍历视频所在文件夹（不递归），为每个视频提取番号并入队采集。
        """
        video_path = item.video_path or self._get_main_path(item)
        folder = os.path.dirname(video_path) if video_path else ""
        videos = self._list_videos_in_folder(folder)
        if not videos:
            messagebox.showinfo("提示", "所在文件夹下没有视频文件。",
                                parent=self._root_window())
            return
        queued = 0
        for path in videos:
            code = self._extract_code(path)
            try:
                if self.crawler_manager is not None and \
                        hasattr(self.crawler_manager, "add_to_queue"):
                    self.crawler_manager.add_to_queue(path, code)
                elif self.crawler_executor is not None and \
                        hasattr(self.crawler_executor, "add_to_queue"):
                    self.crawler_executor.add_to_queue(path, code)
                else:
                    break
                queued += 1
            except Exception as exc:  # noqa: BLE001 - 第三方爬虫异常类型不确定
                self._log(constants.LOG_LEVEL_ERROR,
                          "入队失败: %s" % path, details=str(exc))
        self._log(constants.LOG_LEVEL_INFO,
                  "批量采集入队: %s 共 %d/%d 个视频" % (folder, queued, len(videos)))
        messagebox.showinfo("批量采集",
                            "已将 %d 个视频加入采集队列。\n%s" % (queued, folder),
                            parent=self._root_window())

    def _extract_code_to_clipboard(self, item: GalleryItem) -> None:
        """提取视频番号并复制到剪贴板（设计文档28.2节「📋 提取番号」）。"""
        video_path = item.video_path or self._get_main_path(item)
        code = self._extract_code(video_path)
        if not code:
            messagebox.showinfo("提取番号", "未能从文件名中识别出番号。",
                                parent=self._root_window())
            return
        self._copy_to_clipboard(code)
        self._log(constants.LOG_LEVEL_INFO, "已提取番号: %s（来自 %s）"
                  % (code, os.path.basename(video_path)))
        messagebox.showinfo("提取番号", "番号已复制到剪贴板：\n%s" % code,
                            parent=self._root_window())

    def _delete_video(self, item: GalleryItem) -> None:
        """删除视频文件及数据库记录（设计文档28.2节「✕ 删除视频...」）。

        谨慎实现：两次确认后才执行删除；文件删除失败时中止数据库清理。
        """
        video_path = item.video_path or self._get_main_path(item)
        if not video_path or not os.path.isfile(video_path):
            messagebox.showwarning("删除视频", "视频文件不存在：\n%s" % video_path,
                                   parent=self._root_window())
            return
        video_name = os.path.basename(video_path)
        # 第一次确认
        if not messagebox.askyesno("删除视频",
                                   "确定删除视频吗？\n%s" % video_name,
                                   parent=self._root_window()):
            return
        # 第二次确认（明确告知不可恢复）
        if not messagebox.askyesno(
                "二次确认",
                "即将永久删除文件（不可恢复）：\n%s\n\n"
                "同时删除其数据库记录、演员关联与标签关联。确定继续？" % video_path,
                icon="warning", parent=self._root_window()):
            return
        # 先删文件，失败则中止，避免数据库与磁盘不一致
        try:
            os.remove(video_path)
        except OSError as exc:
            messagebox.showerror("删除失败", "文件删除失败：\n%s" % exc,
                                 parent=self._root_window())
            self._log(constants.LOG_LEVEL_ERROR, "删除视频失败: %s" % video_path,
                      details=str(exc))
            return
        # 再清理数据库记录（含演员/标签关联，由 db_manager 负责级联）
        relative_path = self._get_relative_path(item)
        if self.db_manager is not None and relative_path:
            try:
                self.db_manager.delete_file_record(relative_path)
            except Exception as exc:  # noqa: BLE001 - 数据库异常类型不确定
                self._log(constants.LOG_LEVEL_ERROR,
                          "删除数据库记录失败: %s" % relative_path, details=str(exc))
        self._log(constants.LOG_LEVEL_INFO, "已删除视频: %s" % video_path)
        self._refresh_gallery()

    def _replace_thumbnail(self, item: GalleryItem) -> None:
        """更换视频缩略图（设计文档28.2节「🔄 更换缩略图...」）。

        选择一张图片并复制为与视频同名的图片文件（视频名.ext）。
        """
        video_path = item.video_path or self._get_main_path(item)
        if not video_path:
            return
        image_path = filedialog.askopenfilename(
            parent=self._root_window(),
            title="选择新的缩略图",
            filetypes=[("图片文件", "*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.webp"),
                       ("所有文件", "*.*")])
        if not image_path:
            return
        self._copy_image_as_video_thumbnail(image_path, video_path)

    def _copy_image_as_video_thumbnail(self, image_path: str,
                                       video_path: str) -> bool:
        """把指定图片复制为视频同名缩略图（覆盖前需用户确认）。

        参数：
            image_path: 源图片绝对路径
            video_path: 目标视频绝对路径
        返回：
            bool，复制成功返回 True
        """
        if not os.path.isfile(image_path):
            messagebox.showwarning("更换缩略图", "所选图片不存在：\n%s" % image_path,
                                   parent=self._root_window())
            return False
        target = os.path.splitext(video_path)[0] + os.path.splitext(image_path)[1]
        try:
            if os.path.abspath(target) != os.path.abspath(image_path):
                if os.path.exists(target) and not messagebox.askyesno(
                        "覆盖确认", "缩略图已存在，是否覆盖？\n%s" % target,
                        parent=self._root_window()):
                    return False
                shutil.copyfile(image_path, target)
        except OSError as exc:
            messagebox.showerror("更换缩略图", "复制失败：\n%s" % exc,
                                 parent=self._root_window())
            self._log(constants.LOG_LEVEL_ERROR, "更换缩略图失败: %s" % video_path,
                      details=str(exc))
            return False
        self._log(constants.LOG_LEVEL_INFO, "已更换缩略图: %s" % target)
        self._refresh_gallery()
        return True

    # ------------------------------------------------------------------
    # 图片菜单项动作
    # ------------------------------------------------------------------
    def _collect_image_paths(self, item: GalleryItem):
        """收集图片浏览所需的图片列表与起始索引（与设计文档17.2节一致）。

        参数：
            item: 图库缩略图项
        返回：
            tuple(list[str], int)，图片路径列表与起始索引
        """
        if item.match_mode in (constants.MODE_IMAGE_ONLY, constants.MODE_VIDEO_LINKED):
            return list(item.image_paths), item.current_image_index
        if item.match_mode in (constants.MODE_COVER_MATCH,
                               constants.MODE_EXCLUSIVE_VIDEO,
                               constants.MODE_COVER_PRIORITY):
            folder = os.path.dirname(item.video_path or item.thumbnail_path)
            image_paths = self._list_images_in_folder(folder)
            start_index = image_paths.index(item.thumbnail_path) \
                if item.thumbnail_path in image_paths else 0
            return image_paths, start_index
        if item.is_folder and item.folder_path:
            return self._list_images_in_folder(item.folder_path), 0
        if item.thumbnail_path:
            return [item.thumbnail_path], 0
        return list(item.image_paths), item.current_image_index

    def _list_images_in_folder(self, folder_path: str) -> list:
        """列出文件夹内的全部图片文件（按文件名排序，不递归）。"""
        if not folder_path or not os.path.isdir(folder_path):
            return []
        try:
            names = os.listdir(folder_path)
        except OSError:
            return []
        images = [
            os.path.join(folder_path, name)
            for name in names
            if os.path.splitext(name)[1].lower() in constants.IMAGE_EXTENSIONS
            and os.path.isfile(os.path.join(folder_path, name))
        ]
        images.sort(key=lambda p: os.path.basename(p).lower())
        return images

    def _view_original_image(self, item: GalleryItem) -> None:
        """打开图片浏览窗口查看原图（设计文档28.2节「查看原图」）。

        优先经 gallery_viewer 的公开入口打开；否则惰性导入
        image_viewer.ImageViewerWindow 直接创建。
        """
        image_paths, start_index = self._collect_image_paths(item)
        if not image_paths:
            messagebox.showinfo("查看原图", "没有找到可浏览的图片。",
                                parent=self._root_window())
            return
        # 1. 图库查看器公开入口（若09号模块提供）
        opener = getattr(self.gallery_viewer, "open_image_viewer", None)
        if callable(opener):
            try:
                opener(image_paths, start_index)
                return
            except Exception as exc:
                self._log(constants.LOG_LEVEL_WARNING,
                          "经图库打开图片窗口失败，改由本模块创建: %s" % exc)
        # 2. 惰性导入图片浏览窗口（13号模块可能尚未实现）
        try:
            from image_viewer import ImageViewerWindow
        except Exception as exc:  # noqa: BLE001 - 依赖模块可能缺失
            messagebox.showerror("查看原图",
                                 "图片浏览窗口模块不可用：\n%s" % exc,
                                 parent=self._root_window())
            self._log(constants.LOG_LEVEL_ERROR, "图片浏览窗口模块不可用",
                      details=str(exc))
            return
        try:
            viewer = ImageViewerWindow(
                parent=self._root_window(),
                image_paths=image_paths,
                start_index=start_index,
                title="浏览 - %s" % item.display_name)
            viewer.show()
        except Exception as exc:  # noqa: BLE001 - 窗口创建异常类型不确定
            messagebox.showerror("查看原图", "打开图片浏览窗口失败：\n%s" % exc,
                                 parent=self._root_window())
            self._log(constants.LOG_LEVEL_ERROR, "打开图片浏览窗口失败",
                      details=str(exc))

    def _set_as_video_thumbnail(self, item: GalleryItem) -> None:
        """将当前图片设为指定视频的缩略图（设计文档28.2节「🖼 设为以下视频的缩略图...」）。

        弹出小对话框列出图片所在文件夹内的视频供选择，确认后把图片
        复制为所选视频的同名片。
        """
        image_path = self._get_main_path(item)
        folder = os.path.dirname(image_path) if image_path else (item.folder_path or "")
        videos = self._list_videos_in_folder(folder)
        if not videos:
            messagebox.showinfo("设为缩略图", "图片所在文件夹下没有视频文件。",
                                parent=self._root_window())
            return
        dialog = _VideoSelectDialog(self._root_window(), videos)
        selected = dialog.show_modal()
        if not selected:
            return
        self._copy_image_as_video_thumbnail(image_path, selected)

    # ------------------------------------------------------------------
    # 标签菜单项动作
    # ------------------------------------------------------------------
    def _create_tag(self, item: GalleryItem, relative_path: str) -> None:
        """在标签子菜单中新建标签并自动关联到当前文件。

        参数：
            item:          图库缩略图项（预留）
            relative_path: 文件相对路径
        """
        name = simpledialog.askstring("新建标签", "请输入标签名称：",
                                      parent=self._root_window())
        if not name or not name.strip():
            return
        try:
            tag = self.tag_manager.create_tag(name.strip())
        except Exception as exc:  # noqa: BLE001 - 数据库异常类型不确定
            messagebox.showwarning("新建标签", "标签创建失败：\n%s" % exc,
                                   parent=self._root_window())
            return
        if tag is None:
            messagebox.showwarning("新建标签", "标签创建失败。", parent=self._root_window())
            return
        try:
            self.tag_manager.add_tag_to_file(relative_path, tag.tag_id)
        except Exception as exc:  # noqa: BLE001 - 数据库异常类型不确定
            self._log(constants.LOG_LEVEL_WARNING, "新标签关联文件失败: %s" % exc)

    def _toggle_tag(self, relative_path: str, tag_id: int, checked: bool) -> None:
        """勾选/取消标签时同步文件-标签关联（设计文档28.2节「设置标签...」）。

        参数：
            relative_path: 文件相对路径
            tag_id:        标签ID
            checked:       True=添加标签，False=移除标签
        """
        if self.tag_manager is None:
            return
        try:
            if checked:
                self.tag_manager.add_tag_to_file(relative_path, tag_id)
            else:
                self.tag_manager.remove_tag_from_file(relative_path, tag_id)
        except Exception as exc:  # noqa: BLE001 - 数据库异常类型不确定
            self._log(constants.LOG_LEVEL_ERROR,
                      "更新文件标签失败: %s tag_id=%s" % (relative_path, tag_id),
                      details=str(exc))

    # ------------------------------------------------------------------
    # 图库联动
    # ------------------------------------------------------------------
    def _refresh_gallery(self) -> None:
        """删除/更换缩略图后刷新图库显示。"""
        if self.gallery_viewer is None:
            return
        try:
            self.gallery_viewer.refresh()
        except Exception as exc:  # noqa: BLE001 - 刷新失败不影响主流程
            self._log(constants.LOG_LEVEL_WARNING, "刷新图库失败: %s" % exc)


class _VideoSelectDialog(tk.Toplevel):
    """选择视频的小对话框：列出文件夹内视频，单选后返回所选路径。"""

    def __init__(self, parent: tk.Widget, videos: list):
        """初始化选择对话框。

        参数：
            parent: 父窗口
            videos: 视频绝对路径列表（非空）
        """
        super().__init__(parent)
        self.title("选择视频")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result = None
        self._videos = list(videos or [])

        tk.Label(self, text="将当前图片设为哪个视频的缩略图？").pack(
            padx=12, pady=(12, 6))
        list_frame = tk.Frame(self)
        list_frame.pack(padx=12, pady=6)
        self._listbox = tk.Listbox(list_frame, width=56, height=10,
                                   activestyle="dotbox")
        for path in self._videos:
            self._listbox.insert(tk.END, os.path.basename(path))
        self._listbox.selection_set(0)
        self._listbox.pack(side=tk.LEFT)
        scrollbar = tk.Scrollbar(list_frame, command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self._listbox.bind("<Double-1>", lambda _e: self._on_ok())

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=(6, 12))
        tk.Button(btn_frame, text="确定", width=10,
                  command=self._on_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="取消", width=10,
                  command=self._on_cancel).pack(side=tk.LEFT, padx=6)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        # 居中显示
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))

    def _on_ok(self):
        """确认选择：记录所选视频路径并关闭对话框。"""
        selection = self._listbox.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个视频。", parent=self)
            return
        index = selection[0]
        if 0 <= index < len(self._videos):
            self.result = self._videos[index]
        self.destroy()

    def _on_cancel(self):
        """取消选择并关闭对话框。"""
        self.result = None
        self.destroy()

    def show_modal(self):
        """模态显示对话框，返回所选视频绝对路径（取消时返回 None）。"""
        self.grab_set()
        self.focus_set()
        self.wait_window(self)
        return self.result
