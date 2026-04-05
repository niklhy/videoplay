#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI组件模块 - 包含Tooltip等通用UI组件
"""
import tkinter as tk


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
