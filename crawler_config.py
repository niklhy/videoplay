# -*- coding: utf-8 -*-
"""爬虫配置模块（设计文档第25/三十章）。

本模块为爬虫功能提供纯逻辑层实现，包含：

- ``CrawlerConfig`` / ``ExtractionRule``：配置数据类（序列化/反序列化）
- ``CrawlerConfigManager``：配置的加载、保存、校验、连通性测试、规则测试
- ``ExtractionEngine``：四种提取规则（snippet/css/regex/custom）的沙箱执行引擎
- ``CustomCodeManager``：手动番号管理器（数据仅用于爬虫URL构建，不参与深度学习训练）

UI 面板（CrawlerConfigPanel / CrawlerExecutor）由后续任务实现，本文件不包含任何 tkinter 代码。
beautifulsoup4 为可选依赖，缺失时 CSS / custom 规则优雅降级并给出说明。
"""

import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# BeautifulSoup 为可选依赖：缺失时不影响其余三种规则
try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    BeautifulSoup = None  # type: ignore
    _BS4_AVAILABLE = False


# ---------------------------------------------------------------------------
# 常量（本模块私有，避免与全局契约冲突）
# ---------------------------------------------------------------------------

# 支持的四类提取规则
RULE_TYPE_SNIPPET = "snippet"
RULE_TYPE_CSS = "css"
RULE_TYPE_REGEX = "regex"
RULE_TYPE_CUSTOM = "custom"
RULE_TYPES = (RULE_TYPE_SNIPPET, RULE_TYPE_CSS, RULE_TYPE_REGEX, RULE_TYPE_CUSTOM)

# URL 库轮换模式
URL_MODE_ROTATE = "rotate"      # 轮流使用
URL_MODE_FAILOVER = "failover"  # 失败切换
URL_MODES = (URL_MODE_ROTATE, URL_MODE_FAILOVER)

# 默认配置（对应设计文档 01 号 3.3 节 crawler_config 结构）
DEFAULT_CRAWLER_CONFIG_DICT: Dict[str, Any] = {
    "version": "1.0",
    "url_library": {
        "urls": [
            "https://www.dmmsee.bond/{番号}",
            "https://www.busjav.bond/{番号}",
            "https://www.busfan.cyou/{番号}",
            "https://www.dmmsee.ink/{番号}",
        ],
        "placeholder": "{番号}",
        "mode": "rotate",
    },
    "page_parser": {"global_css_selector": "body > div.container", "encoding": "auto"},
    "anti_crawl": {
        "use_browser_render": False,
        "engine": "playwright",
        "browser": "chromium",
        "interval_min_ms": 1000,
        "interval_max_ms": 3000,
        "timeout_seconds": 10,
        "retry_count": 3,
        "use_proxy": False,
        "proxy_url": "",
    },
    "http_headers": {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "content-type": "text/html;Charset=utf-8;;charset=UTF-8",
    },
    "extraction_rules": [
        {
            "rule_id": "rule_001",
            "field_id": "code",
            "field_name": "番号",
            "enabled": True,
            "rule_type": "custom",
            "is_multi_value": False,
            "code": "result = soup.select_one('span[style=\"color:#CC0000;\"]').text.strip()",
            "description": "从红色span标签提取番号",
        },
        {
            "rule_id": "rule_002",
            "field_id": "release_date",
            "field_name": "发布日期",
            "enabled": True,
            "rule_type": "css",
            "is_multi_value": False,
            "selector": "span.release-date",
            "attribute": "text",
            "description": "CSS选择器提取发布日期",
        },
        {
            "rule_id": "rule_003",
            "field_id": "actors",
            "field_name": "演员名",
            "enabled": True,
            "rule_type": "css",
            "is_multi_value": True,
            "selector": "a.actor-link",
            "attribute": "text",
            "description": "提取所有演员链接文本",
        },
        {
            "rule_id": "rule_004",
            "field_id": "title",
            "field_name": "标题",
            "enabled": True,
            "rule_type": "snippet",
            "is_multi_value": False,
            "html_snippet": '<h1 class="title">{标题}</h1>',
            "placeholder": "{标题}",
            "description": "从h1标题标签提取",
        },
        {
            "rule_id": "rule_005",
            "field_id": "duration",
            "field_name": "时长",
            "enabled": True,
            "rule_type": "regex",
            "is_multi_value": False,
            "pattern": r"(\d+)\s*min",
            "group_index": 1,
            "description": "正则提取时长分钟数",
        },
    ],
}

# custom 沙箱执行超时（秒）
CUSTOM_CODE_TIMEOUT_SECONDS = 5

# custom 沙箱中允许使用的受限内置函数（无 __import__ / open / eval / exec 等危险入口）
_SAFE_BUILTINS = {
    "str": str, "int": int, "float": float, "bool": bool,
    "len": len, "list": list, "dict": dict, "set": set, "tuple": tuple,
    "range": range, "enumerate": enumerate, "sorted": sorted,
    "min": min, "max": max, "abs": abs, "round": round,
    "isinstance": isinstance, "print": print,
}


# ---------------------------------------------------------------------------
# 数据类：ExtractionRule
# ---------------------------------------------------------------------------

@dataclass
class ExtractionRule:
    """单条元数据提取规则（设计 30.2 节 extraction_rules 元素结构）。

    rule_type 决定生效的类型专属字段：

    - ``snippet``：html_snippet + placeholder（代码段模板 + 占位符）
    - ``css``：selector + attribute（CSS 选择器 + 取值属性，"text" 表示文本）
    - ``regex``：pattern + group_index（正则 + 捕获组序号）
    - ``custom``：code（自定义 Python 代码，沙箱执行）
    """

    rule_id: str = ""
    field_id: str = ""
    field_name: str = ""
    enabled: bool = True
    rule_type: str = "css"
    is_multi_value: bool = False
    # CSS 规则
    selector: str = ""
    attribute: str = "text"
    # snippet 规则
    html_snippet: str = ""
    placeholder: str = ""
    # regex 规则
    pattern: str = ""
    group_index: int = 1
    # custom 规则
    code: str = ""
    # 通用说明
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可写入 config.json 的字典。"""
        return {
            "rule_id": self.rule_id,
            "field_id": self.field_id,
            "field_name": self.field_name,
            "enabled": self.enabled,
            "rule_type": self.rule_type,
            "is_multi_value": self.is_multi_value,
            "selector": self.selector,
            "attribute": self.attribute,
            "html_snippet": self.html_snippet,
            "placeholder": self.placeholder,
            "pattern": self.pattern,
            "group_index": self.group_index,
            "code": self.code,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractionRule":
        """从字典反序列化（缺失字段取默认值，容忍历史配置）。"""
        data = data or {}
        return cls(
            rule_id=str(data.get("rule_id", "")),
            field_id=str(data.get("field_id", "")),
            field_name=str(data.get("field_name", "")),
            enabled=bool(data.get("enabled", True)),
            rule_type=str(data.get("rule_type", "css")),
            is_multi_value=bool(data.get("is_multi_value", False)),
            selector=str(data.get("selector", "")),
            attribute=str(data.get("attribute", "text")),
            html_snippet=str(data.get("html_snippet", "")),
            placeholder=str(data.get("placeholder", "")),
            pattern=str(data.get("pattern", "")),
            group_index=int(data.get("group_index", 1) or 1),
            code=str(data.get("code", "")),
            description=str(data.get("description", "")),
        )


# ---------------------------------------------------------------------------
# 数据类：CrawlerConfig
# ---------------------------------------------------------------------------

@dataclass
class CrawlerConfig:
    """爬虫整体配置（设计 01 号文档 3.3 节 crawler_config 结构）。

    - ``url_library``：数据源URL库（urls 列表、占位符、rotate/failover 模式）
    - ``page_parser``：页面解析设置（全局CSS选择器、编码）
    - ``anti_crawl``：反爬策略（浏览器渲染、间隔、超时、重试、代理）
    - ``http_headers``：HTTP 请求头
    - ``extraction_rules``：元数据提取规则列表
    """

    version: str = "1.0"
    url_library: Dict[str, Any] = field(default_factory=lambda: {
        "urls": [],
        "placeholder": "{番号}",
        "mode": URL_MODE_ROTATE,
    })
    page_parser: Dict[str, Any] = field(default_factory=lambda: {
        "global_css_selector": "",
        "encoding": "auto",
    })
    anti_crawl: Dict[str, Any] = field(default_factory=lambda: {
        "use_browser_render": False,
        "engine": "playwright",
        "browser": "chromium",
        "interval_min_ms": 1000,
        "interval_max_ms": 3000,
        "timeout_seconds": 10,
        "retry_count": 3,
        "use_proxy": False,
        "proxy_url": "",
    })
    http_headers: Dict[str, str] = field(default_factory=dict)
    extraction_rules: List[ExtractionRule] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可写入 config.json 的完整字典。"""
        return {
            "version": self.version,
            "url_library": {
                "urls": list(self.url_library.get("urls", [])),
                "placeholder": self.url_library.get("placeholder", "{番号}"),
                "mode": self.url_library.get("mode", URL_MODE_ROTATE),
            },
            "page_parser": dict(self.page_parser),
            "anti_crawl": dict(self.anti_crawl),
            "http_headers": dict(self.http_headers),
            "extraction_rules": [rule.to_dict() for rule in self.extraction_rules],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CrawlerConfig":
        """从字典反序列化（缺失节使用默认值，容忍不完整配置）。"""
        data = data or {}
        url_library = data.get("url_library") or {}
        page_parser = data.get("page_parser") or {}
        anti_crawl = data.get("anti_crawl") or {}
        raw_rules = data.get("extraction_rules") or []
        rules = []
        for raw in raw_rules:
            if isinstance(raw, dict):
                rules.append(ExtractionRule.from_dict(raw))
        return cls(
            version=str(data.get("version", "1.0")),
            url_library={
                "urls": list(url_library.get("urls", []) or []),
                "placeholder": str(url_library.get("placeholder", "{番号}")),
                "mode": str(url_library.get("mode", URL_MODE_ROTATE)),
            },
            page_parser={
                "global_css_selector": str(page_parser.get("global_css_selector", "")),
                "encoding": str(page_parser.get("encoding", "auto")),
            },
            anti_crawl={
                "use_browser_render": bool(anti_crawl.get("use_browser_render", False)),
                "engine": str(anti_crawl.get("engine", "playwright")),
                "browser": str(anti_crawl.get("browser", "chromium")),
                "interval_min_ms": int(anti_crawl.get("interval_min_ms", 1000) or 0),
                "interval_max_ms": int(anti_crawl.get("interval_max_ms", 3000) or 0),
                "timeout_seconds": int(anti_crawl.get("timeout_seconds", 10) or 0),
                "retry_count": int(anti_crawl.get("retry_count", 3) or 0),
                "use_proxy": bool(anti_crawl.get("use_proxy", False)),
                "proxy_url": str(anti_crawl.get("proxy_url", "")),
            },
            http_headers={str(k): str(v) for k, v in (data.get("http_headers") or {}).items()},
            extraction_rules=rules,
        )


# ---------------------------------------------------------------------------
# ExtractionEngine：四种提取规则的沙箱执行引擎（设计 30.3 节）
# ---------------------------------------------------------------------------

class ExtractionEngine:
    """提取规则执行引擎。

    支持四种规则类型：snippet / css / regex / custom。

    - snippet：在 HTML 中查找代码段模板，提取占位符处文本
    - css：BeautifulSoup 的 CSS 选择器（attribute="text" 取文本，否则取属性）
    - regex：正则表达式（取 group_index 捕获组）
    - custom：自定义 Python 代码沙箱执行（可用变量 soup/html/re/result，
      禁止文件操作、网络请求、系统调用，5秒超时）

    返回值：str | list[str] | None（未命中或出错时为 None）。
    """

    @staticmethod
    def execute(rule: ExtractionRule, html_content: str,
                soup: Any = None) -> Union[str, List[str], None]:
        """按规则类型分发执行。

        :param rule: 提取规则
        :param html_content: 原始 HTML 字符串
        :param soup: 可选的 BeautifulSoup 实例（css/custom 缺省时尝试自行创建）
        :return: 提取结果（str / list[str]），未命中或失败返回 None
        """
        if not rule:
            return None
        if rule.rule_type == RULE_TYPE_SNIPPET:
            return ExtractionEngine._execute_snippet(rule, html_content or "")
        if rule.rule_type == RULE_TYPE_CSS:
            return ExtractionEngine._execute_css(rule, html_content or "", soup)
        if rule.rule_type == RULE_TYPE_REGEX:
            return ExtractionEngine._execute_regex(rule, html_content or "")
        if rule.rule_type == RULE_TYPE_CUSTOM:
            return ExtractionEngine._execute_custom(rule, html_content or "", soup)
        return None

    # ------------------------------------------------------------------
    # snippet：HTML 代码段 + 占位符
    # ------------------------------------------------------------------
    @staticmethod
    def _execute_snippet(rule: ExtractionRule, html_content: str) -> Optional[str]:
        """在 HTML 中匹配代码段模板，返回占位符处的文本。

        模板形如 ``<h1 class="title">{标题}</h1>``，按占位符切分为前缀/后缀，
        在 HTML 中定位后提取中间内容。
        """
        snippet = rule.html_snippet
        placeholder = rule.placeholder
        if not snippet or not placeholder or placeholder not in snippet:
            return None
        prefix, suffix = snippet.split(placeholder, 1)
        start = html_content.find(prefix)
        if start < 0:
            return None
        value_start = start + len(prefix)
        end = html_content.find(suffix, value_start) if suffix else -1
        if end < 0:
            # 后缀未找到时，尝试向后截断到标签边界，占位符处无值则失败
            tail = html_content[value_start:value_start + 512]
            tag_end = tail.find("<")
            if tag_end <= 0:
                return None
            return None if not tail[:tag_end].strip() else tail[:tag_end].strip()
        value = html_content[value_start:end]
        return value.strip() if value else None

    # ------------------------------------------------------------------
    # css：CSS 选择器
    # ------------------------------------------------------------------
    @staticmethod
    def _execute_css(rule: ExtractionRule, html_content: str,
                     soup: Any = None) -> Union[str, List[str], None]:
        """用 CSS 选择器提取元素文本或属性。

        :return: 多值规则返回 list[str]，单值返回 str，bs4 缺失或未命中返回 None
        """
        if not _BS4_AVAILABLE:
            return None
        if soup is None and html_content:
            soup = BeautifulSoup(html_content, "html.parser")
        if soup is None or not rule.selector:
            return None

        def _extract_one(element: Any) -> str:
            if rule.attribute and rule.attribute != "text":
                return element.get(rule.attribute, "")
            return element.get_text(strip=True)

        if rule.is_multi_value:
            elements = soup.select(rule.selector)
            if not elements:
                return None
            return [_extract_one(el) for el in elements]
        element = soup.select_one(rule.selector)
        if element is None:
            return None
        return _extract_one(element)

    # ------------------------------------------------------------------
    # regex：正则表达式
    # ------------------------------------------------------------------
    @staticmethod
    def _execute_regex(rule: ExtractionRule, html_content: str) -> Optional[str]:
        """正则匹配并返回指定捕获组内容。"""
        if not rule.pattern:
            return None
        try:
            match = re.search(rule.pattern, html_content, re.DOTALL)
        except re.error:
            return None
        if not match:
            return None
        group_index = max(0, int(rule.group_index))
        if group_index > match.re.groups:
            group_index = 0
        try:
            value = match.group(group_index)
        except IndexError:
            return None
        return value if value is not None else None

    # ------------------------------------------------------------------
    # custom：自定义 Python 代码沙箱
    # ------------------------------------------------------------------
    @staticmethod
    def _execute_custom(rule: ExtractionRule, html_content: str,
                        soup: Any = None) -> Any:
        """沙箱执行自定义 Python 代码。

        安全约束：
        - 命名空间只含 soup / html / re / result 及受限内置函数白名单
        - 禁止文件操作、网络请求、系统调用（无 __import__ / open / eval 等）
        - 5 秒超时（工作线程执行，超时后返回 None）

        :return: 代码赋给 result 的值，异常或超时返回 None
        """
        if not rule.code.strip():
            return None
        if not _BS4_AVAILABLE and soup is None:
            return None
        if soup is None and html_content:
            soup = BeautifulSoup(html_content, "html.parser")

        result_holder: Dict[str, Any] = {"value": None, "done": False}

        def _run() -> None:
            namespace = {
                "soup": soup,
                "html": html_content,
                "re": re,
                "result": None,
                "__builtins__": dict(_SAFE_BUILTINS),
            }
            try:
                exec(compile(rule.code, "<crawler_rule>", "exec"), namespace)  # noqa: S102
                result_holder["value"] = namespace.get("result")
            except Exception as exc:  # noqa: BLE001 - 沙箱必须捕获一切异常
                result_holder["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                result_holder["done"] = True

        worker = threading.Thread(target=_run, daemon=True,
                                  name="crawler-custom-rule")
        worker.start()
        worker.join(CUSTOM_CODE_TIMEOUT_SECONDS)
        if not result_holder["done"]:
            return None
        return result_holder.get("value")


# ---------------------------------------------------------------------------
# CrawlerConfigManager：爬虫配置管理器（设计 30.2 节）
# ---------------------------------------------------------------------------

class CrawlerConfigManager:
    """爬虫配置管理器。

    负责 CrawlerConfig 的加载、保存、校验，以及 URL 连通性测试、
    提取规则测试。配置持久化在 config_manager.data["crawler_config"]。
    """

    def __init__(self, config_manager: Any, event_bus: Any) -> None:
        """初始化。

        :param config_manager: ConfigManager 实例（需有 data 属性和 save() 方法）
        :param event_bus: EventBus 实例（预留，用于后续配置变更通知）
        """
        self.config_manager = config_manager
        self.event_bus = event_bus
        self._config: Optional[CrawlerConfig] = None

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------
    def load_config(self) -> CrawlerConfig:
        """从 config_manager.data["crawler_config"] 读取配置。

        配置不存在或格式异常时返回默认配置（不落盘）。
        """
        raw = None
        if self.config_manager is not None:
            data = getattr(self.config_manager, "data", None) or {}
            raw = data.get("crawler_config")
        if isinstance(raw, dict):
            try:
                self._config = CrawlerConfig.from_dict(raw)
                return self._config
            except Exception:  # noqa: BLE001 - 配置损坏时回退默认
                pass
        self._config = CrawlerConfig.from_dict(DEFAULT_CRAWLER_CONFIG_DICT)
        return self._config

    def save_config(self, config: CrawlerConfig) -> None:
        """保存配置：写回 config_manager.data 并调用 config_manager.save()。"""
        self._config = config
        if self.config_manager is not None:
            data = getattr(self.config_manager, "data", None)
            if data is None:
                self.config_manager.data = {}
                data = self.config_manager.data
            data["crawler_config"] = config.to_dict()
            save = getattr(self.config_manager, "save", None)
            if callable(save):
                save()

    def get_config(self) -> CrawlerConfig:
        """获取当前配置（未加载时先加载）。"""
        if self._config is None:
            return self.load_config()
        return self._config

    def reset_to_default(self) -> CrawlerConfig:
        """重置为默认配置并保存，返回重置后的配置。"""
        config = CrawlerConfig.from_dict(DEFAULT_CRAWLER_CONFIG_DICT)
        self.save_config(config)
        return config

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def validate_config(self, config: CrawlerConfig) -> List[str]:
        """校验配置，返回问题描述字符串列表（空列表表示通过）。

        :param config: 待校验配置
        :return: 问题列表，如 ["URL库为空", ...]
        """
        problems: List[str] = []
        if config is None:
            return ["配置为空"]

        url_library = config.url_library or {}
        urls = url_library.get("urls") or []
        placeholder = url_library.get("placeholder", "{番号}")
        if not urls:
            problems.append("URL库为空，至少需要配置一个数据源网址")
        else:
            for index, url in enumerate(urls):
                if not isinstance(url, str) or not url.strip():
                    problems.append(f"第 {index + 1} 个网址为空")
                elif not url.startswith(("http://", "https://")):
                    problems.append(f"第 {index + 1} 个网址不是有效的HTTP(S)地址: {url}")
                elif placeholder and placeholder not in url:
                    problems.append(f"第 {index + 1} 个网址缺少占位符 {placeholder}: {url}")
        if url_library.get("mode") not in URL_MODES:
            problems.append(f"未知的URL轮换模式: {url_library.get('mode')}（应为 rotate/failover）")

        anti_crawl = config.anti_crawl or {}
        interval_min = anti_crawl.get("interval_min_ms", 0)
        interval_max = anti_crawl.get("interval_max_ms", 0)
        if interval_min < 0 or interval_max < 0:
            problems.append("请求间隔不能为负数")
        elif interval_min > interval_max:
            problems.append("请求间隔下限不能大于上限")
        if int(anti_crawl.get("timeout_seconds", 0) or 0) <= 0:
            problems.append("请求超时时间必须大于0秒")
        if int(anti_crawl.get("retry_count", 0) or 0) < 0:
            problems.append("重试次数不能为负数")
        if anti_crawl.get("use_proxy") and not anti_crawl.get("proxy_url"):
            problems.append("已启用代理但未配置代理地址")

        seen_field_ids = set()
        seen_rule_ids = set()
        for rule in config.extraction_rules or []:
            label = rule.field_name or rule.field_id or rule.rule_id or "未命名规则"
            if rule.rule_type not in RULE_TYPES:
                problems.append(f"规则「{label}」类型无效: {rule.rule_type}")
                continue
            if not rule.field_id:
                problems.append(f"规则「{label}」缺少字段标识 field_id")
            elif rule.field_id in seen_field_ids:
                problems.append(f"字段标识重复: {rule.field_id}")
            seen_field_ids.add(rule.field_id)
            if rule.rule_id:
                if rule.rule_id in seen_rule_ids:
                    problems.append(f"规则ID重复: {rule.rule_id}")
                seen_rule_ids.add(rule.rule_id)
            # 类型专属必填字段检查
            if rule.rule_type == RULE_TYPE_CSS and not rule.selector:
                problems.append(f"CSS规则「{label}」缺少选择器 selector")
            elif rule.rule_type == RULE_TYPE_SNIPPET:
                if not rule.html_snippet or not rule.placeholder:
                    problems.append(f"代码段规则「{label}」缺少 html_snippet 或 placeholder")
                elif rule.placeholder not in rule.html_snippet:
                    problems.append(f"代码段规则「{label}」的模板中不包含占位符 {rule.placeholder}")
            elif rule.rule_type == RULE_TYPE_REGEX:
                if not rule.pattern:
                    problems.append(f"正则规则「{label}」缺少 pattern")
                else:
                    try:
                        re.compile(rule.pattern)
                    except re.error as exc:
                        problems.append(f"正则规则「{label}」编译失败: {exc}")
            elif rule.rule_type == RULE_TYPE_CUSTOM:
                if not rule.code.strip():
                    problems.append(f"自定义规则「{label}」缺少代码 code")
                if not _BS4_AVAILABLE and "soup" in rule.code:
                    problems.append(f"自定义规则「{label}」使用了 soup，但未安装 beautifulsoup4")
        return problems

    # ------------------------------------------------------------------
    # 测试
    # ------------------------------------------------------------------
    def test_url_connectivity(self, url_template: str, test_code: str) -> Dict[str, Any]:
        """测试数据源网址连通性（替换占位符后发送 HTTP 请求）。

        标准库 urllib 实现，超时与异常均安全返回，不抛出。

        :param url_template: 含占位符的网址模板，如 https://example.com/{番号}
        :param test_code: 测试番号，用于替换占位符
        :return: {"success": bool, "status_code": int, "message": str, "elapsed_ms": int}
        """
        result: Dict[str, Any] = {
            "success": False,
            "status_code": 0,
            "message": "",
            "elapsed_ms": 0,
        }
        config = self.get_config()
        placeholder = (config.url_library or {}).get("placeholder", "{番号}")
        url = (url_template or "").replace(placeholder, test_code or "")

        if not url.startswith(("http://", "https://")):
            result["message"] = f"无效的网址: {url}"
            return result

        anti_crawl = config.anti_crawl or {}
        timeout = int(anti_crawl.get("timeout_seconds", 10) or 10)

        headers = dict(config.http_headers or {})
        headers.setdefault("User-Agent",
                           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36")

        handlers = []
        if anti_crawl.get("use_proxy") and anti_crawl.get("proxy_url"):
            handlers.append(urllib.request.ProxyHandler(
                {"http": anti_crawl["proxy_url"], "https": anti_crawl["proxy_url"]}))
        opener = urllib.request.build_opener(*handlers)

        start_time = time.monotonic()
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with opener.open(request, timeout=timeout) as response:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                status_code = getattr(response, "status", 0) or response.getcode()
                result["status_code"] = int(status_code)
                result["elapsed_ms"] = elapsed_ms
                if 200 <= status_code < 300:
                    result["success"] = True
                    result["message"] = f"连接成功（HTTP {status_code}，{elapsed_ms}ms）"
                elif status_code == 404:
                    result["message"] = f"页面不存在（HTTP 404，{elapsed_ms}ms）"
                else:
                    result["message"] = f"服务器返回 HTTP {status_code}（{elapsed_ms}ms）"
        except urllib.error.HTTPError as exc:
            result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
            result["status_code"] = exc.code
            result["message"] = f"HTTP错误 {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
            reason = getattr(exc, "reason", exc)
            result["message"] = f"连接失败: {reason}"
        except Exception as exc:  # noqa: BLE001 - 测试方法必须捕获一切异常
            result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
            result["message"] = f"请求异常: {type(exc).__name__}: {exc}"
        return result

    def test_extraction_rule(self, rule: ExtractionRule, html_content: str) -> Dict[str, Any]:
        """测试单条提取规则。

        :param rule: 提取规则
        :param html_content: 测试 HTML 内容
        :return: {"success": bool, "result": 提取结果, "error": 错误说明}
        """
        result: Dict[str, Any] = {"success": False, "result": None, "error": ""}
        if rule is None:
            result["error"] = "规则为空"
            return result
        if rule.rule_type in (RULE_TYPE_CSS, RULE_TYPE_CUSTOM) and not _BS4_AVAILABLE:
            result["error"] = "未安装 beautifulsoup4，CSS/自定义规则不可用（pip install beautifulsoup4）"
            return result
        if not html_content:
            result["error"] = "HTML内容为空"
            return result
        try:
            value = ExtractionEngine.execute(rule, html_content)
        except Exception as exc:  # noqa: BLE001 - 测试方法必须捕获一切异常
            result["error"] = f"执行异常: {type(exc).__name__}: {exc}"
            return result
        if value is None:
            result["error"] = "未提取到结果（规则未命中或返回空）"
            return result
        result["success"] = True
        result["result"] = value
        return result

    # ------------------------------------------------------------------
    # 规则增删改查
    # ------------------------------------------------------------------
    def get_enabled_rules(self) -> List[ExtractionRule]:
        """获取所有启用的提取规则。"""
        return [rule for rule in self.get_config().extraction_rules if rule.enabled]

    def add_rule(self, rule: ExtractionRule) -> None:
        """添加一条提取规则（直接修改当前配置对象，需调用 save_config 落盘）。"""
        config = self.get_config()
        if rule not in config.extraction_rules:
            config.extraction_rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """按 rule_id 删除规则。

        :param rule_id: 规则ID
        :return: 是否找到并删除了规则
        """
        config = self.get_config()
        for index, rule in enumerate(config.extraction_rules):
            if rule.rule_id == rule_id:
                del config.extraction_rules[index]
                return True
        return False

    def update_rule(self, rule_id: str, rule: ExtractionRule) -> bool:
        """按 rule_id 替换规则。

        :param rule_id: 要替换的规则ID
        :param rule: 新的规则对象
        :return: 是否找到并更新了规则
        """
        config = self.get_config()
        for index, existing in enumerate(config.extraction_rules):
            if existing.rule_id == rule_id:
                config.extraction_rules[index] = rule
                return True
        return False


# ---------------------------------------------------------------------------
# CustomCodeManager：手动番号管理器（设计 30.4 节）
# ---------------------------------------------------------------------------

class CustomCodeManager:
    """手动番号修改管理器。

    明确声明：此模块数据仅用于爬虫URL构建，不参与深度学习训练。
    数据存储在 custom_codes 表（经 db_manager 专用方法读写）。

    注：db_manager 的专用方法以 relative_path 为键；本管理器对外接口
    使用 video_file_path，内部统一将路径分隔符规范化为 "/" 后直接透传，
    由调用方/上层模块负责绝对路径与相对路径的转换。
    """

    def __init__(self, db_manager: Any) -> None:
        """初始化。

        :param db_manager: DatabaseManager 实例（需有 custom_codes 专用方法）
        """
        self.db_manager = db_manager

    @staticmethod
    def _normalize_path(video_file_path: str) -> str:
        """规范化路径分隔符为 /，并去除首尾空白。"""
        return (video_file_path or "").strip().replace("\\", "/")

    def get_custom_code(self, video_file_path: str) -> Optional[str]:
        """查询文件的手动番号。

        :param video_file_path: 视频文件路径
        :return: 番号字符串，未设置或 db_manager 不可用时返回 None
        """
        if self.db_manager is None:
            return None
        getter = getattr(self.db_manager, "get_custom_code", None)
        if not callable(getter):
            return None
        return getter(self._normalize_path(video_file_path))

    def set_custom_code(self, video_file_path: str, custom_code: str) -> None:
        """设置文件的手动番号（UPSERT）。"""
        if self.db_manager is None:
            return
        setter = getattr(self.db_manager, "set_custom_code", None)
        if callable(setter):
            setter(self._normalize_path(video_file_path), custom_code or "")

    def remove_custom_code(self, video_file_path: str) -> None:
        """移除文件的手动番号。"""
        if self.db_manager is None:
            return
        remover = getattr(self.db_manager, "remove_custom_code", None)
        if callable(remover):
            remover(self._normalize_path(video_file_path))

    def has_custom_code(self, video_file_path: str) -> bool:
        """判断文件是否设置了手动番号。"""
        if self.db_manager is None:
            return False
        checker = getattr(self.db_manager, "has_custom_code", None)
        if not callable(checker):
            return False
        return bool(checker(self._normalize_path(video_file_path)))
