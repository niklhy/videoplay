#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爬虫配置GUI - 支持自定义Python代码执行
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import sys
import threading
import re
import urllib.parse
from typing import List, Dict, Optional, Callable, Tuple, Any
from bs4 import BeautifulSoup

try:
    from crawler_core import CrawlerConfig, WebCrawler, CrawlResult, ExtractRule
except ImportError:
    print("错误: 无法导入 crawler_core.py，请确保文件在同目录下")
    sys.exit(1)

import json

# 检查依赖
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_GUI_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_GUI_AVAILABLE = False

try:
    from selenium import webdriver
    SELENIUM_GUI_AVAILABLE = True
except ImportError:
    SELENIUM_GUI_AVAILABLE = False


class SharedCrawlerConfig:
    """共享配置类"""

    def __init__(self, config_file: str):
        self.config_file = config_file
        self._core_config = CrawlerConfig(None)
        self._load_from_shared_config()

    def _load_from_shared_config(self):
        """从共享的 config.json 加载爬虫配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'crawler' in data:
                        self._core_config._config.update(data['crawler'])
        except Exception as e:
            print(f"加载共享配置失败: {e}")

    def _save_to_shared_config(self) -> bool:
        """保存到共享的 config.json"""
        try:
            data = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

            data['crawler'] = self._core_config.to_dict()

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"保存共享配置失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get(self, key, default=None):
        return self._core_config.get(key, default)

    def set(self, key, value):
        self._core_config.set(key, value)

    @property
    def urls(self):
        return self._core_config.urls
    
    @property
    def extract_rules(self):
        return self._core_config.extract_rules
    
    @extract_rules.setter
    def extract_rules(self, rules):
        self._core_config.extract_rules = rules

    def save_config(self) -> bool:
        return self._save_to_shared_config()


class CrawlerConfigApp:
    """爬虫配置应用程序"""
    
    # 基础字体配置（嵌入主程序时使用 - 默认大小）
    FONTS_EMBEDDED = {
        'default': ("微软雅黑", 10),
        'small': ("微软雅黑", 9),
        'large': ("微软雅黑", 11),
        'bold': ("微软雅黑", 10, "bold"),
        'code': ("Consolas", 10),
        'code_large': ("Consolas", 12),
        'code_small': ("Consolas", 9),
        'hint': ("微软雅黑", 8),
        'title': ("微软雅黑", 11, "bold"),
    }
    
    # 独立运行时的字体配置（放大版本）
    FONTS_STANDALONE = {
        'default': ("微软雅黑", 13),
        'small': ("微软雅黑", 11),
        'large': ("微软雅黑", 15),
        'bold': ("微软雅黑", 13, "bold"),
        'code': ("Consolas", 13),
        'code_large': ("Consolas", 15),
        'code_small': ("Consolas", 11),
        'hint': ("微软雅黑", 10),
        'title': ("微软雅黑", 14, "bold"),
    }

    # 自定义代码模板示例
    CUSTOM_CODE_TEMPLATE = '''# 可用变量：
# - html: 原始HTML字符串
# - soup: BeautifulSoup对象（已初始化）
# - re: 正则表达式模块

# 示例1：提取标题
# result = soup.select_one('h1').get_text(strip=True)

# 示例2：提取所有演员（返回列表）
# result = [a.get_text(strip=True) for a in soup.select('span.genre a')]

# 示例3：正则提取日期
# match = re.search(r'(\\d{4}-\\d{2}-\\d{2})', html)
# result = match.group(1) if match else ""

# 示例4：复杂逻辑
elements = soup.select('.product-details li')
result = []
for elem in elements:
    text = elem.get_text(strip=True)
    if '演员' in text:
        result.append(text.replace('演员：', ''))

# 必须将结果赋值给 result 变量
'''

    def __init__(self, root: tk.Tk, config_file: str = None, is_standalone: bool = True):
        self.root = root
        self.is_standalone = is_standalone
        
        # 根据运行模式选择字体配置
        if self.is_standalone:
            self.FONTS = self.FONTS_STANDALONE.copy()
            # 独立运行时窗口更大
            self.root.title("视频爬虫配置工具 - 支持自定义Python代码")
            self.root.geometry("1900x1200")
            self.parent = self.root
        else:
            self.FONTS = self.FONTS_EMBEDDED.copy()
            # 嵌入主程序时窗口适中
            self.parent = self.root

        if config_file:
            self.config_file = config_file
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.config_file = os.path.join(script_dir, "config.json")

        self.config = SharedCrawlerConfig(self.config_file)
        self.crawler = WebCrawler(self.config._core_config)
        self.crawler.set_log_callback(self._log)

        self._current_html = ""
        self._test_results = {}
        self._rule_frames = []

        self._create_ui()
        self._load_config_to_ui()

        self._log(f"配置文件: {self.config_file}")
        mode_text = "独立运行模式（字体已放大）" if is_standalone else "嵌入模式（默认字体）"
        self._log(f"支持自定义Python代码的爬虫配置工具已启动 - {mode_text}")

    def _get_tk_root(self):
        """获取Tk根窗口"""
        if self.is_standalone:
            return self.root
        current = self.parent
        while current and not isinstance(current, (tk.Tk, tk.Toplevel)):
            current = current.master
        return current

    def _create_ui(self):
        """创建双栏布局UI"""
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧面板
        left_container = ttk.Frame(main_frame)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_container.configure(width=750)

        left_canvas = tk.Canvas(left_container, width=730)
        scrollbar = ttk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
        self.left_frame = ttk.Frame(left_canvas)

        self.left_frame.bind(
            "<Configure>",
            lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        )

        left_canvas.create_window((0, 0), window=self.left_frame, anchor="nw", width=730)
        left_canvas.configure(yscrollcommand=scrollbar.set)

        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def on_mousewheel(event):
            left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.root.bind_all("<MouseWheel>", on_mousewheel)

        self._create_url_section(self.left_frame)
        self._create_page_settings_section(self.left_frame)
        self._create_anti_spider_section(self.left_frame)
        self._create_headers_section(self.left_frame)
        self._create_dynamic_rules_section(self.left_frame)
        self._create_log_section(self.left_frame)

        # 右侧面板
        right_frame = ttk.LabelFrame(main_frame, text="网页源码分析", padding=10)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._create_html_panel(right_frame)

    def _create_url_section(self, parent):
        """网址库配置"""
        url_frame = ttk.LabelFrame(parent, text="📍 网址库配置 (使用{番号}作为替换符)", padding=5)
        url_frame.pack(fill=tk.X, pady=5, padx=5)

        self.url_text = scrolledtext.ScrolledText(url_frame, height=4, wrap=tk.WORD, font=self.FONTS['code'])
        self.url_text.pack(fill=tk.X)

        url_tool_frame = ttk.Frame(url_frame)
        url_tool_frame.pack(fill=tk.X, pady=5)

        ttk.Label(url_tool_frame, text="替换符:", font=self.FONTS['default']).pack(side=tk.LEFT, padx=2)
        self.placeholder_entry = ttk.Entry(url_tool_frame, width=10, font=self.FONTS['code'])
        self.placeholder_entry.pack(side=tk.LEFT, padx=2)
        self.placeholder_entry.insert(0, "{番号}")

        ttk.Label(url_tool_frame, text="模式:", font=self.FONTS['default']).pack(side=tk.LEFT, padx=(10, 2))
        self.crawl_mode_var = tk.StringVar(value="round_robin")
        tk.Radiobutton(url_tool_frame, text="单网址", variable=self.crawl_mode_var, 
                      value="single", font=self.FONTS['small'], bg="#f0f0f0").pack(side=tk.LEFT)
        tk.Radiobutton(url_tool_frame, text="轮流", variable=self.crawl_mode_var, 
                      value="round_robin", font=self.FONTS['small'], bg="#f0f0f0").pack(side=tk.LEFT)

        btn_frame = ttk.Frame(url_frame)
        btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="测试连接", command=self._test_urls, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="清除失效", command=self._remove_failed_urls, width=10).pack(side=tk.LEFT, padx=2)

        self.url_status_label = ttk.Label(btn_frame, text="就绪", foreground="gray", font=self.FONTS['small'])
        self.url_status_label.pack(side=tk.LEFT, padx=5)

    def _create_page_settings_section(self, parent):
        """页面解析设置"""
        settings_frame = ttk.LabelFrame(parent, text="🔍 页面解析设置", padding=5)
        settings_frame.pack(fill=tk.X, pady=5, padx=5)

        ttk.Label(settings_frame, text="全局CSS选择器:", font=self.FONTS['default']).grid(row=0, column=0, sticky='w', padx=5, pady=3)
        self.css_selector_entry = ttk.Entry(settings_frame, width=40, font=self.FONTS['code'])
        self.css_selector_entry.grid(row=0, column=1, sticky='ew', padx=5, pady=3)
        self.css_selector_entry.insert(0, "body")
        ttk.Label(settings_frame, text="(用于初步过滤HTML内容)", font=self.FONTS['hint']).grid(row=0, column=2, sticky='w', padx=5)

        ttk.Label(settings_frame, text="编码:", font=self.FONTS['default']).grid(row=1, column=0, sticky='w', padx=5, pady=3)
        self.encoding_var = tk.StringVar(value="auto")
        ttk.Combobox(settings_frame, textvariable=self.encoding_var, 
                    values=["auto", "utf-8", "gb2312", "gbk", "big5"], width=15).grid(row=1, column=1, sticky='w', padx=5, pady=3)

        settings_frame.columnconfigure(1, weight=1)

    def _create_anti_spider_section(self, parent):
        """反爬策略设置"""
        anti_frame = ttk.LabelFrame(parent, text="🛡️ 反爬策略与渲染模式", padding=5)
        anti_frame.pack(fill=tk.X, pady=5, padx=5)

        render_frame = ttk.Frame(anti_frame)
        render_frame.pack(fill=tk.X, pady=3)
        
        self.render_js_var = tk.BooleanVar(value=False)
        tk.Checkbutton(render_frame, text="使用浏览器渲染(JavaScript)", variable=self.render_js_var,
                      font=self.FONTS['default'], bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        ttk.Label(render_frame, text="引擎:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=(15, 2))
        self.render_engine_var = tk.StringVar(value="playwright")
        ttk.Combobox(render_frame, textvariable=self.render_engine_var, 
                    values=["playwright", "selenium"], width=12, state="readonly").pack(side=tk.LEFT, padx=2)
        
        ttk.Label(render_frame, text="浏览器:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=(20, 2))
        self.browser_type_var = tk.StringVar(value="chromium")
        ttk.Combobox(render_frame, textvariable=self.browser_type_var, 
                    values=["chromium", "firefox", "webkit"], width=10, state="readonly").pack(side=tk.LEFT, padx=2)

        static_frame = ttk.Frame(anti_frame)
        static_frame.pack(fill=tk.X, pady=3)

        ttk.Label(static_frame, text="间隔(ms):", font=self.FONTS['default']).pack(side=tk.LEFT, padx=5)
        self.interval_min_entry = ttk.Entry(static_frame, width=8, font=self.FONTS['code'])
        self.interval_min_entry.pack(side=tk.LEFT)
        self.interval_min_entry.insert(0, "1000")
        ttk.Label(static_frame, text="~").pack(side=tk.LEFT)
        self.interval_max_entry = ttk.Entry(static_frame, width=8, font=self.FONTS['code'])
        self.interval_max_entry.pack(side=tk.LEFT)
        self.interval_max_entry.insert(0, "3000")

        ttk.Label(static_frame, text="超时:", font=self.FONTS['default']).pack(side=tk.LEFT, padx=(20, 2))
        self.timeout_entry = ttk.Entry(static_frame, width=6, font=self.FONTS['code'])
        self.timeout_entry.pack(side=tk.LEFT, padx=2)
        self.timeout_entry.insert(0, "10")

        ttk.Label(static_frame, text="重试:", font=self.FONTS['default']).pack(side=tk.LEFT, padx=(10, 2))
        self.retry_entry = ttk.Entry(static_frame, width=6, font=self.FONTS['code'])
        self.retry_entry.pack(side=tk.LEFT, padx=2)
        self.retry_entry.insert(0, "3")

    def _create_headers_section(self, parent):
        """HTTP请求头配置"""
        headers_frame = ttk.LabelFrame(parent, text="🔧 HTTP请求头配置", padding=5)
        headers_frame.pack(fill=tk.X, pady=5, padx=5)

        self.headers_text = scrolledtext.ScrolledText(headers_frame, height=5, wrap=tk.NONE, font=self.FONTS['code'])
        self.headers_text.pack(fill=tk.X, padx=5, pady=2)
        
        btn_frame = ttk.Frame(headers_frame)
        btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="重置默认", command=self._reset_headers_default, width=10).pack(side=tk.LEFT, padx=2)

    def _create_dynamic_rules_section(self, parent):
        """动态抓取规则配置区域"""
        rules_container = ttk.LabelFrame(parent, text="📝 动态抓取规则配置（支持自定义Python代码）", padding=5)
        rules_container.pack(fill=tk.X, pady=5, padx=5)

        # 说明文字
        hint_text = """规则类型说明：
• snippet: HTML片段+占位符提取  • css: CSS选择器提取  • regex: 正则表达式提取  
• custom: 自定义Python代码（可直接编写BeautifulSoup代码，将结果赋值给 result 变量）"""
        ttk.Label(rules_container, text=hint_text, justify=tk.LEFT, 
                 font=self.FONTS['hint'], foreground="blue").pack(anchor='w', pady=5)

        # 规则列表容器（带滚动）
        rules_canvas_container = ttk.Frame(rules_container)
        rules_canvas_container.pack(fill=tk.X, pady=5)

        rules_canvas = tk.Canvas(rules_canvas_container, height=400)
        rules_scrollbar = ttk.Scrollbar(rules_canvas_container, orient="vertical", command=rules_canvas.yview)
        self.rules_list_frame = ttk.Frame(rules_canvas)

        self.rules_list_frame.bind(
            "<Configure>",
            lambda e: rules_canvas.configure(scrollregion=rules_canvas.bbox("all"))
        )

        rules_canvas.create_window((0, 0), window=self.rules_list_frame, anchor="nw", width=700)
        rules_canvas.configure(yscrollcommand=rules_scrollbar.set)

        rules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rules_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 按钮区域
        btn_frame = ttk.Frame(rules_container)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="➕ 添加字段", command=self._add_new_rule, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔄 重置示例", command=self._load_default_rules, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🗑️ 清空所有", command=self._clear_all_rules, width=12).pack(side=tk.LEFT, padx=2)

        # 解析结果预览
        result_frame = ttk.LabelFrame(rules_container, text="解析结果预览", padding=5)
        result_frame.pack(fill=tk.X, pady=5)

        self.result_text = scrolledtext.ScrolledText(result_frame, height=10, wrap=tk.WORD, font=self.FONTS['default'])
        self.result_text.pack(fill=tk.X)

    def _create_rule_ui(self, parent, rule: ExtractRule, index: int):
        """创建单个规则的UI"""
        rule_frame = ttk.LabelFrame(parent, text=f"规则 {index+1}: {rule.field_name}", padding=5)
        rule_frame.pack(fill=tk.X, pady=5, padx=5)
        
        widgets = {'frame': rule_frame}

        # 基本信息行
        basic_frame = ttk.Frame(rule_frame)
        basic_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(basic_frame, text="标识:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=2)
        id_entry = ttk.Entry(basic_frame, width=12, font=self.FONTS['code'])
        id_entry.pack(side=tk.LEFT, padx=2)
        id_entry.insert(0, rule.field_id)
        widgets['field_id'] = id_entry
        
        ttk.Label(basic_frame, text="名称:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=(10, 2))
        name_entry = ttk.Entry(basic_frame, width=15, font=self.FONTS['default'])
        name_entry.pack(side=tk.LEFT, padx=2)
        name_entry.insert(0, rule.field_name)
        widgets['field_name'] = name_entry
        
        enabled_var = tk.BooleanVar(value=rule.enabled)
        tk.Checkbutton(basic_frame, text="启用", variable=enabled_var,
                      font=self.FONTS['small'], bg="#f0f0f0").pack(side=tk.LEFT, padx=10)
        widgets['enabled'] = enabled_var
        
        ttk.Button(basic_frame, text="❌ 删除", command=lambda: self._remove_rule_ui(rule_frame), 
                  width=8).pack(side=tk.RIGHT, padx=2)

        # 规则类型选择
        type_frame = ttk.Frame(rule_frame)
        type_frame.pack(fill=tk.X, pady=2)
        
        ttk.Label(type_frame, text="类型:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=2)
        type_var = tk.StringVar(value=rule.rule_type)
        type_combo = ttk.Combobox(type_frame, textvariable=type_var, 
                                 values=["snippet", "css", "regex", "custom"], 
                                 width=10, state="readonly")
        type_combo.pack(side=tk.LEFT, padx=2)
        widgets['rule_type'] = type_var
        
        # 类型提示标签
        hint_label = ttk.Label(type_frame, text="", font=self.FONTS['hint'], foreground="green")
        hint_label.pack(side=tk.LEFT, padx=10)
        
        # 多值选项
        multiple_var = tk.BooleanVar(value=rule.multiple)
        tk.Checkbutton(type_frame, text="多值(返回list)", variable=multiple_var,
                      font=self.FONTS['small'], bg="#f0f0f0").pack(side=tk.RIGHT, padx=5)
        widgets['multiple'] = multiple_var

        # 规则内容输入区（根据类型切换）
        content_container = ttk.Frame(rule_frame)
        content_container.pack(fill=tk.X, pady=2)
        widgets['content_container'] = content_container
        
        # 动态内容区域
        self._update_rule_content_ui(content_container, widgets, rule, type_var.get())
        
        # 类型切换时更新界面
        def on_type_change(*args):
            new_type = type_var.get()
            # 更新提示
            hints = {
                'snippet': '格式：<div>{占位符}</div>',
                'css': '格式：span.genre a 或 #product-id',
                'regex': '格式：<title>(.*?)</title>',
                'custom': 'Python代码模式 - 可使用soup/html/re，结果赋值给result'
            }
            hint_label.config(text=hints.get(new_type, ''))
            # 刷新内容输入区
            for widget in content_container.winfo_children():
                widget.destroy()
            self._update_rule_content_ui(content_container, widgets, rule, new_type)
        
        type_var.trace('w', on_type_change)
        on_type_change()  # 初始化提示

        self._rule_frames.append(widgets)
        return widgets

    def _update_rule_content_ui(self, container, widgets, rule, rule_type):
        """根据规则类型更新内容输入界面"""
        if rule_type == 'custom':
            # 自定义代码模式 - 大文本框
            code_frame = ttk.Frame(container)
            code_frame.pack(fill=tk.X)
            
            # 可用变量提示
            hint = """# 可用: soup (BeautifulSoup), html (原始文本), re (正则模块)
# 结果必须赋值给: result
# 示例: result = soup.select_one('h1').get_text(strip=True)"""
            ttk.Label(code_frame, text=hint, font=self.FONTS['code_small'], 
                    foreground="gray", justify=tk.LEFT).pack(anchor='w')
            
            # 代码编辑区
            code_text = scrolledtext.ScrolledText(code_frame, height=10, wrap=tk.NONE, 
                                                 font=self.FONTS['code'])
            code_text.pack(fill=tk.X, pady=2)
            code_text.insert(1.0, rule.rule_content if rule.rule_content else self.CUSTOM_CODE_TEMPLATE)
            widgets['rule_content'] = code_text
            
            # 加载模板按钮
            btn_frame = ttk.Frame(code_frame)
            btn_frame.pack(fill=tk.X, pady=2)
            ttk.Button(btn_frame, text="加载模板", 
                      command=lambda: code_text.delete(1.0, tk.END) or code_text.insert(1.0, self.CUSTOM_CODE_TEMPLATE),
                      width=10).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="简单示例(单值)", 
                      command=lambda: code_text.delete(1.0, tk.END) or code_text.insert(1.0, "result = soup.select_one('h1').get_text(strip=True)"),
                      width=15).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="列表示例(多值)", 
                      command=lambda: code_text.delete(1.0, tk.END) or code_text.insert(1.0, "result = [a.get_text(strip=True) for a in soup.select('span a')]"),
                      width=15).pack(side=tk.LEFT, padx=2)
            
        elif rule_type == 'snippet':
            # 代码块模式
            ttk.Label(container, text="HTML片段:", font=self.FONTS['small']).pack(anchor='w')
            content_text = scrolledtext.ScrolledText(container, height=4, wrap=tk.NONE, font=self.FONTS['code'])
            content_text.pack(fill=tk.X, pady=2)
            content_text.insert(1.0, rule.rule_content)
            widgets['rule_content'] = content_text
            
            ph_frame = ttk.Frame(container)
            ph_frame.pack(fill=tk.X, pady=2)
            ttk.Label(ph_frame, text="占位符:", font=self.FONTS['small']).pack(side=tk.LEFT, padx=2)
            ph_entry = ttk.Entry(ph_frame, width=15, font=self.FONTS['code'])
            ph_entry.pack(side=tk.LEFT, padx=2)
            ph_entry.insert(0, rule.placeholder)
            widgets['placeholder'] = ph_entry
            
        elif rule_type == 'css':
            # CSS选择器模式
            ttk.Label(container, text="CSS选择器:", font=self.FONTS['small']).pack(anchor='w')
            content_text = scrolledtext.ScrolledText(container, height=2, wrap=tk.NONE, font=self.FONTS['code'])
            content_text.pack(fill=tk.X, pady=2)
            content_text.insert(1.0, rule.rule_content)
            widgets['rule_content'] = content_text
            
            extract_var = tk.BooleanVar(value=rule.extract_text)
            tk.Checkbutton(container, text="提取纯文本(而非HTML)", variable=extract_var,
                          font=self.FONTS['small'], bg="#f0f0f0").pack(anchor='w')
            widgets['extract_text'] = extract_var
            
        elif rule_type == 'regex':
            # 正则模式
            ttk.Label(container, text="正则表达式:", font=self.FONTS['small']).pack(anchor='w')
            content_text = scrolledtext.ScrolledText(container, height=2, wrap=tk.NONE, font=self.FONTS['code'])
            content_text.pack(fill=tk.X, pady=2)
            content_text.insert(1.0, rule.rule_content)
            widgets['rule_content'] = content_text

    def _add_new_rule(self):
        """添加新规则"""
        existing_ids = [w['field_id'].get() for w in self._rule_frames]
        i = 1
        while f"field_{i}" in existing_ids:
            i += 1
        
        new_rule = ExtractRule(
            field_id=f"field_{i}",
            field_name=f"新字段{i}",
            rule_type="css",  # 默认CSS
            rule_content="",
            placeholder="",
            extract_text=True,
            multiple=False,
            enabled=True
        )
        self._create_rule_ui(self.rules_list_frame, new_rule, len(self._rule_frames))

    def _remove_rule_ui(self, frame):
        """删除规则UI"""
        for i, widgets in enumerate(self._rule_frames):
            if widgets['frame'] == frame:
                self._rule_frames.pop(i)
                break
        frame.destroy()
        
        for i, widgets in enumerate(self._rule_frames):
            try:
                widgets['frame'].config(text=f"规则 {i+1}")
            except:
                pass

    def _clear_all_rules(self):
        """清空所有规则"""
        if messagebox.askyesno("确认", "确定要清空所有抓取规则吗？", parent=self.parent):
            for widgets in self._rule_frames[:]:
                widgets['frame'].destroy()
            self._rule_frames.clear()

    def _load_default_rules(self):
        """加载默认示例规则"""
        if messagebox.askyesno("确认", "确定要重置为默认示例规则吗？当前规则将丢失。", parent=self.parent):
            for widgets in self._rule_frames[:]:
                widgets['frame'].destroy()
            self._rule_frames.clear()
            
            default_rules = [
                ExtractRule("code", "📌 番号", "snippet", "<span>{番号}</span>", "{番号}", True, False, True),
                ExtractRule("date", "📅 日期", "css", ".release-date", "", True, False, True),
                ExtractRule("actors", "👥 演员", "css", "span.genre a", "", True, True, True),
                ExtractRule("custom_demo", "🔧 自定义示例", "custom", 
                           "result = soup.select_one('title').get_text(strip=True) if soup.select_one('title') else ''", 
                           "", True, False, False),
            ]
            
            for i, rule in enumerate(default_rules):
                self._create_rule_ui(self.rules_list_frame, rule, i)

    def _get_rules_from_ui(self) -> List[ExtractRule]:
        """从UI收集所有规则"""
        rules = []
        for widgets in self._rule_frames:
            try:
                rule_type = widgets['rule_type'].get()
                
                # 根据类型获取内容
                if 'rule_content' in widgets:
                    if isinstance(widgets['rule_content'], scrolledtext.ScrolledText):
                        content = widgets['rule_content'].get(1.0, tk.END).strip()
                    else:
                        content = widgets['rule_content'].get().strip()
                else:
                    content = ""
                
                # 获取其他属性
                placeholder = widgets.get('placeholder', type('obj', (), {'get': lambda: ''})()).get().strip() if 'placeholder' in widgets else ""
                extract_text = widgets.get('extract_text', type('obj', (), {'get': lambda: True})()).get() if 'extract_text' in widgets else True
                
                rule = ExtractRule(
                    field_id=widgets['field_id'].get().strip(),
                    field_name=widgets['field_name'].get().strip(),
                    rule_type=rule_type,
                    rule_content=content,
                    placeholder=placeholder,
                    extract_text=extract_text,
                    multiple=widgets['multiple'].get(),
                    enabled=widgets['enabled'].get()
                )
                if rule.field_id:
                    rules.append(rule)
            except Exception as e:
                self._log(f"收集规则时出错: {e}")
        return rules

    def _create_log_section(self, parent):
        """日志和按钮"""
        log_frame = ttk.LabelFrame(parent, text="📋 运行日志", padding=5)
        log_frame.pack(fill=tk.X, pady=5, padx=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, wrap=tk.WORD, font=self.FONTS['code_small'])
        self.log_text.pack(fill=tk.X)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=10, padx=5)

        ttk.Button(btn_frame, text="🧪 测试解析", command=self._test_parse, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🗑️ 清空日志", command=self._clear_log, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="💾 保存配置", command=self._save_config, width=15).pack(side=tk.RIGHT, padx=5)

    def _create_html_panel(self, parent):
        """网页源码显示面板"""
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=5)

        ttk.Label(toolbar, text="测试番号:", font=self.FONTS['default']).pack(side=tk.LEFT, padx=5)
        self.test_code_entry = ttk.Entry(toolbar, width=20, font=self.FONTS['code_large'])
        self.test_code_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(toolbar, text="获取网页", command=self._fetch_html, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="测试解析", command=self._test_parse, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="清空", command=self._clear_html, width=8).pack(side=tk.LEFT, padx=5)

        # HTML显示
        html_frame = ttk.Frame(parent)
        html_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.html_text = scrolledtext.ScrolledText(html_frame, wrap=tk.NONE, font=self.FONTS['code_large'],
                                                 bg="#1e1e1e", fg="#d4d4d4", height=20)
        self.html_text.pack(fill=tk.BOTH, expand=True)

        self.html_info_label = ttk.Label(parent, text="就绪", foreground="gray", font=self.FONTS['small'])
        self.html_info_label.pack(anchor='e', padx=5)

    def _reset_headers_default(self):
        """重置默认请求头"""
        default_headers = """User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"""
        self.headers_text.delete(1.0, tk.END)
        self.headers_text.insert(1.0, default_headers)

    def _load_config_to_ui(self):
        """加载配置到UI"""
        # 基础配置
        self.url_text.delete(1.0, tk.END)
        self.url_text.insert(1.0, '\n'.join(self.config.urls))
        self.placeholder_entry.delete(0, tk.END)
        self.placeholder_entry.insert(0, self.config.get('placeholder', '{番号}'))
        self.crawl_mode_var.set(self.config.get('crawl_mode', 'round_robin'))
        self.css_selector_entry.delete(0, tk.END)
        self.css_selector_entry.insert(0, self.config.get('css_selector', 'body'))
        self.interval_min_entry.delete(0, tk.END)
        self.interval_min_entry.insert(0, str(self.config.get('interval_min', 1000)))
        self.interval_max_entry.delete(0, tk.END)
        self.interval_max_entry.insert(0, str(self.config.get('interval_max', 3000)))
        self.timeout_entry.delete(0, tk.END)
        self.timeout_entry.insert(0, str(self.config.get('timeout', 10)))
        self.retry_entry.delete(0, tk.END)
        self.retry_entry.insert(0, str(self.config.get('retry_times', 3)))
        self.render_js_var.set(self.config.get('render_js', False))
        self.render_engine_var.set(self.config.get('render_engine', 'playwright'))
        
        # 请求头
        headers = self.config.get('headers', {})
        if headers:
            self.headers_text.delete(1.0, tk.END)
            self.headers_text.insert(1.0, "\n".join([f"{k}: {v}" for k, v in headers.items()]))
        else:
            self._reset_headers_default()
        
        # 加载规则
        rules = self.config.extract_rules
        for widgets in self._rule_frames[:]:
            widgets['frame'].destroy()
        self._rule_frames.clear()
        
        if rules:
            for i, rule in enumerate(rules):
                self._create_rule_ui(self.rules_list_frame, rule, i)
        else:
            self._load_default_rules()

    def _get_config_from_ui(self) -> dict:
        """从UI获取完整配置"""
        urls = [u.strip() for u in self.url_text.get(1.0, tk.END).strip().split('\n') if u.strip()]
        rules = self._get_rules_from_ui()
        
        headers = {}
        for line in self.headers_text.get(1.0, tk.END).strip().split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip()] = v.strip()
        
        return {
            'urls': urls,
            'placeholder': self.placeholder_entry.get().strip(),
            'crawl_mode': self.crawl_mode_var.get(),
            'css_selector': self.css_selector_entry.get().strip(),
            'interval_min': int(self.interval_min_entry.get() or 1000),
            'interval_max': int(self.interval_max_entry.get() or 3000),
            'timeout': int(self.timeout_entry.get() or 10),
            'retry_times': int(self.retry_entry.get() or 3),
            'headers': headers,
            'extract_rules': [r.to_dict() for r in rules],
            'render_js': self.render_js_var.get(),
            'render_engine': self.render_engine_var.get(),
        }

    def _save_config(self):
        """保存配置"""
        try:
            config_data = self._get_config_from_ui()
            for key, value in config_data.items():
                self.config.set(key, value)

            if self.config.save_config():
                self._log("✓ 配置已保存")
                messagebox.showinfo("成功", "配置已保存", parent=self.parent)
            else:
                messagebox.showwarning("警告", "配置保存失败", parent=self.parent)
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}", parent=self.parent)

    def _test_urls(self):
        """测试网址连接"""
        urls = [u.strip() for u in self.url_text.get(1.0, tk.END).strip().split('\n') if u.strip()]
        if not urls:
            messagebox.showwarning("提示", "请先输入网址", parent=self.parent)
            return

        def test_thread():
            for url in urls:
                success, msg = self.crawler.test_url(url)
                self._log(f"{'✓' if success else '✗'} {url}: {msg}")

        threading.Thread(target=test_thread, daemon=True).start()

    def _remove_failed_urls(self):
        """移除失效网址"""
        messagebox.showinfo("提示", "请重新测试网址后手动删除失效的URL", parent=self.parent)

    def _fetch_html(self):
        """获取网页HTML"""
        code = self.test_code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入测试番号", parent=self.parent)
            return

        config_data = self._get_config_from_ui()
        for key, value in config_data.items():
            self.config.set(key, value)
        self.crawler.config = self.config._core_config

        self.html_text.delete(1.0, tk.END)
        self._log(f"开始获取网页: {code}")

        def fetch_thread():
            for i, url_template in enumerate(self.config.urls):
                success, html, used_url, msg, extracted = self.crawler.fetch_page(code, i)
                if success:
                    self._current_html = html
                    self.parent.after(0, lambda: self._show_html(extracted or html, len(html)))
                    self._log(f"✓ 获取成功")
                    return
                else:
                    self._log(f"✗ 失败: {msg}")

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _show_html(self, content: str, total_len: int = 0):
        """显示HTML内容"""
        self.html_text.delete(1.0, tk.END)
        self.html_text.insert(1.0, content if content else "(无内容)")
        self.html_info_label.config(text=f"长度: {len(content)}", foreground="green")

    def _test_parse(self):
        """测试解析规则"""
        if not self._current_html:
            messagebox.showwarning("提示", "请先获取网页HTML", parent=self.parent)
            return

        rules = self._get_rules_from_ui()
        self.config.extract_rules = rules
        self.crawler.config = self.config._core_config

        result = self.crawler.parse_info(self._current_html)

        # 显示结果
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, "解析结果:\n")
        self.result_text.insert(tk.END, "=" * 60 + "\n")
        
        for rule in rules:
            if not rule.enabled:
                continue
            value = result.get(rule.field_id)
            self.result_text.insert(tk.END, f"\n【{rule.field_name}】({rule.field_id}) - 类型: {rule.rule_type}\n")
            
            if isinstance(value, list):
                self.result_text.insert(tk.END, f"  📋 列表 (共{len(value)}项):\n")
                for i, item in enumerate(value[:15], 1):
                    self.result_text.insert(tk.END, f"    [{i}] {item}\n")
                if len(value) > 15:
                    self.result_text.insert(tk.END, f"    ... 还有 {len(value)-15} 项 ...\n")
            else:
                self.result_text.insert(tk.END, f"  📄 值: {value if value else '(未提取到)'}\n")

        self._log("解析测试完成")

    def _clear_html(self):
        """清空HTML"""
        self.html_text.delete(1.0, tk.END)
        self._current_html = ""
        self.html_info_label.config(text="已清空", foreground="gray")

    def _clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)

    def _log(self, message: str):
        """添加日志"""
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')


def main():
    """独立运行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='视频爬虫配置工具 - 支持自定义Python代码')
    parser.add_argument('--config', '-c', type=str, default=None, help='配置文件路径')
    args = parser.parse_args()

    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass

    app = CrawlerConfigApp(root, config_file=args.config)
    root.mainloop()


if __name__ == "__main__":
    main()