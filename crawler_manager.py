#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爬虫管理模块 - 主程序兼容层
提供与旧版相同的接口，内部调用新的核心模块

注意：此文件保持向后兼容，供主程序导入使用
独立的爬虫配置工具请使用 crawler_gui.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading

# 从核心模块导入
from crawler_core import CrawlerConfig, WebCrawler, CrawlResult


class CrawlerManager:
    """
    爬虫管理器 - 主程序兼容版
    提供与原版本相同的接口，内部使用新的核心模块
    """
    
    def __init__(self, parent_frame, config_manager, debug_utils=None):
        """
        初始化爬虫管理器
        
        Args:
            parent_frame: 父Frame
            config_manager: 主程序的配置管理器
            debug_utils: 调试工具
        """
        self.parent = parent_frame
        self.debug = debug_utils
        
        # 使用新的核心配置类
        self.crawler_config = CrawlerConfigAdapter(config_manager)
        self.crawler = WebCrawler(self.crawler_config)

        self._current_html = ""
        self._test_results = {}
        
        try:
            self._create_ui()
            self._load_config_to_ui()
            if self.debug:
                self.debug.print("爬虫管理器初始化完成")
        except Exception as e:
            if self.debug:
                self.debug.print(f"爬虫管理器初始化错误: {e}")
            raise
    
    def _create_ui(self):
        """创建三栏布局UI"""
        # 主框架
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧面板
        left_frame = ttk.LabelFrame(main_frame, text="爬虫设置", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.configure(width=350)
        
        self._create_left_panel(left_frame)
        
        # 中间面板
        center_frame = ttk.LabelFrame(main_frame, text="网页源码分析", padding=10)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self._create_center_panel(center_frame)
        
        # 右侧面板
        right_frame = ttk.LabelFrame(main_frame, text="抓取规则配置", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        right_frame.configure(width=400)
        
        self._create_right_panel(right_frame)
    
    def _create_left_panel(self, parent):
        """创建左侧面板"""
        # 网址库
        url_frame = ttk.LabelFrame(parent, text="网址库 (每行一个，最多5个)", padding=5)
        url_frame.pack(fill=tk.X, pady=5)
        
        self.url_text = scrolledtext.ScrolledText(url_frame, height=5, wrap=tk.WORD)
        self.url_text.pack(fill=tk.X)
        
        url_btn_frame = ttk.Frame(url_frame)
        url_btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(url_btn_frame, text="测试连接", command=self._test_urls).pack(side=tk.LEFT, padx=2)
        ttk.Button(url_btn_frame, text="清除失效", command=self._remove_failed_urls).pack(side=tk.LEFT, padx=2)
        
        self.url_status_label = ttk.Label(url_btn_frame, text="", foreground="gray")
        self.url_status_label.pack(side=tk.LEFT, padx=5)
        
        # 基本设置
        basic_frame = ttk.LabelFrame(parent, text="基本设置", padding=5)
        basic_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(basic_frame, text="替换符:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.placeholder_entry = ttk.Entry(basic_frame, width=12)
        self.placeholder_entry.grid(row=0, column=1, sticky='w', padx=5, pady=2)
        self.placeholder_entry.insert(0, "{番号}")
        
        ttk.Label(basic_frame, text="模式:").grid(row=0, column=2, sticky='w', padx=5, pady=2)
        self.crawl_mode_var = tk.StringVar(value="round_robin")
        ttk.Radiobutton(basic_frame, text="单网址", variable=self.crawl_mode_var, 
                       value="single").grid(row=0, column=3, sticky='w')
        ttk.Radiobutton(basic_frame, text="轮流", variable=self.crawl_mode_var, 
                       value="round_robin").grid(row=0, column=4, sticky='w')
        
        ttk.Label(basic_frame, text="间隔(ms):").grid(row=1, column=0, sticky='w', padx=5, pady=2)
        interval_frame = ttk.Frame(basic_frame)
        interval_frame.grid(row=1, column=1, columnspan=4, sticky='w', padx=5, pady=2)
        
        self.interval_min_entry = ttk.Entry(interval_frame, width=8)
        self.interval_min_entry.pack(side=tk.LEFT)
        self.interval_min_entry.insert(0, "1000")
        ttk.Label(interval_frame, text=" ~ ").pack(side=tk.LEFT)
        self.interval_max_entry = ttk.Entry(interval_frame, width=8)
        self.interval_max_entry.pack(side=tk.LEFT)
        self.interval_max_entry.insert(0, "3000")
        
        # 反爬策略
        anti_frame = ttk.LabelFrame(parent, text="反爬策略", padding=5)
        anti_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(anti_frame, text="超时:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.timeout_entry = ttk.Entry(anti_frame, width=8)
        self.timeout_entry.grid(row=0, column=1, sticky='w', padx=5, pady=2)
        self.timeout_entry.insert(0, "10")
        
        ttk.Label(anti_frame, text="重试:").grid(row=0, column=2, sticky='w', padx=5, pady=2)
        self.retry_entry = ttk.Entry(anti_frame, width=8)
        self.retry_entry.grid(row=0, column=3, sticky='w', padx=5, pady=2)
        self.retry_entry.insert(0, "3")
        
        self.use_proxy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(anti_frame, text="使用代理", variable=self.use_proxy_var).grid(
            row=1, column=0, columnspan=2, sticky='w', padx=5, pady=2)
        self.proxy_entry = ttk.Entry(anti_frame, width=30)
        self.proxy_entry.grid(row=1, column=2, columnspan=3, sticky='ew', padx=5, pady=2)
        
        # 底部按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="保存配置", command=self._save_config).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="重置默认", command=self._reset_default).pack(side=tk.RIGHT, padx=5)
    
    def _create_center_panel(self, parent):
        """创建中间面板"""
        # 测试输入
        test_frame = ttk.Frame(parent)
        test_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(test_frame, text="测试番号:").pack(side=tk.LEFT, padx=5)
        self.test_code_entry = ttk.Entry(test_frame, width=20)
        self.test_code_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(test_frame, text="获取网页", command=self._fetch_html).pack(side=tk.LEFT, padx=5)
        ttk.Button(test_frame, text="测试解析", command=self._test_parse).pack(side=tk.LEFT, padx=5)
        
        # HTML显示
        html_frame = ttk.Frame(parent)
        html_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.html_text = scrolledtext.ScrolledText(html_frame, wrap=tk.NONE, font=("Consolas", 9))
        self.html_text.pack(fill=tk.BOTH, expand=True)
        
        # 提示和操作按钮
        hint_frame = ttk.Frame(parent)
        hint_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(hint_frame, text="💡 选中HTML代码段后点击'复制选中'，粘贴到右侧配置",
                 foreground="blue").pack(side=tk.LEFT)
        
        ttk.Button(hint_frame, text="复制选中", command=self._copy_selection).pack(side=tk.RIGHT, padx=5)
        ttk.Button(hint_frame, text="清空HTML", command=self._clear_html).pack(side=tk.RIGHT, padx=5)
    
    def _create_right_panel(self, parent):
        """创建右侧面板"""
        # 说明
        hint_frame = ttk.LabelFrame(parent, text="配置说明", padding=5)
        hint_frame.pack(fill=tk.X, pady=5)
        
        hint_text = """1. 从中间复制包含目标信息的HTML代码
2. 粘贴到下方"代码段"中
3. 将实际值替换为占位符
4. 点击"测试解析"验证

占位符: {番号} {日期} {演员} {标题}"""
        
        ttk.Label(hint_frame, text=hint_text, justify=tk.LEFT, 
                 font=("微软雅黑", 9), foreground="gray").pack(anchor='w')
        
        # 各字段配置
        self.snippet_entries = {}
        self.placeholder_entries = {}
        
        fields = [
            ("code", "📌 番号", "{番号}"),
            ("date", "📅 日期", "{日期}"),
            ("actors", "👥 演员", "{演员}"),
            ("title", "📖 标题", "{标题}")
        ]
        
        for field_id, label, default_ph in fields:
            frame = ttk.LabelFrame(parent, text=label, padding=5)
            frame.pack(fill=tk.X, pady=5)
            
            snippet_entry = scrolledtext.ScrolledText(frame, height=3, wrap=tk.NONE, font=("Consolas", 9))
            snippet_entry.pack(fill=tk.X, pady=2)
            self.snippet_entries[field_id] = snippet_entry
            
            ph_frame = ttk.Frame(frame)
            ph_frame.pack(fill=tk.X, pady=2)
            
            ttk.Label(ph_frame, text="占位符:", font=("微软雅黑", 8)).pack(side=tk.LEFT)
            placeholder_entry = ttk.Entry(ph_frame, font=("Consolas", 9))
            placeholder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            placeholder_entry.insert(0, default_ph)
            self.placeholder_entries[field_id] = placeholder_entry
        
        # 测试结果
        result_frame = ttk.LabelFrame(parent, text="解析结果", padding=5)
        result_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.result_text.pack(fill=tk.BOTH, expand=True)
    
    def _load_config_to_ui(self):
        """加载配置到UI"""
        # 网址
        self.url_text.delete(1.0, tk.END)
        self.url_text.insert(1.0, '\n'.join(self.crawler_config.urls))
        
        # 基本设置
        self.placeholder_entry.delete(0, tk.END)
        self.placeholder_entry.insert(0, self.crawler_config.get('placeholder', '{番号}'))
        
        self.crawl_mode_var.set(self.crawler_config.get('crawl_mode', 'round_robin'))
        self.interval_min_entry.delete(0, tk.END)
        self.interval_min_entry.insert(0, str(self.crawler_config.get('interval_min', 1000)))
        self.interval_max_entry.delete(0, tk.END)
        self.interval_max_entry.insert(0, str(self.crawler_config.get('interval_max', 3000)))
        self.timeout_entry.delete(0, tk.END)
        self.timeout_entry.insert(0, str(self.crawler_config.get('timeout', 10)))
        self.retry_entry.delete(0, tk.END)
        self.retry_entry.insert(0, str(self.crawler_config.get('retry_times', 3)))
        self.use_proxy_var.set(self.crawler_config.get('use_proxy', False))
        self.proxy_entry.delete(0, tk.END)
        self.proxy_entry.insert(0, self.crawler_config.get('proxy_url', ''))
        
        # 代码段配置
        snippets = self.crawler_config.get('snippets', {})
        for field_id in ['code', 'date', 'actors', 'title']:
            if field_id in snippets:
                data = snippets[field_id]
                self.snippet_entries[field_id].delete(1.0, tk.END)
                self.snippet_entries[field_id].insert(1.0, data.get('snippet', ''))
                self.placeholder_entries[field_id].delete(0, tk.END)
                self.placeholder_entries[field_id].insert(0, data.get('placeholder', f'{{{field_id}}}'))
    
    def _get_config_from_ui(self):
        """从UI获取配置"""
        urls = [u.strip() for u in self.url_text.get(1.0, tk.END).strip().split('\n') if u.strip()][:5]
        valid_urls = [u for u in urls if u.startswith(('http://', 'https://'))]
        
        snippets = {}
        for field_id in ['code', 'date', 'actors', 'title']:
            snippet = self.snippet_entries[field_id].get(1.0, tk.END).strip()
            placeholder = self.placeholder_entries[field_id].get().strip()
            if snippet:
                snippets[field_id] = {'snippet': snippet, 'placeholder': placeholder}
        
        return {
            'urls': valid_urls,
            'placeholder': self.placeholder_entry.get().strip() or '{番号}',
            'crawl_mode': self.crawl_mode_var.get(),
            'interval_min': int(self.interval_min_entry.get() or 1000),
            'interval_max': int(self.interval_max_entry.get() or 3000),
            'timeout': int(self.timeout_entry.get() or 10),
            'retry_times': int(self.retry_entry.get() or 3),
            'use_proxy': self.use_proxy_var.get(),
            'proxy_url': self.proxy_entry.get().strip(),
            'snippets': snippets
        }
    
    def _save_config(self):
        """保存配置"""
        try:
            config_data = self._get_config_from_ui()
            for key, value in config_data.items():
                self.crawler_config.set(key, value)
            
            self.crawler_config.save_config()
            
            if self.debug:
                self.debug.print("爬虫配置已保存")
            messagebox.showinfo("成功", "爬虫配置已保存")
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {e}")
    
    def _reset_default(self):
        """重置为默认配置"""
        if messagebox.askyesno("确认", "确定要重置为默认配置吗？"):
            self.crawler_config.reset_to_default()
            self._load_config_to_ui()
    
    def _test_urls(self):
        """测试网址连接"""
        urls = [u.strip() for u in self.url_text.get(1.0, tk.END).strip().split('\n') if u.strip()]
        if not urls:
            messagebox.showwarning("提示", "请先输入网址")
            return
        
        self.url_status_label.config(text="正在测试...", foreground="blue")
        self._test_results = {}
        
        def test_thread():
            for i, url in enumerate(urls):
                if not url.startswith(('http://', 'https://')):
                    self._test_results[url] = (False, "格式错误")
                    continue
                
                success, msg = self.crawler.test_url(url)
                self._test_results[url] = (success, msg)
                
                self.parent.after(0, lambda u=url, s=success: self.url_status_label.config(
                    text=f"{'✓' if s else '✗'} {u[:20]}...",
                    foreground="green" if s else "red"
                ))
            
            success_count = sum(1 for s, _ in self._test_results.values() if s)
            self.parent.after(0, lambda: self.url_status_label.config(
                text=f"完成: {success_count}/{len(urls)} 个可用",
                foreground="green" if success_count == len(urls) else "orange"
            ))
        
        threading.Thread(target=test_thread, daemon=True).start()
    
    def _remove_failed_urls(self):
        """移除失效的网址"""
        if not self._test_results:
            messagebox.showwarning("提示", "请先进行连接测试")
            return
        
        urls = self.url_text.get(1.0, tk.END).strip().split('\n')
        valid_urls = [u.strip() for u in urls if u.strip() and self._test_results.get(u.strip(), (True, ''))[0]]
        
        self.url_text.delete(1.0, tk.END)
        self.url_text.insert(1.0, '\n'.join(valid_urls))
        messagebox.showinfo("完成", f"已清除失效网址，剩余 {len(valid_urls)} 个")
    
    def _fetch_html(self):
        """获取网页HTML"""
        code = self.test_code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入测试番号")
            return
        
        # 更新配置
        config_data = self._get_config_from_ui()
        for key, value in config_data.items():
            self.crawler_config.set(key, value)
        self.crawler.config = self.crawler_config
        
        self.html_text.delete(1.0, tk.END)
        
        def fetch_thread():
            for i, url_template in enumerate(self.crawler_config.urls):
                success, html, used_url, msg = self.crawler.fetch_page(code, i)
                
                if success:
                    self._current_html = html
                    self.parent.after(0, lambda: self._show_html(html))
                    return
            
            self.parent.after(0, lambda: messagebox.showerror("失败", "所有网址都无法获取"))
        
        threading.Thread(target=fetch_thread, daemon=True).start()
    
    def _show_html(self, html: str):
        """显示HTML"""
        display = html[:8000]
        if len(html) > 8000:
            display += f"\n\n... (已截断，共 {len(html)} 字符)"
        self.html_text.delete(1.0, tk.END)
        self.html_text.insert(1.0, display)
    
    def _test_parse(self):
        """测试解析"""
        if not self._current_html:
            messagebox.showwarning("提示", "请先获取网页HTML")
            return
        
        # 更新配置
        config_data = self._get_config_from_ui()
        for key, value in config_data.items():
            self.crawler_config.set(key, value)
        self.crawler.config = self.crawler_config
        
        info = self.crawler.parse_info(self._current_html)
        
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, "解析结果:\n")
        self.result_text.insert(tk.END, "=" * 40 + "\n")
        self.result_text.insert(tk.END, f"📌 番号: {info['code'] or '(未找到)'}\n")
        self.result_text.insert(tk.END, f"📅 日期: {info['date'] or '(未找到)'}\n")
        self.result_text.insert(tk.END, f"👥 演员: {', '.join(info['actors']) if info['actors'] else '(未找到)'}\n")
        self.result_text.insert(tk.END, f"📖 标题: {info['title'] or '(未找到)'}\n")
    
    def _copy_selection(self):
        """复制选中内容"""
        try:
            selected = self.html_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.parent.clipboard_clear()
            self.parent.clipboard_append(selected)
            messagebox.showinfo("成功", "已复制到剪贴板")
        except tk.TclError:
            messagebox.showwarning("提示", "请先在HTML区域选中要复制的代码段")
    
    def _clear_html(self):
        """清空HTML"""
        self.html_text.delete(1.0, tk.END)
        self._current_html = ""


class CrawlerConfigAdapter:
    """
    配置适配器 - 适配主程序的ConfigManager和新的CrawlerConfig
    """
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        # 使用新的核心配置类
        self._core_config = CrawlerConfig(None)
        self._load_from_main_config()
    
    def _load_from_main_config(self):
        """从主程序配置加载"""
        if hasattr(self.config_manager, 'config'):
            main_config = self.config_manager.config
            crawler_data = main_config.get('crawler', {})
            self._core_config._config.update(crawler_data)
    
    def get(self, key, default=None):
        return self._core_config.get(key, default)
    
    def set(self, key, value):
        self._core_config.set(key, value)
    
    @property
    def urls(self):
        return self._core_config.urls
    
    # ===== 新增代码开始 =====
    @property
    def extract_rules(self):
        """获取提取规则列表"""
        return self._core_config.extract_rules
    
    @extract_rules.setter
    def extract_rules(self, rules):
        """设置提取规则列表"""
        self._core_config.extract_rules = rules
    # ===== 新增代码结束 =====
    
    def save_config(self):
        """保存回主程序配置"""
        if hasattr(self.config_manager, 'config'):
            self.config_manager.config['crawler'] = self._core_config.to_dict()
            if hasattr(self.config_manager, 'save_config'):
                return self.config_manager.save_config()
        return False
    
    def reset_to_default(self):
        """重置为默认"""
        self._core_config = CrawlerConfig(None)
    
    def get_random_interval(self):
        return self._core_config.get_random_interval()
    
    def get_random_user_agent(self):
        return self._core_config.get_random_user_agent()
    

# 供主程序调用的便捷接口
class CrawlerInterface:
    """
    爬虫外部接口 - 供主程序调用
    使用新的核心模块实现
    """
    
    def __init__(self, config_manager, debug_utils=None):
        # 创建适配器
        self.config = CrawlerConfigAdapter(config_manager)
        self.crawler = WebCrawler(self.config)
        self.debug = debug_utils
        
        if self.debug:
            self.crawler.set_log_callback(lambda msg: self.debug.print(f"[Crawler] {msg}"))
    
    def crawl_video_info(self, code: str, callback=None):
        """
        抓取视频信息
        
        Args:
            code: 视频番号
            callback: 回调函数(success, info_dict)
        """
        def on_result(result: CrawlResult):
            info = {
                'code': result.data.get('code', ''),
                'date': result.data.get('date', ''),
                'actors': result.data.get('actors', []),
                'title': result.data.get('title', ''),
                'duration': result.data.get('duration', 0),
                'source_url': result.source_url,
                'crawl_time': result.crawl_time,
                'error': result.error,
                'raw_html': result.raw_html
            }
            if callback:
                callback(result.success, info)
        
        self.crawler.crawl(code, on_result if callback else None)
    
    def stop(self):
        """停止爬取"""
        self.crawler.stop()


# 向后兼容的别名
# 保持与旧版相同的导入方式
CrawlerManager = CrawlerManager
CrawlerInterface = CrawlerInterface
