#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI组件模块 - 包含Tooltip等通用UI组件
"""
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser


class ToolTip:
    """简易 Tooltip 类 - 支持动态更新"""
    def __init__(self, widget, text_func=None):
        self.widget = widget
        self.text_func = text_func  # 可以是固定字符串或返回字符串的函数
        self.tip_window = None
        widget.bind('<Enter>', self.enter)
        widget.bind('<Leave>', self.leave)

    def enter(self, event=None):
        # 获取文本（动态或静态）
        if callable(self.text_func):
            text = self.text_func()
        else:
            text = self.text_func or ""
        
        if not text:
            return
            
        x, y, _, _ = self.widget.bbox("insert") if hasattr(self.widget, 'bbox') else (0, 0, 0, 0)
        if hasattr(self.widget, 'winfo_rootx'):
            x = self.widget.winfo_rootx() + (event.x if event else 0) + 25
            y = self.widget.winfo_rooty() + (event.y if event else 0) + 25
        
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         font=("微软雅黑", 9), wraplength=600)
        label.pack()

    def leave(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class DynamicToolTip:
    """支持动态更新内容的Tooltip，适用于Treeview等需要根据鼠标位置显示不同提示的场景"""
    def __init__(self, widget, get_text_callback):
        self.widget = widget
        self.get_text_callback = get_text_callback
        self.tip_window = None
        self._current_text = None
        
        # 绑定事件
        widget.bind('<Motion>', self.on_motion)
        widget.bind('<Leave>', self.leave)
        
    def on_motion(self, event):
        # 获取当前鼠标下的文本
        new_text = self.get_text_callback(event)
        
        if new_text != self._current_text:
            self._current_text = new_text
            self.leave()  # 关闭旧的
            
        if new_text and not self.tip_window:
            self.show(event.x_root + 15, event.y_root + 10, new_text)
        elif not new_text and self.tip_window:
            self.leave()
            
    def show(self, x, y, text):
        if self.tip_window:
            return
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=text, justify=tk.LEFT,
                        background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                        font=("微软雅黑", 9), wraplength=800)
        label.pack()
        
    def leave(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None
        self._current_text = None


class ColorSettingsDialog:
    """图库颜色设置对话框"""
    
    def __init__(self, parent, config_manager, debug_utils=None):
        self.parent = parent
        self.config = config_manager
        self.debug = debug_utils
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("图库颜色设置")
        self.dialog.geometry("450x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self.color_vars = {}
        self.color_labels = {}
        
        self._create_ui()
        self._load_current_colors()
    
    def _create_ui(self):
        """创建UI"""
        # 标题
        title_label = ttk.Label(
            self.dialog, 
            text="设置图库浏览器中各状态标识的背景色",
            font=("微软雅黑", 10, "bold")
        )
        title_label.pack(pady=10)
        
        # 说明文字
        desc_label = ttk.Label(
            self.dialog,
            text="点击颜色按钮可更改对应状态的背景色",
            foreground="gray"
        )
        desc_label.pack(pady=(0, 10))
        
        # 颜色设置区域
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        
        # 颜色配置项
        self.color_items = [
            ("linked_video", "🔗 关联视频", "图片关联到其他位置的视频时显示"),
            ("video_placeholder", "🎬 视频文件", "无缩略图的视频占位符"),
            ("dedicated_match", "🎬 专属视频", "图片与视频完全同名匹配"),
            ("cover_match", "📁 封面匹配", "使用通用封面图片"),
            ("normal_video", "🎬 普通视频", "有其他匹配方式的视频"),
            ("image_only", "🖼️ 仅图片", "无对应视频的独立图片"),
            ("selected", "选中状态", "图片被选中时的高亮色")
        ]
        
        for key, label, desc in self.color_items:
            row = ttk.Frame(main_frame)
            row.pack(fill=tk.X, pady=8)
            
            # 状态标签
            lbl = ttk.Label(row, text=label, width=15)
            lbl.pack(side=tk.LEFT)
            
            # 颜色预览框
            preview = tk.Frame(row, width=60, height=25, relief=tk.SOLID, borderwidth=1)
            preview.pack(side=tk.LEFT, padx=10)
            preview.pack_propagate(False)
            self.color_labels[key] = preview
            
            # 颜色值输入框
            var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=var, width=10)
            entry.pack(side=tk.LEFT, padx=5)
            self.color_vars[key] = var
            
            # 颜色选择按钮
            btn = ttk.Button(
                row, 
                text="选择...",
                command=lambda k=key: self._choose_color(k)
            )
            btn.pack(side=tk.LEFT, padx=5)
            
            # 描述
            desc_lbl = ttk.Label(row, text=desc, foreground="gray", font=("微软雅黑", 8))
            desc_lbl.pack(side=tk.LEFT, padx=10)
        
        # 按钮区域
        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill=tk.X, padx=20, pady=15)
        
        ttk.Button(
            btn_frame, 
            text="恢复默认",
            command=self._reset_defaults
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            btn_frame,
            text="取消",
            command=self.dialog.destroy
        ).pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(
            btn_frame,
            text="保存",
            command=self._save_colors
        ).pack(side=tk.RIGHT, padx=5)
    
    def _load_current_colors(self):
        """加载当前颜色配置"""
        for key, _, _ in self.color_items:
            color = self.config.get_gallery_color(key)
            self.color_vars[key].set(color)
            self._update_preview(key, color)
    
    def _update_preview(self, key, color):
        """更新颜色预览"""
        try:
            self.color_labels[key].config(bg=color)
        except tk.TclError:
            # 无效颜色，使用默认白色
            self.color_labels[key].config(bg="white")
    
    def _choose_color(self, key):
        """打开颜色选择器"""
        current_color = self.color_vars[key].get()
        color = colorchooser.askcolor(
            parent=self.dialog,
            title=f"选择颜色 - {key}",
            initialcolor=current_color
        )
        
        if color[1]:  # color[1] 是十六进制颜色值
            self.color_vars[key].set(color[1])
            self._update_preview(key, color[1])
    
    def _reset_defaults(self):
        """恢复默认颜色"""
        if messagebox.askyesno("确认", "确定要恢复默认颜色设置吗？"):
            defaults = self.config.DEFAULT_CONFIG.get("gallery_colors", {})
            for key, _, _ in self.color_items:
                default_color = defaults.get(key, "white")
                self.color_vars[key].set(default_color)
                self._update_preview(key, default_color)
    
    def _save_colors(self):
        """保存颜色设置"""
        try:
            for key, _, _ in self.color_items:
                color = self.color_vars[key].get().strip()
                # 简单验证颜色格式
                if not color.startswith("#") or len(color) != 7:
                    messagebox.showerror(
                        "错误", 
                        f"颜色格式错误: {color}\n请使用十六进制格式，如 #FFE4B5"
                    )
                    return
                self.config.set_gallery_color(key, color)
            
            self.config.save_config()
            messagebox.showinfo("成功", "颜色设置已保存\n刷新图库后生效")
            self.dialog.destroy()
            
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")
