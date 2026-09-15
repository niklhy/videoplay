#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图库浏览模块 - 完全修复版
修复：鼠标滚轮滚动、右键菜单弹出、缓存保存
"""
import tkinter as tk
from tkinter import scrolledtext
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageDraw, ImageFont
import os
import threading
import re

try:
    from ui_components import ToolTip
except ImportError:
    ToolTip = None

class GalleryViewer:
    """图库查看器 - 增强视觉标识版"""
    
    def __init__(self, parent_frame, config_manager, db_manager, video_player, image_viewer, 
                 on_select_callback, on_double_click_callback, debug_utils=None,
                 on_tags_changed=None, get_browser_folder=None, on_set_tag=None,
                 on_relation_request=None, on_relation_removed=None,
                 on_refresh_request=None):
        self.parent = parent_frame
        self.config = config_manager
        self.db = db_manager
        self.video_player = video_player
        self.image_viewer = image_viewer
        self.on_select = on_select_callback
        self.on_double_click = on_double_click_callback
        self.debug = debug_utils
        self.on_tags_changed = on_tags_changed
        self.get_browser_folder = get_browser_folder
        self.on_set_tag = on_set_tag
        self.on_relation_request = on_relation_request
        self.on_relation_removed = on_relation_removed  # 保存回调引用
        self.on_refresh_request = on_refresh_request  # 统一刷新回调
        self._on_load_complete_callbacks = []  # 加载完成后的回调列表

        self._selected_images = set()
        self._multi_select_mode = False
        self._image_files = []
        self._current_folder = None
        self._load_generation = 0
        self._scroll_save_timer = None
        self._pending_widgets = []
        self._current_item_index = 0
        self._crawler_target_video = None  # 新增：当前采集目标视频
        self._synced_video_paths = set()  # 新增：已联网同步的视频路径集合
        
        # 新增：信息面板展开状态
        self._info_expanded = False
        self._current_info_video_path = None
        self._info_expand_data = None  # 存储展开区域的数据，避免折叠时频繁更新UI
        self._info_update_thread = None  # 防止重复启动后台查询线程

        self._items_dict = {}  # 新增：存储当前显示的字典数据
        self._pending_updates = []  # 新增：待更新的项队列
        self._update_timer = None  # 新增：批量更新定时器

        self._create_ui()
    
    def _create_ui(self):
        """创建UI - 修复滚轮绑定，添加顶部信息面板"""
        # ========== 修改：重构信息面板为左右分栏，支持展开/折叠 ==========
        self.info_panel = ttk.Frame(self.parent, relief=tk.GROOVE, borderwidth=2)
        self.info_panel.pack(fill=tk.X, padx=5, pady=(5, 0))
        
        # 可点击的标题栏
        self.info_title_lbl = ttk.Label(
            self.info_panel,
            text="视频信息 ▼",
            font=("微软雅黑", 9, "bold"),
            cursor="hand2",
            padding=(5, 2)
        )
        self.info_title_lbl.pack(fill=tk.X)
        self.info_title_lbl.bind("<Button-1>", lambda e: self._toggle_info_panel())

        # 主容器使用水平布局 - 这一行必须在前面！
        info_main_frame = ttk.Frame(self.info_panel)
        info_main_frame.pack(fill=tk.X, expand=True, padx=5, pady=2)

        # 左侧：信息区（单行显示）
        info_left_frame = ttk.Frame(info_main_frame)
        info_left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 所有信息合并为一行显示
        info_line_frame = ttk.Frame(info_left_frame)
        info_line_frame.pack(fill=tk.X, pady=2)
        
        # 番号显示（蓝色加粗）
        self.info_code_lbl = ttk.Label(info_line_frame, text="番号: -", font=("微软雅黑", 10, "bold"),
                                    foreground="#0066CC")
        self.info_code_lbl.pack(side=tk.LEFT, padx=(0, 15))
        
        # 演员标签
        self.info_actors_lbl = ttk.Label(info_line_frame, text="演员: -", font=("微软雅黑", 9))
        self.info_actors_lbl.pack(side=tk.LEFT, padx=(0, 15))
        
        # 时长
        self.info_duration_lbl = ttk.Label(info_line_frame, text="时长: -", font=("微软雅黑", 9))
        self.info_duration_lbl.pack(side=tk.LEFT, padx=(0, 15))
        
        # 大小
        self.info_size_lbl = ttk.Label(info_line_frame, text="大小: -", font=("微软雅黑", 9))
        self.info_size_lbl.pack(side=tk.LEFT, padx=(0, 15))
        
        # 日期
        self.info_date_lbl = ttk.Label(info_line_frame, text="日期: -", font=("微软雅黑", 9))
        self.info_date_lbl.pack(side=tk.LEFT)
        
        # 展开信息区域（默认隐藏）—— 放在 info_panel 下而非 info_left_frame 下，
        # 避免影响左侧信息区的内部布局计算
        self.info_expand_frame = ttk.Frame(self.info_panel)
        
        # 展开区域的详细内容
        self.info_title_full_lbl = ttk.Label(
            self.info_expand_frame, text="", font=("微软雅黑", 9, "bold"),
            wraplength=600, justify=tk.LEFT, foreground="#333333"
        )
        self.info_title_full_lbl.pack(fill=tk.X, padx=5, pady=(4, 0))
        
        self.info_actors_full_lbl = ttk.Label(
            self.info_expand_frame, text="", font=("微软雅黑", 9),
            wraplength=600, justify=tk.LEFT, foreground="#333333"
        )
        self.info_actors_full_lbl.pack(fill=tk.X, padx=5, pady=(2, 0))
        
        self.info_path_lbl = ttk.Label(
            self.info_expand_frame, text="", font=("微软雅黑", 8),
            wraplength=600, justify=tk.LEFT, foreground="gray"
        )
        self.info_path_lbl.pack(fill=tk.X, padx=5, pady=(2, 0))
        
        self.info_thumb_lbl = ttk.Label(
            self.info_expand_frame, text="", font=("微软雅黑", 8),
            wraplength=600, justify=tk.LEFT, foreground="gray"
        )
        self.info_thumb_lbl.pack(fill=tk.X, padx=5, pady=(2, 0))

        # ========== 新增：右侧采集控制区（默认隐藏）==========
        self.crawler_frame = ttk.Frame(info_main_frame, padding=5)
        # 不立即pack，需要时才显示
        
        # 番号输入框和标签
        ttk.Label(self.crawler_frame, text="番号:", font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=2)
        self.crawler_code_entry = ttk.Entry(self.crawler_frame, width=20, font=("微软雅黑", 9))
        self.crawler_code_entry.pack(side=tk.LEFT, padx=2)
        self.crawler_code_entry.bind("<Return>", lambda e: self._do_crawl_video_info())
        
        # 提交按钮
        self.crawler_submit_btn = ttk.Button(
            self.crawler_frame, 
            text="🔍 开始采集", 
            command=self._do_crawl_video_info,
            width=10
        )
        self.crawler_submit_btn.pack(side=tk.LEFT, padx=5)
        
        # 取消按钮
        self.crawler_cancel_btn = ttk.Button(
            self.crawler_frame, 
            text="✕", 
            command=self._hide_crawler_frame,
            width=3
        )
        self.crawler_cancel_btn.pack(side=tk.LEFT, padx=2)

        # 原有的滚动框架（保持不变）
        scroll_frame = ttk.Frame(self.parent)
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.canvas = tk.Canvas(scroll_frame, bg="#f5f5f5", highlightthickness=0)
        scrollbar_y = ttk.Scrollbar(scroll_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar_x = ttk.Scrollbar(scroll_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        
        self.canvas.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.images_frame = tk.Frame(self.canvas, bg="#f5f5f5")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.images_frame, anchor="nw")
        
        self.images_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        self._bind_mousewheel_recursive(self.canvas)
        self._bind_mousewheel_recursive(self.images_frame)
        
        self.progress = ttk.Progressbar(self.parent, mode='determinate')
        self.progress.pack(fill=tk.X, padx=5, pady=2)

    def _toggle_info_panel(self):
        """切换信息面板的展开/折叠状态"""
        self._info_expanded = not self._info_expanded
        
        if self._info_expanded:
            self.info_title_lbl.config(text="视频信息 ▲")
            self.info_expand_frame.pack(fill=tk.X, pady=(4, 0))
            # 展开时根据缓存数据刷新内容
            if self._info_expand_data:
                self._apply_info_expand_data()
        else:
            self.info_title_lbl.config(text="视频信息 ▼")
            self.info_expand_frame.pack_forget()
    
    def _apply_info_expand_data(self):
        """将缓存的展开数据应用到UI（仅在展开状态下调用）"""
        if not self._info_expand_data:
            return
        data = self._info_expand_data
        try:
            title_text = data.get('title_text', '')
            actors_text = data.get('actors_text', '')
            video_path = data.get('video_path', '')
            img_path = data.get('img_path', '')
            
            new_title = f"标题: {title_text}" if title_text and title_text != "-" else ""
            new_actors = f"演员: {actors_text}" if actors_text and actors_text != "-" else ""
            new_path = f"路径: {video_path}" if video_path else ""
            new_thumb = f"缩略图: {img_path}" if img_path and img_path != "无缩略图" else ""
            
            # 只在文本实际变化时才调用 config，减少 tkinter 内部重算
            if self.info_title_full_lbl.cget("text") != new_title:
                self.info_title_full_lbl.config(text=new_title)
            if self.info_actors_full_lbl.cget("text") != new_actors:
                self.info_actors_full_lbl.config(text=new_actors)
            if self.info_path_lbl.cget("text") != new_path:
                self.info_path_lbl.config(text=new_path)
            if self.info_thumb_lbl.cget("text") != new_thumb:
                self.info_thumb_lbl.config(text=new_thumb)
            
            # 动态调整 wraplength（只在宽度变化时）
            panel_width = self.info_panel.winfo_width()
            if panel_width > 100:
                wrap = panel_width - 80
                # 缓存上次设置的 wraplength，避免重复设置
                if getattr(self, '_last_wraplength', None) != wrap:
                    self._last_wraplength = wrap
                    self.info_title_full_lbl.config(wraplength=wrap)
                    self.info_actors_full_lbl.config(wraplength=wrap)
                    self.info_path_lbl.config(wraplength=wrap)
                    self.info_thumb_lbl.config(wraplength=wrap)
        except Exception as e:
            if self.debug:
                self.debug.print(f"应用展开数据失败: {e}")
    
    def _refresh_info_expand_content(self, title_text, actors_text, duration_text, 
                                     size_text, date_text, code_text, video_path, img_path):
        """刷新展开区域的内容：折叠时只缓存数据，展开时才更新widget"""
        # 始终缓存数据，供展开时使用
        self._info_expand_data = {
            'title_text': title_text,
            'actors_text': actors_text,
            'duration_text': duration_text,
            'size_text': size_text,
            'date_text': date_text,
            'code_text': code_text,
            'video_path': video_path,
            'img_path': img_path
        }
        # 只有展开状态下才实际更新UI
        if self._info_expanded:
            self._apply_info_expand_data()

    def _update_info_panel(self, video_path, force_refresh=False):
        """
        更新顶部信息面板
        
        Args:
            video_path: 视频文件路径
            force_refresh: 是否强制从数据库读取（而非缓存）
        """
        self._current_info_video_path = video_path
        
        if not video_path:
            # 清空显示
            self.info_actors_lbl.config(text="演员: -")
            self.info_duration_lbl.config(text="时长: -")
            self.info_size_lbl.config(text="大小: -")
            self.info_date_lbl.config(text="日期: -")
            self.info_code_lbl.config(text="番号: -")
            self._hide_crawler_frame()
            if self._info_expanded:
                self._toggle_info_panel()
            self._info_expand_data = None
            
            if self.debug:
                self.debug.print("[信息面板] 已清空")
            return
        
        # 检查是否启用了调试模式
        debug_enabled = self.debug and self.debug.debug_mode
        
        if not self.db or not getattr(self.db, 'is_ready', False):
            if debug_enabled:
                self.debug.print(f"[信息面板] 数据库未就绪，无法查询: {os.path.basename(video_path)}")
            return
        
        # 避免重复查询
        if getattr(self, '_info_update_target', None) == video_path and \
        self._info_update_thread and self._info_update_thread.is_alive():
            return
        self._info_update_target = video_path
        
        def load_and_update():
            try:
                # 获取视频文件在数据库中的记录
                db_file = self.db.get_file_by_path(video_path)

                # ========== 诊断日志 ==========
                if debug_enabled:
                    self.debug.print(f"[信息面板查询] 目标: {os.path.basename(video_path)}")
                    self.debug.print(f"  查询路径: {video_path}")
                    self.debug.print(f"  规范化: {os.path.normpath(video_path)}")
                    self.debug.print(f"  数据库记录: {'找到' if db_file else '未找到'}")

                    if not db_file and self.db and hasattr(self.db, '_cache'):
                        cache_files = self.db._cache.get('files', {})
                        self.debug.print(f"  缓存总文件数: {len(cache_files)}")
                        # 查找同文件夹下的缓存路径
                        folder = os.path.dirname(video_path)
                        folder_norm = os.path.normpath(folder)
                        similar = [p for p in cache_files.keys() 
                                  if os.path.normpath(os.path.dirname(p)) == folder_norm]
                        if similar:
                            self.debug.print(f"  同文件夹缓存 ({len(similar)}个):")
                            for p in similar[:5]:
                                self.debug.print(f"    - {p}")
                        else:
                            # 尝试同名匹配
                            vname = os.path.basename(video_path).lower()
                            matches = [p for p in cache_files.keys() 
                                      if os.path.basename(p).lower() == vname]
                            if matches:
                                self.debug.print(f"  同名文件在其他位置 ({len(matches)}个):")
                                for p in matches[:5]:
                                    self.debug.print(f"    - {p}")
                            else:
                                self.debug.print(f"  缓存中无匹配")

                    if db_file:
                        self.debug.print(f"  文件ID: {db_file.get('file_id', 'N/A')}")
                        self.debug.print(f"  番号: {db_file.get('video_code', '无')}")
                if debug_enabled:
                    self.debug.print(f"[信息面板查询] {os.path.basename(video_path)}")
                    self.debug.print(f"  数据库记录: {'找到' if db_file else '未找到'}")
                    if db_file:
                        self.debug.print(f"  文件ID: {db_file.get('file_id', 'N/A')}")
                        self.debug.print(f"  番号: {db_file.get('video_code', '无')}")
                        self.debug.print(f"  标题: {(db_file.get('video_title') or '无')[:50]}")
                
                if not db_file:
                    if debug_enabled:
                        self.debug.print(f"  数据库中未找到视频记录")
                    return
                
                file_id = db_file.get('file_id')
                if not file_id:
                    return
                
                # 通过file_actors关联表查询演员（包含中文名）
                actors_display = []
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    
                    # 使用JOIN查询演员名称和中文名
                    cursor.execute("""
                        SELECT a.actor_name, a.actor_name_cn 
                        FROM actors a
                        JOIN file_actors fa ON a.actor_id = fa.actor_id
                        WHERE fa.file_id = ?
                        ORDER BY fa.bind_time
                    """, (file_id,))
                    
                    rows = cursor.fetchall()
                    for row in rows:
                        name = row['actor_name'] or ""
                        name_cn = row['actor_name_cn'] or ""
                        
                        # 格式化显示：演员名（中文名）或仅演员名
                        if name and name_cn:
                            actors_display.append(f"{name}（{name_cn}）")
                        elif name:
                            actors_display.append(name)
                        elif name_cn:
                            actors_display.append(name_cn)
                    
                    cursor.close()
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"查询演员失败: {e}")
                    actors_display = []
                
                # 格式化演员文本 - 使用中文逗号分隔
                actors_text = "，".join(actors_display) if actors_display else "-"
                
                # 获取其他字段
                duration = db_file.get('duration', 0)
                if duration:
                    mins = duration // 60
                    secs = duration % 60
                    duration_text = f"{mins}分{secs}秒" if secs else f"{mins}分钟"
                else:
                    duration_text = "-"
                
                # 文件大小格式化
                file_size = db_file.get('file_size', 0)
                if file_size:
                    size_gb = file_size / (1024 * 1024 * 1024)
                    size_mb = file_size / (1024 * 1024)
                    if size_gb >= 1:
                        size_text = f"{size_gb:.2f} GB"
                    else:
                        size_text = f"{size_mb:.1f} MB"
                else:
                    size_text = "-"
                
                date_text = db_file.get('release_date', '-') or "-"
                code_text = db_file.get('video_code', '-') or "-"  # 番号
                title_text = db_file.get('video_title', '-') or "-"  # 标题（用于tooltip）
                
                # 获取缩略图路径
                img_path = self._find_video_thumbnail(video_path)
                if not img_path:
                    img_path = "无缩略图"
                
                # 文件名（不含路径）
                file_name = db_file.get('file_name', os.path.basename(video_path))
                
                # 更新UI（在主线程中执行）
                def do_update_ui():
                    self.info_actors_lbl.config(text=f"演员: {actors_text}")
                    self.info_duration_lbl.config(text=f"时长: {duration_text}")
                    self.info_size_lbl.config(text=f"大小: {size_text}")
                    self.info_date_lbl.config(text=f"日期: {date_text}")
                    self.info_code_lbl.config(text=f"番号: {code_text}")
                    self._refresh_info_expand_content(
                        title_text, actors_text, duration_text, size_text, 
                        date_text, code_text, video_path, img_path
                    )
                
                self.parent.after(0, do_update_ui)
                
                # ========== 关键：恢复日志记录功能 ==========
                if self.debug:
                    def log_video_info():
                        self.debug.print("=" * 60)
                        self.debug.print(f"[选中视频] {file_name}")
                        self.debug.print(f"  📍 视频路径: {video_path}")
                        self.debug.print(f"  🖼️  缩略图路径: {img_path}")
                        self.debug.print(f"  🎬 视频标题: {title_text}")
                        self.debug.print(f"  🆔 番号: {code_text}")
                        self.debug.print(f"  📅 发布日期: {date_text}")
                        self.debug.print(f"  👥 演员: {actors_text}")
                        self.debug.print(f"  💾 文件大小: {size_text} ({file_size} 字节)")
                        self.debug.print(f"  ⏱️ 视频时长: {duration_text} ({duration}秒)")
                        self.debug.print(f"  🗄️  数据库ID: {file_id}")
                        self.debug.print("=" * 60)
                    
                    self.parent.after(0, log_video_info)
                    
            except Exception as e:
                if debug_enabled:
                    self.debug.print(f"更新信息面板失败: {e}")
                    import traceback
                    traceback.print_exc()
        
        # 在后台线程中执行数据库查询
        self._info_update_thread = threading.Thread(target=load_and_update, daemon=True)
        self._info_update_thread.start()

    def _hide_crawler_frame(self):
        """隐藏采集控制区"""
        if hasattr(self, 'crawler_frame'):
            self.crawler_frame.pack_forget()
        self._crawler_target_video = None

    def _do_crawl_video_info(self):
        """执行视频信息采集"""
        code = self.crawler_code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入番号/编号")
            return
        
        video_path = getattr(self, '_crawler_target_video', None)
        if not video_path:
            return
        
        # 隐藏采集控件
        self._hide_crawler_frame()
        
        # 调用主程序的采集回调
        if hasattr(self, 'on_collect_video_info') and self.on_collect_video_info:
            self.on_collect_video_info(video_path, code)

    def _on_mousewheel(self, event):
        """鼠标滚轮事件处理"""
        try:
            if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                scroll = -1
            elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                scroll = 1
            else:
                scroll = 0
            
            if scroll != 0:
                self.canvas.yview_scroll(scroll, "units")
            return "break"
        except Exception as e:
            if self.debug:
                self.debug.print(f"滚轮错误: {e}")
            return None

    def _bind_mousewheel_recursive(self, widget):
        """递归绑定滚轮事件"""
        widget.bind("<MouseWheel>", self._on_mousewheel)
        widget.bind("<Button-4>", self._on_mousewheel)
        widget.bind("<Button-5>", self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child)

    def _on_frame_configure(self, event=None):
        width = self.images_frame.winfo_reqwidth()
        height = self.images_frame.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, width, height))
    
    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)
    
    def get_scroll_y(self):
        return self.canvas.yview()[0]
    
    def set_scroll_y(self, y):
        self.canvas.yview_moveto(float(y))
    
    def bind_scroll_save(self, save_callback):
        def on_scroll(first, last):
            if self._scroll_save_timer:
                self.parent.after_cancel(self._scroll_save_timer)
            self._scroll_save_timer = self.parent.after(500, 
                lambda: save_callback(self.get_scroll_y()))
        
        original_yscroll = self.canvas.cget("yscrollcommand")
        def combined_scroll(first, last):
            if original_yscroll:
                try:
                    if callable(original_yscroll):
                        original_yscroll(first, last)
                    else:
                        self.canvas.tk.call(original_yscroll, first, last)
                except:
                    pass
            on_scroll(first, last)
        
        self.canvas.config(yscrollcommand=combined_scroll)
    
    def _create_thumbnail(self, img_path, max_width):
        try:
            ext = os.path.splitext(img_path)[1].lower()
            
            if img_path == "__VIDEO_PLACEHOLDER__":
                return self._create_video_placeholder(max_width)
            
            if ext == '.pdf':
                return None
            
            if not os.path.exists(img_path):
                return None
            
            with Image.open(img_path) as img:
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                w, h = img.size
                ratio = max_width / w
                new_size = (max_width, int(h * ratio))
                img.thumbnail(new_size, Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img)
        except Exception as e:
            if self.debug:
                self.debug.print(f"缩略图创建失败 {img_path}: {e}")
            return None
    
    def _create_video_placeholder(self, max_width):
        try:
            size = (max_width, int(max_width * 0.6))
            img = Image.new('RGB', size, color='#2C3E50')
            draw = ImageDraw.Draw(img)
            
            try:
                font = ImageFont.truetype("msyh.ttc", 20)
            except:
                try:
                    font = ImageFont.truetype("arial.ttf", 20)
                except:
                    font = ImageFont.load_default()
            
            text = "VIDEO"
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (size[0] - text_width) / 2
            y = (size[1] - text_height) / 2
            
            draw.text((x, y), text, fill='#ECF0F1', font=font)
            draw.rectangle([(0, 0), (size[0]-1, size[1]-1)], outline='#34495E', width=2)
            
            return ImageTk.PhotoImage(img)
        except Exception as e:
            if self.debug:
                self.debug.print(f"创建视频占位图失败: {e}")
            return None
    
    def _find_video_for_image(self, img_path):
        """查找图片对应的视频文件 - 优先同名匹配"""
        from core import PathUtils
        
        folder = os.path.dirname(img_path)
        img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
        
        videos = PathUtils.find_video_files(folder)
        if not videos:
            return None
        
        for video_path in videos:
            video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
            if video_basename == img_basename:
                return video_path
        
        return videos[0] if len(videos) == 1 else None
    
    def _find_video_thumbnail(self, video_path):
        """查找视频缩略图（同名图片或通用封面）"""
        video_dir = os.path.dirname(video_path)
        video_name = os.path.splitext(os.path.basename(video_path))[0].lower()
        
        # 同名图片
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', 
                   '.JPG', '.JPEG', '.PNG', '.GIF', '.BMP', '.WEBP']:
            path = os.path.join(video_dir, video_name + ext)
            if os.path.exists(path):
                return path
        
        # 通用封面
        for ext in ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']:
            for name in ['cover', 'folder', 'poster', 'thumb', 'preview']:
                path = os.path.join(video_dir, name + ext)
                if os.path.exists(path):
                    return path
        
        return None
    
    def _add_thumbnail_widget(self, index, folder_name, img_path, thumbnail, 
                            video_path=None, primary_video_path=None, file_info=None, alternates=None):
        """添加缩略图控件 - 增强视觉标识（已移除重复的关联视频标签）"""
        
        # ===== 关键修复：从 file_info 补充关联信息（如果参数未提供）=====
        if file_info:
            if not primary_video_path and file_info.get('primary_video_path'):
                primary_video_path = file_info.get('primary_video_path')
                if self.debug:
                    self.debug.print(f"[参数修复] 从 file_info 恢复 primary_video_path: {os.path.basename(primary_video_path) if primary_video_path else 'None'}")
            if not video_path and file_info.get('video_path'):
                video_path = file_info.get('video_path')
            # 确保 match_type 反映关联状态
            if primary_video_path and file_info.get('match_type') != 'linked':
                file_info['match_type'] = 'linked'
                if self.debug:
                    self.debug.print(f"[状态修正] 设置 match_type 为 'linked'")
        
        if self.debug and primary_video_path:
            self.debug.print(f"[渲染] {os.path.basename(img_path)} -> 关联视频: {os.path.basename(primary_video_path)}")
        
        thumb_width = self.config.thumbnail_width
        thumb_height = self.config.thumbnail_width
        fixed_height = thumb_height + 100   # 原来是 70，增加30用于显示演员/日期/时长/标题

        frame = tk.Frame(self.images_frame, relief=tk.FLAT, borderwidth=0, bg='white')
        
        is_selected = img_path in self._selected_images
        is_video_placeholder = (img_path == "__VIDEO_PLACEHOLDER__")
        has_video = video_path is not None
        match_type = file_info.get('match_type', '') if file_info else ''
        
        # 根据匹配类型设置背景色和标签文本（从配置读取）
        # 获取关联数量（主视频有关联图片时显示）
        alias_count = file_info.get('alias_count', 0) if file_info else 0
        
        if primary_video_path:
            bg_color = self.config.get_gallery_color("linked_video")
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif is_video_placeholder:
            bg_color = self.config.get_gallery_color("video_placeholder")
            label_text = "🎬 视频文件"
            label_fg = "#ECF0F1"
        elif has_video:
            if match_type == 'dedicated' or match_type == 'sub_dedicated':
                bg_color = self.config.get_gallery_color("dedicated_match")
                # 主视频有关联图片时显示数量
                if alias_count > 0:
                    label_text = f"🎬 专属视频 🔗{alias_count}"
                else:
                    label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type in ['cover', 'sub_cover']:
                bg_color = self.config.get_gallery_color("cover_match")
                if alias_count > 0:
                    label_text = f"📁 封面匹配 🔗{alias_count}"
                else:
                    label_text = "📁 封面匹配"
                label_fg = "teal"
            elif match_type == 'sub_image_only':
                bg_color = self.config.get_gallery_color("sub_image_only")
                label_text = "🖼️ 仅图片"
                label_fg = "#3498DB"
            elif match_type == 'cover_priority':
                bg_color = self.config.get_gallery_color("cover_priority")
                if alias_count > 0:
                    label_text = f"📁 封面优先 🔗{alias_count}"
                else:
                    label_text = "📁 封面优先"
                label_fg = "teal"
            elif match_type == 'auto_match':
                bg_color = self.config.get_gallery_color("auto_match")
                label_text = "🔗 自动匹配"
                label_fg = "purple"
            else:
                bg_color = self.config.get_gallery_color("normal_video")
                # 普通视频有关联图片时显示数量
                if alias_count > 0:
                    label_text = f"🎬 视频 🔗{alias_count}"
                else:
                    label_text = "🎬 视频"
                label_fg = "blue"
        else:
            bg_color = self.config.get_gallery_color("image_only")
            label_text = "🖼️ 仅图片"
            label_fg = "gray"
        
        if is_selected:
            bg_color = self.config.get_gallery_color("selected")
        
        frame.configure(width=thumb_width + 20, height=fixed_height)
        frame.pack_propagate(False)
        
        thumb_container = tk.Frame(frame, width=thumb_width, height=thumb_height, 
                                    bg=bg_color, relief=tk.FLAT, bd=0)
        thumb_container.pack(padx=5, pady=5)
        thumb_container.pack_propagate(False)
        
        lbl = tk.Label(thumb_container, image=thumbnail, bg=bg_color, cursor="hand2")
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        lbl.image = thumbnail
        
        def set_selected_state(selected):
            new_bg = self.config.get_gallery_color("selected") if selected else bg_color
            if thumb_container.winfo_exists():
                thumb_container.config(bg=new_bg)
            if lbl.winfo_exists():
                lbl.config(bg=new_bg)
            frame._selected_state = selected
        
        frame._selected_state = is_selected
        if is_selected:
            thumb_container.config(bg="#f5a2a2")
            lbl.config(bg="#f5a2a2")
        
        # 绑定事件 - 支持双击多图切换
        # 收集所有可切换的图片路径（支持路径字符串或缩略图对象字典）
        all_image_paths = [img_path]
        if alternates and isinstance(alternates, list):
            for a in alternates:
                if isinstance(a, str):
                    all_image_paths.append(a)
                elif isinstance(a, dict) and 'path' in a:
                    all_image_paths.append(a['path'])
        
        lbl.bind("<Double-Button-1>", lambda e, p=img_path, paths=all_image_paths: 
                self.on_double_click(p, image_list=paths))
        lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        lbl.bind("<Button-3>", lambda e, p=img_path, f=frame, v=video_path: 
            self._show_context_menu(e, p, f, v))
        
        frame.video_path = None
        frame.is_alias = False
        frame.alias_path = None
        frame.file_info = file_info
        frame.has_local_video = has_video
        frame.img_path = img_path
        frame.set_selected_state = set_selected_state
        
        # 设置视频路径（优先主视频）- 规范化路径确保与数据库缓存一致
        norm_primary = os.path.normpath(primary_video_path) if primary_video_path else None
        norm_video = os.path.normpath(video_path) if video_path else None

        if primary_video_path and os.path.exists(primary_video_path):
            frame.video_path = norm_primary
            frame.is_alias = True
            frame.alias_path = norm_video
            frame.has_local_video = False
            frame.primary_video_path = norm_primary  # 显式保存主视频路径
        elif video_path and os.path.exists(video_path):
            frame.video_path = norm_video
            frame.is_alias = False
            frame.has_local_video = True
            frame.primary_video_path = None
        
        # 显示状态标签（为所有类型显示，包括视频占位符）
        status_lbl = ttk.Label(frame, text=label_text, 
                            font=("微软雅黑", 7), foreground=label_fg)
        status_lbl.pack(padx=2, pady=(0, 2))
        status_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        status_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))
        
        # 6A: 左上角20x20浮动同步标识（在thumb_container中创建）
        actual_video_path = primary_video_path if primary_video_path else video_path
        if actual_video_path:
            is_synced = self._is_video_synced(actual_video_path)
            if is_synced:
                # 使用统一方法创建同步标识
                sync_badge = self._create_sync_badge_widget(thumb_container, x=2, y=2)
                frame.sync_badge = sync_badge
        
        # 标签显示（如果有）
        if frame.video_path:
            tags = self.config.get_video_tags(frame.video_path)
            if tags:
                tag_text = f"🏷️ {', '.join(tags[:2])}" + (f" +{len(tags)-2}" if len(tags) > 2 else "")
                tag_lbl = ttk.Label(frame, text=tag_text, wraplength=thumb_width-10,
                                font=("微软雅黑", 7), foreground="purple")
                tag_lbl.pack(padx=2, pady=(0, 2))
                tag_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
                tag_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))
        
        # 文件夹名称
        fname = "当前文件夹" if folder_name == "." else folder_name
        color = "green" if folder_name == "." else "blue"
        folder_lbl = ttk.Label(frame, text=fname, wraplength=thumb_width-10,
                            font=("微软雅黑", 8, "bold"), foreground=color)
        folder_lbl.pack(padx=2, pady=(2, 0))
        folder_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        folder_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))
        
        # ========== 修改：准备tooltip文本，优先使用视频标题 ==========
        tooltip_text = fname  # 默认使用文件夹名
        if file_info and isinstance(file_info, dict):
            video_title = file_info.get('video_title', '')
            if video_title:
                tooltip_text = video_title
        
        # 为文件夹名添加tooltip
        if ToolTip:
            ToolTip(folder_lbl, tooltip_text)
        
        # 为缩略图添加相同的tooltip
        if ToolTip:
            ToolTip(lbl, tooltip_text)
        
        # ========== 删除：不再显示图片文件名（原cover.jpg等）==========
        # 原文件名显示代码已删除
        
        # ========== 新增：数据库信息展示区（异步加载）==========
        # 创建信息容器框架（用于演员、日期、时长）
        info_frame = ttk.Frame(frame)
        info_frame.pack(fill=tk.X, padx=2, pady=(0, 2))

        # 视频信息标签（演员、日期、时长），初始为空
        info_lbl = ttk.Label(info_frame, text="", wraplength=thumb_width-10,
                            font=("微软雅黑", 7), foreground="gray")
        info_lbl.pack(fill=tk.X)
        info_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        info_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))

        # 标题标签（截断显示），如果有视频则异步加载
        title_lbl = ttk.Label(frame, text="", wraplength=thumb_width-10,
                            font=("微软雅黑", 7, "italic"), foreground="#666666")
        title_lbl.pack(padx=2, pady=(0, 2))
        title_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        title_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))

        # ========== 关键修复：保存引用到 frame，供增量刷新使用 ==========
        frame.info_lbl = info_lbl
        frame.title_lbl = title_lbl

        # 如果有视频路径，异步加载数据库信息
        actual_video_path = primary_video_path if primary_video_path else video_path
        if actual_video_path and self.db and hasattr(self.db, 'is_ready') and self.db.is_ready:
            self.parent.after(150, lambda: self._load_video_info_async(
                actual_video_path, info_lbl, title_lbl, thumb_width
            ))
        
        # 绑定滚轮
        self._bind_mousewheel_recursive(frame)
        
        items_per_row = max(1150 // (thumb_width + 20), 1)
        row, col = index // items_per_row, index % items_per_row
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

    def _load_video_info_async(self, video_path, info_lbl, title_lbl, wrap_width):
        """异步从数据库加载视频信息（演员、日期、时长、标题）"""
        def load_info():
            try:
                # 查询视频文件在数据库中的记录
                db_file = self.db.get_file_by_path(video_path) if self.db else None

                # 诊断：记录查询详情
                if self.debug and self.debug.debug_mode:
                    self.debug.print(f"[_load_video_info_async] 查询: {os.path.basename(video_path)}")
                    self.debug.print(f"  路径: {video_path}")
                    self.debug.print(f"  规范化: {os.path.normpath(video_path)}")
                    self.debug.print(f"  数据库结果: {'找到' if db_file else '未找到'}")
                    if not db_file and self.db and hasattr(self.db, '_cache'):
                        cache = self.db._cache.get('files', {})
                        self.debug.print(f"  缓存总数: {len(cache)}")
                        # 检查是否有同名文件
                        vname = os.path.basename(video_path).lower()
                        matches = [p for p in cache.keys() if os.path.basename(p).lower() == vname]
                        if matches:
                            self.debug.print(f"  同名匹配 ({len(matches)}个): {matches[:3]}")
                if not db_file:
                    return
                
                file_id = db_file.get('file_id')
                
                # 获取演员信息
                actors = []
                try:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT a.actor_name 
                        FROM actors a
                        JOIN file_actors fa ON a.actor_id = fa.actor_id
                        WHERE fa.file_id = ?
                        ORDER BY fa.bind_time
                    """, (file_id,))
                    actors = [row['actor_name'] for row in cursor.fetchall()]
                    cursor.close()
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"查询演员失败: {e}")
                
                # 获取视频元数据
                release_date = db_file.get('release_date', '')
                duration = db_file.get('duration', 0)
                video_title = db_file.get('video_title', '')
                file_size = db_file.get('file_size', 0)
                
                # 格式化信息文本（演员、日期、时长）
                info_parts = []
                if actors:
                    actors_str = ', '.join(actors[:3]) + ('...' if len(actors) > 3 else '')
                    info_parts.append(f"👤 {actors_str}")
                if release_date:
                    info_parts.append(f"📅 {release_date}")
                if duration:
                    # 转换为分钟
                    mins = duration // 60
                    info_parts.append(f"⏱️ {mins}分")
                if file_size:
                    # 转换为MB
                    size_mb = file_size / (1024 * 1024)
                    if size_mb >= 1024:
                        info_parts.append(f"💾 {size_mb/1024:.2f}GB")
                    else:
                        info_parts.append(f"💾 {size_mb:.1f}MB")
                
                info_text = " | ".join(info_parts) if info_parts else ""
                
                # 截断标题用于显示
                display_title = ""
                if video_title:
                    max_title_len = 30 if wrap_width >= 200 else 20
                    if len(video_title) > max_title_len:
                        display_title = video_title[:max_title_len] + "..."
                    else:
                        display_title = video_title
                
                # 在主线程更新UI
                self.parent.after(0, lambda: self._update_video_info_ui(
                    info_lbl, title_lbl, info_text, display_title, video_title
                ))
                
            except Exception as e:
                if self.debug:
                    self.debug.print(f"加载视频信息失败 {video_path}: {e}")
        
        # 在后台线程执行查询
        threading.Thread(target=load_info, daemon=True).start()

    def _update_video_info_ui(self, info_lbl, title_lbl, info_text, display_title, full_title):
        """更新视频信息的UI（在主线程调用）"""
        try:
            # 更新信息标签（演员、日期、时长）
            if info_lbl.winfo_exists():
                if info_text:
                    info_lbl.config(text=info_text, foreground="#444444")
                else:
                    info_lbl.config(text="")
            
            # 更新标题标签
            if title_lbl.winfo_exists():
                if display_title:
                    title_lbl.config(text=display_title)
                    # 如果标题被截断了，添加 tooltip 显示完整标题
                    if full_title and len(full_title) > len(display_title) and ToolTip:
                        ToolTip(title_lbl, full_title)
                else:
                    title_lbl.config(text="")
                    
        except Exception as e:
            if self.debug:
                self.debug.print(f"更新视频信息UI失败: {e}")

    def _bind_mousewheel_to_widget(self, widget):
        """递归绑定滚轮事件到控件"""
        widget.bind("<MouseWheel>", self._on_mousewheel)
        widget.bind("<Button-4>", self._on_mousewheel)
        widget.bind("<Button-5>", self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel_to_widget(child)

    # gallery_viewer.py - 在 GalleryViewer 类中添加以下方法

    def _show_thumbnail_selector(self, folder_path, video_path):
        """显示缩略图选择对话框"""
        from PIL import Image
        import tkinter as tk
        from tkinter import ttk
        
        # 扫描文件夹中所有图片
        from core import PathUtils
        images = []
        for entry in os.scandir(folder_path):
            if entry.is_file() and PathUtils.is_image_file(entry.path):
                # 跳过已匹配的图片（如果有）
                images.append(entry.path)
        
        if not images:
            messagebox.showinfo("提示", "该文件夹下没有图片文件")
            return None
        
        # 创建选择对话框
        dialog = tk.Toplevel(self.parent)
        dialog.title(f"为 {os.path.basename(video_path)} 选择缩略图")
        dialog.geometry("500x400")
        dialog.transient(self.parent)
        
        # 预览区域
        preview_frame = ttk.LabelFrame(dialog, text="预览")
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        preview_label = ttk.Label(preview_frame, text="点击下方图片预览")
        preview_label.pack(pady=10)
        
        # 图片列表区域
        list_frame = ttk.LabelFrame(dialog, text="选择图片")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 使用 Canvas 实现横向滚动
        canvas = tk.Canvas(list_frame, height=120, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(xscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        thumb_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=thumb_frame, anchor="nw")
        
        selected_image = None
        
        def update_preview(img_path):
            nonlocal selected_image
            selected_image = img_path
            try:
                img = Image.open(img_path)
                img.thumbnail((300, 200))
                from PIL import ImageTk
                photo = ImageTk.PhotoImage(img)
                preview_label.config(image=photo)
                preview_label.image = photo
                preview_label.config(text="")
            except Exception as e:
                preview_label.config(text=f"预览失败: {e}")
        
        # 添加图片缩略图
        thumb_size = 80
        for idx, img_path in enumerate(images):
            try:
                img = Image.open(img_path)
                img.thumbnail((thumb_size, thumb_size))
                from PIL import ImageTk
                photo = ImageTk.PhotoImage(img)
                
                btn = tk.Button(thumb_frame, image=photo, 
                            command=lambda p=img_path: update_preview(p))
                btn.image = photo
                btn.grid(row=0, column=idx, padx=5, pady=5)
            except:
                pass
        
        def on_confirm():
            nonlocal selected_image
            if selected_image:
                dialog.result = selected_image
                dialog.destroy()
            else:
                messagebox.showwarning("提示", "请先选择一个图片")
        
        def on_cancel():
            dialog.result = None
            dialog.destroy()
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="确认选择", command=on_confirm).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.RIGHT, padx=5)
        
        # 更新thumb_frame大小
        thumb_frame.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        
        dialog.result = None
        self.parent.wait_window(dialog)
        
        return dialog.result if hasattr(dialog, 'result') else None


    def _set_video_thumbnail(self, img_path, video_path):
        """手动设置视频缩略图关联"""
        # 获取视频文件名（不含扩展名）
        video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
        img_basename = os.path.splitext(os.path.basename(img_path))[0].lower()
        
        folder = os.path.dirname(video_path)
        
        # 检查是否是同名文件
        if video_basename == img_basename:
            messagebox.showinfo("提示", "该图片已与视频同名，自动匹配")
            return True
        
        # 询问用户是否建立关联
        if messagebox.askyesno("确认关联", 
            f"将图片 '{os.path.basename(img_path)}'\n关联到视频 '{os.path.basename(video_path)}'？\n\n"
            "关联后将使用此图片作为视频缩略图。"):
            
            # 重命名图片为视频同名（可选）或记录关联关系
            # 方案：重命名图片为视频同名（保持原扩展名）
            img_ext = os.path.splitext(img_path)[1]
            new_img_path = os.path.join(folder, video_basename + img_ext)
            
            # 检查目标文件是否已存在
            if os.path.exists(new_img_path):
                if messagebox.askyesno("文件已存在", 
                    f"目标文件 '{os.path.basename(new_img_path)}' 已存在，是否覆盖？"):
                    try:
                        os.remove(new_img_path)
                    except:
                        messagebox.showerror("错误", "无法删除目标文件")
                        return False
                else:
                    return False
            
            try:
                import shutil
                shutil.copy2(img_path, new_img_path)
                messagebox.showinfo("成功", f"已创建缩略图副本:\n{os.path.basename(new_img_path)}")
                return True
            except Exception as e:
                messagebox.showerror("错误", f"创建缩略图失败: {e}")
                return False
        
        return False

    def _auto_select_first_video(self):
        """自动选中第一个有视频路径的项，并更新信息面板"""
        try:
            first_frame_with_video = None
            for child in self.images_frame.winfo_children():
                video_path = getattr(child, 'video_path', None)
                img_path = getattr(child, 'img_path', None)
                if video_path and img_path:
                    first_frame_with_video = child
                    break
            
            if first_frame_with_video:
                img_path = getattr(first_frame_with_video, 'img_path', None)
                video_path = getattr(first_frame_with_video, 'video_path', None)
                
                # 设置选中状态
                self._selected_images.add(img_path)
                if hasattr(first_frame_with_video, 'set_selected_state'):
                    first_frame_with_video.set_selected_state(True)
                
                # 更新信息面板
                self._update_info_panel(video_path)
                
                if self.debug:
                    self.debug.print(f"[自动选中] {os.path.basename(video_path)}")
                    self.debug.print(f"  图片: {os.path.basename(img_path) if img_path else 'N/A'}")
            else:
                if self.debug:
                    self.debug.print("[自动选中] 未找到有视频的项")
        except Exception as e:
            if self.debug:
                self.debug.print(f"自动选中第一个视频失败: {e}")

    def _select_image(self, img_path, frame, event=None):
        """优化版：避免全量遍历和UI重排，修复单选时背景不恢复问题，并更新信息面板"""
        ctrl = (event and (event.state & 0x4) != 0)

        # 隐藏采集控件（当选择改变时）
        self._hide_crawler_frame()

        # 非多选模式下，点击新图片时清除之前的选择
        if not ctrl and not self._multi_select_mode:
            if img_path not in self._selected_images:
                # 清除之前选中的所有项的视觉状态
                for old_path in list(self._selected_images):
                    for child in self.images_frame.winfo_children():
                        if getattr(child, 'img_path', None) == old_path:
                            if hasattr(child, 'set_selected_state'):
                                child.set_selected_state(False)
                            break
                self._selected_images.clear()
                self._update_info_panel(None)  # 清空信息面板

        # 切换当前项的选中状态
        if img_path in self._selected_images:
            self._selected_images.remove(img_path)
            if hasattr(frame, 'set_selected_state'):
                frame.set_selected_state(False)
            self._update_info_panel(None)  # 取消选中时清空信息面板
        else:
            self._selected_images.add(img_path)
            if hasattr(frame, 'set_selected_state'):
                frame.set_selected_state(True)

            # ========== 关键修复：更新顶部信息面板并记录调试日志 ==========
            video_path = getattr(frame, 'video_path', None)
            
            # 获取所有关联信息用于日志记录
            img_path_attr = getattr(frame, 'img_path', None) or img_path
            is_alias = getattr(frame, 'is_alias', False)
            primary_video_path = getattr(frame, 'primary_video_path', None)
            has_local_video = getattr(frame, 'has_local_video', False)
            file_info = getattr(frame, 'file_info', None)
            
            if video_path:
                # 更新信息面板
                self._update_info_panel(video_path)
                
                # ========== 新增：详细的调试日志 ==========
                if self.debug:
                    import os
                    video_name = os.path.basename(video_path)
                    folder_name = os.path.basename(os.path.dirname(video_path))
                    
                    self.debug.print(f"\n{'='*60}")
                    self.debug.print(f"[选中缩略图] {os.path.basename(img_path_attr)}")
                    self.debug.print(f"  图片路径: {img_path_attr}")
                    
                    if is_alias:
                        self.debug.print(f"  类型: 关联视频")
                        self.debug.print(f"  主视频路径: {primary_video_path}")
                        self.debug.print(f"  主视频名称: {os.path.basename(primary_video_path) if primary_video_path else 'N/A'}")
                    elif has_local_video:
                        self.debug.print(f"  类型: 本地视频")
                        self.debug.print(f"  视频路径: {video_path}")
                        self.debug.print(f"  视频名称: {video_name}")
                    else:
                        self.debug.print(f"  类型: 仅图片（无视频）")
                    
                    # 输出数据库信息
                    if self.db and hasattr(self.db, 'is_ready') and self.db.is_ready:
                        try:
                            # 获取实际要查询的视频路径（关联视频用主视频路径）
                            lookup_path = primary_video_path if primary_video_path else video_path
                            db_file = self.db.get_file_by_path(lookup_path)
                            
                            if db_file:
                                self.debug.print(f"\n  [数据库信息]")
                                self.debug.print(f"    数据库ID: {db_file.get('file_id', 'N/A')}")
                                self.debug.print(f"    番号: {db_file.get('video_code', '无') or '无'}")
                                self.debug.print(f"    标题: {db_file.get('video_title', '无') or '无'}")
                                self.debug.print(f"    发布日期: {db_file.get('release_date', '无') or '无'}")
                                self.debug.print(f"    时长: {db_file.get('duration', 0)}秒")
                                self.debug.print(f"    文件大小: {db_file.get('file_size', 0)} 字节")
                                self.debug.print(f"    是否别名: {'是' if db_file.get('is_alias') else '否'}")
                                self.debug.print(f"    标签摘要: {db_file.get('tag_summary', '无') or '无'}")
                                
                                # 查询演员信息
                                file_id = db_file.get('file_id')
                                if file_id:
                                    actors = self.db.get_actors_by_file(file_id)
                                    if actors:
                                        actor_names = []
                                        for actor in actors:
                                            name = actor.get('name', '')
                                            name_cn = actor.get('name_cn', '')
                                            if name_cn:
                                                actor_names.append(f"{name}（{name_cn}）")
                                            else:
                                                actor_names.append(name)
                                        self.debug.print(f"    演员({len(actors)}位): {', '.join(actor_names)}")
                                    else:
                                        self.debug.print(f"    演员: 无")
                                
                                # 查询关联关系
                                if db_file.get('is_alias'):
                                    self.debug.print(f"    关联状态: 别名文件，已关联到主视频")
                                else:
                                    # 检查是否有其他文件关联到这个视频
                                    try:
                                        aliases = self.db.get_related_videos(file_id) if file_id else []
                                        if aliases:
                                            self.debug.print(f"    关联状态: 主视频，有 {len(aliases)} 个关联文件")
                                            for alias in aliases[:3]:  # 最多显示3个
                                                alias_path = alias.get('file_path', '')
                                                self.debug.print(f"      - {os.path.basename(alias_path)}")
                                            if len(aliases) > 3:
                                                self.debug.print(f"      ... 还有 {len(aliases)-3} 个")
                                        else:
                                            self.debug.print(f"    关联状态: 无关联")
                                    except Exception as e:
                                        self.debug.print(f"    关联查询失败: {e}")
                            else:
                                self.debug.print(f"\n  [数据库信息] 未找到该视频的数据库记录")
                                self.debug.print(f"    查找路径: {lookup_path}")
                                self.debug.print(f"    路径存在: {os.path.exists(lookup_path) if lookup_path else 'N/A'}")
                                
                        except Exception as e:
                            self.debug.print(f"  [数据库查询失败] {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        self.debug.print(f"\n  [数据库信息] 数据库未就绪")
                        self.debug.print(f"    db_ready: {getattr(self.db, 'is_ready', False) if self.db else 'db is None'}")
                        self.debug.print(f"    db_cache_ready: {getattr(self.db, 'is_cache_ready', False) if self.db else 'N/A'}")
                    
                    self.debug.print(f"{'='*60}\n")
            else:
                self._update_info_panel(None)
                
                if self.debug:
                    import os
                    self.debug.print(f"\n[选中缩略图] {os.path.basename(img_path_attr)} - 无关联视频")
                    self.debug.print(f"{'='*50}\n")

        if self.on_select:
            self.on_select(img_path, frame, img_path in self._selected_images)

    def _show_context_menu(self, event, img_path, frame, video_path=None):
        """右键菜单 - 批量采集整个文件夹树（采集同级所有文件夹）"""
        from core import PathUtils

        # 先判断是否为视频占位符
        is_video_placeholder = (img_path == "__VIDEO_PLACEHOLDER__")
        has_local_video = getattr(frame, 'has_local_video', False)
        is_alias = getattr(frame, 'is_alias', False)

        # 获取视频路径
        actual_video_path = getattr(frame, 'video_path', None) or video_path
        
        # 确定当前点击的文件夹路径
        if is_video_placeholder and actual_video_path:
            current_folder = os.path.dirname(actual_video_path)
        else:
            current_folder = os.path.dirname(img_path)
        
        # 获取父文件夹（用于批量采集）
        parent_folder = os.path.dirname(current_folder)
        batch_root_folder = parent_folder if parent_folder and parent_folder != current_folder else current_folder

        try:
            top_window = self.parent.winfo_toplevel()
            menu = tk.Menu(top_window, tearoff=0)
        except:
            menu = tk.Menu(self.parent, tearoff=0)

        # 提取主文件夹名称
        menu.add_command(label="📋 提取主文件夹名称", command=self._copy_browser_folder_name)
        menu.add_separator()

        # 播放选项
        if actual_video_path and os.path.exists(actual_video_path):
            if is_alias:
                menu.add_command(label=f"▶ 播放主视频 (关联)", 
                            command=lambda: self.video_player.play_video(actual_video_path),
                            font=("微软雅黑", 9, "bold"), foreground="green")
                display_path = actual_video_path if len(actual_video_path) < 40 else "..." + actual_video_path[-37:]
                menu.add_command(label=f"   主视频: {display_path}", state="disabled")
            else:
                menu.add_command(label=f"▶ 播放: {os.path.basename(actual_video_path)[:40]}", 
                            command=lambda: self.video_player.play_video(actual_video_path),
                            font=("微软雅黑", 9, "bold"))
            menu.add_separator()

        # 采集视频信息
        if actual_video_path and os.path.exists(actual_video_path):
            menu.add_command(label="🔍 采集本视频信息", 
                        command=lambda: self._collect_video_info(actual_video_path, frame),
                        font=("微软雅黑", 9, "bold"), foreground="blue")
            
            # ========== 新增：删除采集视频信息 ==========
            # 检查该视频是否有采集信息（数据库中有视频标题或番号）
            has_collection_info = False
            if self.db and getattr(self.db, 'is_ready', False):
                try:
                    db_file = self.db.get_file_by_path(actual_video_path)
                    if db_file and (db_file.get('video_code') or db_file.get('video_title') 
                                   or db_file.get('duration') or db_file.get('release_date')):
                        has_collection_info = True
                except Exception:
                    pass
            
            # 只有在有采集信息时才显示删除选项
            if has_collection_info:
                menu.add_command(label="🗑️ 删除采集视频信息", 
                            command=lambda: self._delete_video_collection_info(actual_video_path),
                            font=("微软雅黑", 9), foreground="red")
                
            # ========== 新增：删除视频 ==========
            menu.add_separator()
            menu.add_command(label="❌ 删除视频...", 
                        command=lambda: self._delete_video(actual_video_path, frame, img_path),
                        font=("微软雅黑", 9, "bold"), foreground="#CC0000")
            # =============================================
                    
        # 批量采集同级所有文件夹
        if batch_root_folder and os.path.exists(batch_root_folder):
            total_videos = self._count_videos_in_tree(batch_root_folder)
            if parent_folder and parent_folder != current_folder:
                range_desc = f"采集 {os.path.basename(batch_root_folder)} 下所有文件夹的视频"
            else:
                range_desc = f"采集 {os.path.basename(batch_root_folder)} 下所有视频"
            
            if total_videos > 0:
                menu.add_command(label=f"📁 {range_desc} ({total_videos}个)", 
                            command=lambda: self._collect_folder_tree_videos_info(
                                batch_root_folder, 
                                original_folder=current_folder
                            ),
                            font=("微软雅黑", 9, "bold"), foreground="green")
                menu.add_command(label=f"   └─ 范围: {batch_root_folder}", state="disabled", 
                            font=("微软雅黑", 8), foreground="gray")
        
        menu.add_separator()

        # 设为缩略图（使用 current_folder）
        folder_videos = PathUtils.find_video_files(current_folder)
        if folder_videos and not is_video_placeholder:
            video_submenu = tk.Menu(menu, tearoff=0)
            for v_path in folder_videos:
                v_name = os.path.basename(v_path)
                video_submenu.add_command(
                    label=v_name[:40],
                    command=lambda p=v_path: self._set_video_thumbnail(img_path, p)
                )
            menu.add_cascade(label="🖼️ 设为以下视频的缩略图...", menu=video_submenu)

            if actual_video_path and os.path.exists(actual_video_path):
                menu.add_command(
                    label="🔄 更换缩略图...",
                    command=lambda: self._replace_thumbnail(img_path, actual_video_path)
                )
            menu.add_separator()

        # 关联管理
        if is_alias:
            menu.add_command(label="🔗 已关联到主视频", state="disabled")
            menu.add_command(label="   解除关联", 
                        command=lambda: self._remove_video_relation(frame))
            menu.add_separator()
        elif not has_local_video and not is_video_placeholder:
            if self.on_relation_request:
                menu.add_command(label="🔗 智能关联到主视频（自动匹配番号）...", 
                                command=lambda: self.on_relation_request(img_path, frame),
                                font=("微软雅黑", 9, "bold"))
            else:
                menu.add_command(label="🔗 关联到主视频（选择视频文件）...", 
                                command=lambda: self._create_video_relation_for_thumbnail(img_path, frame),
                                font=("微软雅黑", 9, "bold"))
            menu.add_separator()

        # 标签管理
        tag_target_path = video_path if video_path else None
        if tag_target_path:
            tags = self.config.get_video_tags(tag_target_path)
            if tags:
                menu.add_command(label=f"🏷️ 标签: {', '.join(tags[:3])}", state="disabled")
            menu.add_command(label="设置标签...", 
                        command=lambda: self._set_tag(img_path, frame))
            menu.add_separator()

        # 其他功能（使用 current_folder）
        menu.add_command(label="📋 提取番号", command=lambda: self._extract_code(current_folder))
        menu.add_separator()
        menu.add_command(label="查看原图", command=lambda: self.image_viewer.view(img_path, self.parent))
        menu.add_command(label="复制图片路径", command=lambda: self._copy_path(img_path))
        menu.add_command(label="打开所在文件夹", command=lambda: self._open_folder(current_folder))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            if self.debug:
                self.debug.print(f"右键菜单弹出失败: {e}")
        finally:
            menu.grab_release()

    def _get_sibling_folders(self, folder_path):
        """
        获取指定文件夹的同级文件夹列表（包括自身）
        
        Args:
            folder_path: 当前文件夹路径
            
        Returns:
            list: 同级文件夹路径列表
        """
        parent = os.path.dirname(folder_path)
        if not parent or parent == folder_path:
            return [folder_path]
        
        siblings = []
        try:
            for entry in os.scandir(parent):
                if entry.is_dir() and not entry.name.startswith('.'):
                    siblings.append(entry.path)
        except Exception as e:
            if self.debug:
                self.debug.print(f"获取同级文件夹失败: {e}")
            return [folder_path]
        
        return sorted(siblings)

    def _get_all_videos_in_sibling_tree(self, folder_path):
        """
        获取指定文件夹的同级所有文件夹及其子文件夹中的视频文件
        
        Args:
            folder_path: 当前文件夹路径
            
        Returns:
            list: 所有视频文件路径列表
        """
        from core import PathUtils
        
        # 获取同级所有文件夹
        siblings = self._get_sibling_folders(folder_path)
        
        all_videos = []
        for sibling in siblings:
            # 递归获取每个同级文件夹下的所有视频
            videos = self._get_all_videos_in_tree(sibling)
            all_videos.extend(videos)
        
        if self.debug:
            self.debug.print(f"[同级采集] 共 {len(siblings)} 个同级文件夹, 发现 {len(all_videos)} 个视频")
            for sib in siblings:
                self.debug.print(f"  - {os.path.basename(sib)}")
        
        return all_videos
    
    def _collect_folder_tree_videos_info(self, root_folder, original_folder=None):
        """
        批量采集文件夹树（包含所有子文件夹）中所有视频的信息
        
        Args:
            root_folder: 根文件夹路径（父文件夹）
            original_folder: 原始点击的文件夹（用于显示说明）
        """
        from core import extract_code_from_text
        import threading
        import time
        
        # 防止重复执行
        if hasattr(self, '_batch_in_progress') and self._batch_in_progress:
            messagebox.showinfo("提示", "批量采集任务正在进行中，请稍后再试")
            return
        
        # 扫描整个文件夹树中的视频文件
        video_files = self._get_all_videos_in_tree(root_folder)
        
        if not video_files:
            messagebox.showinfo("提示", f"文件夹树中没有视频文件:\n{root_folder}")
            return
        
        # 确定显示名称
        if original_folder:
            display_name = os.path.basename(original_folder)
            batch_desc = f"文件夹 '{display_name}' 的同级所有文件夹"
        else:
            display_name = os.path.basename(root_folder)
            batch_desc = f"文件夹 '{display_name}' 及其所有子文件夹"
        
        # 统计同级文件夹数量（用于显示）
        siblings = self._get_sibling_folders(original_folder) if original_folder else []
        sibling_count = len(siblings) if siblings else 1
        
        # 确认对话框（只有一个）
        if sibling_count > 1 and original_folder:
            confirm_msg = (
                f"将采集以下范围的所有视频信息:\n\n"
                f"📁 根目录: {root_folder}\n"
                f"📂 包含: {sibling_count} 个同级文件夹\n"
                f"   (包括 {os.path.basename(original_folder)} 及其同级文件夹)\n\n"
                f"🎬 共发现 {len(video_files)} 个视频文件\n\n"
                f"⚠️ 注意：\n"
                f"• 每个视频将根据番号联网查询\n"
                f"• 采集过程可能需要较长时间\n"
                f"• 采集在后台进行，不影响其他操作\n\n"
                f"是否继续？"
            )
        else:
            confirm_msg = (
                f"将采集以下范围的所有视频信息:\n\n"
                f"📁 文件夹: {root_folder}\n"
                f"📂 包含所有子文件夹\n\n"
                f"🎬 共发现 {len(video_files)} 个视频文件\n\n"
                f"⚠️ 注意：\n"
                f"• 每个视频将根据番号联网查询\n"
                f"• 采集过程可能需要较长时间\n"
                f"• 采集在后台进行，不影响其他操作\n\n"
                f"是否继续？"
            )
        
        result = messagebox.askyesno("批量采集确认", confirm_msg, icon="question")
        
        if not result:
            return
        
        # 检查采集回调
        if not hasattr(self, 'on_collect_video_info') or not self.on_collect_video_info:
            if hasattr(self.parent, 'master') and hasattr(self.parent.master, 'on_collect_video_info'):
                self.on_collect_video_info = self.parent.master.on_collect_video_info
        
        if not self.on_collect_video_info:
            messagebox.showerror("错误", "采集功能未就绪，请检查爬虫配置")
            return
        
        # 显示日志标签页
        self._show_log_tab()
        
        # 设置批量采集进行中标志
        self._batch_in_progress = True
        self._batch_stop_flag = False
        
        # 记录采集范围信息
        if self.debug:
            self.debug.print(f"\n{'='*60}")
            self.debug.print(f"[批量采集] 开始采集")
            self.debug.print(f"[批量采集] 根目录: {root_folder}")
            if sibling_count > 1 and original_folder:
                self.debug.print(f"[批量采集] 模式: 同级采集 (共 {sibling_count} 个文件夹)")
                self.debug.print(f"[批量采集] 原始点击: {original_folder}")
            self.debug.print(f"[批量采集] 共发现 {len(video_files)} 个视频文件")
            self.debug.print(f"{'='*60}")
        
        # 启动后台采集线程（传递3个参数）
        threading.Thread(target=self._batch_collect_worker, 
                        args=(video_files, root_folder, original_folder), 
                        daemon=True).start()
        
    def _batch_collect_worker(self, video_files, root_folder, original_folder=None):
        """
        后台批量采集工作线程
        
        Args:
            video_files: 视频文件路径列表
            root_folder: 根文件夹路径（用于显示）
            original_folder: 原始点击的文件夹（可选，用于日志显示）
        """
        import threading
        import time
        from core import extract_code_from_text
        
        total = len(video_files)
        results = {
            'total': total,
            'success': 0,
            'failed': 0,
            'failed_list': []
        }
        
        # 更新状态栏
        self.parent.after(0, lambda: self._update_batch_status(
            f"开始采集 {total} 个视频...", 0, total))
        
        # 在调试日志中记录开始
        if self.debug:
            self.debug.print(f"\n{'='*60}")
            self.debug.print(f"[批量采集] 开始采集文件夹树: {root_folder}")
            if original_folder:
                self.debug.print(f"[批量采集] 原始点击文件夹: {original_folder}")
            self.debug.print(f"[批量采集] 共发现 {total} 个视频文件")
            self.debug.print(f"{'='*60}")
        
        try:
            for i, video_path in enumerate(video_files):
                # 检查是否停止
                if hasattr(self, '_batch_stop_flag') and self._batch_stop_flag:
                    if self.debug:
                        self.debug.print(f"[批量采集] 用户停止，已处理 {i} 个视频")
                    break
                
                video_name = os.path.basename(video_path)
                folder_name = os.path.basename(os.path.dirname(video_path))
                # 计算相对路径（相对于根文件夹）
                try:
                    rel_path = os.path.relpath(video_path, root_folder)
                except:
                    rel_path = f"{folder_name}/{video_name}"
                
                # 更新进度（主线程）
                progress = int((i + 1) / total * 100)
                self.parent.after(0, lambda p=progress, idx=i+1, t=total, v=video_name: 
                    self._update_batch_status(f"采集 [{idx}/{t}] {v}", p, t))
                
                # 提取番号
                video_code = extract_code_from_text(video_name)
                folder_code = extract_code_from_text(folder_name)
                
                code = ""
                if video_code:
                    code = video_code['canonical']
                elif folder_code:
                    code = folder_code['canonical']
                
                if not code:
                    self._log_batch_message(f"⚠️ 跳过: {rel_path} (无法提取番号)", is_error=True)
                    results['failed'] += 1
                    results['failed_list'].append({
                        'name': rel_path,
                        'reason': '无法提取番号',
                        'code': None
                    })
                    continue
                
                # 采集单个视频（同步等待）
                self._log_batch_message(f"🔍 采集: {rel_path}")
                self._log_batch_message(f"   番号: {code}")
                
                success, info, used_url = self._collect_single_video_sync(video_path, code)
                
                # 显示使用的网址
                if used_url:
                    self._log_batch_message(f"   网址: {used_url}")
                else:
                    self._log_batch_message(f"   网址: (获取失败)")
                
                if success:
                    actors = info.get('actors', [])
                    actor_str = ', '.join(actors[:3]) + ('...' if len(actors) > 3 else '')
                    title = info.get('title', '')
                    title_short = title[:50] + '...' if len(title) > 50 else title
                    self._log_batch_message(f"✓ 成功: {code}")
                    if title:
                        self._log_batch_message(f"   标题: {title_short}")
                    if actors:
                        self._log_batch_message(f"   演员: {actor_str}")
                    results['success'] += 1
                else:
                    error = info.get('error', '未知错误')
                    self._log_batch_message(f"✗ 失败: {code} - {error}", is_error=True)
                    results['failed'] += 1
                    results['failed_list'].append({
                        'name': rel_path,
                        'reason': error,
                        'code': code,
                        'url': used_url
                    })
                
                # 添加空行分隔
                self._log_batch_message("")
        finally:
            # 清除批量采集进行中标志
            self._batch_in_progress = False
        
        # 完成后的统计
        self.parent.after(0, lambda: self._update_batch_status(
            f"采集完成: 成功 {results['success']}, 失败 {results['failed']}", 
            100, total))
        
        # 输出最终统计
        self._log_batch_message(f"\n{'='*50}")
        self._log_batch_message(f"采集完成！总计: {total}, 成功: {results['success']}, 失败: {results['failed']}")
        
        if results['failed_list']:
            self._log_batch_message(f"\n失败列表 ({len(results['failed_list'])}个):")
            for item in results['failed_list']:
                url_info = f" [网址: {item.get('url', 'N/A')}]" if item.get('url') else ""
                code_info = f" [番号: {item.get('code', 'N/A')}]" if item.get('code') else ""
                self._log_batch_message(f"  - {item['name']}: {item['reason']}{code_info}{url_info}", is_error=True)
        
        # 刷新图库（更新同步标识）
        if results['success'] > 0:
            self.parent.after(500, lambda: self._refresh_gallery_sync())
        
        # 显示完成消息框
        if results['failed'] == 0:
            self.parent.after(0, lambda: messagebox.showinfo(
                "批量采集完成", f"成功采集 {results['success']} 个视频的信息！"))
        else:
            self.parent.after(0, lambda: messagebox.showwarning(
                "批量采集完成", f"采集完成！\n成功: {results['success']}\n失败: {results['failed']}\n\n详细信息请查看操作日志"))
        
        if self.debug:
            self.debug.print(f"[批量采集] 完成 - 成功: {results['success']}, 失败: {results['failed']}")

    def _delete_video_collection_info(self, video_path):
        """
        删除视频的采集信息（番号、时长、发布日期、演员、视频标题）
        从数据库中清除从网上采集到的视频信息
        """
        if not self.db or not getattr(self.db, 'is_ready', False):
            messagebox.showwarning("提示", "数据库未就绪")
            return
        
        if not video_path or not os.path.exists(video_path):
            messagebox.showwarning("提示", "视频文件路径无效")
            return
        
        # 确认对话框
        video_name = os.path.basename(video_path)
        result = messagebox.askyesno(
            "确认删除采集信息",
            f"确定要删除以下视频的采集信息吗？\n\n"
            f"视频: {video_name}\n\n"
            f"将清除的信息:\n"
            f"• 视频番号/编号\n"
            f"• 视频时长\n"
            f"• 发布日期\n"
            f"• 视频标题\n"
            f"• 演员关联信息\n\n"
            f"⚠️ 此操作不可撤销！",
            icon="warning"
        )
        
        if not result:
            return
        
        try:
            # 1. 获取视频在数据库中的记录
            db_file = self.db.get_file_by_path(video_path)
            if not db_file:
                messagebox.showwarning("提示", f"数据库中未找到该视频:\n{video_name}")
                return
            
            file_id = db_file.get('file_id')
            if not file_id:
                messagebox.showwarning("提示", "无法获取视频的数据库ID")
                return
            
            # 2. 获取当前采集信息用于日志记录
            old_code = db_file.get('video_code', '') or '-'
            old_title = db_file.get('video_title', '') or '-'
            old_date = db_file.get('release_date', '') or '-'
            old_duration = db_file.get('duration', 0) or 0
            
            # 3. 获取关联的演员信息（用于日志）
            old_actors = []
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.actor_name 
                    FROM actors a
                    JOIN file_actors fa ON a.actor_id = fa.actor_id
                    WHERE fa.file_id = ?
                """, (file_id,))
                old_actors = [row['actor_name'] for row in cursor.fetchall()]
                cursor.close()
            except Exception as e:
                if self.debug:
                    self.debug.print(f"查询旧演员信息失败: {e}")
            
            # 4. 开始删除操作 - 使用显式事务
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("BEGIN IMMEDIATE")
                
                # 4.1 删除演员关联信息（file_actors表）
                cursor.execute("DELETE FROM file_actors WHERE file_id = ?", (file_id,))
                actors_deleted = cursor.rowcount
                
                # 4.2 清除视频采集字段（media_files表）
                cursor.execute("""
                    UPDATE media_files 
                    SET video_code = NULL,
                        video_title = NULL,
                        release_date = NULL,
                        duration = NULL,
                        last_modified = datetime('now')
                    WHERE file_id = ?
                """, (file_id,))
                
                conn.commit()
                
                # 5. 更新内存缓存
                with self.db._cache_lock:
                    # 更新文件缓存
                    if video_path in self.db._cache['files']:
                        self.db._cache['files'][video_path]['video_code'] = None
                        self.db._cache['files'][video_path]['video_title'] = None
                        self.db._cache['files'][video_path]['release_date'] = None
                        self.db._cache['files'][video_path]['duration'] = 0
                    
                    # 删除演员关联缓存
                    if file_id in self.db._cache['file_actors']:
                        del self.db._cache['file_actors'][file_id]
                
                # 6. 刷新数据库缓存（后台异步）
                self.db._load_all_to_cache_async()
                
                # 7. 从同步集合中移除（移除同步标识）
                if hasattr(self, '_synced_video_paths') and video_path in self._synced_video_paths:
                    self._synced_video_paths.discard(video_path)
                    # 更新UI中的同步标识
                    self.update_video_sync_status(video_path, False)
                
                # 8. 刷新图库显示（更新信息面板和缩略图）
                self.refresh(keep_scroll=True)
                
                # 9. 记录日志
                if self.debug:
                    self.debug.print(f"\n{'='*60}")
                    self.debug.print(f"[删除采集信息] {video_name}")
                    self.debug.print(f"  数据库ID: {file_id}")
                    self.debug.print(f"  清除前信息:")
                    self.debug.print(f"    番号: {old_code}")
                    self.debug.print(f"    标题: {old_title[:50]}{'...' if len(old_title) > 50 else ''}")
                    self.debug.print(f"    日期: {old_date}")
                    self.debug.print(f"    时长: {old_duration}秒")
                    self.debug.print(f"    演员: {', '.join(old_actors) if old_actors else '无'}")
                    self.debug.print(f"  删除结果:")
                    self.debug.print(f"    演员关联删除: {actors_deleted} 条")
                    self.debug.print(f"{'='*60}")
                
                # 10. 显示成功消息
                actor_msg = f"\n演员关联: {actors_deleted} 个已删除" if actors_deleted > 0 else ""
                messagebox.showinfo(
                    "删除成功",
                    f"已清除视频采集信息:\n\n"
                    f"视频: {video_name}\n"
                    f"番号: {old_code}{actor_msg}"
                )
                
                # 11. 清除顶部信息面板（如果当前显示的是这个视频）
                if getattr(self, '_current_info_video_path', None) == video_path:
                    self._update_info_panel(None)
                
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
                
        except Exception as e:
            error_msg = str(e)
            if self.debug:
                self.debug.print(f"[删除采集信息失败] {error_msg}")
                import traceback
                traceback.print_exc()
            messagebox.showerror("删除失败", f"清除采集信息时出错:\n{error_msg}")

    def _delete_video(self, video_path, frame, img_path):
        """
        删除视频 - 支持情况A(单图单视频删文件夹)和情况B(同名匹配删文件)
        
        Args:
            video_path: 视频文件路径
            frame: 缩略图frame对象
            img_path: 当前缩略图对应的图片路径
        """
        if not video_path or not os.path.exists(video_path):
            messagebox.showwarning("提示", "视频文件不存在")
            return
        
        video_folder = os.path.dirname(video_path)
        video_name = os.path.basename(video_path)
        video_basename = os.path.splitext(video_name)[0].lower()
        folder_name = os.path.basename(video_folder)
        
        # ========== 第一步：判断删除类型（A或B）==========
        delete_mode = None  # 'folder' = 情况A, 'files' = 情况B
        delete_target_desc = ""
        
        try:
            # 扫描当前文件夹内容
            entries = list(os.scandir(video_folder))
        except Exception as e:
            messagebox.showerror("错误", f"无法扫描文件夹: {e}")
            return
        
        files_in_folder = []
        sub_folders = []
        videos_in_folder = []
        images_in_folder = []
        
        for entry in entries:
            if entry.is_dir():
                sub_folders.append(entry)
            elif entry.is_file():
                files_in_folder.append(entry)
                ext = os.path.splitext(entry.name)[1].lower()
                if PathUtils.is_video_file(entry.path):
                    videos_in_folder.append(entry)
                elif PathUtils.is_image_file(entry.path):
                    images_in_folder.append(entry)
        
        # 检查子文件夹中是否有视频（递归检查一层）
        has_video_in_subfolders = False
        for sub in sub_folders:
            try:
                for sub_entry in os.scandir(sub.path):
                    if sub_entry.is_file() and PathUtils.is_video_file(sub_entry.path):
                        has_video_in_subfolders = True
                        break
            except:
                pass
            if has_video_in_subfolders:
                break
        
        # ========== 前置检查：禁止删除条件 ==========
        if len(videos_in_folder) > 1:
            messagebox.showwarning(
                "禁止删除",
                f"该文件夹下存在 {len(videos_in_folder)} 个视频文件。\n\n"
                f"只允许在文件夹下仅有1个视频时删除。"
            )
            return
        
        if has_video_in_subfolders:
            messagebox.showwarning(
                "禁止删除",
                f"该文件夹的子文件夹中存在视频文件。\n\n"
                f"为避免误删，不允许删除。"
            )
            return
        
        # ========== 判断情况A：单图单视频 ==========
        if len(videos_in_folder) == 1 and len(images_in_folder) == 1:
            delete_mode = 'folder'
            delete_target_desc = f"文件夹: {folder_name}\n(包含: {images_in_folder[0].name} + {videos_in_folder[0].name})"
        
        # ========== 判断情况B：同名匹配（当前文件夹下的专属匹配）==========
        elif len(videos_in_folder) == 1 and len(images_in_folder) > 1:
            # 检查是否有与视频同名的图片
            matched_img = None
            for img in images_in_folder:
                img_basename = os.path.splitext(img.name)[0].lower()
                if img_basename == video_basename:
                    matched_img = img
                    break
            
            if matched_img:
                delete_mode = 'files'
                delete_target_desc = f"文件: {matched_img.name} + {video_name}"
            else:
                messagebox.showwarning(
                    "禁止删除",
                    f"该视频没有同名匹配的图片文件。\n\n"
                    f"只允许删除有专属缩略图匹配的视频。"
                )
                return
        
        # ========== 判断情况B扩展：多视频场景中的当前选中项 ==========
        elif len(videos_in_folder) == 1 and len(images_in_folder) == 0:
            # 无图片的视频占位符，检查是否有外部关联图片
            # 这种情况通常是子文件夹中的视频，但前面已经检查了子文件夹
            messagebox.showwarning(
                "禁止删除",
                f"该视频没有对应的缩略图文件。\n\n"
                f"只允许删除有图片关联的视频。"
            )
            return
        
        if not delete_mode:
            messagebox.showwarning("禁止删除", "当前情况不符合删除条件。")
            return
        
        # ========== 第二步：第一次确认 ==========
        first_confirm = messagebox.askyesno(
            "第一次确认 - 删除视频",
            f"⚠️ 您即将删除视频文件，此操作不可撤销！\n\n"
            f"删除模式: {'删除整个文件夹' if delete_mode == 'folder' else '删除同名文件'}\n"
            f"目标: {delete_target_desc}\n\n"
            f"路径: {video_folder}\n\n"
            f"是否继续？",
            icon="warning"
        )
        
        if not first_confirm:
            return
        
        # ========== 第三步：第二次确认（更明确的警告）==========
        if delete_mode == 'folder':
            warning_text = (
                f"🔴 最终确认 - 删除文件夹\n\n"
                f"文件夹: {folder_name}\n"
                f"路径: {video_folder}\n\n"
                f"将永久删除以下内容:\n"
                f"• 视频文件: {videos_in_folder[0].name}\n"
                f"• 图片文件: {images_in_folder[0].name}\n"
                f"• 整个文件夹将被移除\n\n"
                f"数据库中的相关记录也将清除。\n\n"
                f"⚠️ 此操作不可撤销！确定要删除吗？"
            )
        else:
            warning_text = (
                f"🔴 最终确认 - 删除文件\n\n"
                f"视频: {video_name}\n"
                f"图片: {matched_img.name}\n"
                f"路径: {video_folder}\n\n"
                f"将永久删除以上两个文件。\n\n"
                f"数据库中的相关记录也将清除。\n\n"
                f"⚠️ 此操作不可撤销！确定要删除吗？"
            )
        
        second_confirm = messagebox.askyesno(
            "第二次确认 - 最终确认",
            warning_text,
            icon="warning"
        )
        
        if not second_confirm:
            return
        
        # ========== 第四步：执行删除 ==========
        # 4.1 先删除数据库记录
        db_success = self._delete_video_from_database(video_path, video_folder, delete_mode)
        if not db_success:
            messagebox.showerror("错误", "数据库记录删除失败，已中止文件删除操作。")
            return
        
        # 4.2 删除实体文件/文件夹
        file_success = True
        deleted_items = []
        error_msg = ""
        
        try:
            if delete_mode == 'folder':
                # 情况A：删除整个文件夹
                import shutil
                shutil.rmtree(video_folder)
                deleted_items.append(f"文件夹: {folder_name}")
            else:
                # 情况B：删除同名文件
                os.remove(video_path)
                deleted_items.append(f"视频: {video_name}")
                
                if matched_img and os.path.exists(matched_img.path):
                    os.remove(matched_img.path)
                    deleted_items.append(f"图片: {matched_img.name}")
                
                # 检查文件夹是否为空，如果是则删除空文件夹
                remaining = [e for e in os.scandir(video_folder) if e.is_file()]
                if not remaining:
                    os.rmdir(video_folder)
                    deleted_items.append(f"空文件夹: {folder_name} (已自动清理)")
        
        except Exception as e:
            file_success = False
            error_msg = str(e)
            if self.debug:
                self.debug.print(f"[删除文件失败] {e}")
                import traceback
                traceback.print_exc()
        
        # ========== 第五步：刷新UI ==========
        if file_success:
            # 刷新图库
            self.refresh(keep_scroll=True)
            
            # 记录日志
            if self.debug:
                self.debug.print(f"\n{'='*60}")
                self.debug.print(f"[删除视频成功]")
                self.debug.print(f"  模式: {'删除文件夹' if delete_mode == 'folder' else '删除文件'}")
                for item in deleted_items:
                    self.debug.print(f"  ✓ {item}")
                self.debug.print(f"{'='*60}")
            
            # 显示成功消息
            deleted_str = "\n".join(deleted_items)
            messagebox.showinfo(
                "删除成功",
                f"已成功删除:\n\n{deleted_str}"
            )
        else:
            messagebox.showerror(
                "部分删除失败",
                f"数据库记录已删除，但文件删除时出错:\n{error_msg}\n\n"
                f"请手动检查文件夹: {video_folder}"
            )
            # 仍然刷新UI，因为数据库记录已清除
            self.refresh(keep_scroll=True)

    def _delete_video_from_database(self, video_path, video_folder, delete_mode):
        """
        从数据库中删除视频相关信息
        顺序：文件夹信息 -> 视频信息/采集信息/演员关联 -> 文件记录
        
        Args:
            video_path: 视频文件路径
            video_folder: 视频所在文件夹路径
            delete_mode: 'folder' 或 'files'
        
        Returns:
            bool: 是否成功
        """
        if not self.db or not getattr(self.db, 'is_ready', False):
            return True  # 数据库未就绪，跳过数据库删除，直接删文件
        
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("BEGIN IMMEDIATE")
                
                # 1. 获取视频文件的数据库ID
                cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (video_path,))
                result = cursor.fetchone()
                file_id = result['file_id'] if result else None
                
                # 2. 获取文件夹的数据库ID
                cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (video_folder,))
                folder_result = cursor.fetchone()
                folder_id = folder_result['folder_id'] if folder_result else None
                
                if self.debug:
                    self.debug.print(f"[数据库删除] 视频ID: {file_id}, 文件夹ID: {folder_id}")
                
                # 3. 删除演员关联（file_actors表）
                if file_id:
                    cursor.execute("DELETE FROM file_actors WHERE file_id = ?", (file_id,))
                    actors_deleted = cursor.rowcount
                    if self.debug and actors_deleted > 0:
                        self.debug.print(f"[数据库删除] 清除演员关联: {actors_deleted} 条")
                
                # 4. 删除视频关联关系（video_relations表）
                # 4.1 如果该视频是主视频，删除所有关联到它的记录
                if file_id:
                    cursor.execute("DELETE FROM video_relations WHERE primary_file_id = ?", (file_id,))
                    primary_relations_deleted = cursor.rowcount
                
                # 4.2 如果该视频是关联视频，删除它的关联记录
                if file_id:
                    cursor.execute("DELETE FROM video_relations WHERE alias_file_id = ?", (file_id,))
                    alias_relations_deleted = cursor.rowcount
                
                # 5. 删除媒体文件记录（media_files表）
                if file_id:
                    cursor.execute("DELETE FROM media_files WHERE file_id = ?", (file_id,))
                
                # 6. 如果是情况A（删除文件夹），还需要：
                #    - 删除文件夹下其他文件的记录
                #    - 删除文件夹记录
                if delete_mode == 'folder' and folder_id:
                    # 6.1 删除该文件夹下所有文件记录
                    cursor.execute("DELETE FROM media_files WHERE folder_id = ?", (folder_id,))
                    files_deleted = cursor.rowcount
                    if self.debug and files_deleted > 0:
                        self.debug.print(f"[数据库删除] 清除文件夹内文件记录: {files_deleted} 条")
                    
                    # 6.2 删除文件夹记录
                    cursor.execute("DELETE FROM folders WHERE folder_id = ?", (folder_id,))
                
                # 7. 如果是情况B（删除文件），检查文件夹是否还有文件
                elif delete_mode == 'files' and folder_id:
                    cursor.execute("SELECT COUNT(*) as count FROM media_files WHERE folder_id = ?", (folder_id,))
                    remaining = cursor.fetchone()['count']
                    if remaining == 0:
                        # 文件夹内无文件，删除文件夹记录
                        cursor.execute("DELETE FROM folders WHERE folder_id = ?", (folder_id,))
                        if self.debug:
                            self.debug.print(f"[数据库删除] 文件夹已空，清除文件夹记录")
                
                conn.commit()
                
                # 8. 更新内存缓存
                with self.db._cache_lock:
                    # 删除文件缓存
                    if video_path in self.db._cache['files']:
                        del self.db._cache['files'][video_path]
                    
                    # 删除演员关联缓存
                    if file_id and file_id in self.db._cache['file_actors']:
                        del self.db._cache['file_actors'][file_id]
                    
                    # 删除视频关联缓存
                    if file_id:
                        # 删除该视频作为alias的关联
                        alias_ids_to_remove = []
                        for alias_id, primary_id in list(self.db._cache['video_relations'].items()):
                            if alias_id == file_id or primary_id == file_id:
                                alias_ids_to_remove.append(alias_id)
                        for aid in alias_ids_to_remove:
                            if aid in self.db._cache['video_relations']:
                                del self.db._cache['video_relations'][aid]
                    
                    # 如果是删除文件夹，删除文件夹缓存
                    if delete_mode == 'folder':
                        if video_folder in self.db._cache['folders']:
                            del self.db._cache['folders'][video_folder]
                        # 删除该文件夹下所有文件缓存
                        paths_to_remove = [p for p in self.db._cache['files'] 
                                          if self.db._cache['files'][p].get('folder_path') == video_folder]
                        for p in paths_to_remove:
                            del self.db._cache['files'][p]
                
                # 9. 异步刷新数据库缓存
                self.db._load_all_to_cache_async()
                
                if self.debug:
                    self.debug.print(f"[数据库删除] 完成")
                
                return True
                
            except Exception as e:
                conn.rollback()
                if self.debug:
                    self.debug.print(f"[数据库删除失败] {e}")
                    import traceback
                    traceback.print_exc()
                return False
            finally:
                cursor.close()
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"[数据库删除异常] {e}")
            return False
        
    def _collect_single_video_sync(self, video_path, code):
        """
        同步采集单个视频信息（用于批量采集）
        
        Args:
            video_path: 视频文件路径
            code: 番号
            
        Returns:
            tuple: (success, info_dict, used_url)
        """
        import threading
        
        result_event = threading.Event()
        crawl_result = {'success': False, 'info': {}, 'used_url': None}
        
        def callback(success, info):
            crawl_result['success'] = success
            crawl_result['info'] = info
            # 保存使用的网址
            crawl_result['used_url'] = info.get('source_url', '') if success else info.get('source_url', '')
            result_event.set()
        
        try:
            self.on_collect_video_info(video_path, code, callback=callback)
            
            # 等待结果（最多90秒，网络请求可能较慢）
            timeout = 90
            result_event.wait(timeout)
            
            if crawl_result['success']:
                return True, crawl_result['info'], crawl_result['used_url']
            else:
                return False, crawl_result['info'], crawl_result['used_url']
                
        except Exception as e:
            return False, {'error': str(e)}, None
        
    def _update_batch_status(self, message, progress, total):
        """更新批量采集状态（使用底部进度条）"""
        try:
            # 更新状态栏
            if hasattr(self.parent, 'master') and hasattr(self.parent.master, 'status_label'):
                status_label = self.parent.master.status_label
                status_label.config(text=message, foreground="blue")
            
            # 更新进度条
            if hasattr(self, 'progress') and self.progress.winfo_exists():
                self.progress['value'] = progress
                self.progress.update_idletasks()
            
            # 更新计数标签
            if hasattr(self.parent, 'master') and hasattr(self.parent.master, 'count_label'):
                count_label = self.parent.master.count_label
                count_label.config(text=f"采集进度: {progress}%")
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"更新状态失败: {e}")

    def _log_batch_message(self, message, is_error=False):
        """在操作日志中记录批量采集消息"""
        import time
        timestamp = time.strftime("%H:%M:%S")
        
        # 输出到调试日志
        if self.debug:
            if is_error:
                self.debug.print(f"[{timestamp}] {message}")
            else:
                self.debug.print(f"[{timestamp}] {message}")
        
        # 输出到UI日志（通过主程序）
        try:
            if hasattr(self.parent, 'master') and hasattr(self.parent.master, 'debug'):
                main_debug = self.parent.master.debug
                if main_debug and hasattr(main_debug, 'print'):
                    main_debug.print(message)
        except Exception as e:
            pass

    def _refresh_gallery_sync(self):
        """刷新图库以更新同步标识"""
        try:
            if hasattr(self, 'refresh') and callable(self.refresh):
                self.refresh(keep_scroll=True)
                if self.debug:
                    self.debug.print("[批量采集] 图库已刷新，同步标识已更新")
        except Exception as e:
            if self.debug:
                self.debug.print(f"[批量采集] 刷新图库失败: {e}")

    def _show_log_tab(self):
        """切换到日志标签页"""
        try:
            # 查找主窗口的 notebook 并切换到日志标签页
            root = self.parent.winfo_toplevel()
            if hasattr(root, 'notebook'):
                # 查找日志标签页索引
                for i in range(root.notebook.index('end')):
                    tab_text = root.notebook.tab(i, "text")
                    if tab_text == "操作日志":
                        root.notebook.select(i)
                        break
        except Exception as e:
            if self.debug:
                self.debug.print(f"切换到日志标签页失败: {e}")

    def _count_videos_in_tree(self, root_folder):
        """统计文件夹树中的视频文件数量"""
        from core import PathUtils
        video_count = 0
        
        try:
            for dirpath, dirnames, filenames in os.walk(root_folder):
                # 跳过隐藏文件夹
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for filename in filenames:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in PathUtils.VIDEO_EXTENSIONS:
                        video_count += 1
        except Exception as e:
            if self.debug:
                self.debug.print(f"统计视频数量失败: {e}")
        
        return video_count

    def _get_all_videos_in_tree(self, root_folder):
        """获取文件夹树中的所有视频文件路径"""
        from core import PathUtils
        video_files = []
        
        try:
            for dirpath, dirnames, filenames in os.walk(root_folder):
                # 跳过隐藏文件夹
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for filename in filenames:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in PathUtils.VIDEO_EXTENSIONS:
                        full_path = os.path.join(dirpath, filename)
                        video_files.append(full_path)
        except Exception as e:
            if self.debug:
                self.debug.print(f"扫描视频文件失败: {e}")
        
        return video_files

    def _collect_folder_videos_info(self, folder_path):
        """
        批量采集文件夹下所有视频的信息
        
        Args:
            folder_path: 文件夹路径
        """
        from core import PathUtils, extract_code_from_text
        import threading
        import time
        
        # 扫描文件夹中的视频文件
        video_files = PathUtils.find_video_files(folder_path)
        
        if not video_files:
            messagebox.showinfo("提示", f"文件夹中没有视频文件:\n{folder_path}")
            return
        
        # 确认对话框
        result = messagebox.askyesno(
            "批量采集确认", 
            f"将采集以下文件夹中的 {len(video_files)} 个视频信息:\n\n"
            f"{folder_path}\n\n"
            f"每个视频将根据番号联网查询，请确保网络正常。\n"
            f"采集过程可能需要较长时间，是否继续？",
            icon="question"
        )
        
        if not result:
            return
        
        # 检查数据库是否就绪
        if not hasattr(self, 'on_collect_video_info') or not self.on_collect_video_info:
            # 尝试获取主程序的采集回调
            if hasattr(self.parent, 'master') and hasattr(self.parent.master, 'on_collect_video_info'):
                self.on_collect_video_info = self.parent.master.on_collect_video_info
        
        if not self.on_collect_video_info:
            messagebox.showerror("错误", "采集功能未就绪，请检查爬虫配置")
            return
        
        # 创建进度对话框
        progress_dialog = tk.Toplevel(self.parent)
        progress_dialog.title("批量采集视频信息")
        progress_dialog.geometry("500x400")
        progress_dialog.transient(self.parent)
        progress_dialog.grab_set()
        
        # 进度条
        ttk.Label(progress_dialog, text=f"正在采集 {len(video_files)} 个视频...", 
                font=("微软雅黑", 10)).pack(pady=10)
        
        progress_bar = ttk.Progressbar(progress_dialog, mode='determinate', 
                                    maximum=len(video_files))
        progress_bar.pack(fill=tk.X, padx=20, pady=5)
        
        # 当前处理视频标签
        current_label = ttk.Label(progress_dialog, text="准备开始...", 
                                font=("微软雅黑", 9), foreground="blue")
        current_label.pack(fill=tk.X, padx=20, pady=5)
        
        # 日志区域
        log_frame = ttk.LabelFrame(progress_dialog, text="采集日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, 
                                            font=("Consolas", 9), height=12)
        log_text.pack(fill=tk.BOTH, expand=True)
        
        # 关闭按钮（初始禁用）
        close_btn = ttk.Button(progress_dialog, text="关闭", 
                            command=progress_dialog.destroy, state="disabled")
        close_btn.pack(pady=10)
        
        # 结果统计
        results = {
            'total': len(video_files),
            'success': 0,
            'failed': 0,
            'failed_list': []
        }
        
        def log_message(msg, is_error=False):
            """在日志区域添加消息"""
            timestamp = time.strftime("%H:%M:%S")
            tag = "error" if is_error else "info"
            log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
            if is_error:
                log_text.tag_add("error", "end-2l", "end-1l")
                log_text.tag_config("error", foreground="red")
            log_text.see(tk.END)
            progress_dialog.update_idletasks()
        
        def update_progress(current, video_name, status):
            """更新进度显示"""
            progress_bar['value'] = current
            current_label.config(text=f"[{current}/{results['total']}] {video_name}: {status}")
            progress_dialog.update_idletasks()
        
        def collect_single_video(video_path, index):
            """采集单个视频"""
            from core import extract_code_from_text
            
            video_name = os.path.basename(video_path)
            folder_name = os.path.basename(os.path.dirname(video_path))
            
            # 提取番号
            video_code = extract_code_from_text(video_name)
            folder_code = extract_code_from_text(folder_name)
            
            code = ""
            if video_code:
                code = video_code['canonical']
            elif folder_code:
                code = folder_code['canonical']
            
            if not code:
                log_message(f"⚠️ 跳过 {video_name}: 无法提取番号", is_error=True)
                return {'success': False, 'reason': '无法提取番号'}
            
            # 创建事件对象用于同步等待
            import threading
            result_event = threading.Event()
            crawl_result = {'success': False, 'info': {}}
            
            def callback(success, info):
                crawl_result['success'] = success
                crawl_result['info'] = info
                result_event.set()
            
            # 调用采集回调
            try:
                self.on_collect_video_info(video_path, code, callback=callback)
                
                # 等待结果（最多60秒）
                timeout = 60
                result_event.wait(timeout)
                
                if crawl_result['success']:
                    actors = crawl_result['info'].get('actors', [])
                    actor_str = ', '.join(actors[:3]) + ('...' if len(actors) > 3 else '')
                    log_message(f"✓ 成功: {video_name} -> {code} (演员: {actor_str})")
                    return {'success': True, 'code': code}
                else:
                    error = crawl_result['info'].get('error', '未知错误')
                    log_message(f"✗ 失败: {video_name} -> {code} ({error})", is_error=True)
                    return {'success': False, 'reason': error}
                    
            except Exception as e:
                log_message(f"✗ 异常: {video_name} -> {str(e)}", is_error=True)
                return {'success': False, 'reason': str(e)}
        
        def collect_all():
            """批量采集所有视频"""
            for i, video_path in enumerate(video_files):
                # 更新状态
                video_name = os.path.basename(video_path)
                update_progress(i, video_name, "正在采集...")
                
                # 采集
                result = collect_single_video(video_path, i)
                
                if result['success']:
                    results['success'] += 1
                else:
                    results['failed'] += 1
                    results['failed_list'].append({
                        'name': video_name,
                        'reason': result.get('reason', '未知错误')
                    })
                
                # 更新进度
                update_progress(i + 1, video_name, 
                            "完成" if result['success'] else f"失败: {result.get('reason', '')[:30]}")
                
                # 检查是否取消（如果对话框被销毁则停止）
                if not progress_dialog.winfo_exists():
                    break
            
            # 显示最终结果
            log_message("\n" + "=" * 50)
            log_message(f"采集完成！总计: {results['total']}, 成功: {results['success']}, 失败: {results['failed']}")
            
            if results['failed_list']:
                log_message("\n失败列表:")
                for item in results['failed_list']:
                    log_message(f"  - {item['name']}: {item['reason']}", is_error=True)
            
            # 刷新图库（更新同步标识）
            if results['success'] > 0:
                try:
                    self.refresh(keep_scroll=True)
                    log_message("\n已刷新图库，同步标识已更新")
                except Exception as e:
                    log_message(f"刷新图库失败: {e}", is_error=True)
            
            # 启用关闭按钮
            close_btn.config(state="normal")
            
            # 根据结果显示不同的消息框
            if results['failed'] == 0:
                messagebox.showinfo("批量采集完成", 
                                f"成功采集 {results['success']} 个视频的信息！")
            else:
                messagebox.showwarning("批量采集完成", 
                                    f"采集完成！\n成功: {results['success']}\n失败: {results['failed']}\n\n详细信息请查看日志窗口")
        
        # 启动后台采集线程
        threading.Thread(target=collect_all, daemon=True).start()

    def _replace_thumbnail(self, current_img_path, video_path):
        """替换视频的缩略图"""
        folder = os.path.dirname(video_path)
        video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
        current_basename = os.path.splitext(os.path.basename(current_img_path))[0].lower()
        
        # 如果是同名文件，已经是缩略图
        if current_basename == video_basename:
            messagebox.showinfo("提示", "当前图片已是视频的缩略图（同名文件）")
            return
        
        # 获取该文件夹下所有图片
        from core import PathUtils
        images = []
        for entry in os.scandir(folder):
            if entry.is_file() and PathUtils.is_image_file(entry.path):
                images.append(entry.path)
        
        if len(images) <= 1:
            messagebox.showinfo("提示", "没有其他图片可用")
            return
        
        # 显示选择对话框
        selected = self._show_thumbnail_selector(folder, video_path)
        if selected and selected != current_img_path:
            # 询问是否删除原缩略图
            if messagebox.askyesno("确认替换", 
                f"将使用 '{os.path.basename(selected)}' 作为缩略图。\n\n"
                f"是否删除原缩略图 '{os.path.basename(current_img_path)}'？"):
                try:
                    os.remove(current_img_path)
                except:
                    pass
            
            # 复制或移动新缩略图
            img_ext = os.path.splitext(selected)[1]
            new_path = os.path.join(folder, video_basename + img_ext)
            
            if os.path.exists(new_path) and new_path != selected:
                if not messagebox.askyesno("文件已存在", f"目标文件 '{os.path.basename(new_path)}' 已存在，是否覆盖？"):
                    return
                try:
                    os.remove(new_path)
                except:
                    pass
            
            try:
                import shutil
                shutil.copy2(selected, new_path)
                messagebox.showinfo("成功", f"缩略图已更新为 '{os.path.basename(new_path)}'")
                self.refresh(keep_scroll=True)
            except Exception as e:
                messagebox.showerror("错误", f"替换缩略图失败: {e}")

    def load_from_db(self, files_info, status_callback=None, scroll_y=None):
        """从数据库加载文件 - 修复：确保正确获取关联视频信息"""
        self._load_generation += 1
        current_gen = self._load_generation
        
        # 清理
        self.clear()
        
        if not files_info:
            if status_callback:
                status_callback("数据库中无文件", 100)
            return
        
        # 去重
        seen_paths = set()
        unique_files = []
        for f in files_info:
            fp = f.get('file_path')
            if not fp:
                continue
            normalized = os.path.normpath(fp).lower().replace('\\', '/') if fp != "__VIDEO_PLACEHOLDER__" else fp
            if normalized not in seen_paths:
                seen_paths.add(normalized)
                unique_files.append(f)
        
        files_info = unique_files
        total = len(files_info)
        
        if total == 0:
            if status_callback:
                status_callback("无有效文件", 100)
            return
        
        # ==================== 关键修复：预加载数据库关联信息 ====================
        db_alias_map = {}  # {img_path: primary_video_path}
        if self.db:
            for f in files_info:
                file_path = f.get('file_path')
                file_type = f.get('file_type', '')
                is_image = (file_type == 'image') or (file_path and file_path != "__VIDEO_PLACEHOLDER__" and file_type != 'video')
                if is_image and not f.get('primary_video_path'):
                    try:
                        db_file = self.db.get_file_by_path(file_path)
                        if db_file and db_file.get('is_alias'):
                            primary_path = self.db.get_primary_video_path(db_file.get('id'))
                            if primary_path:
                                db_alias_map[file_path] = primary_path
                                # 将 id 和 is_alias 写回 file_info，供后续使用
                                f['id'] = db_file.get('id')
                                f['is_alias'] = 1
                                f['primary_video_path'] = primary_path  # 关键：写入 primary_video_path
                                f['video_path'] = primary_path  # 同时设置 video_path
                                f['match_type'] = 'linked'
                                if self.debug:
                                    self.debug.print(f"数据库关联发现: {os.path.basename(file_path)} -> {os.path.basename(primary_path)}")
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"查询关联失败 {file_path}: {e}")
                
        # 准备待渲染的控件
        self._pending_widgets = []
        self._current_item_index = 0

        for i, file_info in enumerate(files_info):
            if current_gen != self._load_generation:
                return
            
            db_file_path = file_info.get('file_path')
            if not db_file_path or (not os.path.exists(db_file_path) and db_file_path != "__VIDEO_PLACEHOLDER__"):
                continue
            
            folder_name = os.path.basename(file_info.get('folder_path', '')) if file_info.get('folder_path') else "[未知]"
            thumb_path = None
            video_path = None
            
            # 修复：扩展视频类型判断，包含 video_placeholder，确保缓存加载时能正确处理纯视频项
            is_video_type = (file_info.get('file_type') in ('video', 'video_placeholder'))
            
            if is_video_type:
                thumb_path = self._find_video_thumbnail(db_file_path)
                video_path = file_info.get('video_path') or db_file_path
                if not thumb_path:
                    thumb_path = "__VIDEO_PLACEHOLDER__"
            else:
                thumb_path = db_file_path
                # 修复：优先使用缓存中保存的 video_path，避免 _find_video_for_image 推断失败
                video_path = file_info.get('video_path')
                if not video_path:
                    video_path = self._find_video_for_image(db_file_path)
            
            # 检查关联视频（数据库关系）
            primary_video_path = file_info.get('primary_video_path')
            file_id = file_info.get('id') or file_info.get('file_id')
            is_alias = file_info.get('is_alias', 0)
            is_alias_flag = (is_alias == 1) if isinstance(is_alias, int) else bool(is_alias)
            
            # 应用预加载的关联信息
            if not primary_video_path and db_file_path in db_alias_map:
                primary_video_path = db_alias_map[db_file_path]
                video_path = primary_video_path
                file_info['primary_video_path'] = primary_video_path
                file_info['is_alias'] = 1
                file_info['match_type'] = 'linked'
                is_alias_flag = True
            elif self.db and file_id and is_alias_flag:
                # 备用：单独查询（处理已有的 is_alias 标记但无 primary_video_path 的情况）
                primary_video_path = self.db.get_primary_video_path(file_id)
                if primary_video_path:
                    video_path = primary_video_path
            elif self.db and not file_id and not primary_video_path:
                # 缓存数据没有 ID，尝试用路径查询
                try:
                    db_file = self.db.get_file_by_path(db_file_path)
                    if db_file and db_file.get('is_alias'):
                        file_id = db_file.get('id')
                        primary_video_path = self.db.get_primary_video_path(file_id)
                        if primary_video_path:
                            video_path = primary_video_path
                            file_info['primary_video_path'] = primary_video_path
                            file_info['is_alias'] = 1
                            file_info['match_type'] = 'linked'
                            if self.debug:
                                self.debug.print(f"实时查询关联: {os.path.basename(db_file_path)} -> {os.path.basename(primary_video_path)}")
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"实时查询关联失败 {db_file_path}: {e}")
            
            thumb = self._create_thumbnail(thumb_path, self.config.thumbnail_width)
            
            if thumb:
                # 检查是否有附加缩略图（从缓存的alternates读取）
                alternates = file_info.get('alternates', []) if file_info else []
                alternate_thumbs = []
                if alternates:
                    for alt_path in alternates:
                        if alt_path and os.path.exists(alt_path):
                            alt_thumb = self._create_thumbnail(alt_path, self.config.thumbnail_width)
                            if alt_thumb:
                                alternate_thumbs.append({'path': alt_path, 'thumb': alt_thumb})
                
                # 查询视频的关联数量（如果是主视频）
                # 优先使用缓存中的值，如果缓存中没有则查询数据库
                alias_count = file_info.get('alias_count', 0)
                if self.db and video_path and not primary_video_path and alias_count == 0:
                    try:
                        db_video = self.db.get_file_by_path(video_path)
                        if db_video and not db_video.get('is_alias'):
                            aliases = self.db.get_related_videos(db_video.get('id'))
                            alias_count = len(aliases) if aliases else 0
                            file_info['alias_count'] = alias_count
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"查询视频关联数量失败: {e}")
                
                self._pending_widgets.append({
                    'index': len(self._pending_widgets),
                    'folder_name': folder_name,
                    'thumb_path': thumb_path,
                    'thumb': thumb,
                    'alternates': alternate_thumbs,
                    'video_path': video_path,
                    'primary_video_path': primary_video_path,
                    'file_info': file_info,
                    'gen': current_gen
                })
            
            if status_callback and i % 10 == 0:
                progress = int((i + 1) / total * 100)
                msg = f"准备中... ({i+1}/{total})"
                self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
        
        # 批量渲染
        def batch_render(start_idx=0, batch_size=5):
            if start_idx >= len(self._pending_widgets):
                # 完成
                if status_callback:
                    self.parent.after(0, lambda: status_callback("完成", 100))
                
                if scroll_y is not None:
                    def restore():
                        if current_gen == self._load_generation:
                            self.set_scroll_y(scroll_y)
                    delay = min(200 + len(self._pending_widgets) * 2, 500)
                    self.parent.after(delay, restore)
                
                # ========== 修复：渲染完成后执行一系列操作 ==========
                def on_render_complete():
                    # 1. 自动选中第一个有视频的项
                    self._auto_select_first_video()
                    
                    # 2. 更新同步标识
                    self._update_synced_indicators()
                    
                    # 3. 通知加载完成回调
                    self._notify_load_complete(
                        self._current_folder if hasattr(self, '_current_folder') else None
                    )
                    
                    # 4. 延迟再次检查视频信息
                    if self._current_folder:
                        self.parent.after(500, lambda: self._ensure_info_after_load(self._current_folder))
                
                self.parent.after(250, on_render_complete)
                return
            
            end_idx = min(start_idx + batch_size, len(self._pending_widgets))
            
            for widget_info in self._pending_widgets[start_idx:end_idx]:
                if widget_info['gen'] != self._load_generation:
                    return
                
                # 如果有附加缩略图，使用带切换功能的方法
                if widget_info.get('alternates'):
                    self._add_thumbnail_with_alternates(
                        widget_info['index'],
                        widget_info['folder_name'],
                        widget_info['thumb_path'],
                        widget_info['thumb'],
                        widget_info['alternates'],
                        widget_info['video_path'],
                        widget_info['primary_video_path'],
                        widget_info['file_info'].get('match_type', 'standalone') if widget_info['file_info'] else 'standalone',
                        widget_info['file_info']
                    )
                else:
                    self._add_thumbnail_widget(
                        widget_info['index'],
                        widget_info['folder_name'],
                        widget_info['thumb_path'],
                        widget_info['thumb'],
                        video_path=widget_info['video_path'],
                        primary_video_path=widget_info['primary_video_path'],
                        file_info=widget_info['file_info'],
                        alternates=widget_info['file_info'].get('alternates', []) if widget_info.get('file_info') else None
                    )
            
            if status_callback:
                progress = min(100, int((end_idx / len(self._pending_widgets)) * 100)) if self._pending_widgets else 100
                msg = f"渲染中... ({end_idx}/{len(self._pending_widgets)})"
                self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
            
            # 继续下一批
            if end_idx < len(self._pending_widgets):
                self.parent.after(10, lambda: batch_render(end_idx, batch_size))
        
        if self._pending_widgets:
            batch_render(0, 5)
        else:
            if status_callback:
                status_callback("无可显示文件", 100)

    def load_items(self, items, status_callback=None, scroll_y=None):
        """加载处理好的项目列表（支持附加缩略图）- 兼容旧版本"""
        self.load_items_new(items, status_callback, scroll_y)
    
    def load_items_new(self, items, status_callback=None, scroll_y=None):
        """新逻辑：加载处理好的项目列表，支持宫格显示"""
        self._load_generation += 1
        current_gen = self._load_generation
        
        self.clear()
        
        if not items:
            if status_callback:
                status_callback("无项目", 100)
            return
        
        total = len(items)
        
        # 准备渲染
        self._pending_widgets = []
        
        for i, item in enumerate(items):
            if current_gen != self._load_generation:
                return
            
            display_mode = item.get('display_mode', 'single')
            main_img = item['main_image']
            alternates = item.get('alternates', [])
            video_path = item.get('video_path')
            primary_video = item.get('primary_video_path')
            match_type = item.get('match_type', 'standalone')
            folder_name = item['folder_name']
            
            # 宫格显示模式
            if display_mode in ['grid2', 'grid4']:
                grid_images = item.get('grid_images', [])
                if grid_images:
                    grid_thumbs = []
                    for img_path in grid_images:
                        if img_path and os.path.exists(img_path):
                            thumb = self._create_thumbnail(img_path, self.config.thumbnail_width // 2 - 5)
                            if thumb:
                                grid_thumbs.append({'path': img_path, 'thumb': thumb})
                    
                    if grid_thumbs:
                        self._pending_widgets.append({
                            'index': i,
                            'folder_name': folder_name,
                            'display_mode': display_mode,
                            'grid_thumbs': grid_thumbs,
                            'all_images': item.get('all_images', []),
                            'video_path': video_path,
                            'match_type': match_type,
                            'file_info': item,
                            'gen': current_gen
                        })
            
            # 普通单图显示模式
            else:
                if main_img == "__VIDEO_PLACEHOLDER__":
                    thumb = self._create_video_placeholder(self.config.thumbnail_width)
                elif main_img and os.path.exists(main_img):
                    thumb = self._create_thumbnail(main_img, self.config.thumbnail_width)
                else:
                    thumb = None
                
                if thumb:
                    # 如果有附加缩略图，创建所有缩略图
                    alternate_thumbs = []
                    for alt_path in alternates:
                        if os.path.exists(alt_path):
                            alt_thumb = self._create_thumbnail(alt_path, self.config.thumbnail_width)
                            if alt_thumb:
                                alternate_thumbs.append({'path': alt_path, 'thumb': alt_thumb})
                    
                    # 查询视频的关联数量（如果是主视频）
                    # 优先使用传入的值，如果没有则查询数据库
                    if 'alias_count' not in item:
                        if self.db and video_path and not primary_video:
                            try:
                                db_video = self.db.get_file_by_path(video_path)
                                if db_video and not db_video.get('is_alias'):
                                    aliases = self.db.get_related_videos(db_video.get('id'))
                                    item['alias_count'] = len(aliases) if aliases else 0
                            except Exception as e:
                                if self.debug:
                                    self.debug.print(f"load_items_new: 查询视频关联数量失败: {e}")
                    
                    self._pending_widgets.append({
                        'index': i,
                        'folder_name': folder_name,
                        'display_mode': 'single',
                        'main_img': main_img,
                        'main_thumb': thumb,
                        'alternates': alternate_thumbs,
                        'video_path': video_path,
                        'primary_video_path': primary_video,
                        'match_type': match_type,
                        'file_info': item,
                        'gen': current_gen
                    })
            
            if status_callback and i % 5 == 0:
                progress = int((i + 1) / total * 100)
                msg = f"准备中... ({i+1}/{total})"
                self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
        
        # 批量渲染
        def batch_render(start_idx=0, batch_size=3):
            if start_idx >= len(self._pending_widgets):
                if status_callback:
                    self.parent.after(0, lambda: status_callback("完成", 100))
                if scroll_y is not None:
                    self.parent.after(200, lambda: self.set_scroll_y(scroll_y))
                
                # 渲染完成后自动选中第一个有视频的项
                self.parent.after(250, lambda: self._auto_select_first_video())
                
                # ========== 新增：渲染完成后更新同步标识 ==========
                self.parent.after(300, lambda: self._update_synced_indicators())
                return
            
            end_idx = min(start_idx + batch_size, len(self._pending_widgets))
            
            for widget_info in self._pending_widgets[start_idx:end_idx]:
                if widget_info['gen'] != self._load_generation:
                    return
                
                display_mode = widget_info.get('display_mode', 'single')
                
                if display_mode in ['grid2', 'grid4']:
                    self._add_grid_widget(
                        widget_info['index'],
                        widget_info['folder_name'],
                        widget_info['grid_thumbs'],
                        widget_info.get('all_images', []),
                        widget_info['match_type'],
                        widget_info['file_info']
                    )
                elif widget_info.get('alternates'):
                    self._add_thumbnail_with_alternates(
                        widget_info['index'],
                        widget_info['folder_name'],
                        widget_info['main_img'],
                        widget_info['main_thumb'],
                        widget_info['alternates'],
                        widget_info['video_path'],
                        widget_info['primary_video_path'],
                        widget_info['match_type'],
                        widget_info['file_info']
                    )
                else:
                    self._add_thumbnail_widget(
                        widget_info['index'],
                        widget_info['folder_name'],
                        widget_info['main_img'],
                        widget_info['main_thumb'],
                        video_path=widget_info['video_path'],
                        primary_video_path=widget_info['primary_video_path'],
                        file_info=widget_info['file_info'],
                        alternates=widget_info.get('alternates', [])
                    )
            
            if status_callback:
                progress = min(100, int((end_idx / total) * 100)) if total > 0 else 100
                msg = f"渲染中... ({end_idx}/{len(self._pending_widgets)})"
                self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
            
            if end_idx < len(self._pending_widgets):
                self.parent.after(10, lambda: batch_render(end_idx, batch_size))
        
        if self._pending_widgets:
            batch_render(0, 3)
        else:
            if status_callback:
                status_callback("无可显示文件", 100)

    def _add_thumbnail_with_alternates(self, index, folder_name, main_img_path, main_thumb, 
                                       alternates, video_path, primary_video_path, match_type, file_info):
        """添加带附加缩略图切换功能的控件"""
        thumb_width = self.config.thumbnail_width
        thumb_height = self.config.thumbnail_width
        fixed_height = thumb_height + 90  # 额外空间给切换按钮

        frame = tk.Frame(self.images_frame, relief=tk.FLAT, borderwidth=0, bg='white')
        frame.configure(width=thumb_width + 20, height=fixed_height)
        frame.pack_propagate(False)
        
        # 确定背景色和标签（从配置读取）
        if primary_video_path:
            bg_color = self.config.get_gallery_color("linked_video")
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif video_path:
            if match_type == 'dedicated':
                bg_color = self.config.get_gallery_color("dedicated_match")
                label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type == 'cover':
                bg_color = self.config.get_gallery_color("cover_match")
                label_text = "📁 封面匹配"
                label_fg = "teal"
            elif match_type == 'sub_image_only':
                bg_color = self.config.get_gallery_color("sub_image_only")
                label_text = "🖼️ 仅图片"
                label_fg = "#3498DB"
            elif match_type == 'cover_priority':
                bg_color = self.config.get_gallery_color("cover_priority")
                label_text = "📁 封面优先"
                label_fg = "teal"
            elif match_type == 'auto_match':
                bg_color = self.config.get_gallery_color("auto_match")
                label_text = "🔗 自动匹配"
                label_fg = "purple"
            else:
                bg_color = self.config.get_gallery_color("normal_video")
                label_text = "🎬 视频"
                label_fg = "blue"
        else:
            bg_color = self.config.get_gallery_color("image_only")
            label_text = "🖼️ 仅图片"
            label_fg = "gray"
        
        # 缩略图容器
        thumb_container = tk.Frame(frame, width=thumb_width, height=thumb_height, 
                                    bg=bg_color, relief=tk.FLAT, bd=0)
        thumb_container.pack(padx=5, pady=(5, 0))
        thumb_container.pack_propagate(False)
        
        # 图片标签
        lbl = tk.Label(thumb_container, image=main_thumb, bg=bg_color, cursor="hand2")
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        lbl.image = main_thumb
        lbl.current_index = 0  # 当前显示的图片索引
        lbl.all_images = [{'path': main_img_path, 'thumb': main_thumb}] + alternates
        
        # 左切换按钮（悬浮）
        if len(lbl.all_images) > 1:
            left_btn = tk.Label(thumb_container, text="◀", bg="#666666", fg="white",
                               font=("微软雅黑", 10, "bold"), cursor="hand2", width=2)
            left_btn.place(relx=0.05, rely=0.5, anchor="w")
            left_btn.bind("<Button-1>", lambda e, l=lbl: self._switch_alternate(l, -1))
            left_btn.bind("<Enter>", lambda e, b=left_btn: b.config(bg="#333333"))
            left_btn.bind("<Leave>", lambda e, b=left_btn: b.config(bg="#666666"))
            
            # 右切换按钮
            right_btn = tk.Label(thumb_container, text="▶", bg="#666666", fg="white",
                                font=("微软雅黑", 10, "bold"), cursor="hand2", width=2)
            right_btn.place(relx=0.95, rely=0.5, anchor="e")
            right_btn.bind("<Button-1>", lambda e, l=lbl: self._switch_alternate(l, 1))
            right_btn.bind("<Enter>", lambda e, b=right_btn: b.config(bg="#333333"))
            right_btn.bind("<Leave>", lambda e, b=right_btn: b.config(bg="#666666"))
            
            # 指示器（小圆点）
            indicator_frame = tk.Frame(thumb_container, bg=bg_color)
            indicator_frame.place(relx=0.5, rely=0.9, anchor="center")
            
            for idx in range(len(lbl.all_images)):
                dot = tk.Label(indicator_frame, text="●", font=("微软雅黑", 6),
                              fg="#333333" if idx == 0 else "#cccccc", bg=bg_color, cursor="hand2")
                dot.pack(side=tk.LEFT, padx=1)
                dot.bind("<Button-1>", lambda e, i=idx, l=lbl: self._switch_to_alternate(l, i))
                if idx == 0:
                    lbl.current_dot = dot
        
        # 绑定事件
        # 收集所有可切换的图片路径（主图 + 附加缩略图）
        try:
            all_image_paths = [main_img_path]
            if alternates and isinstance(alternates, list):
                for alt in alternates:
                    if isinstance(alt, dict) and 'path' in alt:
                        all_image_paths.append(alt['path'])
                    elif isinstance(alt, str):
                        all_image_paths.append(alt)
        except Exception:
            all_image_paths = [main_img_path]
        
        lbl.bind("<Double-Button-1>", lambda e, p=main_img_path, paths=all_image_paths: 
                self.on_double_click(p, image_list=paths))
        lbl.bind("<Button-1>", lambda e, p=main_img_path, f=frame: self._select_image(p, f, e))
        lbl.bind("<Button-3>", lambda e, p=main_img_path, f=frame, v=video_path: 
                self._show_context_menu(e, p, f, v))
        
        # 设置frame属性 - 规范化路径
        norm_primary = os.path.normpath(primary_video_path) if primary_video_path else None
        norm_video = os.path.normpath(video_path) if video_path else None
        frame.video_path = norm_primary if norm_primary else norm_video
        frame.is_alias = bool(norm_primary)
        frame.alias_path = os.path.normpath(main_img_path) if norm_primary else None
        frame.has_local_video = bool(norm_video) and not norm_primary
        frame.file_info = file_info
        frame.img_path = os.path.normpath(main_img_path)
        frame.thumb_label = lbl  # 保存引用以便切换时更新路径
        
        def set_selected_state(selected):
            new_bg = "#f5a2a2" if selected else bg_color
            thumb_container.config(bg=new_bg)
            lbl.config(bg=new_bg)
            if hasattr(lbl, 'current_dot'):
                lbl.current_dot.config(bg=new_bg)
        
        frame.set_selected_state = set_selected_state
        
        # ========== 新增：6A 同步标识（左上角20x20浮动） ==========
        actual_video_path = primary_video_path if primary_video_path else video_path
        if actual_video_path:
            is_synced = self._is_video_synced(actual_video_path)
            if is_synced:
                # 使用统一方法创建同步标识
                sync_badge = self._create_sync_badge_widget(thumb_container, x=2, y=2)
                frame.sync_badge = sync_badge
        
        # 状态标签
        status_lbl = ttk.Label(frame, text=f"{label_text} ({len(lbl.all_images)}图)", 
                              font=("微软雅黑", 7), foreground=label_fg)
        status_lbl.pack(padx=2, pady=(0, 2))
        
        # 文件夹名
        fname = "当前文件夹" if folder_name == "." else folder_name
        color = "green" if folder_name == "." else "blue"
        folder_lbl = ttk.Label(frame, text=fname, wraplength=thumb_width-10,
                              font=("微软雅黑", 8, "bold"), foreground=color)
        folder_lbl.pack(padx=2, pady=(2, 0))
        
        # 准备tooltip文本：优先使用视频标题，其次使用文件夹名
        tooltip_text = fname
        if file_info and isinstance(file_info, dict):
            video_title = file_info.get('video_title', '')
            if video_title:
                tooltip_text = video_title
        
        # 为文件夹名添加tooltip
        if ToolTip:
            ToolTip(folder_lbl, tooltip_text)
        
        # 为缩略图添加tooltip
        if ToolTip:
            ToolTip(lbl, tooltip_text)
        
        # ========== 新增：数据库信息展示区（异步加载）==========
        # 创建信息容器框架（用于演员、日期、时长）
        info_frame = ttk.Frame(frame)
        info_frame.pack(fill=tk.X, padx=2, pady=(0, 2))
        
        info_lbl = ttk.Label(info_frame, text="", wraplength=thumb_width-10,
                            font=("微软雅黑", 7), foreground="gray")
        info_lbl.pack(fill=tk.X)
        info_lbl.bind("<Button-1>", lambda e, p=main_img_path, f=frame: self._select_image(p, f, e))
        info_lbl.bind("<Button-3>", lambda e, p=main_img_path, f=frame: self._show_context_menu(e, p, f))
        
        title_lbl = ttk.Label(frame, text="", wraplength=thumb_width-10,
                             font=("微软雅黑", 7, "italic"), foreground="#666666")
        title_lbl.pack(padx=2, pady=(0, 2))
        title_lbl.bind("<Button-1>", lambda e, p=main_img_path, f=frame: self._select_image(p, f, e))
        title_lbl.bind("<Button-3>", lambda e, p=main_img_path, f=frame: self._show_context_menu(e, p, f))
        
        # ========== 关键修复：保存引用到 frame，供增量刷新使用 ==========
        frame.info_lbl = info_lbl
        frame.title_lbl = title_lbl

                # 如果有视频路径，异步加载数据库信息
        actual_video_path = primary_video_path if primary_video_path else video_path
        if actual_video_path and self.db and hasattr(self.db, 'is_ready') and self.db.is_ready:
            self.parent.after(150, lambda: self._load_video_info_async(
                actual_video_path, info_lbl, title_lbl, thumb_width
            ))
        
        # 标签显示
        if frame.video_path:
            tags = self.config.get_video_tags(frame.video_path)
            if tags:
                tag_text = f"🏷️ {', '.join(tags[:2])}" + (f" +{len(tags)-2}" if len(tags) > 2 else "")
                tag_lbl = ttk.Label(frame, text=tag_text, wraplength=thumb_width-10,
                                  font=("微软雅黑", 7), foreground="purple")
                tag_lbl.pack(padx=2, pady=(0, 2))
        
        # 网格布局
        items_per_row = max(1150 // (thumb_width + 20), 1)
        row, col = index // items_per_row, index % items_per_row
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        
        # 绑定滚轮
        self._bind_mousewheel_recursive(frame)

    def _add_grid_widget(self, index, folder_name, grid_thumbs, all_images, match_type, file_info):
        """添加宫格显示控件（2宫格/4宫格）- 用于子文件夹的多图多视频或纯图片场景"""
        thumb_width = self.config.thumbnail_width
        thumb_height = self.config.thumbnail_width
        
        # 宫格大小
        is_grid2 = len(grid_thumbs) == 2
        grid_cols = 2
        grid_rows = 1 if is_grid2 else 2
        
        # 外框大小与单图一致
        frame = tk.Frame(self.images_frame, relief=tk.FLAT, borderwidth=0, bg='white')
        frame.configure(width=thumb_width + 20, height=thumb_height + 100)
        frame.pack_propagate(False)
        
        # 确定背景色和标签
        if match_type == 'sub_multi_video_grid':
            bg_color = self.config.get_gallery_color("sub_multi_video")
            label_text = f"📁 多视频 ({file_info.get('video_count', 0)}个)"
            label_fg = "#E74C3C"
        elif match_type == 'sub_image_only_grid':
            bg_color = self.config.get_gallery_color("sub_image_only")
            label_text = f"🖼️ 仅图片 ({file_info.get('image_count', 0)}张)"
            label_fg = "#3498DB"
        else:
            bg_color = self.config.get_gallery_color("normal")
            label_text = "📁 文件夹"
            label_fg = "gray"
        
        # 宫格容器
        grid_container = tk.Frame(frame, width=thumb_width, height=thumb_height,
                                   bg=bg_color, relief=tk.FLAT, bd=1)
        grid_container.pack(padx=5, pady=5)
        grid_container.pack_propagate(False)
        
        # 创建宫格图片
        grid_labels = []
        for i, thumb_data in enumerate(grid_thumbs[:4]):  # 最多4张
            row = i // 2
            col = i % 2
            
            cell = tk.Frame(grid_container, bg=bg_color)
            cell.place(relx=col * 0.5, rely=row * 0.5, relwidth=0.5, relheight=0.5)
            
            lbl = tk.Label(cell, image=thumb_data['thumb'], bg=bg_color, cursor="hand2")
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            lbl.image = thumb_data['thumb']
            lbl.img_path = thumb_data['path']
            grid_labels.append(lbl)
            
            # 绑定点击事件
            lbl.bind("<Button-1>", lambda e, p=thumb_data['path'], f=frame: self._select_image(p, f, e))
            lbl.bind("<Button-3>", lambda e, p=thumb_data['path'], f=frame: self._show_context_menu(e, p, f))
        
        # 绑定双击事件 - 进入子文件夹
        sub_folder_path = file_info.get('sub_folder_path')
        if sub_folder_path:
            grid_container.bind("<Double-Button-1>", 
                               lambda e, p=sub_folder_path: self._on_double_click_sub_folder(p))
            for lbl in grid_labels:
                lbl.bind("<Double-Button-1>", 
                        lambda e, p=sub_folder_path: self._on_double_click_sub_folder(p))
        
        # ========== 新增：宫格同步标识 ==========
        # 检查该宫格项下是否有已同步的视频
        has_synced = False
        if file_info and isinstance(file_info, dict):
            # 优先使用 gallery_loader 注入的标志
            has_synced = file_info.get('has_synced_video', False)
            # 备用：检查 videos 列表中的视频是否在同步集合中
            if not has_synced and hasattr(self, '_synced_video_paths'):
                videos = file_info.get('videos', [])
                for v_path in videos:
                    if v_path in self._synced_video_paths:
                        has_synced = True
                        break
        
        if has_synced:
            sync_badge = self._create_sync_badge_widget(grid_container, x=2, y=2)
            frame.sync_badge = sync_badge
        
        # 状态标签
        status_lbl = ttk.Label(frame, text=label_text, font=("微软雅黑", 7), foreground=label_fg)
        status_lbl.pack(padx=2, pady=(0, 2))
        
        # 文件夹名
        fname = folder_name
        color = "blue"
        folder_lbl = ttk.Label(frame, text=fname, wraplength=thumb_width-10,
                              font=("微软雅黑", 8, "bold"), foreground=color)
        folder_lbl.pack(padx=2, pady=(2, 0))
        
        # 设置frame属性
        frame.sub_folder_path = sub_folder_path
        frame.file_info = file_info
        frame.img_path = grid_thumbs[0]['path'] if grid_thumbs else None
        frame.set_selected_state = lambda selected: None  # 宫格不支持选中状态
        
        # 网格布局
        items_per_row = max(1150 // (thumb_width + 20), 1)
        row, col = index // items_per_row, index % items_per_row
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        
        # 绑定滚轮
        self._bind_mousewheel_recursive(frame)
    
    def _on_double_click_sub_folder(self, sub_folder_path):
        """双击宫格时进入子文件夹"""
        if self.debug:
            self.debug.print(f"双击进入子文件夹: {sub_folder_path}")
        
        # 调用父窗口的回调来切换文件夹
        if hasattr(self, 'on_enter_sub_folder') and self.on_enter_sub_folder:
            self.on_enter_sub_folder(sub_folder_path)

    def _switch_alternate(self, lbl, direction):
        """切换到上一个/下一个附加缩略图"""
        new_index = (lbl.current_index + direction) % len(lbl.all_images)
        self._switch_to_alternate(lbl, new_index)

    def _switch_to_alternate(self, lbl, index):
        """切换到指定索引的缩略图"""
        if index < 0 or index >= len(lbl.all_images):
            return
        
        # 更新图片
        img_data = lbl.all_images[index]
        lbl.config(image=img_data['thumb'])
        lbl.image = img_data['thumb']
        lbl.current_index = index
        
        # 更新frame的img_path（用于右键菜单等）
        frame = lbl.master.master
        frame.img_path = img_data['path']
        
        # 更新文件名显示
        if hasattr(frame, 'name_label'):
            frame.name_label.config(text=os.path.basename(img_data['path']))
        
        # 更新指示器
        if hasattr(lbl, 'current_dot'):
            lbl.current_dot.config(fg="#cccccc")
        
        # 找到新的当前指示器
        indicator_frame = None
        for child in lbl.master.winfo_children():
            if isinstance(child, tk.Frame) and child != lbl and not isinstance(child.winfo_children()[0] if child.winfo_children() else None, tk.Label) or \
               (isinstance(child, tk.Frame) and any(isinstance(c, tk.Label) and c.cget('text') in ["●", "◀", "▶"] for c in child.winfo_children())):
                indicator_frame = child
                break
        
        if indicator_frame:
            dots = [w for w in indicator_frame.winfo_children() if isinstance(w, tk.Label) and w.cget('text') == "●"]
            if index < len(dots):
                dots[index].config(fg="#333333")
                lbl.current_dot = dots[index]
                
    def _update_frame_visuals(self, frame, primary_video_path):
        """更新 frame 的视觉标识（背景色、标签文字、同步标识）- 修复：支持解除关联状态和同步标识动态更新"""
        # 根据当前状态确定视觉样式（从配置读取颜色）
        has_local_video = getattr(frame, 'has_local_video', False)
        
        if primary_video_path:
            # 关联视频状态
            bg_color = self.config.get_gallery_color("linked_video")
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif has_local_video:
            # 有本地视频但未关联 - 检查 match_type
            match_type = getattr(frame, 'match_type', '')
            if match_type in ['dedicated', 'sub_dedicated']:
                bg_color = self.config.get_gallery_color("dedicated_match")
                label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type in ['cover', 'sub_cover']:
                bg_color = self.config.get_gallery_color("cover_match")
                label_text = "📁 封面匹配"
                label_fg = "teal"
            elif match_type == 'sub_image_only':
                bg_color = self.config.get_gallery_color("sub_image_only")
                label_text = "🖼️ 仅图片"
                label_fg = "#3498DB"
            elif match_type == 'cover_priority':
                bg_color = self.config.get_gallery_color("cover_priority")
                label_text = "📁 封面优先"
                label_fg = "teal"
            elif match_type == 'auto_match':
                bg_color = self.config.get_gallery_color("auto_match")
                label_text = "🔗 自动匹配"
                label_fg = "purple"
            else:
                bg_color = self.config.get_gallery_color("normal_video")
                label_text = "🎬 视频"
                label_fg = "blue"
        else:
            # 仅图片状态（解除关联后）
            bg_color = self.config.get_gallery_color("image_only")
            label_text = "🖼️ 仅图片"
            label_fg = "gray"
        
        # 更新 thumb_container 的背景色
        thumb_container = None
        thumb_label = None
        
        for child in frame.winfo_children():
            if isinstance(child, tk.Frame):
                # 找到 thumb_container（通常是第一个 Frame，且有固定大小）
                if child.winfo_width() > 50:  # 排除小框架如指示器
                    thumb_container = child
                    # 更新容器背景
                    child.config(bg=bg_color)
                    # 找到其中的 Label（缩略图）
                    for sub in child.winfo_children():
                        if isinstance(sub, tk.Label) and hasattr(sub, 'image'):
                            thumb_label = sub
                            sub.config(bg=bg_color)
                    break
        
        # 更新状态标签文字和颜色
        for child in frame.winfo_children():
            if isinstance(child, ttk.Label):
                current_text = child.cget('text')
                # 识别状态标签（包含特定emoji的）
                if any(emoji in current_text for emoji in ["🔗", "🎬", "📁", "🖼️", "📄"]):
                    child.config(text=label_text, foreground=label_fg)
                    break
        
        # ========== 新增：更新同步标识（联网同步标识） ==========
        if primary_video_path and self._is_video_synced(primary_video_path):
            # 需要显示同步标识
            if not hasattr(frame, 'sync_badge') or not frame.sync_badge.winfo_exists():
                # 查找 thumb_container（如果上面没找到，再找一次）
                if not thumb_container:
                    for child in frame.winfo_children():
                        if isinstance(child, tk.Frame) and child.winfo_width() > 50:
                            thumb_container = child
                            break
                
                if thumb_container:
                    # 创建同步标识
                    sync_badge = self._create_sync_badge_widget(thumb_container, x=2, y=2)
                    frame.sync_badge = sync_badge
                    if self.debug:
                        self.debug.print(f"添加同步标识: {os.path.basename(primary_video_path)}")
            else:
                # 确保已存在的标识可见
                frame.sync_badge.place(x=2, y=2, anchor=tk.NW)
        else:
            # 不需要显示同步标识（无关联或视频未同步），隐藏现有标识
            if hasattr(frame, 'sync_badge') and frame.sync_badge.winfo_exists():
                frame.sync_badge.place_forget()
        
        # 强制更新显示
        if thumb_container:
            thumb_container.update_idletasks()
        frame.update_idletasks()

    def clear(self):
        for widget in self.images_frame.grid_slaves():
            widget.destroy()
        self._image_files = []
        self._selected_images.clear()
        self.progress['value'] = 0
        self.canvas.yview_moveto(0)
        self._pending_widgets = []
        self._current_item_index = 0
        # 关键修复：清空时重置信息面板
        self._update_info_panel(None)
        self._current_info_video_path = None
        self._info_expand_data = None
        if self._info_expanded:
            self._toggle_info_panel()
    
    def clear_selection(self):
        self._selected_images.clear()
        for child in self.images_frame.winfo_children():
            if hasattr(child, 'set_selected_state'):
                child.set_selected_state(False)
        # 关键修复：清除选中时清空信息面板
        self._update_info_panel(None)
        self._current_info_video_path = None


    def get_selected_images(self):
        return list(self._selected_images)
    
    def get_selected_frames(self):
        frames = {}
        for child in self.images_frame.winfo_children():
            img_path = getattr(child, 'img_path', None)
            if img_path and img_path in self._selected_images:
                frames[img_path] = child
        return frames
    
    def get_image_count(self):
        return len(self.images_frame.winfo_children())
    
    def set_multi_select_mode(self, enabled):
        self._multi_select_mode = enabled
        if not enabled:
            self.clear_selection()
    
    def refresh(self, keep_scroll=True):
        """刷新当前文件夹显示 - 修复：使用统一的加载方法确保显示效果一致"""
        if not self._current_folder:
            return
        
        # 优先使用外部统一加载回调（确保与打开/树切换/菜单刷新效果一致）
        if self.on_refresh_request:
            self.on_refresh_request(keep_scroll=keep_scroll)
            return
        
        # 回退到旧的内部加载逻辑（兼容其他调用场景）
        if keep_scroll:
            current_y = self.get_scroll_y()
            self.load_folder(self._current_folder, None, scroll_y=current_y)
        else:
            self.load_folder(self._current_folder, None)
    
    def load_folder(self, folder_path, status_callback=None, scroll_y=None):
        """加载文件夹（入口）"""
        # ========== 新增：记录打开文件夹日志 ==========
        if self.debug:
            self.debug.print(f"[打开文件夹] {folder_path}")
        
        self._load_generation += 1
        current_gen = self._load_generation
        self.clear()
        self._current_folder = folder_path
        
        if status_callback:
            status_callback("正在扫描...", 0)
        
        thread = threading.Thread(
            target=self._load_thread,
            args=(folder_path, status_callback, current_gen, scroll_y)
        )
        thread.daemon = True
        thread.start()

    def _load_thread(self, folder_path, status_callback, generation, scroll_y=None):
        """加载线程 - 三级智能匹配 + 子文件夹递归 + 数据库关联补充"""
        from core import PathUtils
        
        self._image_files = []
        
        # 辅助递归函数
        def scan_folder(folder, folder_display_name):
            """扫描单个文件夹，返回该文件夹下的 (匹配项列表, 子文件夹列表)"""
            try:
                entries = list(os.scandir(folder))
            except:
                return [], []
            
            videos = {}      # basename -> video_path
            images = {}      # basename -> image_path
            all_images_ordered = []
            sub_folders = []
            
            for entry in entries:
                if entry.is_dir():
                    sub_folders.append(entry)
                    continue
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                basename = os.path.splitext(entry.name)[0].lower()
                if PathUtils.is_video_file(entry.path):
                    videos[basename] = entry.path
                elif PathUtils.is_image_file(entry.path):
                    images[basename] = entry.path
                    all_images_ordered.append(entry.path)
            
            items = []
            matched_images = set()
            
            # 情况1: 单视频
            if len(videos) == 1:
                video_basename, video_path = next(iter(videos.items()))
                if len(images) == 1:
                    # 单视频单图片 -> 直接匹配
                    img_basename, img_path = next(iter(images.items()))
                    items.append({
                        'folder_name': folder_display_name,
                        'img_path': img_path,
                        'video_path': video_path,
                        'is_placeholder': False,
                        'match_type': 'single'
                    })
                    matched_images.add(img_basename)
                elif len(images) > 1:
                    # 单视频多图片 -> 查找通用封面作为主缩略图，其他作为副缩略图
                    preferred = ['cover', 'folder', 'poster', 'thumb', 'preview']
                    selected = None
                    for name in preferred:
                        if name in images:
                            selected = images[name]
                            break
                    if not selected and all_images_ordered:
                        selected = all_images_ordered[0]
                    if selected:
                        # 收集其他图片作为副缩略图
                        alternates = [p for p in all_images_ordered if p != selected]
                        items.append({
                            'folder_name': folder_display_name,
                            'img_path': selected,
                            'video_path': video_path,
                            'is_placeholder': False,
                            'match_type': 'cover',
                            'alternates': alternates  # 添加副缩略图列表
                        })
                        matched_images.add(os.path.splitext(os.path.basename(selected))[0].lower())
                        # 将所有图片标记为已匹配，避免重复显示
                        for img_path in all_images_ordered:
                            matched_images.add(os.path.splitext(os.path.basename(img_path))[0].lower())
                    else:
                        # 无图片 -> 占位符
                        items.append({
                            'folder_name': folder_display_name,
                            'img_path': "__VIDEO_PLACEHOLDER__",
                            'video_path': video_path,
                            'is_placeholder': True,
                            'match_type': 'placeholder',
                            'file_name': os.path.basename(video_path)
                        })
            # 情况2: 多视频
            elif len(videos) > 1:
                for v_basename, v_path in videos.items():
                    if v_basename in images:
                        # 同名专属匹配
                        items.append({
                            'folder_name': folder_display_name,
                            'img_path': images[v_basename],
                            'video_path': v_path,
                            'is_placeholder': False,
                            'match_type': 'dedicated'
                        })
                        matched_images.add(v_basename)
                    else:
                        # 尝试通用封面
                        thumb = self._find_video_thumbnail(v_path)
                        if thumb:
                            items.append({
                                'folder_name': folder_display_name,
                                'img_path': thumb,
                                'video_path': v_path,
                                'is_placeholder': False,
                                'match_type': 'general'
                            })
                            matched_images.add(os.path.splitext(os.path.basename(thumb))[0].lower())
                        else:
                            items.append({
                                'folder_name': folder_display_name,
                                'img_path': "__VIDEO_PLACEHOLDER__",
                                'video_path': v_path,
                                'is_placeholder': True,
                                'match_type': 'placeholder',
                                'file_name': os.path.basename(v_path)
                            })
            
            # 添加未匹配的独立图片
            for img_basename, img_path in images.items():
                if img_basename not in matched_images:
                    items.append({
                        'folder_name': folder_display_name,
                        'img_path': img_path,
                        'video_path': None,
                        'is_placeholder': False,
                        'match_type': 'standalone'
                    })
            
            return items, sub_folders
        
        # 扫描当前文件夹
        current_items, sub_folders = scan_folder(folder_path, ".")
        self._image_files.extend(current_items)
        
        # 扫描子文件夹（递归）
        for sub in sub_folders:
            if generation != self._load_generation:
                return
            sub_items, _ = scan_folder(sub.path, sub.name)
            self._image_files.extend(sub_items)
            
            if status_callback:
                progress = int((len(self._image_files) % 50) / 50 * 100)
                self.parent.after(0, lambda m=f"扫描 {sub.name}", p=progress: status_callback(m, p))
        
        # ==================== 关键修复：补充数据库关联信息 ====================
        # 对于只有图片没有视频的文件夹，检查数据库中的关联关系
        if self.db:
            for item in self._image_files:  # 扫描后存储在 self._image_files
                img_path = item.get('img_path')
                if not img_path or img_path == "__VIDEO_PLACEHOLDER__":
                    continue
                
                # 只要没有本地视频路径，就检查数据库关联
                if not item.get('video_path'):
                    try:
                        db_file = self.db.get_file_by_path(img_path)
                        if db_file and db_file.get('is_alias'):
                            # 查询关联的主视频路径
                            primary_path = self.db.get_primary_video_path(db_file.get('id'))
                            if primary_path:
                                # ========== 关键修复：使用正确的变量名 primary_path ==========
                                # 原代码错误写成 primary_video_path（未定义变量，导致值为None）
                                item['primary_video_path'] = primary_path  # 修复：使用 primary_path
                                item['video_path'] = primary_path           # 修复：使用 primary_path
                                item['is_alias'] = 1
                                item['match_type'] = 'linked'
                                
                                # 确保 file_info 中也包含这些信息（供渲染使用）
                                if 'file_info' not in item or not item['file_info']:
                                    item['file_info'] = {}
                                item['file_info']['primary_video_path'] = primary_path
                                item['file_info']['is_alias'] = 1
                                item['file_info']['match_type'] = 'linked'
                                item['file_info']['video_path'] = primary_path
                                
                                if self.debug:
                                    self.debug.print(f"[关联绑定] {os.path.basename(img_path)} -> {os.path.basename(primary_path)}")
                            else:
                                if self.debug:
                                    self.debug.print(f"[关联警告] 找到别名记录但无关联视频: {img_path}")
                        else:
                            if self.debug and db_file:
                                self.debug.print(f"[关联调试] 文件存在但非别名: {os.path.basename(img_path)}, is_alias={db_file.get('is_alias')}")
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"[关联错误] 查询失败 {img_path}: {e}")
            
            # 重新扫描统计关联数量（用于主视频显示关联计数）
            for item in self._image_files:
                if item.get('video_path') and not item.get('is_alias'):
                    try:
                        video_file_info = self.db.get_file_by_path(item['video_path'])
                        if video_file_info and not video_file_info.get('is_alias'):
                            aliases = self.db.get_related_videos(video_file_info.get('id'))
                            alias_count = len(aliases) if aliases else 0
                            item['alias_count'] = alias_count
                            if 'file_info' in item:
                                item['file_info']['alias_count'] = alias_count
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"统计关联数量失败: {e}")
        
        total = len(self._image_files)
        if total == 0:
            if status_callback and generation == self._load_generation:
                self.parent.after(0, lambda: status_callback("文件夹为空", 100))
            return
        
        # 批量创建缩略图并显示
        # 批量创建缩略图并显示
        for idx, item in enumerate(self._image_files):
            if generation != self._load_generation:
                return
            
            db_file_path = item.get('img_path')
            if not db_file_path or (not os.path.exists(db_file_path) and db_file_path != "__VIDEO_PLACEHOLDER__"):
                continue
            
            folder_name = os.path.basename(item.get('folder_path', '')) if item.get('folder_path') else "[未知]"
            thumb_path = None
            video_path = None
            
            is_video_type = (item.get('file_type') == 'video')
            
            if is_video_type:
                thumb_path = self._find_video_thumbnail(db_file_path)
                video_path = item.get('video_path') or db_file_path
                if not thumb_path:
                    thumb_path = "__VIDEO_PLACEHOLDER__"
            else:
                thumb_path = db_file_path
                video_path = self._find_video_for_image(db_file_path)
            
            # 检查关联视频（数据库关系）- 使用 .get() 安全获取
            # 关键修复：使用 item.get() 避免 KeyError
            primary_video_path = item.get('primary_video_path')  # 安全获取，不存在则返回 None
            alias_count = item.get('alias_count', 0)
            
            thumb = self._create_thumbnail(thumb_path, self.config.thumbnail_width)
            
            if thumb:
                # 准备 file_info，确保包含所有关联信息
                file_info = item.get('file_info', {})
                file_info['match_type'] = item.get('match_type', '')
                file_info['alias_count'] = alias_count
                # 使用 .get() 安全获取，避免 KeyError
                file_info['primary_video_path'] = item.get('primary_video_path')
                file_info['is_alias'] = 1 if item.get('primary_video_path') else 0
                file_info['video_path'] = item.get('video_path')
                if 'file_name' in item:
                    file_info['file_name'] = item['file_name']
                
                # 准备副缩略图列表
                alternates = item.get('alternates', [])
                
                # 关键修复：使用 .get() 访问字典，避免 KeyError
                self.parent.after(0, lambda i=idx, f=item['folder_name'], img=item['img_path'],
                                th=thumb, vp=item.get('video_path'), pvp=item.get('primary_video_path'),  # 使用 .get()
                                fi=file_info, alts=alternates:
                                self._add_thumbnail_widget(i, f, img, th, vp, pvp, fi, alts))
            
            if status_callback and idx % 5 == 0:
                progress = int((idx + 1) / total * 100)
                self.parent.after(0, lambda m=f"加载 {idx+1}/{total}", p=progress: status_callback(m, p))
        
        # 完成
        if generation == self._load_generation:
            def finish():
                if status_callback:
                    status_callback(f"完成 ({total} 项)", 100)
                if scroll_y is not None:
                    self.parent.after(200, lambda: self.set_scroll_y(scroll_y))
            self.parent.after(100, finish)

    def _update_relation_in_cache(self, img_path, primary_video_path, is_linked=True):
        """
        关联操作后更新内存缓存 - 确保缩略图标识、视频地址、播放操作正常
        
        Args:
            img_path: 图片路径（关联文件）
            primary_video_path: 主视频路径
            is_linked: True表示建立关联，False表示解除关联
        """
        try:
            # 1. 更新当前显示的缩略图 frame 的属性（立即生效）
            for child in self.images_frame.winfo_children():
                if getattr(child, 'img_path', None) == img_path:
                    if is_linked and primary_video_path:
                        # 建立关联：更新 frame 属性
                        child.video_path = primary_video_path
                        child.is_alias = True
                        child.has_local_video = False
                        child.alias_path = img_path
                        child.primary_video_path = primary_video_path
                        
                        # 更新 file_info
                        if hasattr(child, 'file_info') and child.file_info:
                            child.file_info['primary_video_path'] = primary_video_path
                            child.file_info['video_path'] = primary_video_path
                            child.file_info['is_alias'] = 1
                            child.file_info['match_type'] = 'linked'
                        
                        # 立即更新视觉标识（背景色、标签等）
                        self._update_frame_visuals(child, primary_video_path)
                        
                        if self.debug:
                            self.debug.print(f"[缓存更新] 已建立关联: {os.path.basename(img_path)} -> {os.path.basename(primary_video_path)}")
                    else:
                        # 解除关联：清除 frame 属性
                        child.video_path = None
                        child.is_alias = False
                        child.has_local_video = False
                        child.alias_path = None
                        child.primary_video_path = None
                        
                        # 更新 file_info
                        if hasattr(child, 'file_info') and child.file_info:
                            child.file_info['primary_video_path'] = None
                            child.file_info['video_path'] = None
                            child.file_info['is_alias'] = 0
                            child.file_info['match_type'] = 'standalone'
                        
                        # 立即更新视觉标识
                        self._update_frame_visuals(child, None)
                        
                        if self.debug:
                            self.debug.print(f"[缓存更新] 已解除关联: {os.path.basename(img_path)}")
                    break
            
            # 2. 更新 folder_cache 中的 JSON 缓存（持久化）
            if hasattr(self, 'folder_cache') and self._current_folder:
                cache_data = self.folder_cache.load_folder_cache(self._current_folder)
                if cache_data and 'files_info' in cache_data:
                    updated = False
                    for file_info in cache_data['files_info']:
                        if file_info.get('file_path') == img_path:
                            if is_linked and primary_video_path:
                                file_info['primary_video_path'] = primary_video_path
                                file_info['video_path'] = primary_video_path
                                file_info['is_alias'] = 1
                                file_info['match_type'] = 'linked'
                            else:
                                file_info['primary_video_path'] = None
                                file_info['video_path'] = None
                                file_info['is_alias'] = 0
                                file_info['match_type'] = 'standalone'
                            updated = True
                            break
                    
                    if updated:
                        # 重新保存缓存
                        self.folder_cache.save_folder_cache(
                            self._current_folder,
                            sub_folders=cache_data.get('sub_folders', []),
                            files_info=cache_data['files_info'],
                            tree_state=cache_data.get('tree_state', {})
                        )
                        if self.debug:
                            self.debug.print(f"[缓存持久化] 已更新JSON缓存: {os.path.basename(img_path)}")
            
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"[缓存更新失败] {e}")
                import traceback
                traceback.print_exc()
            return False
        
    def _format_file_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"

    def _log_folder_file_list(self, folder_path, entries):
        """在调试模式下，按文件名升序记录文件夹内的所有文件"""
        if not self.debug or not self.debug.debug_mode:
            return
        
        from core import PathUtils
        
        self.debug.print(f"\n【文件列表: {os.path.basename(folder_path)}】")
        self.debug.print("-" * 60)
        
        # 收集所有文件（排除目录）
        all_files = []
        for entry in entries:
            if entry.is_file() and not entry.name.startswith('.'):
                all_files.append(entry)
        
        # 按文件名升序排序
        all_files.sort(key=lambda x: x.name.lower())
        
        # 统计各类文件数量
        video_count = 0
        image_count = 0
        other_count = 0
        
        for entry in all_files:
            ext = os.path.splitext(entry.name)[1].lower()
            file_size = entry.stat().st_size
            size_str = self._format_file_size(file_size)
            
            if PathUtils.is_video_file(entry.path):
                file_type = "视频"
                video_count += 1
                marker = "🎬"
            elif PathUtils.is_image_file(entry.path):
                file_type = "图片"
                image_count += 1
                marker = "🖼️"
            else:
                file_type = "其他"
                other_count += 1
                marker = "📄"
            
            self.debug.print(f"  {marker} {entry.name:<40} [{file_type}] [{size_str:>8}]")
        
        self.debug.print("-" * 60)
        self.debug.print(f"统计: {video_count}个视频, {image_count}张图片, {other_count}个其他文件")
        self.debug.print(f"总计: {len(all_files)} 个文件\n")


    def _log_matching_result(self, match_type, video_name, image_name=None, status="success"):
        """记录匹配结果"""
        if not self.debug or not self.debug.debug_mode:
            return
        
        if status == "success":
            if image_name:
                self.debug.print(f"  ✓ {match_type}: {video_name} ← {image_name}")
            else:
                self.debug.print(f"  ✓ {match_type}: {video_name}")
        elif status == "warning":
            self.debug.print(f"  ⚠ {match_type}: {video_name}")
        else:
            self.debug.print(f"  ✗ {match_type}: {video_name}")

    def load_files_by_paths(self, file_paths, status_callback=None, scroll_y=None):
        """按文件路径列表加载（标签筛选结果）"""
        self._load_generation += 1
        current_gen = self._load_generation
        self.clear()
        self._current_folder = "[标签筛选结果]"
        
        if not file_paths:
            if status_callback:
                status_callback("无匹配文件", 100)
            return
        
        def load_paths_thread():
            from core import PathUtils
            pending = []
            for i, file_path in enumerate(file_paths):
                if current_gen != self._load_generation:
                    return
                if not os.path.exists(file_path):
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                is_video = ext in PathUtils.VIDEO_EXTENSIONS
                if is_video:
                    thumb_path = self._find_video_thumbnail(file_path) or "__VIDEO_PLACEHOLDER__"
                else:
                    thumb_path = file_path
                thumb = self._create_thumbnail(thumb_path, self.config.thumbnail_width)
                if thumb:
                    folder_name = os.path.basename(os.path.dirname(file_path))
                    file_info = self.db.get_file_by_path(file_path) if self.db else None
                    primary_video_path = None
                    if file_info and file_info.get('is_alias'):
                        primary_video_path = self.db.get_primary_video_path(file_info.get('id'))
                    pending.append({
                        'index': len(pending),
                        'folder_name': folder_name,
                        'img_path': thumb_path,
                        'thumb': thumb,
                        'video_path': file_path if is_video else None,
                        'primary_video_path': primary_video_path,
                        'file_info': file_info or {}
                    })
                if status_callback and i % 10 == 0:
                    self.parent.after(0, lambda p=i+1, t=len(file_paths): status_callback(f"加载 {p}/{t}", int(p/t*100)))
            
            if current_gen == self._load_generation:
                for w in pending:
                    self._add_thumbnail_widget(w['index'], w['folder_name'], w['img_path'], w['thumb'],
                                               w['video_path'], w['primary_video_path'], w['file_info'],
                                               w.get('alternates', []))
                if status_callback:
                    self.parent.after(0, lambda: status_callback(f"完成 ({len(pending)} 项)", 100))
                if scroll_y is not None:
                    self.parent.after(200, lambda: self.set_scroll_y(scroll_y))
        
        threading.Thread(target=load_paths_thread, daemon=True).start()
    
    def _show_match_confirmation(self, folder_path, unmatched_videos):
        """显示匹配确认对话框"""
        if not unmatched_videos:
            return
        
        dialog = tk.Toplevel(self.parent)
        dialog.title("视频缩略图匹配确认")
        dialog.geometry("600x400")
        dialog.transient(self.parent)
        
        ttk.Label(dialog, text=f"以下 {len(unmatched_videos)} 个视频没有找到对应的缩略图：",
                font=("微软雅黑", 10)).pack(pady=10)
        
        # 创建列表
        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tree = ttk.Treeview(frame, columns=('video', 'status'), show='headings', height=10)
        tree.heading('video', text='视频文件')
        tree.heading('status', text='状态')
        tree.column('video', width=350)
        tree.column('status', width=150)
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        for video in unmatched_videos:
            tree.insert('', 'end', values=(os.path.basename(video['path']), '未匹配'))
        
        def on_manual_match():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("提示", "请先选择要匹配的视频")
                return
            
            item = selection[0]
            values = tree.item(item, 'values')
            video_name = values[0]
            
            # 找到对应的视频路径
            video_path = None
            for v in unmatched_videos:
                if os.path.basename(v['path']) == video_name:
                    video_path = v['path']
                    break
            
            if video_path:
                selected_img = self._show_thumbnail_selector(folder_path, video_path)
                if selected_img:
                    # 建立关联
                    video_basename = os.path.splitext(os.path.basename(video_path))[0].lower()
                    img_ext = os.path.splitext(selected_img)[1]
                    new_path = os.path.join(folder_path, video_basename + img_ext)
                    
                    try:
                        import shutil
                        shutil.copy2(selected_img, new_path)
                        tree.item(item, values=(video_name, '已匹配'))
                        if self.debug:
                            self.debug.print(f"手动匹配: {video_name} -> {os.path.basename(new_path)}")
                    except Exception as e:
                        messagebox.showerror("错误", f"创建缩略图失败: {e}")
        
        def on_close():
            dialog.destroy()
            self.refresh(keep_scroll=True)
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="手动匹配选中视频", command=on_manual_match).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="跳过", command=on_close).pack(side=tk.RIGHT, padx=5)
        
        dialog.wait_window()

    def _load_paths_thread(self, file_paths, status_callback, generation, scroll_y=None):
        """按路径加载文件（标签筛选结果等）- 补充关联信息"""
        from core import PathUtils
        
        total = len(file_paths)
        loaded_count = 0
        pending = []
        
        # ==================== 关键修复：预加载数据库关联信息 ====================
        db_alias_map = {}
        if self.db:
            for file_path in file_paths:
                if not file_path:
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                # 只检查图片文件
                if ext in PathUtils.IMAGE_EXTENSIONS:
                    try:
                        db_file = self.db.get_file_by_path(file_path)
                        if db_file and db_file.get('is_alias'):
                            primary = self.db.get_primary_video_path(db_file.get('id'))
                            if primary:
                                db_alias_map[file_path] = primary
                                if self.debug:
                                    self.debug.print(f"路径加载关联发现: {os.path.basename(file_path)} -> {os.path.basename(primary)}")
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"路径加载查询关联失败 {file_path}: {e}")
        
        for i, file_path in enumerate(file_paths):
            if generation != self._load_generation:
                return
            
            if not os.path.exists(file_path):
                continue
            
            try:
                ext = os.path.splitext(file_path)[1].lower()
                is_video = ext in PathUtils.VIDEO_EXTENSIONS
                
                if is_video:
                    thumb_path = self._find_video_thumbnail(file_path)
                    if not thumb_path:
                        thumb_path = "__VIDEO_PLACEHOLDER__"
                    
                    thumb = self._create_thumbnail(thumb_path, self.config.thumbnail_width)
                    if thumb:
                        folder_name = os.path.basename(os.path.dirname(file_path))
                        
                        file_info = None
                        if self.db:
                            file_info = self.db.get_file_by_path(file_path)
                        
                        primary_video_path = None
                        alias_count = 0
                        if file_info:
                            if file_info.get('is_alias'):
                                primary_video_path = self.db.get_primary_video_path(file_info.get('id'))
                            else:
                                # 查询主视频的关联数量
                                try:
                                    aliases = self.db.get_related_videos(file_info.get('id'))
                                    alias_count = len(aliases) if aliases else 0
                                    file_info['alias_count'] = alias_count
                                except Exception as e:
                                    if self.debug:
                                        self.debug.print(f"查询视频关联数量失败: {e}")
                        
                        pending.append({
                            'index': len(pending),
                            'folder_name': folder_name,
                            'thumb_path': thumb_path,
                            'thumb': thumb,
                            'video_path': file_path,
                            'primary_video_path': primary_video_path,
                            'file_info': file_info
                        })
                        loaded_count += 1
                else:
                    # 图片文件
                    if PathUtils.is_image_file(file_path):
                        thumb = self._create_thumbnail(file_path, self.config.thumbnail_width)
                        if thumb:
                            folder_name = os.path.basename(os.path.dirname(file_path))
                            
                            # 检查数据库关联
                            file_info = None
                            primary_video_path = None
                            
                            if file_path in db_alias_map:
                                # 使用预加载的关联信息
                                primary_video_path = db_alias_map[file_path]
                                file_info = {
                                    'is_alias': 1,
                                    'match_type': 'linked',
                                    'file_path': file_path
                                }
                            elif self.db:
                                file_info = self.db.get_file_by_path(file_path)
                                if file_info and file_info.get('is_alias'):
                                    primary_video_path = self.db.get_primary_video_path(file_info.get('id'))
                            
                            pending.append({
                                'index': len(pending),
                                'folder_name': folder_name,
                                'thumb_path': file_path,
                                'thumb': thumb,
                                'video_path': primary_video_path,  # 如果有关联，指向主视频
                                'primary_video_path': primary_video_path,
                                'file_info': file_info or {'file_path': file_path}
                            })
                            loaded_count += 1
                
                if status_callback and i % 5 == 0:
                    progress = int((i + 1) / total * 100)
                    msg = f"加载中... ({i+1}/{total})"
                    self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
                    
            except Exception as e:
                if self.debug:
                    self.debug.print(f"加载文件失败 {file_path}: {e}")
        
        if generation == self._load_generation:
            # 批量添加
            for widget_info in pending:
                self._add_thumbnail_widget(
                    widget_info['index'],
                    widget_info['folder_name'],
                    widget_info['thumb_path'],
                    widget_info['thumb'],
                    video_path=widget_info['video_path'],
                    primary_video_path=widget_info['primary_video_path'],
                    file_info=widget_info['file_info'],
                    alternates=widget_info.get('alternates', [])
                )
            
            if status_callback:
                msg = f"完成，共 {loaded_count} 个文件"
                self.parent.after(0, lambda: status_callback(msg, 100))
            
            if scroll_y is not None:
                def restore_scroll():
                    if generation == self._load_generation:
                        self.set_scroll_y(scroll_y)
                delay = min(100 + total * 10, 500)
                self.parent.after(delay, restore_scroll)

    def _extract_code(self, folder_path):
        from core import extract_code_from_text
        code_info = extract_code_from_text(os.path.basename(folder_path))
        if code_info:
            code = code_info['canonical']
            self.parent.clipboard_clear()
            self.parent.clipboard_append(code)
            messagebox.showinfo("成功", f"已复制番号: {code}")
        else:
            messagebox.showwarning("提示", "未识别到番号格式")
    
    def _copy_path(self, path):
        if path:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(path)
    
    def _copy_browser_folder_name(self):
        if self.get_browser_folder:
            folder = self.get_browser_folder()
            if folder and os.path.exists(folder) and not folder.startswith("["):
                name = os.path.basename(folder)
                self.parent.clipboard_clear()
                self.parent.clipboard_append(name)
                messagebox.showinfo("成功", f"已复制主文件夹名称: {name}")
            else:
                messagebox.showwarning("提示", "当前处于筛选模式或无有效文件夹")
    
    def _set_tag(self, img_path, frame):
        if img_path not in self._selected_images:
            class FakeEvent:
                pass
            event = FakeEvent()
            event.state = 0
            self._select_image(img_path, frame, event)
        
        if self.on_set_tag:
            self.on_set_tag()
    
    def _open_folder(self, folder_path):
        try:
            import subprocess
            import platform

            if not folder_path or not os.path.exists(folder_path):
                messagebox.showwarning("提示", "文件夹不存在")
                return

            system = platform.system()
            if system == 'Windows':
                subprocess.run(['explorer', folder_path], shell=True)
            elif system == 'Darwin':
                subprocess.run(['open', folder_path])
            else:
                subprocess.run(['xdg-open', folder_path])

            if self.debug:
                self.debug.print(f"打开文件夹: {folder_path}")
        except Exception as e:
            if self.debug:
                self.debug.print(f"打开文件夹失败: {e}")
            messagebox.showerror("错误", f"无法打开文件夹:\n{e}")
    

    def _create_video_relation_for_thumbnail(self, img_path, frame):
        """为只有缩略图的文件夹创建视频关联 - 优化：关联后立即刷新"""
        try:
            from video_relation_dialog import VideoRelationDialog
        except ImportError:
            messagebox.showerror("错误", "未找到 video_relation_dialog 模块")
            return
        
        if not self.db:
            messagebox.showwarning("提示", "数据库未就绪")
            return
        
        file_info = self.db.get_file_by_path(img_path)
        
        if not file_info:
            if not messagebox.askyesno("创建记录", 
                "当前缩略图在数据库中无对应记录。\n是否创建虚拟视频记录以便建立关联？"):
                return
            
            try:
                folder_path = os.path.normpath(os.path.dirname(img_path))
                img_path = os.path.normpath(img_path)
                folder_id = self._ensure_folder_in_db(folder_path)
                
                conn = self.db._get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO media_files 
                    (folder_id, file_path, file_name, file_type, file_size, last_modified, is_alias)
                    VALUES (?, ?, ?, 'video', 0, datetime('now'), 0)
                """, (folder_id, img_path, os.path.basename(img_path)))
                
                conn.commit()
                cursor.close()
                
                self.db._load_all_to_cache_async()
                file_info = self.db.get_file_by_path(img_path)
                
                if not file_info:
                    messagebox.showerror("错误", "创建记录失败")
                    return
                    
            except Exception as e:
                messagebox.showerror("错误", f"创建视频记录失败: {e}")
                return
        
        # 打开关联对话框
        dialog = VideoRelationDialog(self.parent, self.db, self.config, file_info, self.debug, 
                                    get_bookmark_callback=self._get_current_bookmark_root)
        
        if dialog.show():
            # 关键修复：关联成功后，立即更新该 frame 的属性以反映新状态
            updated_file_info = self.db.get_file_by_path(img_path)
            if updated_file_info and updated_file_info.get('is_alias'):
                primary_video_path = self.db.get_primary_video_path(updated_file_info.get('id'))
                if primary_video_path:
                    # 立即更新 frame 属性
                    frame.video_path = primary_video_path
                    frame.is_alias = True
                    frame.has_local_video = False
                    frame.alias_path = img_path
                    frame.file_info = updated_file_info
                    # 更新视觉标识
                    self._update_frame_visuals(frame, primary_video_path)
                    # 更新缓存（内存缓存 + JSON缓存）
                    self._update_relation_in_cache(img_path, primary_video_path, is_linked=True)
                    # 更新路径显示
                    if self.on_select:
                        self.on_select(img_path, frame, True)
                    if self.debug:
                        self.debug.print(f"关联成功并已更新UI：{os.path.basename(img_path)} -> {os.path.basename(primary_video_path)}")
            
            # 使用统一刷新方法刷新整个图库（确保与其他入口效果一致）
            self.refresh(keep_scroll=True)

    def _get_current_bookmark_root(self):
        if self.get_browser_folder:
            current = self.get_browser_folder()
            if current:
                for dev_id, device in self.config.devices.items():
                    for bm in device.get('bookmarks', []):
                        bm_path = bm['path']
                        if current.startswith(bm_path):
                            return bm_path
        return None
    
    def _ensure_folder_in_db(self, folder_path):
        if not self.db:
            return None
        try:
            folder_path = os.path.normpath(folder_path)
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder_path,))
            result = cursor.fetchone()
            if result:
                return result['folder_id']
            
            parent = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
            if parent:
                parent = os.path.normpath(parent)
            cursor.execute("""
                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                VALUES (?, ?, ?, datetime('now'))
            """, (folder_path, os.path.basename(folder_path), parent))
            folder_id = cursor.lastrowid
            conn.commit()
            cursor.close()
            return folder_id
        except Exception as e:
            if self.debug:
                self.debug.print(f"确保文件夹失败: {e}")
            return None
    
    def _remove_video_relation(self, frame):
        """解除视频关联 - 修复：立即刷新视觉标识"""
        if not self.db:
            return
        
        img_path = getattr(frame, 'img_path', None)
        if not img_path:
            return
        
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT file_id, is_alias FROM media_files WHERE file_path = ?", (img_path,))
            result = cursor.fetchone()
            
            if not result:
                messagebox.showwarning("提示", "数据库中未找到该文件")
                return
            
            file_id = result['file_id']
            is_alias = result['is_alias']
            
            if not is_alias:
                messagebox.showinfo("提示", "这不是关联视频，无需解除")
                return
            
            if not messagebox.askyesno("确认", "确定解除该视频的关联关系吗？"):
                return
            
            success = self.db.remove_video_relation(file_id)
            cursor.close()
            
            if success:
                messagebox.showinfo("成功", "关联已解除")
                
                # ===== 关键修复：立即更新数据属性 =====
                old_video_path = frame.video_path  # 保存旧值用于调试
                frame.video_path = None
                frame.is_alias = False
                frame.has_local_video = False
                frame.alias_path = None
                
                # ===== 关键修复：立即更新视觉标识（给用户即时反馈）=====
                self._update_frame_visuals(frame, None)  # None 表示解除关联，回到"仅图片"状态
                
                if self.debug:
                    self.debug.print(f"关联已解除并立即刷新UI: {os.path.basename(img_path)}")
                
                # 异步刷新整个图库（确保数据一致性）
                self.refresh(keep_scroll=True)
                
                # 调用父窗口的回调（如果存在）
                if self.on_relation_removed:
                    try:
                        self.on_relation_removed(img_path)
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"解除关联回调失败: {e}")
                
        except Exception as e:
            messagebox.showerror("错误", f"解除关联失败: {e}")
            import traceback
            traceback.print_exc()

    def _collect_video_info(self, video_path, frame):
        """采集视频信息 - 在信息面板右侧显示可编辑文本框"""
        from core import extract_code_from_text

        # 从视频文件名和文件夹名提取番号
        video_name = os.path.basename(video_path)
        folder_name = os.path.basename(os.path.dirname(video_path))

        video_code = extract_code_from_text(video_name)
        folder_code = extract_code_from_text(folder_name)

        # 优先使用文件名中的番号，其次是文件夹名
        detected_code = ""
        if video_code:
            detected_code = video_code['canonical']
        elif folder_code:
            detected_code = folder_code['canonical']

        # 保存当前要采集的视频路径
        self._crawler_target_video = video_path
        
        # 设置文本框值
        self.crawler_code_entry.delete(0, tk.END)
        self.crawler_code_entry.insert(0, detected_code)
        
        # 显示采集控制区（在信息面板右侧）
        self.crawler_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(20, 5))
        
        # 聚焦到输入框并全选文本
        self.crawler_code_entry.focus()
        self.crawler_code_entry.select_range(0, tk.END)
        
        if self.debug:
            self.debug.print(f"显示采集控制区，目标视频: {video_name}, 预填番号: {detected_code}")


    def set_synced_videos(self, synced_paths):
        """设置已联网同步的视频路径集合，并更新UI显示"""
        self._synced_video_paths = synced_paths if synced_paths else set()
        if self.debug:
            self.debug.print(f"已设置 {len(self._synced_video_paths)} 个同步视频标记")
        
        # 更新现有缩略图的同步标识
        self._update_synced_indicators()
    
    def rebuild_synced_set(self, db_manager=None):
        """
        从数据库缓存重建已同步视频集合（纯数据操作，线程安全）
        
        判断标准：视频在数据库中有 video_title、video_code、release_date、
        duration 任一信息，或在 file_actors 中有演员关联。
        
        Args:
            db_manager: 数据库管理器实例（可选，默认使用 self.db）
            
        Returns:
            int: 同步视频数量
        """
        db = db_manager or self.db
        if not db or not getattr(db, 'is_cache_ready', False):
            return 0
        
        synced = set()
        try:
            cache = getattr(db, '_cache', {})
            files_cache = cache.get('files', {})
            file_actors_cache = cache.get('file_actors', {})
            
            for file_path, file_info in files_cache.items():
                if file_info.get('type') in ('video', 'video_placeholder'):
                    file_id = file_info.get('id') or file_info.get('file_id')
                    has_info = bool(
                        file_info.get('video_title') or 
                        file_info.get('video_code') or 
                        file_info.get('release_date') or 
                        file_info.get('duration') or
                        (file_id and file_id in file_actors_cache)
                    )
                    if has_info:
                        synced.add(file_path)
            
            self._synced_video_paths = synced
            return len(synced)
        except Exception as e:
            if self.debug:
                self.debug.print(f"重建同步集合失败: {e}")
            return 0
    
    def on_db_ready(self, db_manager):
        """
        数据库就绪后统一刷新图库标识和信息
        此方法应在主线程中调用，完成所有UI更新。
        
        Args:
            db_manager: 数据库管理器实例
        """
        if db_manager:
            self.db = db_manager
        
        count = self.rebuild_synced_set()
        self._update_synced_indicators()
        
        if self._current_folder:
            self.refresh_sync_and_info(self._current_folder)
        
        if self.debug:
            self.debug.print(f"[GalleryViewer] 数据库就绪，已标记 {count} 个同步视频")
    
    def _update_synced_indicators(self):
        """
        更新所有缩略图的同步标识显示（6A: 20x20浮动标识）
        支持单图、宫格、关联视频等多种widget类型。
        
        关键修复：数据库就绪后，为之前没有标识但现在应该有标识的缩略图动态创建标识。
        """
        try:
            for child in self.images_frame.winfo_children():
                has_synced = False
                
                # 类型1: 单图/视频 widget（有 video_path 属性）
                if hasattr(child, 'video_path') and child.video_path:
                    has_synced = child.video_path in self._synced_video_paths
                
                # 类型2: 宫格 widget（检查 file_info 中的 videos 列表）
                elif hasattr(child, 'file_info') and child.file_info:
                    file_info = child.file_info
                    # 优先使用 gallery_loader 注入的标志
                    has_synced = file_info.get('has_synced_video', False)
                    # 备用：检查 videos 列表
                    if not has_synced:
                        videos = file_info.get('videos', [])
                        for v_path in videos:
                            if v_path in self._synced_video_paths:
                                has_synced = True
                                break
                
                # 更新同步标识显示状态
                has_badge = hasattr(child, 'sync_badge') and child.sync_badge.winfo_exists()
                if has_badge:
                    if has_synced:
                        child.sync_badge.place(x=2, y=2, anchor=tk.NW)
                    else:
                        child.sync_badge.place_forget()
                elif has_synced:
                    # 关键修复：动态创建缺失的同步标识
                    self._add_sync_badge_to_frame(child)
        except Exception as e:
            if self.debug:
                self.debug.print(f"更新同步标识失败: {e}")
    
    def _is_video_synced(self, video_path):
        """检查视频是否已联网同步"""
        if not video_path:
            return False
        return video_path in self._synced_video_paths


    # ==================== 同步标识管理方法 ====================
    
    def _create_sync_badge_widget(self, parent, x=2, y=2):
        """
        创建同步标识部件（6A: 20x20浮动标识）
        
        Args:
            parent: 父容器（通常是 thumb_container）
            x, y: 标识的位置坐标
            
        Returns:
            tk.Frame: 同步标识框架，包含连接符号
        """
        # 创建20x20浮动标识，白色背景
        sync_badge = tk.Frame(parent, width=20, height=20, 
                              bg='white', relief=tk.RAISED, bd=1)
        sync_badge.place(x=x, y=y, anchor=tk.NW)
        sync_badge.pack_propagate(False)
        
        # 连接符号
        sync_icon = tk.Label(sync_badge, text="🔗", bg='white', 
                            font=("Segoe UI Emoji", 10))
        sync_icon.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        return sync_badge
    
    def update_video_sync_status(self, video_path, is_synced=True):
        """
        更新单个视频的同步状态（用于实时更新）
        
        当对单个视频进行联网同步抓取信息后，调用此方法即时更新
        对应缩略图上的同步标识显示。
        
        Args:
            video_path: 视频文件路径（用于查找对应缩略图）
            is_synced: True 添加同步标识，False 移除同步标识
            
        Returns:
            bool: 是否成功找到并更新了对应的缩略图
            
        Example:
            # 抓取视频信息后
            gallery_viewer.update_video_sync_status(video_path, True)
        """
        if not video_path:
            return False
        
        import os
        # 规范化路径以确保一致性
        norm_video_path = os.path.normpath(video_path)
        
        try:
            found = False
            # 遍历所有缩略图 frame 查找匹配的视频路径
            for frame in self.images_frame.winfo_children():
                frame_video_path = getattr(frame, 'video_path', None)
                
                # 检查是否匹配（考虑主视频和关联视频，路径规范化后比较）
                if frame_video_path and os.path.normpath(frame_video_path) == norm_video_path:
                    found = True
                    
                    if is_synced:
                        # 添加同步标识
                        self._add_sync_badge_to_frame(frame)
                        # 添加到同步集合
                        self._synced_video_paths.add(video_path)
                    else:
                        # 移除同步标识
                        self._remove_sync_badge_from_frame(frame)
                        # 从同步集合移除
                        self._synced_video_paths.discard(video_path)
                    
                    if self.debug:
                        self.debug.print(f"已更新同步标识: {os.path.basename(video_path)} -> {'显示' if is_synced else '隐藏'}")
                    
                    # 找到一个匹配的 frame 后可以继续查找
                    # 因为一个视频可能对应多个缩略图（关联情况）
            
            return found
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"更新视频同步状态失败: {e}")
            return False
    
    def _add_sync_badge_to_frame(self, frame):
        """
        为指定 frame 添加同步标识
        支持单图 widget 和宫格 widget。
        
        Args:
            frame: 缩略图 frame 对象
        """
        try:
            # 检查是否已有同步标识
            if hasattr(frame, 'sync_badge') and frame.sync_badge.winfo_exists():
                # 已存在，确保显示
                frame.sync_badge.place(x=2, y=2, anchor=tk.NW)
                return
            
            # 查找 thumb_container / grid_container
            thumb_container = None
            for child in frame.winfo_children():
                if isinstance(child, tk.Frame):
                    # 检查单图 thumb_container（包含直接放置的 Label）
                    for sub_child in child.winfo_children():
                        if isinstance(sub_child, tk.Label) and hasattr(sub_child, 'image'):
                            thumb_container = child
                            break
                        # 检查宫格 grid_container（cell Frame 中包含 Label）
                        if isinstance(sub_child, tk.Frame):
                            for cell_child in sub_child.winfo_children():
                                if isinstance(cell_child, tk.Label) and hasattr(cell_child, 'image'):
                                    thumb_container = child
                                    break
                    if thumb_container:
                        break
            
            if thumb_container:
                # 创建同步标识
                sync_badge = self._create_sync_badge_widget(thumb_container, x=2, y=2)
                frame.sync_badge = sync_badge
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"添加同步标识失败: {e}")
    
    def _remove_sync_badge_from_frame(self, frame):
        """
        从指定 frame 移除同步标识
        
        Args:
            frame: 缩略图 frame 对象
        """
        try:
            if hasattr(frame, 'sync_badge') and frame.sync_badge.winfo_exists():
                frame.sync_badge.place_forget()
        except Exception as e:
            if self.debug:
                self.debug.print(f"移除同步标识失败: {e}")

    def ensure_all_video_info_loaded(self, folder_path, db_ready=False, db_manager=None, thumbnail_width=None):
        """
        确保当前文件夹所有视频信息都已加载并显示
        此方法应由外部调用，传入数据库状态
        
        Args:
            folder_path: 文件夹路径（用于验证是否是当前显示的文件夹）
            db_ready: 数据库是否就绪
            db_manager: 数据库管理器实例
            thumbnail_width: 缩略图宽度
        """
        if not hasattr(self, '_current_folder') or not self._current_folder:
            return
        
        if os.path.normpath(self._current_folder) != os.path.normpath(folder_path):
            return
        
        if not db_ready or not db_manager:
            return
        
        width = thumbnail_width or self.config.thumbnail_width
        
        # 遍历所有已渲染的缩略图，检查视频信息是否已加载
        for frame in self.images_frame.winfo_children():
            video_path = getattr(frame, 'video_path', None)
            if not video_path:
                continue
            
            info_lbl = getattr(frame, 'info_lbl', None)
            title_lbl = getattr(frame, 'title_lbl', None)
            
            # 检查是否需要重新加载信息（info_lbl 无内容）
            need_reload = False
            if info_lbl:
                try:
                    current_text = info_lbl.cget('text')
                    if not current_text or current_text == '':
                        need_reload = True
                except tk.TclError:
                    need_reload = True
            
            if need_reload and db_ready and hasattr(db_manager, 'is_ready') and db_manager.is_ready:
                self._load_video_info_async(video_path, info_lbl, title_lbl, width)
        
        if self.debug:
            self.debug.print(f"[确保加载] 已检查 {os.path.basename(folder_path)} 的视频信息")


    def refresh_sync_and_info(self, folder_path, db_ready=False, db_manager=None):
        """
        刷新同步标识和视频信息显示
        此方法统一处理增量刷新，可在启动完成、切换文件夹等场景调用。
        
        流程：
        1. 如果数据库就绪，重建同步标识集合
        2. 更新所有缩略图上的同步标识（🔗）
        3. 异步加载所有缩略图的视频信息（演员、日期、时长、标题）
        4. 刷新当前选中项的顶部信息面板
        
        Args:
            folder_path: 当前文件夹路径
            db_ready: 数据库是否就绪
            db_manager: 数据库管理器实例（可选，默认使用 self.db）
        """
        # 验证当前文件夹
        if not self._current_folder or os.path.normpath(self._current_folder) != os.path.normpath(folder_path):
            return
        
        db = db_manager or self.db
        db_is_ready = db_ready or (db and getattr(db, 'is_cache_ready', False))
        
        # 1. 如果数据库就绪，重建同步标识集合
        if db_is_ready:
            self.rebuild_synced_set(db)
        
        # 2. 更新所有缩略图上的同步标识
        self._update_synced_indicators()
        
        # 3. 异步加载所有缩略图的视频信息
        if db_is_ready:
            for frame in self.images_frame.winfo_children():
                # 处理单图/视频 widget
                video_path = getattr(frame, 'video_path', None)
                if video_path:
                    info_lbl = getattr(frame, 'info_lbl', None)
                    title_lbl = getattr(frame, 'title_lbl', None)
                    if info_lbl or title_lbl:
                        self._load_video_info_async(
                            video_path, info_lbl, title_lbl, self.config.thumbnail_width
                        )
        
        # 4. 更新当前选中项的信息面板
        selected = self.get_selected_images()
        if selected:
            frames = self.get_selected_frames()
            for img_path in selected:
                if img_path in frames:
                    frame = frames[img_path]
                    video_path = getattr(frame, 'video_path', None)
                    if video_path:
                        self._update_info_panel(video_path, force_refresh=True)
                        break
        
        if self.debug:
            self.debug.print(f"[同步刷新] 已完成: {os.path.basename(folder_path)}")


    def clear_with_reset(self):
        """
        清空图库并重置所有状态
        包含信息面板重置、同步标识集合清空等
        """
        for widget in self.images_frame.grid_slaves():
            widget.destroy()
        self._image_files = []
        self._selected_images.clear()
        self.progress['value'] = 0
        self.canvas.yview_moveto(0)
        self._pending_widgets = []
        self._current_item_index = 0
        
        # 重置信息面板
        self._update_info_panel(None)
        self._current_info_video_path = None
        self._info_expand_data = None
        
        # 折叠信息面板
        if self._info_expanded:
            self._toggle_info_panel()
        
        # 清空同步标识集合引用
        self._synced_video_paths.clear()
        
        if self.debug:
            self.debug.print("[图库] 已清空并重置所有状态")

    def find_frame_by_video_path(self, video_path):
        """
        根据视频路径查找对应的缩略图 frame
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            list: 匹配的 frame 列表（可能多个，如关联视频情况）
        """
        matching_frames = []
        
        if not video_path:
            return matching_frames
        
        try:
            for frame in self.images_frame.winfo_children():
                frame_video_path = getattr(frame, 'video_path', None)
                if frame_video_path and frame_video_path == video_path:
                    matching_frames.append(frame)
        except Exception as e:
            if self.debug:
                self.debug.print(f"查找 frame 失败: {e}")
        
        return matching_frames
    
    def find_frame_by_image_path(self, img_path):
        """
        根据图片路径查找对应的缩略图 frame
        
        Args:
            img_path: 图片文件路径
            
        Returns:
            frame: 匹配的 frame 对象，或 None
        """
        if not img_path:
            return None
        
        try:
            for frame in self.images_frame.winfo_children():
                frame_img_path = getattr(frame, 'img_path', None)
                if frame_img_path and frame_img_path == img_path:
                    return frame
        except Exception as e:
            if self.debug:
                self.debug.print(f"查找 frame 失败: {e}")
        
        return None
    
    def refresh_sync_badges(self):
        """
        刷新所有同步标识显示
        
        在批量更新同步状态后调用，确保所有缩略图的同步标识显示正确
        """
        self._update_synced_indicators()

    def add_load_complete_callback(self, callback):
        """
        添加加载完成后的回调函数
        回调函数签名: callback(folder_path)
        """
        if callback not in self._on_load_complete_callbacks:
            self._on_load_complete_callbacks.append(callback)


    def remove_load_complete_callback(self, callback):
        """移除加载完成回调"""
        if callback in self._on_load_complete_callbacks:
            self._on_load_complete_callbacks.remove(callback)


    def _notify_load_complete(self, folder_path):
        """通知所有加载完成回调"""
        for callback in self._on_load_complete_callbacks:
            try:
                callback(folder_path)
            except Exception as e:
                if self.debug:
                    self.debug.print(f"加载完成回调失败: {e}")

    def _ensure_info_after_load(self, folder_path):
        """
        加载完成后确保视频信息已正确显示
        检查所有缩略图的信息标签是否有内容，如果没有则重新加载
        """
        if not self._current_folder:
            return
        
        if os.path.normpath(self._current_folder) != os.path.normpath(folder_path):
            return
        
        if not self.db or not getattr(self.db, 'is_cache_ready', False):
            return
        
        loaded_count = 0
        missing_count = 0
        
        for frame in self.images_frame.winfo_children():
            video_path = getattr(frame, 'video_path', None)
            if not video_path:
                continue
            
            info_lbl = getattr(frame, 'info_lbl', None)
            title_lbl = getattr(frame, 'title_lbl', None)
            
            # 检查信息标签是否有内容
            has_info = False
            if info_lbl:
                try:
                    text = info_lbl.cget('text')
                    if text and text.strip():
                        has_info = True
                except tk.TclError:
                    pass
            
            if not has_info:
                missing_count += 1
                if self.debug:
                    self.debug.print(f"[加载后检查] 重新加载视频信息: {os.path.basename(video_path)}")
                self._load_video_info_async(
                    video_path, info_lbl, title_lbl, self.config.thumbnail_width
                )
            else:
                loaded_count += 1
        
        if self.debug:
            self.debug.print(f"[加载后检查] 已完成: {loaded_count} 个已加载, {missing_count} 个补充加载")
        
        # 如果有缺失的，再次延迟更新同步标识
        if missing_count > 0:
            self.parent.after(600, self._update_synced_indicators)

    def load_items_with_dict(self, items, items_dict, status_callback=None, scroll_y=None):
        """
        加载项目列表并保存字典引用
        
        Args:
            items: 项目列表（用于渲染）
            items_dict: 字典结构（用于快速更新）
            status_callback: 状态回调
            scroll_y: 滚动位置
        """
        self._items_dict = items_dict
        self.load_items_new(items, status_callback, scroll_y)
    
    def update_item_by_code(self, video_code, updates):
        """
        根据番号更新单个视频项的数据
        
        Args:
            video_code: 视频番号
            updates: 要更新的字段字典
            
        Returns:
            bool: 是否更新成功
        """
        if video_code not in self._items_dict:
            if self.debug:
                self.debug.print(f"[字典更新] 未找到番号: {video_code}")
            return False
        
        item = self._items_dict[video_code]
        
        # 更新字典数据
        for key, value in updates.items():
            item[key] = value
        
        # 查找对应的缩略图frame并更新UI
        video_path = item.get('video_path')
        if video_path:
            frames = self.find_frame_by_video_path(video_path)
            for frame in frames:
                self._update_frame_data(frame, updates)
        
        if self.debug:
            self.debug.print(f"[字典更新] 已更新 {video_code}: {list(updates.keys())}")
        
        return True
    
    def queue_update(self, video_code, updates):
        """将更新加入队列，批量处理"""
        self._pending_updates.append((video_code, updates))
        
        if self._update_timer is None:
            self._update_timer = self.parent.after(500, self._process_pending_updates)
    
    def _process_pending_updates(self):
        """处理待更新的队列"""
        self._update_timer = None
        
        if not self._pending_updates:
            return
        
        # 合并相同video_code的更新
        merged = {}
        for video_code, updates in self._pending_updates:
            if video_code not in merged:
                merged[video_code] = {}
            merged[video_code].update(updates)
        
        # 批量更新
        for video_code, updates in merged.items():
            self.update_item_by_code(video_code, updates)
        
        self._pending_updates.clear()
    
    def update_item_by_path(self, video_path, updates):
        """根据视频路径更新项"""
        for code, item in self._items_dict.items():
            if item.get('video_path') == video_path:
                return self.update_item_by_code(code, updates)
        return False
    
    def get_item_by_code(self, video_code):
        """根据番号获取数据项"""
        return self._items_dict.get(video_code)
    
    def get_item_by_path(self, video_path):
        """根据视频路径获取数据项"""
        for code, item in self._items_dict.items():
            if item.get('video_path') == video_path:
                return item
        return None
    
    def get_all_codes(self):
        """获取所有视频番号列表"""
        return list(self._items_dict.keys())
    
    def get_items_by_tag(self, tag):
        """根据标签获取视频项"""
        results = []
        for code, item in self._items_dict.items():
            tags = item.get('tags', [])
            if tag in tags:
                results.append(item)
        return results
    
    def _update_frame_data(self, frame, updates):
        """更新frame的UI显示"""
        # 更新视频信息标签
        if 'video_title' in updates and hasattr(frame, 'title_lbl'):
            title = updates['video_title']
            if title:
                thumb_width = self.config.thumbnail_width
                max_len = 30 if thumb_width >= 200 else 20
                display_title = title[:max_len] + '...' if len(title) > max_len else title
                frame.title_lbl.config(text=display_title)
        
        if 'actors' in updates and hasattr(frame, 'info_lbl'):
            actors = updates['actors']
            if actors:
                frame.info_lbl.config(text=f"👤 {actors}")
        
        if 'alias_count' in updates:
            # 更新状态标签中的关联数量
            for child in frame.winfo_children():
                if isinstance(child, ttk.Label):
                    try:
                        current_text = child.cget('text')
                        if '🔗' in current_text:
                            base_text = current_text.split('🔗')[0].strip()
                            new_count = updates['alias_count']
                            new_text = f"{base_text} 🔗{new_count}" if new_count > 0 else base_text
                            child.config(text=new_text)
                            break
                    except:
                        pass
        
        if 'duration' in updates and hasattr(frame, 'info_lbl'):
            duration = updates['duration']
            if duration:
                mins = duration // 60
                current_text = frame.info_lbl.cget('text')
                if '⏱️' not in current_text:
                    new_text = f"{current_text} | ⏱️ {mins}分" if current_text else f"⏱️ {mins}分"
                    frame.info_lbl.config(text=new_text)
        
        if 'has_synced_video' in updates:
            # 更新同步标识
            is_synced = updates['has_synced_video']
            if is_synced:
                self._add_sync_badge_to_frame(frame)
            else:
                self._remove_sync_badge_from_frame(frame)
    
    def batch_update_items(self, updates_dict):
        """
        批量更新多个视频项
        
        Args:
            updates_dict: {video_code: {field: value, ...}, ...}
        """
        for video_code, updates in updates_dict.items():
            self.update_item_by_code(video_code, updates)
    
    def clear_with_dict_reset(self):
        """清空图库并重置字典"""
        self.clear()
        self._items_dict.clear()
        self._pending_updates.clear()
        if self._update_timer:
            self.parent.after_cancel(self._update_timer)
            self._update_timer = None

