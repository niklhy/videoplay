#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心模块 - 配置管理、工具函数、媒体播放器
"""
import json
import os
import re
import subprocess
import sys
import tempfile  # 新增：添加缺失的导入
import threading
import time
import hashlib
import unicodedata
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk


class ConfigManager:
    """配置管理器 - 统一管理程序配置"""
    
    DEFAULT_CONFIG = {
        "player_path": r"C:\Program Files\PotPlayer\PotPlayerMini64.exe",
        "viewer_geometry": None,
        "thumbnail_size": "中",
        "debug_mode": False,
        "bookmarks": [],
        "tags": [],
        "video_tags": {},
        "tag_filter_mode": "union",
        "last_folder": None,
        "last_scroll_y": 0.0,
        "remember_location": True,
        "gallery_colors": {
            "linked_video": "#FFE4B5",
            "video_placeholder": "#2C3E50",
            "dedicated_match": "#D4EDDA",
            "cover_match": "#D1ECF1",
            "cover_priority": "#C5E1A5",
            "auto_match": "#E8D5F2",        
            "normal_video": "#D6EAF8",
            "image_only": "white",
            "selected": "#f5a2a2",
            "sub_multi_video": "#FADBD8",
            "sub_image_only": "#D6EAF8",
            "sub_dedicated": "#D5F5E3",
            "sub_cover": "#D1F2EB"
        }
    }
    
    THUMBNAIL_SIZES = {"小": 150, "中": 200, "大": 250, "最大": 300}
    
    def __init__(self, config_file=None):
        if config_file is None:
            self.config_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "config.json"
            )
        else:
            self.config_file = config_file
        self._config = {}
        self._load_config()
    
    def _load_config(self):
        import copy
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self._config = copy.deepcopy(self.DEFAULT_CONFIG)
                    self._config.update(loaded)
                    for key in ["last_folder", "last_scroll_y", "remember_location"]:
                        if key not in self._config:
                            self._config[key] = self.DEFAULT_CONFIG[key]
            else:
                self._config = copy.deepcopy(self.DEFAULT_CONFIG)
                self.save_config()
        except Exception as e:
            print(f"加载配置失败: {e}")
            self._config = copy.deepcopy(self.DEFAULT_CONFIG)
    
    def save_config(self):
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False
    
    @property
    def player_path(self): 
        return self._config.get("player_path", self.DEFAULT_CONFIG["player_path"])
    @player_path.setter
    def player_path(self, value): 
        self._config["player_path"] = value
    
    @property
    def viewer_geometry(self): 
        return self._config.get("viewer_geometry")
    @viewer_geometry.setter
    def viewer_geometry(self, value): 
        self._config["viewer_geometry"] = value
    
    @property
    def thumbnail_size(self): 
        return self._config.get("thumbnail_size", "中")
    @thumbnail_size.setter
    def thumbnail_size(self, value): 
        if value in self.THUMBNAIL_SIZES: 
            self._config["thumbnail_size"] = value
    
    @property
    def thumbnail_width(self): 
        return self.THUMBNAIL_SIZES.get(self.thumbnail_size, 200)
    
    @property
    def debug_mode(self): 
        return self._config.get("debug_mode", False)
    @debug_mode.setter
    def debug_mode(self, value): 
        self._config["debug_mode"] = bool(value)
    
    @property
    def bookmarks(self): 
        return self._config.get("bookmarks", [])
    
    @property
    def last_folder(self): 
        return self._config.get("last_folder")
    @last_folder.setter
    def last_folder(self, value): 
        self._config["last_folder"] = value
    
    @property
    def last_scroll_y(self): 
        return self._config.get("last_scroll_y", 0.0)
    @last_scroll_y.setter
    def last_scroll_y(self, value): 
        self._config["last_scroll_y"] = float(value)
    
    @property
    def remember_location(self): 
        return self._config.get("remember_location", True)
    @remember_location.setter
    def remember_location(self, value): 
        self._config["remember_location"] = bool(value)
    
    @property
    def gallery_colors(self):
        """获取图库颜色配置"""
        default_colors = self.DEFAULT_CONFIG.get("gallery_colors", {})
        return self._config.get("gallery_colors", default_colors)
    
    def get_gallery_color(self, color_key):
        """获取指定的图库颜色"""
        colors = self.gallery_colors
        default_colors = self.DEFAULT_CONFIG.get("gallery_colors", {})
        return colors.get(color_key, default_colors.get(color_key, "white"))
    
    def set_gallery_color(self, color_key, color_value):
        """设置指定的图库颜色"""
        import copy
        if "gallery_colors" not in self._config:
            self._config["gallery_colors"] = copy.deepcopy(self.DEFAULT_CONFIG.get("gallery_colors", {}))
        else:
            # 确保我们不修改默认配置
            self._config["gallery_colors"] = copy.deepcopy(self._config["gallery_colors"])
        self._config["gallery_colors"][color_key] = color_value
    
    def reset_gallery_colors(self):
        """重置图库颜色为默认值"""
        import copy
        self._config["gallery_colors"] = copy.deepcopy(self.DEFAULT_CONFIG.get("gallery_colors", {}))
    
    def add_bookmark(self, path):
        path = os.path.normpath(path.strip()) if path else ""
        if path and path not in self._config["bookmarks"]:
            self._config["bookmarks"].append(path)
            return True
        return False
    
    def remove_bookmark(self, index):
        if 0 <= index < len(self._config["bookmarks"]):
            return self._config["bookmarks"].pop(index)
        return None
    
    def get_bookmark(self, index):
        if 0 <= index < len(self._config["bookmarks"]):
            return self._config["bookmarks"][index]
        return None
    
    @property
    def tags(self): 
        return self._config.get("tags", [])
    
    def add_tag(self, tag_name):
        tag_name = tag_name.strip() if tag_name else ""
        if tag_name and tag_name not in self._config["tags"]:
            self._config["tags"].append(tag_name)
            return True
        return False
    
    def remove_tag(self, tag_name):
        if tag_name in self._config["tags"]:
            self._config["tags"].remove(tag_name)
            for video_path in list(self._config["video_tags"].keys()):
                if tag_name in self._config["video_tags"][video_path]:
                    self._config["video_tags"][video_path].remove(tag_name)
                    if not self._config["video_tags"][video_path]:
                        del self._config["video_tags"][video_path]
            return True
        return False
    
    def rename_tag(self, old_name, new_name):
        new_name = new_name.strip() if new_name else ""
        if not new_name or new_name in self._config["tags"] or old_name not in self._config["tags"]:
            return False
        idx = self._config["tags"].index(old_name)
        self._config["tags"][idx] = new_name
        for video_path in self._config["video_tags"]:
            if old_name in self._config["video_tags"][video_path]:
                self._config["video_tags"][video_path].remove(old_name)
                self._config["video_tags"][video_path].append(new_name)
        return True
    
    @property
    def tag_filter_mode(self): 
        return self._config.get("tag_filter_mode", "union")
    @tag_filter_mode.setter
    def tag_filter_mode(self, mode):
        if mode in ["union", "intersection"]:
            self._config["tag_filter_mode"] = mode
    
    @property
    def video_tags(self): 
        return self._config.get("video_tags", {})
    
    def get_video_tags(self, video_path):
        return self._config["video_tags"].get(video_path, [])
    
    def set_video_tags(self, video_path, tags_list):
        if tags_list:
            self._config["video_tags"][video_path] = list(tags_list)
        elif video_path in self._config["video_tags"]:
            del self._config["video_tags"][video_path]
    
    def get_videos_by_tag(self, tag_name):
        return [vp for vp, tags in self._config["video_tags"].items() if tag_name in tags]
    
    def get_videos_by_tags(self, tag_names, mode="union"):
        if not tag_names:
            return []
        if len(tag_names) == 1:
            return self.get_videos_by_tag(tag_names[0])
        
        result = set()
        first = True
        for tag_name in tag_names:
            videos = set(self.get_videos_by_tag(tag_name))
            if mode == "intersection":
                result = videos if first else result & videos
            else:
                result |= videos
            first = False
        return list(result)
    
    def get_all_tagged_videos(self):
        return list(self._config["video_tags"].keys())
    
    def get_tag_statistics(self):
        return {tag: len(self.get_videos_by_tag(tag)) for tag in self._config["tags"]}


class PathUtils:
    """路径工具类"""
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.pdf'}
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.ts', '.m4v', '.mpg', '.mpeg'}
    
    @classmethod
    def is_image_file(cls, filepath):
        return os.path.splitext(filepath)[1].lower() in cls.IMAGE_EXTENSIONS
    
    @classmethod
    def is_video_file(cls, filepath):
        return os.path.splitext(filepath)[1].lower() in cls.VIDEO_EXTENSIONS
    
    @classmethod
    def find_video_files(cls, folder_path):
        try:
            return sorted([e.path for e in os.scandir(folder_path) 
                          if e.is_file() and cls.is_video_file(e.path)])
        except:
            return []
    
    @classmethod
    def find_image_files(cls, folder_path):
        try:
            return sorted([e.path for e in os.scandir(folder_path) 
                          if e.is_file() and cls.is_image_file(e.path)])
        except:
            return []
    
    @staticmethod
    def open_folder_in_explorer(folder_path):
        if not folder_path or not os.path.exists(folder_path):
            return False, f"文件夹不存在: {folder_path}"
        try:
            folder_path = os.path.normpath(folder_path)
            if os.name == 'nt':
                subprocess.Popen(['explorer', folder_path], shell=False)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder_path])
            else:
                subprocess.Popen(['xdg-open', folder_path])
            return True, ""
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def extract_code_from_folder_name(folder_name):
        """已废弃，请使用 core.extract_code_from_text"""
        result = extract_code_from_text(folder_name)
        return result['canonical'] if result else None

class PatternMatcher:
    """番号提取与匹配器 - 支持多种格式的番号/编号识别"""
    
    def __init__(self):
        # 预编译正则提高性能
        # 从开头匹配的模式（用于文件名/文件夹名整体匹配）
        self.patterns = [
            (re.compile(r'^([fF][cC]2?[_-]?[pP][pP][vV][_-]?(\d{6,}))'), 'fc2_ppv'),
            (re.compile(r'^([fF][cC]2?[_-]?(\d{6,}))'), 'fc2'),
            (re.compile(r'^(\d+[a-zA-Z]+[_-]\d+)'), 'digit_prefix'),
            (re.compile(r'^([a-zA-Z]+\d*[_-]\d+)'), 'standard'),
            (re.compile(r'^(\d{3,}[_-]\d{3,})'), 'numeric'),
        ]
        
        # 用于search()的模式，可在字符串任意位置查找番号
        self.search_patterns = [
            (re.compile(r'([a-zA-Z]{2,}\d*-\d{3,})'), 'standard_search'),
            (re.compile(r'(\d+[a-zA-Z]{2,}-\d+)'), 'digit_prefix_search'),
            (re.compile(r'(fc2[_-]?\d{6,})', re.I), 'fc2_search'),
            (re.compile(r'(fc2?[_-]?ppv[_-]?\d{6,})', re.I), 'fc2_ppv_search'),
        ]
    
    def extract_code(self, text: str):
        """
        提取完整番号信息
        返回包含完整番号、字母部分、数字部分的字典，若未找到则返回 None
        """
        if not text:
            return None
        
        # 1. 从开头匹配
        for pattern_regex, ptype in self.patterns:
            match = pattern_regex.match(text)
            if match:
                matched_text = match.group(1)
                full_code = matched_text.replace('-', '').replace('_', '').lower()
                letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                numbers = ''.join(re.findall(r'\d+', full_code))
                
                return {
                    'full': full_code,
                    'original': matched_text,
                    'canonical': matched_text.replace('_', '-'),
                    'letters': letters,
                    'numbers': numbers,
                    'type': ptype,
                    'is_short_numeric': False
                }
        
        # 2. 搜索匹配（任意位置）
        for pattern_regex, ptype in self.search_patterns:
            match = pattern_regex.search(text)
            if match:
                matched_text = match.group(1)
                full_code = matched_text.replace('-', '').replace('_', '').lower()
                letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                numbers = ''.join(re.findall(r'\d+', full_code))
                
                # 过滤掉纯数字过长的情况（可能是日期）
                if len(numbers) > 10:
                    continue
                if len(letters) < 2 and len(numbers) > 4:
                    continue
                
                return {
                    'full': full_code,
                    'original': matched_text,
                    'canonical': matched_text.replace('_', '-'),
                    'letters': letters,
                    'numbers': numbers,
                    'type': ptype,
                    'is_short_numeric': False
                }
        
        # 3. 通用提取（必须同时有字母和数字）
        letters = ''.join(re.findall(r'[a-zA-Z]+', text)).lower()
        numbers = ''.join(re.findall(r'\d+', text))
        if letters and numbers:
            full_code = letters + numbers
            # 取第一个单词作为原始文本
            original = text.split()[0] if text else text
            return {
                'full': full_code,
                'original': original,
                'canonical': original.replace('_', '-'),
                'letters': letters,
                'numbers': numbers,
                'type': 'generic',
                'is_short_numeric': False
            }
        
        # 4. 纯数字短文件夹（长度<=4）
        if numbers and not letters and len(numbers) <= 4:
            return {
                'full': numbers,
                'original': text,
                'canonical': text.replace('_', '-'),
                'letters': '',
                'numbers': numbers,
                'type': 'short_numeric',
                'is_short_numeric': True
            }
        
        return None
    
    @staticmethod
    def normalize_number(number: str, target_length: int = 3) -> str:
        """规范化数字，处理前导零"""
        stripped = number.lstrip('0')
        if not stripped:
            return '0' * target_length
        if len(stripped) <= target_length:
            return stripped.zfill(target_length)
        return stripped[-target_length:]
    
    def is_strict_match(self, file_code, folder_code) -> int:
        """
        严格匹配检查
        返回：0=不匹配，1=弱匹配（字母和数字分别包含），2=强匹配（完全相等或包含）
        """
        if not file_code or not folder_code:
            return 0
        
        # 短数字特殊处理：必须完全相等
        if file_code.get('is_short_numeric') or folder_code.get('is_short_numeric'):
            return 2 if file_code['full'] == folder_code['full'] else 0
        
        file_full = file_code['full']
        folder_full = folder_code['full']
        
        if file_full == folder_full:
            return 2
        
        if folder_full in file_full or file_full in folder_full:
            return 2
        
        file_letters = file_code['letters']
        folder_letters = folder_code['letters']
        file_numbers = file_code['numbers']
        folder_numbers = folder_code['numbers']
        
        letters_match = (folder_letters in file_letters) or (file_letters in folder_letters)
        
        numbers_match = False
        if file_numbers and folder_numbers:
            file_num_stripped = file_numbers.lstrip('0') or '0'
            folder_num_stripped = folder_numbers.lstrip('0') or '0'
            
            if file_num_stripped == folder_num_stripped:
                numbers_match = True
            elif folder_num_stripped in file_num_stripped:
                numbers_match = True
            elif file_num_stripped in folder_num_stripped:
                numbers_match = True
            elif file_numbers.endswith(folder_numbers) or folder_numbers.endswith(file_numbers):
                numbers_match = True
        
        if letters_match and numbers_match:
            return 1
        
        return 0


# 全局单例，方便调用
_pattern_matcher = None

def get_pattern_matcher():
    global _pattern_matcher
    if _pattern_matcher is None:
        _pattern_matcher = PatternMatcher()
    return _pattern_matcher

def extract_code_from_text(text):
    """便捷函数：从文本中提取番号信息（返回字典或None）"""
    matcher = get_pattern_matcher()
    return matcher.extract_code(text)

# core.py - 修改 DebugUtils 类

class DebugUtils:
    """调试工具 - 支持GUI日志显示"""
    
    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode
        self.log_callback = None  # GUI日志回调函数
        self.log_buffer = []  # 日志缓冲区
        self.max_buffer_size = 5000
        self._history_sent = False  # 新增：标记历史日志是否已发送
        
    def set_debug_mode(self, mode):
        self.debug_mode = bool(mode)
        if self.debug_mode:
            self.print("调试模式已开启")
        else:
            self.print("调试模式已关闭")
    
    def set_log_callback(self, callback):
        """设置日志回调 - 修复重复问题"""
        self.log_callback = callback
        
        # 只在第一次设置回调时发送历史日志
        if not self._history_sent and self.log_buffer:
            for msg in self.log_buffer:
                if callback:
                    callback(msg)
            self._history_sent = True
    
    def print(self, *args, **kwargs):
        """打印调试信息"""
        if not self.debug_mode:
            return
        
        import time
        timestamp = time.strftime("%H:%M:%S")
        msg = " ".join(str(arg) for arg in args)
        formatted_msg = f"[{timestamp}] {msg}"
        
        # 输出到终端
        print(formatted_msg)
        
        # 输出到GUI日志（只调用回调，不重复添加到缓冲区）
        if self.log_callback:
            try:
                self.log_callback(formatted_msg)
            except Exception as e:
                print(f"日志回调失败: {e}")
        
        # 保存到缓冲区（仅在回调设置前保存，避免重复）
        # 如果已经发送过历史日志，就不再添加到缓冲区
        if not self._history_sent:
            self.log_buffer.append(formatted_msg)
            if len(self.log_buffer) > self.max_buffer_size:
                self.log_buffer = self.log_buffer[-self.max_buffer_size:]
    
    def clear_buffer(self):
        """清空日志缓冲区"""
        self.log_buffer.clear()
        self._history_sent = False
    
    def get_log_buffer(self):
        """获取日志缓冲区"""
        return self.log_buffer.copy()
    
    def export_logs(self, filepath):
        """导出日志到文件"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"调试日志导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")
                for log in self.log_buffer:
                    f.write(log + "\n")
            return True, "导出成功"
        except Exception as e:
            return False, str(e)

class ImageViewer:
    """图片查看器类 - 管理原图查看窗口，支持多图切换浏览"""
    
    def __init__(self, config_manager, debug_utils=None):
        self.config = config_manager
        self.debug = debug_utils
        self._current_viewer = None
    
    def view(self, img_path, parent=None, image_list=None):
        """查看原图
        
        Args:
            img_path: 当前显示的图片路径
            parent: 父窗口
            image_list: 可选，图片路径列表（用于多图切换浏览）
        """
        if not img_path or not os.path.exists(img_path):
            messagebox.showerror("错误", "图片文件不存在")
            return
        
        # 检查是否为PDF
        ext = os.path.splitext(img_path)[1].lower()
        if ext == '.pdf':
            self._open_external(img_path)
            return
        
        # 创建查看窗口
        viewer = tk.Toplevel(parent) if parent else tk.Tk()
        viewer.title(f"查看原图 - {os.path.basename(img_path)}")
        
        # 设置窗口大小和位置
        if self.config.viewer_geometry:
            viewer.geometry(self.config.viewer_geometry)
        else:
            viewer.geometry("800x600+100+100")
        
        # 处理图片列表
        if image_list and len(image_list) > 0:
            # 过滤掉非图片文件和PDF
            valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif')
            viewer.all_images = [p for p in image_list if p.lower().endswith(valid_extensions)]
            # 找到当前图片的索引
            if img_path in viewer.all_images:
                viewer.current_index = viewer.all_images.index(img_path)
            else:
                viewer.all_images.insert(0, img_path)
                viewer.current_index = 0
        else:
            viewer.all_images = [img_path]
            viewer.current_index = 0
        
        # 创建主框架
        main_frame = ttk.Frame(viewer)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建Canvas
        canvas = tk.Canvas(main_frame, bg="#2b2b2b", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        
        # 如果有多个图片，添加底部控制栏
        has_multiple = len(viewer.all_images) > 1
        if has_multiple:
            control_frame = ttk.Frame(viewer, padding=5)
            control_frame.pack(fill=tk.X, side=tk.BOTTOM)
            
            # 左切换按钮
            left_btn = ttk.Button(control_frame, text="◀ 上一张", 
                                 command=lambda: self._switch_image(viewer, -1))
            left_btn.pack(side=tk.LEFT, padx=5)
            
            # 图片计数标签
            viewer.count_label = ttk.Label(control_frame, 
                                          text=f"{viewer.current_index + 1} / {len(viewer.all_images)}",
                                          font=("微软雅黑", 10))
            viewer.count_label.pack(side=tk.LEFT, padx=20)
            
            # 右切换按钮
            right_btn = ttk.Button(control_frame, text="下一张 ▶", 
                                  command=lambda: self._switch_image(viewer, 1))
            right_btn.pack(side=tk.LEFT, padx=5)
            
            # 图片名称标签
            viewer.name_label = ttk.Label(control_frame, 
                                         text=os.path.basename(viewer.all_images[viewer.current_index]),
                                         font=("微软雅黑", 9), foreground="gray")
            viewer.name_label.pack(side=tk.RIGHT, padx=10)
            
            # 绑定键盘左右键
            viewer.bind("<Left>", lambda e: self._switch_image(viewer, -1))
            viewer.bind("<Right>", lambda e: self._switch_image(viewer, 1))
        
        # 加载并显示当前图片
        self._load_and_show_image(viewer, canvas, viewer.all_images[viewer.current_index])
        
        # 绑定滚轮缩放
        canvas.bind("<MouseWheel>", lambda e, v=viewer: self._on_zoom(e, v))
        canvas.bind("<Button-4>", lambda e, v=viewer: self._on_zoom(e, v))
        canvas.bind("<Button-5>", lambda e, v=viewer: self._on_zoom(e, v))
        
        # 拖拽移动
        drag_data = {"x": 0, "y": 0}
        
        def on_drag_start(event):
            drag_data["x"] = event.x
            drag_data["y"] = event.y
        
        def on_drag_move(event):
            dx = event.x - drag_data["x"]
            dy = event.y - drag_data["y"]
            event.widget.move("all", dx, dy)
            drag_data["x"] = event.x
            drag_data["y"] = event.y
        
        canvas.bind("<ButtonPress-1>", on_drag_start)
        canvas.bind("<B1-Motion>", on_drag_move)
        
        # 双击关闭
        canvas.bind("<Double-Button-1>", lambda e, v=viewer: v.destroy())
        
        # 关闭处理
        def on_close():
            self.config.viewer_geometry = viewer.geometry()
            self.config.save_config()
            viewer.destroy()
        
        viewer.protocol("WM_DELETE_WINDOW", on_close)
        
        # 保存位置（防抖）
        def on_move(event):
            if event.widget == viewer:
                if hasattr(viewer, '_move_timer'):
                    viewer.after_cancel(viewer._move_timer)
                viewer._move_timer = viewer.after(
                    100,
                    lambda: self._save_position(viewer)
                )
        
        viewer.bind("<Configure>", on_move)
        
        self._current_viewer = viewer
    
    def _load_and_show_image(self, viewer, canvas, img_path):
        """加载并显示指定图片 - 支持自适应缩放和居中显示"""
        try:
            # 加载图片
            original_img = Image.open(img_path)
            
            if original_img.mode == 'RGBA':
                bg = Image.new('RGB', original_img.size, (43, 43, 43))
                bg.paste(original_img, mask=original_img.split()[3])
                original_img = bg
            elif original_img.mode != 'RGB':
                original_img = original_img.convert('RGB')
            
            # 存储引用
            viewer.original_img = original_img
            viewer.canvas = canvas
            viewer.current_img_path = img_path
            
            # 计算图片原始尺寸
            img_w, img_h = original_img.size
            
            # 定义加载和显示函数（内部函数）
            def do_load_and_display():
                # 获取Canvas当前尺寸
                canvas.update_idletasks()
                canvas_w = canvas.winfo_width()
                canvas_h = canvas.winfo_height()
                
                # 如果Canvas尺寸无效，使用窗口尺寸估算
                if canvas_w <= 1 or canvas_h <= 1:
                    viewer.update_idletasks()
                    # 减去边距和滚动条等
                    canvas_w = max(viewer.winfo_width() - 40, 400)
                    canvas_h = max(viewer.winfo_height() - 150, 300)
                
                # 计算自适应缩放比例（保持宽高比，适应窗口）
                margin = 40  # 增加边距确保居中效果更明显
                max_w = max(canvas_w - margin * 2, 100)
                max_h = max(canvas_h - margin * 2, 100)
                
                # 计算缩放比例
                scale_w = max_w / img_w if img_w > max_w else 1.0
                scale_h = max_h / img_h if img_h > max_h else 1.0
                scale = min(scale_w, scale_h, 1.0)  # 最大1.0（不放大）
                
                viewer.zoom_level = scale
                
                # 更新显示（居中）
                self._update_image(viewer, canvas, original_img, viewer.zoom_level, center=True)
                
                # 更新窗口标题
                viewer.title(f"查看原图 - {os.path.basename(img_path)}")
                
                # 更新计数和名称标签
                if hasattr(viewer, 'count_label'):
                    viewer.count_label.config(
                        text=f"{viewer.current_index + 1} / {len(viewer.all_images)}"
                    )
                if hasattr(viewer, 'name_label'):
                    viewer.name_label.config(text=os.path.basename(img_path))
            
            # 关键：延迟执行以确保窗口布局完成，实现真正的居中
            viewer.after(100, do_load_and_display)
                
        except Exception as e:
            messagebox.showerror("错误", f"无法打开图片: {e}")
            if self.debug:
                self.debug.log_exception("打开图片失败")

    def _switch_image(self, viewer, direction):
        """切换图片"""
        if not viewer.winfo_exists():
            return
        
        new_index = viewer.current_index + direction
        
        # 循环切换
        if new_index < 0:
            new_index = len(viewer.all_images) - 1
        elif new_index >= len(viewer.all_images):
            new_index = 0
        
        viewer.current_index = new_index
        img_path = viewer.all_images[new_index]
        
        # 加载新图片（会自动居中）
        self._load_and_show_image(viewer, viewer.canvas, img_path)
    
    def _update_image(self, viewer, canvas, img, zoom, center=True):
        """更新查看器中的图片 - 支持居中显示"""
        w, h = img.size
        new_w = int(w * zoom)
        new_h = int(h * zoom)
        
        # 限制最大尺寸（防止内存溢出）
        max_size = 4000
        if new_w > max_size or new_h > max_size:
            ratio = min(max_size / new_w, max_size / new_h)
            new_w = int(new_w * ratio)
            new_h = int(new_h * ratio)
        
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        tk_img = ImageTk.PhotoImage(resized)
        
        canvas.delete("all")
        canvas.image = tk_img  # 保持引用
        
        # 居中显示计算
        canvas.update_idletasks()
        canvas_w = canvas.winfo_width()
        canvas_h = canvas.winfo_height()
        
        if center:
            # 计算居中位置
            x = max((canvas_w - new_w) // 2, 0)
            y = max((canvas_h - new_h) // 2, 0)
        else:
            x = 0
            y = 0
        
        canvas.create_image(x, y, anchor=tk.NW, image=tk_img)
        
        # 更新信息标签
        if hasattr(viewer, 'info_label') and viewer.info_label.winfo_exists():
            viewer.info_label.config(
                text=f"尺寸: {w}x{h} | 缩放: {int(zoom * 100)}% | 滚轮缩放 拖拽移动 双击退出"
            )

    def _on_zoom(self, event, viewer):
        """处理缩放"""
        if not viewer.winfo_exists() or not hasattr(viewer, 'canvas'):
            return
        
        if not viewer.canvas.winfo_exists():
            return
        
        if event.num == 5 or event.delta < 0:
            viewer.zoom_level *= 0.9
        elif event.num == 4 or event.delta > 0:
            viewer.zoom_level *= 1.1
        
        viewer.zoom_level = max(0.1, min(viewer.zoom_level, 5.0))
        # 缩放时保持居中
        self._update_image(
            viewer,
            viewer.canvas,
            viewer.original_img,
            viewer.zoom_level,
            center=True
        )
    
    def _save_position(self, viewer):
        """保存窗口位置"""
        if viewer.winfo_exists():
            self.config.viewer_geometry = viewer.geometry()
    
    def _open_external(self, file_path):
        """使用系统默认程序打开文件"""
        try:
            if os.name == 'nt':
                os.startfile(file_path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', file_path])
            else:
                subprocess.Popen(['xdg-open', file_path])
        except Exception as e:
            messagebox.showerror("错误", f"无法打开文件: {e}")

    def reset_position(self):
        """重置查看窗口位置"""
        self.config.viewer_geometry = None
        self.config.save_config()
        messagebox.showinfo("完成", "查看窗口位置已重置")


class VideoPlayer:
    """视频播放器"""
    def __init__(self, config_manager, debug_utils=None):
        self.config = config_manager
        self.debug = debug_utils
    
    def find_videos_in_folder(self, folder_path):
        return PathUtils.find_video_files(folder_path)
    
    def find_same_name_video(self, image_path):
        folder = os.path.dirname(image_path)
        img_basename = os.path.splitext(os.path.basename(image_path))[0]
        for video_path in self.find_videos_in_folder(folder):
            video_basename = os.path.splitext(os.path.basename(video_path))[0]
            if video_basename.lower() == img_basename.lower():
                return video_path
        return None
    
    def get_player_path(self):
        player = self.config.player_path
        if player and os.path.exists(player):
            return player
        
        possible_paths = [
            r"C:\Program Files\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files\PotPlayer\PotPlayerMini.exe",
            r"C:\Program Files (x86)\PotPlayer\PotPlayerMini.exe",
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe"
        ]
        for p in possible_paths:
            if os.path.exists(p):
                self.config.player_path = p
                self.config.save_config()
                return p
        return None
    
    def play_video(self, video_path):
        player = self.get_player_path()
        if not player:
            return False, "未找到播放器，请在设置中配置"
        if not os.path.exists(video_path):
            return False, f"视频文件不存在: {video_path}"
        
        try:
            cmd = [player, video_path]
            if os.name == 'nt':
                subprocess.Popen(cmd, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(cmd, shell=False)
            return True, f"正在播放: {os.path.basename(video_path)}"
        except Exception as e:
            return False, str(e)
    
    def play_videos(self, video_files):
        if not video_files:
            return False, "没有视频文件"
        if len(video_files) == 1:
            return self.play_video(video_files[0])
        
        player = self.get_player_path()
        if not player:
            return False, "未找到播放器"
        
        try:
            folder = os.path.dirname(video_files[0])
            playlist_path = os.path.join(folder, "temp_playlist.m3u")
            with open(playlist_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for video in video_files:
                    f.write(f"{video}\n")
            
            cmd = [player, playlist_path]
            if os.name == 'nt':
                subprocess.Popen(cmd, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(cmd, shell=False)
            
            import threading, time
            def delete_later():
                time.sleep(10)
                try: os.remove(playlist_path)
                except: pass
            threading.Thread(target=delete_later, daemon=True).start()
            
            return True, f"正在播放 {len(video_files)} 个视频"
        except Exception as e:
            return False, str(e)
    
    def play_for_image(self, image_path, prefer_same_name=True):
        folder = os.path.dirname(image_path)
        video_files = self.find_videos_in_folder(folder)
        if not video_files:
            return False, "该文件夹下未找到视频文件"
        
        if prefer_same_name:
            same_name = self.find_same_name_video(image_path)
            if same_name:
                return self.play_video(same_name)
        return self.play_videos(video_files)
    
    def set_player_path(self, path):
        self.config.player_path = path
        self.config.save_config()
    
    def check_player(self, silent=False):
        player = self.get_player_path()
        if player and os.path.exists(player):
            if not silent:
                messagebox.showinfo("检查完成", f"播放器已就绪:\n{player}")
            return True
        if not silent:
            messagebox.showwarning("未找到", "未找到PotPlayer，请手动设置路径")
        return False
    
# 在文件顶部导入部分确保有以下导入（如果没有请添加）：
# import hashlib
# import time

class FolderCacheManager:
    """文件夹内容缓存管理器 - 用于快速恢复浏览状态"""
    
    def __init__(self, debug_utils=None, cache_dir=None):
        self.debug = debug_utils
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "folder_cache"
        )
        self._ensure_cache_dir()
    
    def _ensure_cache_dir(self):
        """确保缓存目录存在 - 强化错误处理"""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            # 测试写入权限
            test_file = os.path.join(self.cache_dir, ".write_test")
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            if self.debug:
                self.debug.print(f"✓ 缓存目录就绪: {self.cache_dir}")
        except Exception as e:
            if self.debug:
                self.debug.print(f"✗ 缓存目录错误: {e}")
    
    def _get_cache_file_path(self, folder_path):
        """根据文件夹路径生成缓存文件路径"""
        # 使用路径的MD5作为文件名，避免特殊字符问题
        folder_hash = hashlib.md5(folder_path.encode('utf-8')).hexdigest()
        return os.path.join(self.cache_dir, f"{folder_hash}.json")
    
    def clear_all_cache(self):
        """清空所有缓存（程序启动时调用）"""
        try:
            if os.path.exists(self.cache_dir):
                count = 0
                for filename in os.listdir(self.cache_dir):
                    if filename.endswith('.json'):
                        filepath = os.path.join(self.cache_dir, filename)
                        try:
                            os.remove(filepath)
                            count += 1
                        except:
                            pass
                if self.debug:
                    self.debug.print(f"已清空缓存文件: {count} 个")
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"清空缓存失败: {e}")
            return False
    
    def save_folder_cache(self, folder_path, sub_folders=None, files_info=None, tree_state=None):
        """保存文件夹缓存 - 修复：使用原子写入防止缓存损坏，并强化去重"""
        try:
            if not folder_path or not os.path.exists(folder_path):
                return False
            
            # 使用规范化路径进行去重，避免大小写或斜杠差异导致重复
            seen_paths = set()
            unique_files = []
            for f in (files_info or []):
                fp = f.get('file_path')
                if fp:
                    # 规范化路径（统一大小写和斜杠方向）
                    normalized_fp = os.path.normpath(fp).lower().replace('\\', '/')
                    if normalized_fp not in seen_paths:
                        seen_paths.add(normalized_fp)
                        unique_files.append(f)
            
            cache_data = {
                'folder_path': folder_path,
                'cache_time': time.time(),
                'folder_mtime': os.path.getmtime(folder_path),
                'sub_folders': sub_folders or [],
                'files_info': unique_files,
                'tree_state': tree_state or {}
            }
            
            cache_file = self._get_cache_file_path(folder_path)
                        
            # 确保缓存目录存在
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            
            # 创建临时文件（在同一目录以确保重命名是原子的）
            fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(cache_file),
                prefix='.cache_tmp_',
                suffix='.json'
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                
                # 原子重命名
                os.replace(temp_path, cache_file)
                
                if self.debug:
                    orig_count = len(files_info) if files_info else 0
                    dedup_count = orig_count - len(unique_files)
                    self.debug.print(f"已缓存文件夹: {os.path.basename(folder_path)} ({len(unique_files)} 个文件, 去重{dedup_count}个)")
                return True
                
            except Exception as e:
                # 清理临时文件
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except:
                    pass
                raise e
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"保存缓存失败 {folder_path}: {e}")
            return False

    def clear_cache_for_folder(self, folder_path):
        """清除指定文件夹的缓存"""
        import hashlib
        cache_key = hashlib.md5(folder_path.encode('utf-8')).hexdigest()
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        if os.path.exists(cache_file):
            try:
                os.remove(cache_file)
                if self.debug:
                    self.debug.print(f"已清除缓存: {cache_file}")
                return True
            except Exception as e:
                if self.debug:
                    self.debug.print(f"清除缓存失败: {e}")
        return False

    def load_folder_cache(self, folder_path, max_age=3600):
        """加载文件夹缓存，返回缓存数据或None"""
        try:
            cache_file = self._get_cache_file_path(folder_path)
            if not os.path.exists(cache_file):
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证缓存是否有效（文件夹未修改且缓存未过期）
            if not os.path.exists(folder_path):
                return None
            
            current_mtime = os.path.getmtime(folder_path)
            cached_mtime = cache_data.get('folder_mtime', 0)
            cache_time = cache_data.get('cache_time', 0)
            
            # 检查文件夹是否被修改过
            if current_mtime != cached_mtime:
                if self.debug:
                    self.debug.print(f"缓存过期(文件夹已修改): {folder_path}")
                return None
            
            # 检查缓存时间（默认1小时）
            if time.time() - cache_time > max_age:
                if self.debug:
                    self.debug.print(f"缓存过期(超时): {folder_path}")
                return None
            
            if self.debug:
                files_count = len(cache_data.get('files_info', []))
                self.debug.print(f"命中缓存: {folder_path} ({files_count} 个文件)")
            
            return cache_data
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"加载缓存失败 {folder_path}: {e}")
            return None
    
    def is_cache_valid(self, folder_path):
        """检查缓存是否有效（不加载内容）"""
        try:
            cache_file = self._get_cache_file_path(folder_path)
            if not os.path.exists(cache_file):
                return False
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            if not os.path.exists(folder_path):
                return False
            
            current_mtime = os.path.getmtime(folder_path)
            cached_mtime = cache_data.get('folder_mtime', 0)
            cache_time = cache_data.get('cache_time', 0)
            
            # 检查文件夹是否被修改或缓存是否过期（1小时）
            if current_mtime != cached_mtime or (time.time() - cache_time > 3600):
                return False
            
            return True
            
        except:
            return False