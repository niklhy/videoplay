# -*- coding: utf-8 -*-
"""爬虫采集模块（设计文档第13章）。

职责：元数据自动采集调度——任务队列、worker守护线程、单条采集流程。
HTTP 抓取由 HttpCrawler 基于标准库 urllib 实现，规则解析复用
25号模块 crawler_config.py 中的 CrawlerConfigManager / ExtractionEngine /
CustomCodeManager。

共享契约（禁止重复定义）：
- models.FileMetadata（元数据数据类）
- events.EVENT_METADATA_UPDATED（元数据更新事件）
- constants.LOG_LEVEL_*（日志级别）

仅使用 Python 标准库，不引入第三方 HTTP 依赖。
"""

import abc
import gzip
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Dict, List, Optional
import queue as queue_module

from models import FileMetadata
from events import EVENT_METADATA_UPDATED
from constants import (
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_INFO,
    LOG_LEVEL_WARNING,
    LOG_LEVEL_ERROR,
    CONFIG_FILE,
)
from log_module import LogManager
from crawler_config import (
    CrawlerConfig,
    CrawlerConfigManager,
    CustomCodeManager,
    ExtractionEngine,
)


# 模块日志来源标识
_LOG_SOURCE = "crawler_module"

# 重试指数退避的基础等待秒数与上限
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0

# worker 线程从队列取任务的轮询超时（秒）
_WORKER_POLL_TIMEOUT = 0.5

# 无明确编码时依次尝试的编码候选
_ENCODING_CANDIDATES = ("utf-8", "gb18030", "big5", "latin-1")

# 默认 User-Agent（配置未提供时兜底）
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BaseCrawler(abc.ABC):
    """爬虫抽象基类（设计 13.2 节）。

    所有具体爬虫实现（HttpCrawler、未来的浏览器渲染爬虫等）
    必须实现以下两个抽象方法。
    """

    @abc.abstractmethod
    def search(self, video_code: str) -> dict:
        """按番号抓取网页。

        :param video_code: 视频番号/编号
        :return: 结果字典，至少包含 success(bool)、html(str)、url(str)、error(str)
        """

    @abc.abstractmethod
    def parse_metadata(self, html: str) -> FileMetadata:
        """从网页 HTML 中解析元数据。

        :param html: 网页 HTML 文本
        :return: 填充了元数据字段的 FileMetadata 对象
        """


class HttpCrawler(BaseCrawler):
    """基于 urllib 的 HTTP 爬虫实现。

    按 CrawlerConfig 配置执行抓取：
    - URL 模板轮换（rotate 轮询 / failover 故障转移）
    - 占位符替换（config.url_library.placeholder）
    - 随机请求间隔（anti_crawl.interval_min_ms ~ interval_max_ms）
    - 超时（anti_crawl.timeout_seconds）
    - 重试（anti_crawl.retry_count 次，指数退避）
    - 请求头（http_headers + 默认 User-Agent）
    - 可选代理（anti_crawl.use_proxy / proxy_url）
    - 编码自动检测（Content-Type / meta 标签 / 候选编码回退）
    - gzip / deflate 解压
    """

    def __init__(self, config: CrawlerConfig) -> None:
        """初始化。

        :param config: 爬虫配置对象（CrawlerConfigManager.get_config() 的返回）
        """
        self._config = config or CrawlerConfig()
        # URL 轮换游标：rotate 模式每次抓取后前进；failover 模式停留在
        # 最近成功的 URL 上，仅失败时切换
        self._url_cursor = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # BaseCrawler 接口实现
    # ------------------------------------------------------------------
    def search(self, video_code: str) -> dict:
        """按番号执行 HTTP 抓取。

        :param video_code: 视频番号/编号
        :return: {"success": bool, "html": str, "url": str, "error": str}
        """
        result: Dict[str, Any] = {
            "success": False,
            "html": "",
            "url": "",
            "error": "",
        }
        code = (video_code or "").strip()
        if not code:
            result["error"] = "番号为空，无法构建请求URL"
            return result

        url_library = self._config.url_library or {}
        urls = [u for u in (url_library.get("urls") or []) if isinstance(u, str) and u.strip()]
        placeholder = url_library.get("placeholder", "{番号}")
        mode = url_library.get("mode", "rotate")
        if not urls:
            result["error"] = "URL库为空，请先在爬虫配置中至少添加一个数据源网址"
            return result

        with self._lock:
            start_index = self._url_cursor % len(urls)

        # 遍历顺序：rotate 从游标处开始依次尝试所有 URL；
        # failover 从当前首选 URL 开始，失败才顺延到后续 URL
        ordered_indexes = [(start_index + i) % len(urls) for i in range(len(urls))]

        last_error = ""
        for position, url_index in enumerate(ordered_indexes):
            url = urls[url_index].strip().replace(placeholder, code)
            result["url"] = url
            html, error = self._fetch_with_retry(url)
            if error is None:
                result["success"] = True
                result["html"] = html
                # rotate：成功后游标前进到下一个 URL；failover：停留在成功 URL
                with self._lock:
                    if mode == "rotate":
                        self._url_cursor = (url_index + 1) % len(urls)
                    else:
                        self._url_cursor = url_index
                return result
            last_error = error
            # 非首个 URL 也失败时继续顺延
            if position == len(ordered_indexes) - 1:
                break

        result["error"] = last_error or "所有数据源网址均抓取失败"
        # failover 全部失败：游标归零从头再来
        if mode != "rotate":
            with self._lock:
                self._url_cursor = 0
        return result

    def parse_metadata(self, html: str) -> FileMetadata:
        """用 ExtractionEngine 逐条执行启用的提取规则并组装元数据。

        :param html: 网页 HTML 文本
        :return: 填充了 title/duration/release_date/video_code/actor_names 的 FileMetadata
        """
        metadata = FileMetadata()
        extracted = self.extract_fields(html)
        metadata.title = self._first_value(extracted.get("title"))
        metadata.duration = self._first_value(extracted.get("duration"))
        metadata.release_date = (
            self._first_value(extracted.get("release_date"))
            or self._first_value(extracted.get("date"))
        )
        metadata.video_code = (
            self._first_value(extracted.get("code"))
            or self._first_value(extracted.get("video_code"))
        )
        actors = extracted.get("actors")
        if isinstance(actors, list):
            metadata.actor_names = [str(a) for a in actors if a]
        elif actors:
            metadata.actor_names = [str(actors)]
        return metadata

    # ------------------------------------------------------------------
    # 规则提取（公开方法，便于上层直接获取原始提取字典）
    # ------------------------------------------------------------------
    def extract_fields(self, html: str) -> Dict[str, Any]:
        """执行所有启用的提取规则，返回 field_id -> 提取结果 字典。

        :param html: 网页 HTML 文本
        :return: 提取结果字典（单个规则失败不影响其余规则）
        """
        extracted: Dict[str, Any] = {}
        for rule in self._config.extraction_rules or []:
            if not getattr(rule, "enabled", True):
                continue
            field_id = getattr(rule, "field_id", "") or getattr(rule, "rule_id", "")
            if not field_id:
                continue
            try:
                value = ExtractionEngine.execute(rule, html or "")
            except Exception as exc:  # noqa: BLE001 - 单条规则失败不应中断整体解析
                self._log(LOG_LEVEL_WARNING,
                          f"提取规则执行失败 [{getattr(rule, 'field_name', field_id)}]",
                          f"{type(exc).__name__}: {exc}")
                continue
            if value is not None:
                extracted[field_id] = value
        return extracted

    # ------------------------------------------------------------------
    # HTTP 抓取内部实现
    # ------------------------------------------------------------------
    def _fetch_with_retry(self, url: str) -> (Optional[str], Optional[str]):
        """带重试机制的 HTTP 抓取。

        :param url: 完整请求地址
        :return: (html文本, None) 成功；(None, 错误说明) 失败
        """
        anti_crawl = self._config.anti_crawl or {}
        retry_count = max(0, int(anti_crawl.get("retry_count", 3) or 0))
        timeout = max(1, int(anti_crawl.get("timeout_seconds", 10) or 10))

        last_error = ""
        for attempt in range(retry_count + 1):
            if attempt > 0:
                # 指数退避：1s, 2s, 4s, ...（上限 30s）
                backoff = min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                              _BACKOFF_MAX_SECONDS)
                time.sleep(backoff)
            self._sleep_interval()
            html, error = self._fetch_once(url, timeout)
            if error is None:
                return html, None
            last_error = error
            # HTTP 404 属于"资源不存在"，重试没有意义，直接换 URL
            if error.startswith("HTTP错误 404"):
                break
        return None, last_error

    def _fetch_once(self, url: str, timeout: int) -> (Optional[str], Optional[str]):
        """执行单次 HTTP 请求并解码响应。

        :param url: 完整请求地址
        :param timeout: 超时秒数
        :return: (html文本, None) 成功；(None, 错误说明) 失败
        """
        headers = dict(self._config.http_headers or {})
        headers["User-Agent"] = headers.get("User-Agent") or _DEFAULT_USER_AGENT

        anti_crawl = self._config.anti_crawl or {}
        handlers = []
        if anti_crawl.get("use_proxy") and anti_crawl.get("proxy_url"):
            handlers.append(urllib.request.ProxyHandler(
                {"http": anti_crawl["proxy_url"],
                 "https": anti_crawl["proxy_url"]}))

        # 本类自行处理 gzip/deflate 解压，避免重复声明导致服务器返回 br 等
        # 无法处理的编码
        headers.pop("Accept-Encoding", None)
        headers["Accept-Encoding"] = "gzip, deflate"

        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
                content_encoding = (response.headers.get("Content-Encoding") or "").lower()
                raw = self._decompress(raw, content_encoding)
                charset = self._detect_charset(response.headers, raw)
                return raw.decode(charset, errors="replace"), None
        except urllib.error.HTTPError as exc:
            return None, f"HTTP错误 {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return None, f"连接失败: {reason}"
        except Exception as exc:  # noqa: BLE001 - 网络层异常必须全部捕获
            return None, f"请求异常: {type(exc).__name__}: {exc}"

    def _sleep_interval(self) -> None:
        """按反爬配置在请求前随机休眠。"""
        anti_crawl = self._config.anti_crawl or {}
        interval_min = max(0, int(anti_crawl.get("interval_min_ms", 0) or 0))
        interval_max = max(0, int(anti_crawl.get("interval_max_ms", 0) or 0))
        if interval_max < interval_min:
            interval_max = interval_min
        if interval_max > 0:
            time.sleep(random.uniform(interval_min, interval_max) / 1000.0)

    @staticmethod
    def _decompress(raw: bytes, content_encoding: str) -> bytes:
        """按 Content-Encoding 解压响应体。"""
        if not raw:
            return raw
        try:
            if "gzip" in content_encoding:
                return gzip.decompress(raw)
            if "deflate" in content_encoding:
                try:
                    return zlib.decompress(raw)
                except zlib.error:
                    # 少数服务器发送无 zlib 头的 deflate 数据
                    return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:  # noqa: BLE001 - 解压失败时按原始字节处理
            return raw
        return raw

    def _detect_charset(self, headers: Any, raw: bytes) -> str:
        """检测响应编码：显式配置 > Content-Type > meta标签 > 候选编码回退。"""
        # 1) 配置中显式指定编码（page_parser.encoding，"auto" 表示自动检测）
        configured = str((self._config.page_parser or {}).get("encoding", "auto"))
        if configured and configured.lower() != "auto":
            return configured
        # 2) Content-Type 响应头中的 charset
        content_type = ""
        try:
            content_type = headers.get("Content-Type", "")
        except Exception:  # noqa: BLE001 - 头部读取失败忽略
            content_type = ""
        match = re.search(r"charset=([\w\-]+)", content_type, re.IGNORECASE)
        if match:
            return match.group(1)
        # 3) HTML meta 标签中的 charset
        head = raw[:4096].decode("ascii", errors="ignore")
        match = re.search(
            r'<meta[^>]+charset=["\']?([\w\-]+)', head, re.IGNORECASE)
        if match:
            return match.group(1)
        # 4) 候选编码回退（逐个尝试，取第一个能解码的）
        for encoding in _ENCODING_CANDIDATES:
            try:
                raw.decode(encoding)
                return encoding
            except (UnicodeDecodeError, LookupError):
                continue
        return "utf-8"

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _first_value(value: Any) -> str:
        """把提取结果（str/list）归一为字符串。"""
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value) if value is not None else ""

    def _log(self, level: str, message: str, details: str = "") -> None:
        """写日志（LogManager 单例，任何异常都不影响主流程）。"""
        try:
            LogManager.get_instance().log(level, _LOG_SOURCE, message, details)
        except Exception:  # noqa: BLE001 - 日志失败不应中断爬虫流程
            pass


class CrawlerManager:
    """爬虫调度管理器（设计 13.1 节）。

    负责元数据自动采集的队列调度、worker 守护线程与单条采集完整流程：
    取自定义番号 -> search 抓取 -> parse_metadata 解析 ->
    组装 FileMetadata(sync_status='synced') -> 数据库持久化 ->
    发布 EVENT_METADATA_UPDATED 事件 -> 写日志。

    :ivar db_manager: DatabaseManager 实例
    :ivar event_bus: EventBus 实例
    :ivar _crawler_impl: BaseCrawler 具体实现（默认 HttpCrawler，可注入替换）
    :ivar _queue: 采集任务队列
    :ivar _is_running: worker 线程运行标志
    """

    def __init__(self, db_manager: Any, event_bus: Any,
                 crawler_config_manager: Optional[CrawlerConfigManager] = None,
                 custom_code_manager: Optional[CustomCodeManager] = None,
                 crawler_impl: Optional[BaseCrawler] = None) -> None:
        """初始化。

        :param db_manager: 数据库管理器实例（需有 save_file_metadata 等方法）
        :param event_bus: 事件总线实例（publish 方法）
        :param crawler_config_manager: 可选，爬虫配置管理器；为 None 时懒创建
        :param custom_code_manager: 可选，手动番号管理器；为 None 时懒创建
        :param crawler_impl: 可选，BaseCrawler 实现注入（测试/替换抓取引擎用）
        """
        self.db_manager = db_manager
        self.event_bus = event_bus
        self._crawler_config_manager = crawler_config_manager
        self._custom_code_manager = custom_code_manager
        self._crawler_impl = crawler_impl
        self._queue: "queue_module.Queue" = queue_module.Queue()
        self._is_running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._crawled_count = 0
        self._failed_count = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 懒加载的协作者
    # ------------------------------------------------------------------
    def _get_custom_code_manager(self) -> CustomCodeManager:
        """获取手动番号管理器（未注入时基于 db_manager 懒创建）。"""
        if self._custom_code_manager is None:
            self._custom_code_manager = CustomCodeManager(self.db_manager)
        return self._custom_code_manager

    def _get_crawler_config_manager(self) -> CrawlerConfigManager:
        """获取爬虫配置管理器（未注入时基于 config_manager 懒创建）。"""
        if self._crawler_config_manager is None:
            from config_manager import ConfigManager
            config_manager = ConfigManager(CONFIG_FILE)
            self._crawler_config_manager = CrawlerConfigManager(
                config_manager, self.event_bus)
        return self._crawler_config_manager

    def _get_crawler_impl(self) -> BaseCrawler:
        """获取爬虫实现（未注入时基于当前配置懒创建 HttpCrawler）。

        每次抓取前重新读取配置，保证配置面板修改即时生效。
        """
        config = self._get_crawler_config_manager().get_config()
        if self._crawler_impl is None:
            self._crawler_impl = HttpCrawler(config)
        else:
            # 同步最新配置到已注入的实现（HttpCrawler 支持热更新）
            try:
                self._crawler_impl._config = config
            except Exception:  # noqa: BLE001 - 注入实现无 _config 时跳过
                pass
        return self._crawler_impl

    # ------------------------------------------------------------------
    # 自动采集（worker 线程）
    # ------------------------------------------------------------------
    def start_auto_crawl(self) -> None:
        """启动自动采集：守护线程循环从队列取任务执行。"""
        if self._is_running:
            return
        self._is_running = True
        self._log(LOG_LEVEL_INFO, "自动采集已启动")
        self._worker_thread = threading.Thread(
            target=self._crawl_worker, name="crawler-worker", daemon=True)
        self._worker_thread.start()

    def stop_auto_crawl(self) -> None:
        """停止自动采集：置标志位，worker 线程在下一轮轮询时退出。"""
        if not self._is_running:
            return
        self._is_running = False
        self._log(LOG_LEVEL_INFO, "自动采集已停止")

    def _crawl_worker(self) -> None:
        """worker 守护线程主循环：阻塞取任务并执行单条采集。"""
        while self._is_running:
            try:
                task = self._queue.get(timeout=_WORKER_POLL_TIMEOUT)
            except queue_module.Empty:
                continue
            except Exception:  # noqa: BLE001 - 取任务异常不应杀死 worker
                continue
            try:
                file_path = task.get("file_path", "")
                video_code = task.get("video_code", "")
                self.crawl_single(file_path, video_code)
            except Exception as exc:  # noqa: BLE001 - 单任务异常不应杀死 worker
                self._log(LOG_LEVEL_ERROR, "采集任务执行异常",
                          f"{type(exc).__name__}: {exc}")
                with self._lock:
                    self._failed_count += 1
            finally:
                try:
                    self._queue.task_done()
                except Exception:  # noqa: BLE001 - 队列已完成时忽略
                    pass

    # ------------------------------------------------------------------
    # 任务入口
    # ------------------------------------------------------------------
    def add_to_queue(self, file_path: str, video_code: str) -> None:
        """向采集队列追加任务。

        :param file_path: 视频文件路径（作为持久化的 relative_path 键）
        :param video_code: 视频番号/编号
        """
        self._queue.put({"file_path": file_path, "video_code": video_code})
        self._log(LOG_LEVEL_DEBUG, f"任务已入队: {file_path} ({video_code})")

    def crawl_single(self, file_path: str, video_code: str) -> Optional[FileMetadata]:
        """单条采集完整流程。

        流程：取自定义番号（覆盖传入番号）-> search 抓取网页 ->
        parse_metadata 解析 -> 组装 FileMetadata(sync_status='synced') ->
        db_manager.save_file_metadata 持久化 ->
        event_bus.publish(EVENT_METADATA_UPDATED) -> 写日志。

        全部异常在本方法内捕获并记日志，绝不向上抛出。

        :param file_path: 视频文件路径（作为 relative_path 键）
        :param video_code: 视频番号/编号
        :return: 成功返回 FileMetadata；失败返回 None
        """
        try:
            # 1) 取自定义番号（手动番号覆盖识别番号）
            effective_code = video_code
            try:
                custom_code = self._get_custom_code_manager().get_custom_code(file_path)
                if custom_code:
                    effective_code = custom_code
            except Exception as exc:  # noqa: BLE001 - 番号查询失败不阻断采集
                self._log(LOG_LEVEL_WARNING,
                          f"查询手动番号失败，使用原番号 {video_code}",
                          f"{type(exc).__name__}: {exc}")

            if not effective_code:
                self._log(LOG_LEVEL_WARNING, f"番号为空，跳过采集: {file_path}")
                with self._lock:
                    self._failed_count += 1
                return None

            # 2) 抓取网页
            crawler = self._get_crawler_impl()
            search_result = crawler.search(effective_code)
            if not search_result.get("success"):
                error = search_result.get("error", "未知错误")
                self._log(LOG_LEVEL_ERROR,
                          f"抓取失败 [{effective_code}]",
                          f"{error}\nURL: {search_result.get('url', '')}")
                with self._lock:
                    self._failed_count += 1
                return None

            # 3) 解析元数据
            metadata = crawler.parse_metadata(search_result.get("html", ""))
            if not isinstance(metadata, FileMetadata):
                metadata = FileMetadata()

            # 4) 组装完整 FileMetadata
            relative_path = (file_path or "").strip()
            metadata.relative_path = relative_path
            metadata.file_name = os.path.basename(relative_path)
            metadata.sync_status = "synced"
            if not metadata.video_code:
                metadata.video_code = effective_code

            # 5) 数据库持久化
            try:
                if self.db_manager is not None:
                    self.db_manager.save_file_metadata(relative_path, metadata)
            except Exception as exc:  # noqa: BLE001 - 持久化失败仍发事件
                self._log(LOG_LEVEL_ERROR,
                          f"元数据持久化失败: {relative_path}",
                          f"{type(exc).__name__}: {exc}")

            # 6) 发布元数据更新事件
            try:
                if self.event_bus is not None:
                    self.event_bus.publish(
                        EVENT_METADATA_UPDATED,
                        relative_path=relative_path,
                        metadata=metadata,
                    )
            except Exception as exc:  # noqa: BLE001 - 事件失败不影响主流程
                self._log(LOG_LEVEL_WARNING, "发布元数据更新事件失败",
                          f"{type(exc).__name__}: {exc}")

            with self._lock:
                self._crawled_count += 1
            self._log(LOG_LEVEL_INFO,
                      f"采集成功: {relative_path}",
                      f"番号: {effective_code} | 标题: {metadata.title or '无'} | "
                      f"来源: {search_result.get('url', '')}")
            return metadata
        except Exception as exc:  # noqa: BLE001 - 最后一道防线
            self._log(LOG_LEVEL_ERROR, f"单条采集异常: {file_path}",
                      f"{type(exc).__name__}: {exc}")
            with self._lock:
                self._failed_count += 1
            return None

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def get_crawl_status(self) -> dict:
        """获取采集状态。

        :return: {"is_running": bool, "queue_size": int,
                  "crawled": int, "failed": int}
        """
        with self._lock:
            crawled = self._crawled_count
            failed = self._failed_count
        return {
            "is_running": self._is_running,
            "queue_size": self._queue.qsize(),
            "crawled": crawled,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # 日志辅助
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str, details: str = "") -> None:
        """写日志（LogManager 单例，任何异常都不影响主流程）。"""
        try:
            LogManager.get_instance().log(level, _LOG_SOURCE, message, details)
        except Exception:  # noqa: BLE001 - 日志失败不应中断采集流程
            pass
