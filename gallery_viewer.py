#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图库浏览模块 - 完全修复版
修复：鼠标滚轮滚动、右键菜单弹出、缓存保存
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageDraw, ImageFont
import os
import threading
import re


class GalleryViewer:
    """图库查看器 - 增强视觉标识版"""
    
    def __init__(self, parent_frame, config_manager, db_manager, video_player, image_viewer, 
                 on_select_callback, on_double_click_callback, debug_utils=None,
                 on_tags_changed=None, get_browser_folder=None, on_set_tag=None,
                 on_relation_request=None, on_relation_removed=None):
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

        self._selected_images = set()
        self._multi_select_mode = False
        self._image_files = []
        self._current_folder = None
        self._load_generation = 0
        self._scroll_save_timer = None
        self._pending_widgets = []
        self._current_item_index = 0
        
        self._create_ui()
    
    def _create_ui(self):
        """创建UI - 修复滚轮绑定"""
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
                            video_path=None, primary_video_path=None, file_info=None):
        """添加缩略图控件 - 增强视觉标识（已移除重复的关联视频标签）"""
        
        thumb_width = self.config.thumbnail_width
        thumb_height = self.config.thumbnail_width
        fixed_height = thumb_height + 70

        frame = tk.Frame(self.images_frame, relief=tk.FLAT, borderwidth=0, bg='white')
        
        is_selected = img_path in self._selected_images
        is_video_placeholder = (img_path == "__VIDEO_PLACEHOLDER__")
        has_video = video_path is not None
        match_type = file_info.get('match_type', '') if file_info else ''
        
        # 根据匹配类型设置背景色和标签文本
        if primary_video_path:
            bg_color = "#FFE4B5"
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif is_video_placeholder:
            bg_color = "#2C3E50"
            label_text = "🎬 视频文件"
            label_fg = "#ECF0F1"
        elif has_video:
            if match_type == 'dedicated' or match_type == 'sub_dedicated':
                bg_color = "#D4EDDA"
                label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type == 'cover' or match_type == 'sub_cover':
                bg_color = "#D1ECF1"
                label_text = "📁 封面匹配"
                label_fg = "teal"
            else:
                bg_color = "#D6EAF8"
                label_text = "🎬 视频"
                label_fg = "blue"
        else:
            bg_color = "white"
            label_text = "🖼️ 仅图片"
            label_fg = "gray"
        
        if is_selected:
            bg_color = "#f5a2a2"
        
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
            new_bg = "#f5a2a2" if selected else bg_color
            if thumb_container.winfo_exists():
                thumb_container.config(bg=new_bg)
            if lbl.winfo_exists():
                lbl.config(bg=new_bg)
            frame._selected_state = selected
        
        frame._selected_state = is_selected
        if is_selected:
            thumb_container.config(bg="#f5a2a2")
            lbl.config(bg="#f5a2a2")
        
        # 绑定事件
        lbl.bind("<Double-Button-1>", lambda e, p=img_path: self.on_double_click(p))
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
        
        # 设置视频路径（优先主视频）
        if primary_video_path and os.path.exists(primary_video_path):
            frame.video_path = primary_video_path
            frame.is_alias = True
            frame.alias_path = video_path
            frame.has_local_video = False
        elif video_path and os.path.exists(video_path):
            frame.video_path = video_path
            frame.is_alias = False
            frame.has_local_video = True
        
        # 显示状态标签（只此一个，不再重复创建）
        if not is_video_placeholder:
            status_lbl = ttk.Label(frame, text=label_text, 
                                font=("微软雅黑", 7), foreground=label_fg)
            status_lbl.pack(padx=2, pady=(0, 2))
            status_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
            status_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))
        
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
        
        # 文件名
        if is_video_placeholder and file_info:
            filename = file_info.get('file_name', 'Unknown Video')
        else:
            filename = os.path.basename(img_path)
            
        max_len = 30 if thumb_width >= 200 else 20
        display = filename[:max_len] + "..." if len(filename) > max_len else filename
        name_lbl = ttk.Label(frame, text=display, wraplength=thumb_width-10, font=("微软雅黑", 8))
        name_lbl.pack(padx=2, pady=(0, 2))
        name_lbl.bind("<Button-1>", lambda e, p=img_path, f=frame: self._select_image(p, f, e))
        name_lbl.bind("<Button-3>", lambda e, p=img_path, f=frame: self._show_context_menu(e, p, f))
        
        # 绑定滚轮
        self._bind_mousewheel_recursive(frame)
        
        items_per_row = max(1150 // (thumb_width + 20), 1)
        row, col = index // items_per_row, index % items_per_row
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        
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

    def _select_image(self, img_path, frame, event=None):
        """优化版：避免全量遍历和UI重排"""
        ctrl = (event and (event.state & 0x4) != 0)
        
        # 非多选模式且没有按Ctrl
        if not self._multi_select_mode and not ctrl:
            # 获取之前选中的项
            old_selected = list(self._selected_images)
            
            # 如果当前点击的就是唯一选中的项，直接返回
            if len(old_selected) == 1 and old_selected[0] == img_path:
                return
            
            # 取消所有其他选中
            for old_path in old_selected:
                if old_path != img_path:
                    # 找到对应的frame
                    for child in self.images_frame.winfo_children():
                        if hasattr(child, 'img_path') and child.img_path == old_path:
                            if hasattr(child, 'set_selected_state'):
                                child.set_selected_state(False)
                            break
                    self._selected_images.remove(old_path)
        
        # 切换当前项的选中状态
        if img_path in self._selected_images:
            self._selected_images.remove(img_path)
            if hasattr(frame, 'set_selected_state'):
                frame.set_selected_state(False)
        else:
            self._selected_images.add(img_path)
            if hasattr(frame, 'set_selected_state'):
                frame.set_selected_state(True)
        
        if self.on_select:
            self.on_select(img_path, frame, img_path in self._selected_images)
    
    def _show_context_menu(self, event, img_path, frame, video_path=None):
        """右键菜单 - 修复：确保可以播放关联视频"""
        from core import PathUtils
        
        folder = os.path.dirname(img_path)
        is_video_placeholder = (img_path == "__VIDEO_PLACEHOLDER__")
        has_local_video = getattr(frame, 'has_local_video', False)
        is_alias = getattr(frame, 'is_alias', False)
        
        # 关键修复：确保能获取到关联视频路径（包括主视频路径）
        actual_video_path = getattr(frame, 'video_path', None) or video_path
        
        try:
            top_window = self.parent.winfo_toplevel()
            menu = tk.Menu(top_window, tearoff=0)
        except:
            menu = tk.Menu(self.parent, tearoff=0)
        
        menu.add_command(label="📋 提取主文件夹名称", command=self._copy_browser_folder_name)
        menu.add_separator()
        
        # 关键修复：只要有 actual_video_path（包括关联视频），就显示播放选项
        if actual_video_path and os.path.exists(actual_video_path):
            if is_alias:
                menu.add_command(label=f"▶ 播放主视频 (关联)", 
                            command=lambda: self.video_player.play_video(actual_video_path),
                            font=("微软雅黑", 9, "bold"), foreground="green")
                # 添加显示主视频路径的提示（只读）
                display_path = actual_video_path if len(actual_video_path) < 40 else "..." + actual_video_path[-37:]
                menu.add_command(label=f"   主视频: {display_path}", state="disabled")
            else:
                menu.add_command(label=f"▶ 播放: {os.path.basename(actual_video_path)[:40]}", 
                            command=lambda: self.video_player.play_video(actual_video_path),
                            font=("微软雅黑", 9, "bold"))
            menu.add_separator()
        
        if not is_video_placeholder:
            folder_videos = PathUtils.find_video_files(folder)
            if folder_videos:
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
        
       # 关键修复：在"解除关联"菜单项中，确保状态正确显示
        if is_alias:
            menu.add_command(label="🔗 已关联到主视频", state="disabled")
            menu.add_command(label="   解除关联", 
                        command=lambda: self._remove_video_relation(frame))
            menu.add_separator()
        elif not has_local_video and not is_video_placeholder:
            # 无本地视频且无关联时，显示创建关联选项
            if self.on_relation_request:
                menu.add_command(label="🔗 智能关联到主视频（自动匹配番号）...", 
                                command=lambda: self.on_relation_request(img_path, frame),
                                font=("微软雅黑", 9, "bold"))
            else:
                # 降级使用原有方法
                menu.add_command(label="🔗 关联到主视频（选择视频文件）...", 
                                command=lambda: self._create_video_relation_for_thumbnail(img_path, frame),
                                font=("微软雅黑", 9, "bold"))
            menu.add_separator()
        
        tag_target_path = video_path if video_path else None
        if tag_target_path:
            tags = self.config.get_video_tags(tag_target_path)
            if tags:
                menu.add_command(label=f"🏷️ 标签: {', '.join(tags[:3])}", state="disabled")
            menu.add_command(label="设置标签...", 
                           command=lambda: self._set_tag(img_path, frame))
            menu.add_separator()
        
        menu.add_command(label="📋 提取番号", command=lambda: self._extract_code(folder))
        menu.add_separator()
        menu.add_command(label="查看原图", command=lambda: self.image_viewer.view(img_path, self.parent))
        menu.add_command(label="复制图片路径", command=lambda: self._copy_path(img_path))
        menu.add_command(label="打开所在文件夹", command=lambda: self._open_folder(folder))
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            if self.debug:
                self.debug.print(f"右键菜单弹出失败: {e}")
        finally:
            menu.grab_release()

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
            
            is_video_type = (file_info.get('file_type') == 'video')
            
            if is_video_type:
                thumb_path = self._find_video_thumbnail(db_file_path)
                video_path = file_info.get('video_path') or db_file_path
                if not thumb_path:
                    thumb_path = "__VIDEO_PLACEHOLDER__"
            else:
                thumb_path = db_file_path
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
                self._pending_widgets.append({
                    'index': len(self._pending_widgets),
                    'folder_name': folder_name,
                    'thumb_path': thumb_path,
                    'thumb': thumb,
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
                return
            
            end_idx = min(start_idx + batch_size, len(self._pending_widgets))
            
            for widget_info in self._pending_widgets[start_idx:end_idx]:
                if widget_info['gen'] != self._load_generation:
                    return
                
                self._add_thumbnail_widget(
                    widget_info['index'],
                    widget_info['folder_name'],
                    widget_info['thumb_path'],
                    widget_info['thumb'],
                    video_path=widget_info['video_path'],
                    primary_video_path=widget_info['primary_video_path'],
                    file_info=widget_info['file_info']
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
        """加载处理好的项目列表（支持附加缩略图）"""
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
            
            main_img = item['main_image']
            alternates = item.get('alternates', [])
            video_path = item.get('video_path')
            primary_video = item.get('primary_video_path')
            match_type = item.get('match_type', 'standalone')
            folder_name = item['folder_name']
            
            # 创建缩略图
            if main_img == "__VIDEO_PLACEHOLDER__":
                thumb = self._create_video_placeholder(self.config.thumbnail_width)
            else:
                thumb = self._create_thumbnail(main_img, self.config.thumbnail_width)
            
            if thumb:
                # 如果有附加缩略图，创建所有缩略图
                alternate_thumbs = []
                for alt_path in alternates:
                    alt_thumb = self._create_thumbnail(alt_path, self.config.thumbnail_width)
                    if alt_thumb:
                        alternate_thumbs.append({'path': alt_path, 'thumb': alt_thumb})
                
                self._pending_widgets.append({
                    'index': i,
                    'folder_name': folder_name,
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
                return
            
            end_idx = min(start_idx + batch_size, len(self._pending_widgets))
            
            for widget_info in self._pending_widgets[start_idx:end_idx]:
                if widget_info['gen'] != self._load_generation:
                    return
                
                has_alternates = len(widget_info['alternates']) > 0
                
                if has_alternates:
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
                        widget_info['video_path'],
                        widget_info['primary_video_path'],
                        widget_info['file_info']
                    )
            
            if status_callback:
                progress = min(100, int((end_idx / total) * 100))
                msg = f"渲染中... ({end_idx}/{total})"
                self.parent.after(0, lambda m=msg, p=progress: status_callback(m, p))
            
            self.parent.after(10, lambda: batch_render(end_idx, batch_size))
        
        batch_render(0, 3)

    def _add_thumbnail_with_alternates(self, index, folder_name, main_img_path, main_thumb, 
                                       alternates, video_path, primary_video_path, match_type, file_info):
        """添加带附加缩略图切换功能的控件"""
        thumb_width = self.config.thumbnail_width
        thumb_height = self.config.thumbnail_width
        fixed_height = thumb_height + 90  # 额外空间给切换按钮

        frame = tk.Frame(self.images_frame, relief=tk.FLAT, borderwidth=0, bg='white')
        frame.configure(width=thumb_width + 20, height=fixed_height)
        frame.pack_propagate(False)
        
        # 确定背景色
        if primary_video_path:
            bg_color = "#FFE4B5"  # 关联视频 - 橙色
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif video_path:
            if match_type == 'dedicated':
                bg_color = "#D4EDDA"  # 专属匹配 - 绿色
                label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type == 'cover':
                bg_color = "#D1ECF1"  # 封面匹配 - 青色
                label_text = "📁 封面匹配"
                label_fg = "teal"
            else:
                bg_color = "#D6EAF8"  # 普通视频 - 蓝色
                label_text = "🎬 视频"
                label_fg = "blue"
        else:
            bg_color = "white"
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
        lbl.bind("<Double-Button-1>", lambda e, p=main_img_path: self.on_double_click(p))
        lbl.bind("<Button-1>", lambda e, p=main_img_path, f=frame: self._select_image(p, f, e))
        lbl.bind("<Button-3>", lambda e, p=main_img_path, f=frame, v=video_path: 
                self._show_context_menu(e, p, f, v))
        
        # 设置frame属性
        frame.video_path = primary_video_path if primary_video_path else video_path
        frame.is_alias = bool(primary_video_path)
        frame.alias_path = main_img_path if primary_video_path else None
        frame.has_local_video = bool(video_path) and not primary_video_path
        frame.file_info = file_info
        frame.img_path = main_img_path
        frame.thumb_label = lbl  # 保存引用以便切换时更新路径
        
        def set_selected_state(selected):
            new_bg = "#f5a2a2" if selected else bg_color
            thumb_container.config(bg=new_bg)
            lbl.config(bg=new_bg)
            if hasattr(lbl, 'current_dot'):
                lbl.current_dot.config(bg=new_bg)
        
        frame.set_selected_state = set_selected_state
        
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
        
        # 文件名（显示当前图片名）
        display_name = os.path.basename(main_img_path)
        name_lbl = ttk.Label(frame, text=display_name, wraplength=thumb_width-10, 
                            font=("微软雅黑", 8))
        name_lbl.pack(padx=2, pady=(0, 2))
        frame.name_label = name_lbl
        
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
        """更新 frame 的视觉标识（背景色、标签文字）- 修复：支持解除关联状态"""
        # 根据当前状态确定视觉样式
        has_local_video = getattr(frame, 'has_local_video', False)
        
        if primary_video_path:
            # 关联视频状态 - 橙色
            bg_color = "#FFE4B5"
            label_text = "🔗 关联视频"
            label_fg = "orange"
        elif has_local_video:
            # 有本地视频但未关联 - 检查 match_type
            match_type = getattr(frame, 'match_type', '')
            if match_type in ['dedicated', 'sub_dedicated']:
                bg_color = "#D4EDDA"  # 专属匹配 - 绿色
                label_text = "🎬 专属视频"
                label_fg = "green"
            elif match_type in ['cover', 'sub_cover']:
                bg_color = "#D1ECF1"  # 封面匹配 - 青色
                label_text = "📁 封面匹配"
                label_fg = "teal"
            else:
                bg_color = "#D6EAF8"  # 普通视频 - 蓝色
                label_text = "🎬 视频"
                label_fg = "blue"
        else:
            # 仅图片状态（解除关联后）- 白色
            bg_color = "white"
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
    
    def clear_selection(self):
        self._selected_images.clear()
        for child in self.images_frame.winfo_children():
            if hasattr(child, 'set_selected_state'):
                child.set_selected_state(False)
    
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
        """刷新当前文件夹显示 - 优化：确保视觉状态正确更新"""
        if not self._current_folder:
            return
        
        if keep_scroll:
            current_y = self.get_scroll_y()
            # 记录当前选中的图片，以便刷新后恢复
            current_selected = list(self._selected_images)
            
            self.load_folder(self._current_folder, None, scroll_y=current_y)
            
            # 刷新后恢复选中状态（如果有）
            if current_selected and self.debug:
                self.debug.print(f"刷新后恢复 {len(current_selected)} 个选中项")
        else:
            self.load_folder(self._current_folder, None)
    
    def load_folder(self, folder_path, status_callback=None, scroll_y=None):
        """加载文件夹（入口）"""
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
                    # 单视频多图片 -> 查找通用封面
                    preferred = ['cover', 'folder', 'poster', 'thumb', 'preview']
                    selected = None
                    for name in preferred:
                        if name in images:
                            selected = images[name]
                            break
                    if not selected and all_images_ordered:
                        selected = all_images_ordered[0]
                    if selected:
                        items.append({
                            'folder_name': folder_display_name,
                            'img_path': selected,
                            'video_path': video_path,
                            'is_placeholder': False,
                            'match_type': 'cover'
                        })
                        matched_images.add(os.path.splitext(os.path.basename(selected))[0].lower())
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
            for item in self._image_files:
                img_path = item.get('img_path')
                # 只处理非占位符的图片，且尚未设置 primary_video_path 的
                if (img_path and 
                    img_path != "__VIDEO_PLACEHOLDER__" and 
                    not item.get('primary_video_path') and
                    not item.get('video_path')):  # 也没有本地视频
                    
                    try:
                        db_file = self.db.get_file_by_path(img_path)
                        if db_file and db_file.get('is_alias'):
                            primary = self.db.get_primary_video_path(db_file.get('id'))
                            if primary:
                                item['primary_video_path'] = primary
                                item['video_path'] = primary  # 确保 video_path 指向主视频
                                item['is_alias'] = 1
                                item['match_type'] = 'linked'
                                if self.debug:
                                    self.debug.print(f"实时扫描关联发现: {os.path.basename(img_path)} -> {os.path.basename(primary)}")
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"实时扫描查询关联失败 {img_path}: {e}")
        
        total = len(self._image_files)
        if total == 0:
            if status_callback and generation == self._load_generation:
                self.parent.after(0, lambda: status_callback("文件夹为空", 100))
            return
        
        # 批量创建缩略图并显示
        for idx, item in enumerate(self._image_files):
            if generation != self._load_generation:
                return
            thumb = self._create_thumbnail(item['img_path'], self.config.thumbnail_width)
            if thumb:
                # 检查数据库关联（已有补充逻辑，这里保留原有检查）
                primary_video_path = item.get('primary_video_path')
                if not primary_video_path and self.db and item.get('video_path'):
                    file_info = self.db.get_file_by_path(item['video_path'])
                    if file_info and file_info.get('is_alias'):
                        primary_video_path = self.db.get_primary_video_path(file_info.get('id'))
                
                # 添加额外信息到file_info
                item['file_info'] = item.get('file_info', {})
                item['file_info']['match_type'] = item.get('match_type', '')
                if 'file_name' in item:
                    item['file_info']['file_name'] = item['file_name']
                
                self.parent.after(0, lambda i=idx, f=item['folder_name'], img=item['img_path'],
                                th=thumb, vp=item['video_path'], pvp=primary_video_path,
                                fi=item['file_info']:
                                self._add_thumbnail_widget(i, f, img, th, vp, pvp, fi))
            
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
                                               w['video_path'], w['primary_video_path'], w['file_info'])
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
                        if file_info and file_info.get('is_alias'):
                            primary_video_path = self.db.get_primary_video_path(file_info.get('id'))
                        
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
                    file_info=widget_info['file_info']
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
                folder_path = os.path.dirname(img_path)
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
                    # 更新路径显示
                    if self.on_select:
                        self.on_select(img_path, frame, True)
                    if self.debug:
                        self.debug.print(f"关联成功并已更新UI：{os.path.basename(img_path)} -> {os.path.basename(primary_video_path)}")
            
            # 刷新整个图库以更新其他状态（但可能丢失滚动位置，可保留）
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
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder_path,))
            result = cursor.fetchone()
            if result:
                return result['folder_id']
            
            parent = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
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

