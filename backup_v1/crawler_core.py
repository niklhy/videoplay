#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爬虫核心模块 - 支持动态抓取规则 + 自定义Python代码执行
"""
import json
import re
import time
import threading
import random
import urllib.request
import urllib.error
import urllib.parse
import ssl
import gzip
import os
import textwrap
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Callable, Tuple, Any

# 添加HTML解析支持
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    import chardet
    CHARDET_AVAILABLE = True
except ImportError:
    CHARDET_AVAILABLE = False

# 浏览器渲染支持
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Playwright支持
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


@dataclass
class CrawlResult:
    """爬取结果数据类"""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    crawl_time: str = ""
    error: str = ""
    raw_html: str = ""
    extracted_content: str = ""

    def get(self, key: str, default=None):
        return self.data.get(key, default)
    
    def __getitem__(self, key):
        return self.data[key]
    
    def __setitem__(self, key, value):
        self.data[key] = value


@dataclass
class ExtractRule:
    """单个提取规则定义"""
    field_id: str
    field_name: str
    rule_type: str  # snippet, css, regex, custom
    rule_content: str
    placeholder: str = ""
    extract_text: bool = True
    multiple: bool = False
    enabled: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ExtractRule':
        return cls(**data)


class CrawlerConfig:
    """爬虫配置管理"""
    
    DEFAULT_CONFIG = {
        "urls": [],
        "interval_min": 1000,
        "interval_max": 3000,
        "placeholder": "{番号}",
        "crawl_mode": "round_robin",
        "request_method": "GET",
        "post_data": "",
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ],
        "use_proxy": False,
        "proxy_url": "",
        "retry_times": 3,
        "timeout": 10,
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        },
        "extract_rules": [
            {
                "field_id": "code",
                "field_name": "📌 番号",
                "rule_type": "snippet",
                "rule_content": "<span>{番号}</span>",
                "placeholder": "{番号}",
                "extract_text": True,
                "multiple": False,
                "enabled": True
            },
            {
                "field_id": "date",
                "field_name": "📅 日期",
                "rule_type": "css",
                "rule_content": ".release-date",
                "placeholder": "",
                "extract_text": True,
                "multiple": False,
                "enabled": True
            },
            {
                "field_id": "actors",
                "field_name": "👥 演员",
                "rule_type": "css",
                "rule_content": "span.genre a",
                "placeholder": "",
                "extract_text": True,
                "multiple": True,
                "enabled": True
            },
            {
                "field_id": "title",
                "field_name": "📖 标题",
                "rule_type": "css",
                "rule_content": "h1.title",
                "placeholder": "",
                "extract_text": True,
                "multiple": False,
                "enabled": True
            }
        ],
        "css_selector": "body",
        "show_full_html": True,
        "encoding": "auto",
        "render_js": False,
        "render_engine": "playwright",
        "browser_type": "chromium",
        "headless": True,
        "browser_timeout": 30,
        "wait_for_selector": "",
        "window_size": "1920,1080",
        "block_images": True,
    }

    def __init__(self, config_file: Optional[str] = None, shared_config: Dict = None):
        self.config_file = config_file
        self._config = self.DEFAULT_CONFIG.copy()
        
        if not self.config_file:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            possible_paths = [
                os.path.join(script_dir, 'config.json'),
                os.path.join(os.path.dirname(script_dir), 'config.json'),
                os.path.join(os.getcwd(), 'config.json'),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    self.config_file = path
                    break
        
        if shared_config is not None and 'crawler' in shared_config:
            self._load_from_dict(shared_config['crawler'])
        elif shared_config is not None:
            self._load_from_dict(shared_config)
        else:
            self._load_config()

    def _load_config(self):
        """从共享的 config.json 加载爬虫配置"""
        if not self.config_file or not os.path.exists(self.config_file):
            return
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                full_config = json.load(f)
                crawler_data = full_config.get('crawler', {})
                self._load_from_dict(crawler_data)
        except Exception as e:
            print(f"加载爬虫配置失败: {e}")

    def _load_from_dict(self, data: Dict):
        """从字典加载配置"""
        for key, value in data.items():
            if key in self._config:
                if key == 'headers' and isinstance(value, dict):
                    self._config[key].update(value)
                elif key == 'extract_rules' and isinstance(value, list):
                    self._config[key] = value
                else:
                    self._config[key] = value

    def save_config(self) -> bool:
        """保存配置到共享的 config.json"""
        if not self.config_file:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.config_file = os.path.join(script_dir, 'config.json')
        
        try:
            full_config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    full_config = json.load(f)
            
            full_config['crawler'] = self._config
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(full_config, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def get(self, key: str, default=None):
        return self._config.get(key, default)

    def set(self, key: str, value):
        self._config[key] = value

    @property
    def urls(self) -> List[str]:
        return self._config.get('urls', [])

    @urls.setter
    def urls(self, value: List[str]):
        self._config['urls'] = value[:5] if isinstance(value, list) else []

    @property
    def extract_rules(self) -> List[ExtractRule]:
        """获取提取规则列表"""
        rules_data = self._config.get('extract_rules', [])
        return [ExtractRule.from_dict(r) if isinstance(r, dict) else r for r in rules_data]
    
    @extract_rules.setter
    def extract_rules(self, rules: List[ExtractRule]):
        """设置提取规则列表"""
        self._config['extract_rules'] = [r.to_dict() if isinstance(r, ExtractRule) else r for r in rules]

    def get_random_interval(self) -> int:
        min_val = self._config.get('interval_min', 1000)
        max_val = self._config.get('interval_max', 3000)
        return random.randint(min_val, max_val)

    def get_random_user_agent(self) -> str:
        agents = self._config.get('user_agents', self.DEFAULT_CONFIG['user_agents'])
        return random.choice(agents) if agents else self.DEFAULT_CONFIG['user_agents'][0]

    def to_dict(self) -> Dict:
        return self._config.copy()

    def from_dict(self, data: Dict):
        self._load_from_dict(data)


class WebCrawler:
    """网页爬虫核心类"""

    def __init__(self, config: CrawlerConfig):
        self.config = config
        self._stop_flag = False
        self._current_url_index = 0
        self._log_callback: Optional[Callable] = None
        self._driver: Optional[webdriver.Remote] = None

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def set_log_callback(self, callback: Callable[[str], None]):
        self._log_callback = callback

    def _log(self, message: str):
        if self._log_callback:
            self._log_callback(message)
        else:
            print(message)

    def _decompress_response(self, data: bytes, headers) -> bytes:
        """解压 gzip/deflate 响应"""
        content_encoding = ""
        if hasattr(headers, 'get'):
            content_encoding = headers.get('Content-Encoding', '').lower()
        elif isinstance(headers, dict):
            content_encoding = headers.get('Content-Encoding', '').lower()
        
        if content_encoding == 'gzip':
            try:
                return gzip.decompress(data)
            except Exception as e:
                self._log(f"Gzip解压失败: {e}")
        elif content_encoding == 'deflate':
            import zlib
            try:
                return zlib.decompress(data, -zlib.MAX_WBITS)
            except:
                try:
                    return zlib.decompress(data)
                except Exception as e:
                    self._log(f"Deflate解压失败: {e}")
        
        if len(data) > 3 and data[:3] == b'\x1f\x8b\x08':
            try:
                return gzip.decompress(data)
            except Exception as e:
                self._log(f"魔数解压失败: {e}")
        
        return data

    def _detect_encoding(self, html_bytes: bytes, headers) -> str:
        """智能检测网页编码"""
        content_type = ""
        if isinstance(headers, dict):
            content_type = headers.get('Content-Type', '')
        elif hasattr(headers, 'get'):
            content_type = headers.get('Content-Type', '')
        
        if content_type:
            charset_match = re.search(r'charset=["\']?([A-Za-z0-9-]+)["\']?', content_type, re.IGNORECASE)
            if charset_match:
                return charset_match.group(1)

        if html_bytes:
            for trial_encoding in ['utf-8', 'gb2312', 'gbk', 'big5', 'latin-1']:
                try:
                    sample = html_bytes[:8000].decode(trial_encoding, errors='ignore')
                    patterns = [
                        r'<meta[^>]+charset=["\']?([A-Za-z0-9-]+)["\'\s/>]',
                        r'<meta[^>]+http-equiv=["\']?content-type["\']?[^>]+content=["\'][^"\']*charset=([A-Za-z0-9-]+)',
                    ]
                    for pattern in patterns:
                        meta_match = re.search(pattern, sample, re.IGNORECASE)
                        if meta_match:
                            return meta_match.group(1)
                except:
                    continue

        if html_bytes.startswith(b'\xef\xbb\xbf'):
            return 'utf-8'
        
        if CHARDET_AVAILABLE and html_bytes:
            detected = chardet.detect(html_bytes)
            if detected and detected['encoding'] and detected['confidence'] > 0.5:
                return detected['encoding']

        return 'utf-8'

    def stop(self):
        self._stop_flag = True

    def reset(self):
        self._stop_flag = False
        self._current_url_index = 0

    def _extract_root_url(self, url: str) -> str:
        """提取根域名"""
        placeholder = self.config.get('placeholder', '{番号}')
        if placeholder in url:
            root_part = url.split(placeholder)[0]
            parsed = urllib.parse.urlparse(root_part)
            return f"{parsed.scheme}://{parsed.netloc}/"
        parsed = urllib.parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def test_url(self, url: str) -> Tuple[bool, str]:
        """测试网址连接"""
        try:
            root_url = self._extract_root_url(url)
            self._log(f"测试根域名: {root_url}")
            
            headers = {
                'User-Agent': self.config.get_random_user_agent(),
                **self.config.get('headers', {})
            }
            req = urllib.request.Request(root_url, headers=headers, method='HEAD')

            with urllib.request.urlopen(
                req, 
                timeout=self.config.get('timeout', 10),
                context=self.ssl_context
            ) as response:
                return True, f"HTTP {response.status}"
        except Exception as e:
            return False, str(e)

    def _init_browser(self) -> Optional[webdriver.Remote]:
        """初始化浏览器驱动（Selenium）"""
        if not SELENIUM_AVAILABLE:
            return None

        browser_type = self.config.get('browser_type', 'chrome').lower()
        headless = self.config.get('headless', True)
        window_size = self.config.get('window_size', '1920,1080')

        try:
            if browser_type == 'chrome':
                from selenium.webdriver.chrome.service import Service as ChromeService
                from webdriver_manager.chrome import ChromeDriverManager
                
                options = ChromeOptions()
                if headless:
                    options.add_argument('--headless=new')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument(f'--window-size={window_size}')
                options.add_argument(f'user-agent={self.config.get_random_user_agent()}')
                
                service = ChromeService(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
            elif browser_type == 'firefox':
                from selenium.webdriver.firefox.service import Service as FirefoxService
                from webdriver_manager.firefox import GeckoDriverManager
                
                options = FirefoxOptions()
                if headless:
                    options.add_argument('--headless')
                
                service = FirefoxService(GeckoDriverManager().install())
                driver = webdriver.Firefox(service=service, options=options)
            else:
                return None

            self._log(f"✓ Selenium初始化成功 ({browser_type})")
            return driver
            
        except Exception as e:
            self._log(f"❌ Selenium初始化失败: {str(e)}")
            return None

    def _get_page_with_browser(self, url_template: str, code: str) -> Tuple[bool, str, str]:
        """使用Selenium获取渲染后的页面"""
        if not SELENIUM_AVAILABLE:
            return False, "", "Selenium未安装"

        if not self._driver:
            self._driver = self._init_browser()
            if not self._driver:
                return False, "", "浏览器驱动初始化失败"

        try:
            placeholder = self.config.get('placeholder', '{番号}')
            url = url_template.replace(placeholder, urllib.parse.quote(code))
            
            self._log(f"🌐 Selenium加载: {url}")
            start_time = time.time()
            
            self._driver.get(url)
            WebDriverWait(self._driver, 30).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            
            wait_selector = self.config.get('wait_for_selector', '').strip()
            if wait_selector:
                try:
                    WebDriverWait(self._driver, 30).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                    )
                except TimeoutException:
                    pass
            
            time.sleep(2)
            html = self._driver.page_source
            
            self._log(f"⏱️ Selenium耗时: {time.time() - start_time:.2f}秒")
            return True, html, "成功(Selenium)"
            
        except Exception as e:
            return False, "", f"Selenium失败: {str(e)}"

    def _get_page_with_playwright(self, url_template: str, code: str) -> Tuple[bool, str, str]:
        """使用Playwright获取渲染后的页面"""
        if not PLAYWRIGHT_AVAILABLE:
            return False, "", "Playwright未安装"

        try:
            placeholder = self.config.get('placeholder', '{番号}')
            url = url_template.replace(placeholder, urllib.parse.quote(code))
            
            self._log(f"🚀 Playwright加载: {url}")
            start_time = time.time()
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.config.get('headless', True))
                context = browser.new_context(
                    user_agent=self.config.get_random_user_agent(),
                    viewport={'width': 1920, 'height': 1080}
                )
                
                if self.config.get('block_images', True):
                    context.route("**/*.{png,jpg,jpeg,gif,svg,webp}", lambda route: route.abort())
                
                page = context.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                wait_selector = self.config.get('wait_for_selector', '').strip()
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=10000)
                    except:
                        pass
                
                html = page.content()
                browser.close()
                
                self._log(f"⏱️ Playwright耗时: {time.time() - start_time:.2f}秒")
                return True, html, "成功(Playwright)"
                
        except Exception as e:
            return False, "", f"Playwright失败: {str(e)}"

    def close_browser(self):
        """关闭浏览器驱动"""
        if self._driver:
            try:
                self._driver.quit()
            except:
                pass
            self._driver = None

    def fetch_page(self, code: str, url_index: Optional[int] = None) -> Tuple[bool, str, str, str, str]:
        """抓取单个页面"""
        urls = self.config.urls
        if not urls:
            return False, "", "", "没有配置网址", ""

        if url_index is not None and 0 <= url_index < len(urls):
            url_template = urls[url_index]
        elif self.config.get('crawl_mode') == 'single':
            url_template = urls[0]
        else:
            url_template = urls[self._current_url_index]
            self._current_url_index = (self._current_url_index + 1) % len(urls)

        placeholder = self.config.get('placeholder', '{番号}')
        
        if self.config.get('render_js', False):
            render_engine = self.config.get('render_engine', 'playwright').lower()
            
            if render_engine == 'playwright' and PLAYWRIGHT_AVAILABLE:
                success, html, msg = self._get_page_with_playwright(url_template, code)
                if success:
                    used_url = url_template.replace(placeholder, urllib.parse.quote(code))
                    extracted = self._extract_by_css_selector(html)
                    return True, html, used_url, msg, extracted
            
            if SELENIUM_AVAILABLE:
                success, html, msg = self._get_page_with_browser(url_template, code)
                if success:
                    used_url = url_template.replace(placeholder, urllib.parse.quote(code))
                    extracted = self._extract_by_css_selector(html)
                    return True, html, used_url, msg, extracted
        
        return self._fetch_static(code, url_index)

    def _fetch_static(self, code: str, url_index: Optional[int] = None) -> Tuple[bool, str, str, str, str]:
        """静态抓取逻辑"""
        urls = self.config.urls
        if url_index is not None and 0 <= url_index < len(urls):
            url_template = urls[url_index]
        else:
            url_template = urls[self._current_url_index]
            
        placeholder = self.config.get('placeholder', '{番号}')
        url = url_template.replace(placeholder, urllib.parse.quote(code))

        retry_times = self.config.get('retry_times', 3)
        for attempt in range(retry_times):
            if self._stop_flag:
                return False, "", url, "已停止", ""

            try:
                delay = self.config.get_random_interval()
                time.sleep(delay / 1000.0)

                headers = {
                    'User-Agent': self.config.get_random_user_agent(),
                    **self.config.get('headers', {})
                }
                
                req = urllib.request.Request(url, headers=headers)
                
                with urllib.request.urlopen(
                    req,
                    timeout=self.config.get('timeout', 10),
                    context=self.ssl_context
                ) as response:
                    raw_data = response.read()
                    html_bytes = self._decompress_response(raw_data, response.headers)
                    encoding = self._detect_encoding(html_bytes, response.headers)
                    html = html_bytes.decode(encoding, errors='replace')
                    
                    extracted = self._extract_by_css_selector(html)
                    return True, html, url, "成功(静态)", extracted

            except Exception as e:
                if attempt < retry_times - 1:
                    time.sleep(2 ** attempt)
                else:
                    return False, "", url, str(e), ""

        return False, "", url, "重试次数用尽", ""

    def _extract_by_css_selector(self, html: str) -> str:
        """根据全局CSS选择器提取内容"""
        selector = self.config.get('css_selector', 'body')
        if not selector or selector == 'body':
            return html

        if not BS4_AVAILABLE:
            return html

        try:
            soup = BeautifulSoup(html, 'html.parser')
            elements = soup.select(selector)
            if elements:
                return str(elements[0])
        except Exception as e:
            self._log(f"CSS选择器提取失败: {e}")
        
        return html

    def _extract_by_rule(self, html: str, rule: ExtractRule) -> Any:
        """根据单个规则提取内容"""
        if not rule.enabled or not rule.rule_content:
            return [] if rule.multiple else ""
        
        try:
            if rule.rule_type == 'snippet':
                return self._extract_by_snippet(html, rule.rule_content, rule.placeholder, rule.multiple)
            elif rule.rule_type == 'css':
                return self._extract_by_css_rule(html, rule.rule_content, rule.extract_text, rule.multiple)
            elif rule.rule_type == 'regex':
                return self._extract_by_regex(html, rule.rule_content, rule.multiple)
            elif rule.rule_type == 'custom':
                return self._extract_by_custom_code(html, rule.rule_content, rule.multiple)
            else:
                return [] if rule.multiple else ""
        except Exception as e:
            self._log(f"规则 '{rule.field_name}' 提取失败: {e}")
            return [] if rule.multiple else ""

    def _extract_by_snippet(self, html: str, snippet_template: str, placeholder: str, multiple: bool = False):
        """通过代码块+占位符提取"""
        if not snippet_template or placeholder not in snippet_template:
            return [] if multiple else ""
        
        parts = snippet_template.split(placeholder, 1)
        if len(parts) != 2:
            return [] if multiple else ""
        
        before, after = re.escape(parts[0]), re.escape(parts[1])
        pattern = f"{before}([^<>]*?){after}"
        
        if multiple:
            matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
            return [m.strip() for m in matches if m.strip()]
        else:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

    def _extract_by_css_rule(self, html: str, selector: str, extract_text: bool, multiple: bool = False):
        """通过CSS选择器提取"""
        if not BS4_AVAILABLE or not selector:
            return [] if multiple else ""
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            elements = soup.select(selector)
            
            if not elements:
                return [] if multiple else ""
            
            if multiple:
                if extract_text:
                    texts = [elem.get_text(strip=True) for elem in elements if elem.get_text(strip=True)]
                    seen = set()
                    result = []
                    for text in texts:
                        if text not in seen:
                            seen.add(text)
                            result.append(text)
                    return result
                else:
                    return [str(elem) for elem in elements]
            else:
                elem = elements[0]
                return elem.get_text(strip=True) if extract_text else str(elem)
                
        except Exception as e:
            self._log(f"CSS提取错误: {e}")
            return [] if multiple else ""

    def _extract_by_regex(self, html: str, pattern: str, multiple: bool = False):
        """通过正则表达式提取"""
        try:
            if multiple:
                matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                cleaned = []
                for m in matches:
                    if isinstance(m, tuple):
                        for group in m:
                            if group and group.strip():
                                cleaned.append(group.strip())
                                break
                    elif m and m.strip():
                        cleaned.append(m.strip())
                seen = set()
                result = []
                for item in cleaned:
                    if item not in seen:
                        seen.add(item)
                        result.append(item)
                return result
            else:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    if match.groups():
                        for group in match.groups():
                            if group:
                                return group.strip()
                    return match.group(0).strip()
                return ""
        except Exception as e:
            self._log(f"正则提取错误: {e}")
            return [] if multiple else ""

    def _extract_by_custom_code(self, html: str, code: str, multiple: bool = False) -> Any:
        """
        执行自定义Python代码提取内容
        可用变量：
        - html: 原始HTML字符串
        - soup: BeautifulSoup对象（已初始化）
        - re: 正则表达式模块
        - result: 需要返回的结果（单值或列表）
        """
        if not BS4_AVAILABLE:
            self._log("❌ BeautifulSoup未安装，无法执行自定义代码")
            return [] if multiple else ""
        
        try:
            # 创建BeautifulSoup对象
            soup = BeautifulSoup(html, 'html.parser')
            
            # 准备执行环境（受限的安全环境）
            # 只允许访问特定的内置函数和模块
            safe_builtins = {
                'len': len,
                'str': str,
                'int': int,
                'list': list,
                'dict': dict,
                'set': set,
                'tuple': tuple,
                'range': range,
                'enumerate': enumerate,
                'zip': zip,
                'map': map,
                'filter': filter,
                'sum': sum,
                'min': min,
                'max': max,
                'any': any,
                'all': all,
                'isinstance': isinstance,
                'hasattr': hasattr,
                'getattr': getattr,
                'print': lambda *args: self._log(" ".join(str(a) for a in args)),
            }
            
            # 创建执行命名空间
            exec_globals = {
                "__builtins__": safe_builtins,
                "re": re,
                "html": html,
                "soup": soup,
            }
            exec_locals = {}
            
            # 执行用户代码
            exec(code, exec_globals, exec_locals)
            
            # 获取结果
            result = exec_locals.get('result', None)
            
            # 处理结果
            if result is None:
                return [] if multiple else ""
            
            # 如果结果是列表/元组且 multiple=True，直接返回
            if multiple:
                if isinstance(result, (list, tuple)):
                    return list(result)
                elif isinstance(result, str):
                    return [result] if result else []
                else:
                    return [str(result)] if result else []
            else:
                # 单值模式
                if isinstance(result, (list, tuple)):
                    # 如果是列表，取第一个元素
                    return result[0] if result else ""
                else:
                    return str(result) if result else ""
                    
        except SyntaxError as e:
            self._log(f"❌ 自定义代码语法错误: {e}")
            return [] if multiple else ""
        except Exception as e:
            self._log(f"❌ 自定义代码执行错误: {e}")
            return [] if multiple else ""

    def parse_info(self, html: str) -> Dict[str, Any]:
        """使用动态规则解析信息"""
        result = {}
        if not html:
            return result
        
        rules = self.config.extract_rules
        
        for rule in rules:
            if not rule.enabled:
                continue
            
            value = self._extract_by_rule(html, rule)
            result[rule.field_id] = value
            
            # 日志记录
            if rule.multiple:
                count = len(value) if isinstance(value, list) else (1 if value else 0)
                self._log(f"✓ [{rule.field_name}] 提取到 {count} 个结果 (类型: {rule.rule_type})")
            else:
                preview = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                self._log(f"✓ [{rule.field_name}] => {preview} (类型: {rule.rule_type})")
        
        return result

    def crawl(self, code: str, callback: Optional[Callable[[CrawlResult], None]] = None) -> Optional[CrawlResult]:
        def do_crawl():
            success, html, used_url, msg, extracted = self.fetch_page(code)

            if success:
                info = self.parse_info(html)
                result = CrawlResult(
                    success=True,
                    data=info,
                    source_url=used_url,
                    crawl_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    raw_html=html,
                    extracted_content=extracted
                )
            else:
                result = CrawlResult(
                    success=False,
                    source_url=used_url,
                    crawl_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    error=msg
                )

            if callback:
                callback(result)
            return result

        if callback:
            threading.Thread(target=do_crawl, daemon=True).start()
            return None
        else:
            return do_crawl()