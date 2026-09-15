# AGENTS.md - 媒体库浏览器项目指南

## 项目概述

这是一个基于 Python + tkinter 开发的 Windows 桌面应用程序，用于浏览和管理本地视频及图片资源。程序采用图库浏览器形式，支持通过文件夹树导航、缩略图预览、视频播放、标签管理和演员管理等功能。

**主要用途**：管理本地视频收藏，支持视频与缩略图关联、演员信息维护、跨设备书签同步等功能。

## 技术栈

- **编程语言**: Python 3
- **GUI 框架**: tkinter (Python 标准库)
- **数据库**: SQLite3 (media_library.db)
- **图像处理**: Pillow (PIL)
- **可选依赖**: 
  - `pypinyin` - 中文拼音转换（演员排序）
  - `pykakasi` - 日文罗马音转换
  - `beautifulsoup4` - HTML解析（爬虫模块）
  - `playwright` - 浏览器渲染（爬虫模块）
  - `selenium` - 浏览器自动化（爬虫模块）

## 项目结构

```
项目根目录/
├── launcher.py           # 程序启动器，自动检查并初始化数据库
├── main.py              # 主程序（约3300行），包含主窗口和各类对话框
├── core.py              # 核心模块：配置管理、图片查看器、视频播放器、工具函数
├── config_manager.py    # 扩展配置管理器（设备分组、跨设备标签）
├── database_manager.py  # 数据库管理器（性能优化版，支持异步缓存）
├── init_database.py     # 数据库初始化和升级脚本
├── gallery_loader.py    # 图库加载器（文件夹扫描、视频-图片匹配、缓存管理）
├── gallery_viewer.py    # 图库查看器（缩略图展示、鼠标滚轮、右键菜单）
├── bookmark_manager.py  # 书签管理器（支持设备分组树形结构）
├── tree_explorer.py     # 资源管理器（文件夹树形浏览）
├── tag_manager.py       # 标签管理模块（跨设备标签存储）
├── actor_manager.py     # 演员管理模块（支持拼音排序、文件夹关联）
├── video_relation_manager.py  # 视频关联管理器（单图关联+批量扫描）
├── crawler_core.py      # 爬虫核心模块（独立，可被主程序或其他工具导入）
├── crawler_gui.py       # 爬虫配置GUI（可独立运行）
├── crawler_manager.py   # 爬虫管理兼容层（供主程序使用，调用core）
├── ui_components.py     # UI组件模块（ToolTip等通用组件）
├── config.json          # 程序配置文件（JSON格式）
├── media_library.db     # SQLite数据库文件
└── data/                # 数据目录
```

## 模块职责

### 1. main.py
- 程序主入口，包含 `main()` 函数
- 主窗口类 `ImageBrowserApp`，整合所有模块
- 各类对话框：设置、路径替换等
- 视频关联相关方法委托给 `RelationManager`

### 2. core.py
- `ConfigManager`: 基础配置管理（JSON读写）
- `ImageViewer`: 图片查看器（支持缩放、拖拽）
- `VideoPlayer`: 视频播放器封装（调用外部播放器如PotPlayer）
- `DebugUtils`: 调试工具类
- `PathUtils`: 路径工具函数
- `PatternMatcher`: 番号/编号提取器
- `FolderCacheManager`: 文件夹缓存管理器

### 3. config_manager.py
- `ExtendedConfigManager`: 扩展配置管理器
- 支持设备分组（`devices` 结构）
- 跨设备标签存储（`tags_v2` 结构）
- 书签状态管理（`bookmark_states`）
- 相对路径标签存储

### 4. database_manager.py
- `DatabaseManager`: SQLite数据库管理器
- 支持快速启动模式（fast_mode）
- 异步缓存加载，不阻塞主线程
- WAL模式优化并发性能
- 内存缓存：folders, files, actors, file_actors, bookmarks, video_relations

### 5. init_database.py
- 数据库初始化和结构升级脚本
- **支持重复运行**，自动检查和创建缺失的表/字段
- 命令行参数：`--force`（强制重建）、`--check`（仅检查）
- 创建的表：
  - `folders` - 文件夹信息
  - `actors` - 演员信息
  - `actor_relations` - 演员别名关系
  - `actor_folders` - 演员文件夹映射
  - `media_files` - 媒体文件（视频/图片）
  - `file_actors` - 文件-演员关联
  - `video_relations` - 视频主从关联关系

### 6. 图库模块 (gallery_loader.py + gallery_viewer.py)

图库模块采用**数据层与表现层分离**的设计，由两个文件协同工作：

#### gallery_loader.py - 数据加载层
- `GalleryLoader`: 图库加载器类
- 职责：文件夹扫描、视频-图片匹配逻辑、缓存管理
- 核心方法：
  - `load_gallery()` - 线程安全的加载入口
  - `_scan_folder()` - 扫描文件夹，处理匹配逻辑
  - `_filter_files()` - 文件去重和过滤
- **匹配逻辑**：
  1. 完全同名匹配（`video.mp4` + `video.jpg`）
  2. 附加缩略图识别（`video_1.jpg`）
  3. 通用封面识别（cover/folder/poster/thumb/preview/default）
  4. 关联视频标记处理（数据库 `video_relations` 表）

#### gallery_viewer.py - UI表现层
- `GalleryViewer`: 图库查看器类（900+行）
- 职责：缩略图渲染、用户交互、右键菜单
- 核心功能：
  - 缩略图网格展示（Canvas + Frame）
  - 鼠标滚轮滚动支持（递归绑定）
  - 右键菜单（播放、关联、标签、缩略图替换等）
  - 视觉标识（视频、仅图片、关联视频、封面匹配等状态）
  - 批量渲染优化（分批加载避免UI卡顿）
- **颜色配置**: 所有状态背景色从 `config.gallery_colors` 读取，支持通过设置菜单自定义

#### 协作关系
```
┌─────────────────┐     调用      ┌─────────────────┐
│  GalleryLoader  │ ───────────> │  GalleryViewer  │
│   (数据加载层)   │  load_items() │   (UI表现层)     │
└─────────────────┘              └─────────────────┘
        │                                │
        ▼                                ▼
   文件夹扫描                        缩略图渲染
   视频-图片匹配                     用户交互
   缓存管理                          状态反馈
```

#### 设计原则
- **单一职责**：Loader 专注数据获取，Viewer 专注UI渲染
- **低耦合**：Loader 调用 Viewer 的公共方法，不依赖内部实现
- **可复用性**：Loader 可在后台扫描等场景独立使用
- **维护性**：分离后便于单独测试文件扫描逻辑

### 7. bookmark_manager.py
- `BookmarkManager`: 书签管理器
- 树形结构展示，支持设备分组
- 当前设备置顶显示
- 支持自定义显示名称

### 8. tree_explorer.py
- `TreeExplorer`: 文件夹树形资源管理器
- 延迟加载子文件夹
- 状态保存和恢复（展开节点、选中节点、滚动位置）

### 9. tag_manager.py
- `TagManager`: 标签管理器
- 跨设备标签存储（tags_v2结构）
- 标签排序功能（上移、下移、置顶、置底）
- 已标记视频列表展示

### 10. actor_manager.py
- `ActorManager`: 演员管理器
- 演员文件夹扫描和管理
- 拼音排序支持（依赖pypinyin）
- 配置持久化（优先从config.json加载）

### 11. video_relation_manager.py
- `VideoRelationDialog`: 单张图片关联对话框
  - 基于番号搜索候选视频
  - 支持手动浏览选择主视频
- `RelationManager`: 批量关联管理器
  - 异步扫描关联建议
  - 批量管理视频关联关系
  - 支持动态Tooltip和文件夹预览

### 12. 爬虫模块 (crawler_core.py + crawler_gui.py + crawler_manager.py)

爬虫模块采用**核心+界面分离**的架构，支持独立运行和主程序集成两种模式。

#### crawler_core.py - 爬虫核心模块

**文件说明**：完全独立的爬虫功能核心，不依赖 tkinter 或主程序，可被任何 Python 脚本导入使用。

**主要类：**

| 类名 | 功能说明 |
|------|---------|
| `CrawlResult` | 爬取结果数据类（dataclass），包含完整爬取结果信息 |
| `ExtractRule` | 提取规则数据类，定义单个字段的提取规则 |
| `CrawlerConfig` | 爬虫配置数据类，管理网址、间隔、规则等配置 |
| `WebCrawler` | 网络爬虫核心，处理 HTTP 请求、HTML 解析、重试机制 |

**核心功能：**
- 异步 HTTP 请求（支持 HEAD/GET）
- 随机请求间隔（防爬）
- User-Agent 轮换
- 指数退避重试机制
- SSL 证书忽略（兼容性）
- 智能编码检测（支持UTF-8/GBK/GB2312等）
- 响应解压（gzip/deflate）
- 浏览器渲染支持（Playwright/Selenium）
- 多种提取规则：snippet代码段、CSS选择器、正则表达式、自定义Python代码

**使用示例（外部调用）：**

```python
from crawler_core import CrawlerConfig, WebCrawler, CrawlResult

# 方式1: 同步调用（简单场景）
config = CrawlerConfig('config.json')
crawler = WebCrawler(config)

# 爬取单个视频信息
result = crawler.crawl('ABC-123')  # 传入番号，自动拼接URL
if result.success:
    print(f"番号: {result.data.get('code')}")
    print(f"日期: {result.data.get('date')}")
    print(f"演员: {result.data.get('actors')}")  # 列表
    print(f"标题: {result.data.get('title')}")

# 方式2: 异步调用（不阻塞UI）
def on_result(result: CrawlResult):
    if result.success:
        print(f"爬取成功: {result.data}")
    else:
        print(f"爬取失败: {result.error}")

crawler.crawl('ABC-123', callback=on_result)  # 后台线程执行
```

**CrawlResult 数据结构：**

```python
@dataclass
class CrawlResult:
    success: bool                    # 爬取是否成功
    data: Dict[str, Any]            # 提取的数据字典（字段ID -> 值）
    source_url: str                 # 实际请求的URL
    crawl_time: str                 # 爬取时间（格式：YYYY-MM-DD HH:MM:SS）
    error: str                      # 错误信息（失败时）
    raw_html: str                   # 原始HTML内容
    extracted_content: str          # 按CSS选择器提取的内容
```

**ExtractRule 提取规则结构：**

```python
@dataclass
class ExtractRule:
    field_id: str                   # 字段标识（如 'code', 'date'）
    field_name: str                 # 字段显示名称（如 '📌 番号'）
    rule_type: str                  # 规则类型：snippet/css/regex/custom
    rule_content: str               # 规则内容（代码段/选择器/正则/代码）
    placeholder: str                # 占位符（如 '{番号}'，用于snippet类型）
    extract_text: bool              # 是否提取纯文本（仅CSS类型有效）
    multiple: bool                  # 是否返回列表（如多个演员）
    enabled: bool                   # 是否启用此规则
```

**WebCrawler 核心方法：**

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__(config)` | `CrawlerConfig` | - | 初始化爬虫实例 |
| `crawl(code, callback)` | code: str, callback: Callable | `CrawlResult`/`None` | 爬取入口，有callback则异步执行 |
| `fetch_page(code, url_index)` | code: str, url_index: int(可选) | `(success, html, url, msg, extracted)` | 获取网页HTML |
| `parse_info(html)` | html: str | `Dict[str, Any]` | 按配置规则解析HTML |
| `test_url(url)` | url: str | `(bool, str)` | 测试网址连接 |
| `stop()` | - | - | 停止爬取（设置标志位） |
| `close_browser()` | - | - | 关闭浏览器驱动（Selenium） |

**爬虫执行流程（外部调用时）：**

```
1. 传入视频番号
   ↓
2. CrawlerConfig.urls 中选择一个URL模板
   ↓
3. 替换占位符（如 https://example.com/{番号} → https://example.com/ABC-123）
   ↓
4. 发送HTTP请求（带随机间隔、User-Agent轮换、重试机制）
   ↓
5. 获取HTML响应（自动解压、编码检测）
   ↓
6. 按配置规则提取信息
   - snippet: HTML代码段+占位符匹配
   - css: CSS选择器提取
   - regex: 正则表达式匹配
   - custom: 执行自定义Python代码
   ↓
7. 返回 CrawlResult 数据
```

---

#### crawler_gui.py - 独立配置工具

**文件说明**：可独立运行的可视化爬虫配置工具，不依赖主程序，但配置保存到主程序的 `config.json` 中。

**运行方式：**
```bash
# 默认使用同目录下的 config.json
python crawler_gui.py

# 或指定配置文件路径
python crawler_gui.py --config D:\path\to\config.json
```

**主要类：**

| 类名 | 功能说明 |
|------|---------|
| `CrawlerConfigApp` | 独立的 Tkinter 应用程序，完整的配置界面 |
| `SharedCrawlerConfig` | 共享配置适配器，读写主程序的 config.json |

**界面布局（双栏式）：**

| 面板 | 功能 |
|------|------|
| **左侧：爬虫设置** | 网址库、基本设置（替换符、模式、间隔）、反爬策略（超时、重试、代理）、请求头配置、动态规则配置、运行日志 |
| **右侧：网页源码分析** | 测试番号输入、获取网页按钮、原始HTML显示（带语法高亮）、解析结果预览 |

**配置流程：**
1. 在左侧添加目标网址（支持 `{番号}` 占位符）
2. 在中间输入测试番号，点击"获取网页"
3. 在HTML显示区选中包含目标信息的代码段
4. 在左侧配置提取规则（支持4种类型）
5. 点击"测试解析"验证配置
6. 点击"保存配置"保存到 config.json

---

#### crawler_manager.py - 主程序兼容层

**文件说明**：供主程序导入使用，提供与旧版兼容的接口，内部调用 `crawler_core`。

**主要类：**

| 类名 | 功能说明 |
|------|---------|
| `CrawlerManager` | 主程序 Tab 页 UI（与独立版界面相同，但嵌入主程序） |
| `CrawlerConfigAdapter` | 适配主程序的 ConfigManager，读写 config.json 的 crawler 字段 |
| `CrawlerInterface` | **供主程序其他菜单调用的核心接口** |

**在主程序中使用：**

```python
# 导入
from crawler_manager import CrawlerManager, CrawlerInterface

# 在 __init__ 中初始化
self.crawler_manager = None

# 在标签切换事件中延迟初始化
def _on_tab_changed(self, event):
    tab_text = self.notebook.tab(current_tab, "text")
    if tab_text == "爬虫配置":
        self._init_crawler_manager()

def _init_crawler_manager(self):
    if self.crawler_manager is None:
        self.crawler_manager = CrawlerManager(
            self.crawler_tab, self.config, self.debug
        )
```

**在其他菜单/功能中调用爬取（重点）：**

```python
from crawler_manager import CrawlerInterface

# 创建接口实例
crawler = CrawlerInterface(self.config, self.debug)

# 方式1: 异步爬取（推荐，不阻塞UI）
def on_result(success, info):
    if success:
        print(f"番号: {info['code']}")
        print(f"日期: {info['date']}")
        print(f"演员: {info['actors']}")  # 演员是列表
        print(f"标题: {info['title']}")
        print(f"来源URL: {info['source_url']}")
        print(f"爬取时间: {info['crawl_time']}")
    else:
        print(f"失败: {info['error']}")

crawler.crawl_video_info('ABC-123', callback=on_result)

# 方式2: 需要停止爬取时
crawler.stop()
```

**CrawlerInterface.crawl_video_info 方法详解：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | str | 是 | 视频番号/编号（如 'ABC-123'） |
| `callback` | Callable | 否 | 回调函数，签名：`callback(success: bool, info: dict)` |

**回调函数 info 字典结构：**

```python
{
    'code': str,          # 番号
    'date': str,          # 发行日期
    'actors': List[str],  # 演员列表
    'title': str,         # 视频标题
    'source_url': str,    # 来源网址
    'crawl_time': str,    # 爬取时间
    'error': str,         # 错误信息（失败时）
    'raw_html': str       # 原始HTML（调试用）
}
```

---

#### 爬虫配置格式 (config.json)

```json
{
  "crawler": {
    "urls": [
      "https://example1.com/{番号}",
      "https://example2.com/search?code={番号}"
    ],
    "interval_min": 1000,
    "interval_max": 3000,
    "placeholder": "{番号}",
    "crawl_mode": "round_robin",
    "request_method": "GET",
    "post_data": "",
    "timeout": 10,
    "retry_times": 3,
    "use_proxy": false,
    "proxy_url": "",
    "headers": {
      "Accept": "text/html,...",
      "Accept-Language": "zh-CN,zh;q=0.9"
    },
    "extract_rules": [
      {
        "field_id": "code",
        "field_name": "📌 番号",
        "rule_type": "snippet",
        "rule_content": "<span>{番号}</span>",
        "placeholder": "{番号}",
        "extract_text": true,
        "multiple": false,
        "enabled": true
      },
      {
        "field_id": "date",
        "field_name": "📅 日期",
        "rule_type": "css",
        "rule_content": ".release-date",
        "placeholder": "",
        "extract_text": true,
        "multiple": false,
        "enabled": true
      },
      {
        "field_id": "actors",
        "field_name": "👥 演员",
        "rule_type": "css",
        "rule_content": "span.genre a",
        "placeholder": "",
        "extract_text": true,
        "multiple": true,
        "enabled": true
      },
      {
        "field_id": "title",
        "field_name": "📖 标题",
        "rule_type": "css",
        "rule_content": "h1.title",
        "placeholder": "",
        "extract_text": true,
        "multiple": false,
        "enabled": true
      }
    ],
    "css_selector": "body",
    "show_full_html": true,
    "encoding": "auto",
    "render_js": false,
    "render_engine": "playwright",
    "browser_type": "chromium",
    "headless": true,
    "browser_timeout": 30,
    "wait_for_selector": "",
    "window_size": "1920,1080",
    "block_images": true
  }
}
```

---

#### 反爬策略

| 策略 | 说明 | 配置位置 |
|------|------|---------|
| 随机间隔 | 每次请求前等待 1-3 秒（可配置） | interval_min / interval_max |
| User-Agent 轮换 | 内置常见 UA，支持自定义 | headers.User-Agent |
| 指数退避重试 | 失败后 2^n 秒重试 | retry_times |
| 代理服务器 | 支持 HTTP/HTTPS 代理 | use_proxy / proxy_url |
| SSL 忽略 | 兼容各种证书环境 | 自动启用 |
| 多网址轮流 | 多个网址轮流使用，分散压力 | crawl_mode: round_robin |
| 浏览器渲染 | 使用 Playwright/Selenium 模拟真实浏览器 | render_js / render_engine |

---

#### 提取规则类型详解

**1. snippet（代码段+占位符）**

适用于：从HTML中复制包含目标值的完整代码段

```html
<!-- 原始HTML -->
<h1 class="title">ABC-123</h1>

<!-- 配置 -->
规则内容: <h1 class="title">{番号}</h1>
占位符: {番号}

<!-- 实际提取 -->
结果: ABC-123
```

**2. css（CSS选择器）**

适用于：使用BeautifulSoup的CSS选择器语法

```python
# 配置示例
规则内容: span.genre a
提取纯文本: true
多值: true

# 等效代码
soup.select('span.genre a')  # 返回列表
[a.get_text(strip=True) for a in elements]
```

**3. regex（正则表达式）**

适用于：复杂文本匹配

```python
# 配置示例
规则内容: <title>(.*?)</title>
多值: false

# 匹配结果取第一个捕获组
```

**4. custom（自定义Python代码）**

适用于：复杂逻辑处理

```python
# 可用变量：
# - html: 原始HTML字符串
# - soup: BeautifulSoup对象（已初始化）
# - re: 正则表达式模块

# 示例：提取标题
result = soup.select_one('h1').get_text(strip=True)

# 示例：提取所有演员（多值）
result = [a.get_text(strip=True) for a in soup.select('span.genre a')]

# 示例：正则提取日期
match = re.search(r'(\d{4}-\d{2}-\d{2})', html)
result = match.group(1) if match else ""

# 必须将结果赋值给 result 变量
```

### 13. ui_components.py
- `ToolTip`: 简易Tooltip类（支持动态更新）
- `DynamicToolTip`: 动态Tooltip（适用于Treeview等）

## 配置文件 (config.json)

```json
{
  "player_path": "外部视频播放器路径",
  "viewer_geometry": "窗口几何尺寸",
  "thumbnail_size": "缩略图大小（小/中/大/最大）",
  "debug_mode": true/false,
  "devices": {
    "设备ID": {
      "display_name": "显示名称",
      "bookmarks": [{"path": "...", "display_name": "...", "id": "..."}]
    }
  },
  "tags_v2": {
    "设备ID": {
      "书签ID": {
        "相对路径": ["标签1", "标签2"]
      }
    }
  },
  "bookmark_states": {
    "书签ID": {
      "last_folder": "最后浏览文件夹",
      "tree_state": {...},
      "gallery_scroll_y": 滚动位置
    }
  },
  "gallery_colors": {
    "linked_video": "#FFE4B5",
    "video_placeholder": "#2C3E50",
    "dedicated_match": "#D4EDDA",
    "cover_match": "#D1ECF1",
    "normal_video": "#D6EAF8",
    "image_only": "white",
    "selected": "#f5a2a2"
  },
  "crawler": {
    "urls": [
      "https://example1.com/{番号}",
      "https://example2.com/{番号}"
    ],
    "interval_min": 1000,
    "interval_max": 3000,
    "placeholder": "{番号}",
    "crawl_mode": "round_robin",
    "extract_rules": [...],
    "headers": {...},
    "render_js": false,
    "render_engine": "playwright"
  }
}
```

## 数据库结构

### 核心表

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `folders` | 文件夹信息 | folder_path, folder_name, is_bookmark |
| `media_files` | 媒体文件 | file_path, file_name, file_type(video/image), is_alias, file_size, release_date, video_title, duration |
| `actors` | 演员信息 | actor_name, actor_name_cn, sort_char, avatar_url, sort_order |
| `file_actors` | 文件-演员关联 | file_id, actor_id |
| `video_relations` | 视频主从关联 | primary_file_id, alias_file_id, alias_folder_path |
| `actor_relations` | 演员别名关系 | actor_id, related_actor_id, relation_type |

### 详细字段说明

#### 1. folders 表 - 文件夹信息

| 字段名 | 类型 | 说明 |
|--------|------|------|
| folder_id | INTEGER | 主键，自增ID |
| folder_path | TEXT | 文件夹完整路径（唯一） |
| folder_name | TEXT | 文件夹名称 |
| parent_path | TEXT | 父文件夹路径 |
| is_bookmark | INTEGER | 是否为书签（0=否，1=是） |
| is_deleted | INTEGER | 是否已删除标记（软删除） |
| last_modified | TIMESTAMP | 最后修改时间 |
| scan_time | TIMESTAMP | 扫描时间（默认CURRENT_TIMESTAMP） |
| file_count | INTEGER | 文件数量（默认0） |
| device_id | TEXT | 所在设备的ID |
| bookmark_id | TEXT | 所在书签的ID |


#### 2. actors 表 - 演员信息

| 字段名 | 类型 | 说明 |
|--------|------|------|
| actor_id | INTEGER | 主键，自增ID |
| actor_name | TEXT | 演员名称（必填） |
| actor_name_cn | TEXT | 演员中文名（别名） |
| sort_char | TEXT | 排序字符（拼音首字母等） |
| avatar_url | TEXT | 头像URL地址 |
| sort_order | INTEGER | 自定义排序顺序（默认0） |
| created_date | TIMESTAMP | 创建时间（默认CURRENT_TIMESTAMP） |
| updated_date | TIMESTAMP | 更新时间（默认CURRENT_TIMESTAMP） |

#### 3. actor_relations 表 - 演员别名关系

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键，自增ID |
| actor_id | INTEGER | 主演员ID（外键→actors） |
| related_actor_id | INTEGER | 关联演员ID（外键→actors） |
| relation_type | TEXT | 关系类型（默认'alias'别名） |
| created_date | TIMESTAMP | 创建时间（默认CURRENT_TIMESTAMP） |

#### 4. actor_folders 表 - 演员文件夹映射

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键，自增ID |
| folder_path | TEXT | 文件夹路径（唯一） |
| folder_name | TEXT | 文件夹名称 |
| is_active | INTEGER | 是否激活（默认1） |
| created_date | TIMESTAMP | 创建时间（默认CURRENT_TIMESTAMP） |
| last_scan | TIMESTAMP | 最后扫描时间 |

#### 5. media_files 表 - 媒体文件

| 字段名 | 类型 | 说明 |
|--------|------|------|
| file_id | INTEGER | 主键，自增ID |
| folder_id | INTEGER | 所属文件夹ID（外键→folders） |
| file_path | TEXT | 文件完整路径（唯一） |
| file_name | TEXT | 文件名 |
| file_type | TEXT | 文件类型（'video'=视频, 'image'=图片） |
| file_size | INTEGER | 文件大小（字节，默认0） |
| release_date | DATE | 发行日期 |
| video_title | TEXT | 视频标题 |
| duration | INTEGER | 视频时长（秒，默认0） |
| last_modified | TIMESTAMP | 最后修改时间 |
| is_deleted | INTEGER | 是否已删除标记（软删除，默认0） |
| is_alias | INTEGER | 是否为关联文件（默认0） |
| tag_summary | TEXT | 标签摘要（JSON或逗号分隔） |
| video_code | TEXT | 视频番号/编号（18位以下文本，如 ABC-123） |

#### 6. file_actors 表 - 文件-演员关联

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键，自增ID |
| file_id | INTEGER | 文件ID（外键→media_files，级联删除） |
| actor_id | INTEGER | 演员ID（外键→actors，级联删除） |
| bind_time | TIMESTAMP | 绑定时间（默认CURRENT_TIMESTAMP） |

#### 7. video_relations 表 - 视频主从关联关系

| 字段名 | 类型 | 说明 |
|--------|------|------|
| relation_id | INTEGER | 主键，自增ID |
| primary_file_id | INTEGER | 主视频文件ID（外键→media_files） |
| alias_file_id | INTEGER | 关联视频/图片文件ID（外键，唯一） |
| alias_folder_path | TEXT | 关联文件所在文件夹路径（冗余存储） |
| created_date | TIMESTAMP | 创建时间（默认CURRENT_TIMESTAMP） |
| updated_date | TIMESTAMP | 更新时间（默认CURRENT_TIMESTAMP） |

**说明**：
- `primary_file_id`：实际的视频文件
- `alias_file_id`：关联的图片或其他表示形式（如封面图）
- 用途：实现图片文件夹与视频文件的跨文件夹关联

### 索引列表

| 索引名 | 所属表 | 字段 | 用途 |
|--------|--------|------|------|
| idx_folder_path | folders | folder_path | 按路径快速查找文件夹 |
| idx_parent | folders | parent_path | 按父路径查找子文件夹 |
| idx_actor_name | actors | actor_name | 按名称查找演员 |
| idx_actor_name_cn | actors | actor_name_cn | 按中文名查找演员 |
| idx_actor_relation | actor_relations | actor_id | 查找演员关联关系 |
| idx_related_actor | actor_relations | related_actor_id | 反向查找关联 |
| idx_file_path | media_files | file_path | 按路径查找文件 |
| idx_folder | media_files | folder_id | 查找文件夹内文件 |
| idx_type | media_files | file_type | 按类型筛选文件 |
| idx_release_date | media_files | release_date | 按日期排序/筛选 |
| idx_is_alias | media_files | is_alias | 筛选关联文件 |
| idx_file_actor | file_actors | file_id, actor_id | 文件演员联合查询 |
| idx_actor_file | file_actors | actor_id | 查找演员的所有文件 |
| idx_video_relation_primary | video_relations | primary_file_id | 查找主视频的所有关联 |
| idx_video_relation_alias | video_relations | alias_file_id | 反向查找关联 |
| idx_video_relation_folder | video_relations | alias_folder_path | 按文件夹查找关联 |

## 启动流程

1. **launcher.py** 检查数据库文件是否存在
2. 如不存在，自动运行 **init_database.py** 创建数据库
3. 导入并运行 **main.py** 的 `main()` 函数
4. 主程序初始化各模块，加载配置和缓存

## 模块依赖关系

```
main.py
├── core.py (ConfigManager, ImageViewer, VideoPlayer, DebugUtils, PathUtils)
├── config_manager.py (ExtendedConfigManager)
├── ui_components.py (ToolTip, DynamicToolTip)
├── database_manager.py (DatabaseManager)
├── bookmark_manager.py (BookmarkManager)
├── tree_explorer.py (TreeExplorer)
├── gallery_loader.py (GalleryLoader) ──> gallery_viewer.py
├── gallery_viewer.py (GalleryViewer)
├── tag_manager.py (TagManager)
├── actor_manager.py (ActorManager)
├── video_relation_manager.py (RelationManager, VideoRelationDialog)
└── crawler_manager.py (CrawlerManager, WebCrawler, CrawlerConfig)
```

### 关键依赖说明

| 模块 | 依赖 | 说明 |
|------|------|------|
| `gallery_loader.py` | `core.PathUtils`, `FolderCacheManager` | 文件类型判断和缓存管理 |
| `gallery_viewer.py` | `core.ImageViewer`, `VideoPlayer` | 图片查看和视频播放 |
| `gallery_loader.py` | `database_manager.DatabaseManager` | 查询关联视频信息 |
| `actor_manager.py` | `pypinyin`, `pykakasi` | 中文拼音/日文罗马音排序 |
| `crawler_manager.py` | `crawler_core.CrawlerConfig`, `WebCrawler` | 爬虫功能核心 |
| `crawler_gui.py` | `crawler_core` | 独立配置工具依赖核心 |

## 开发规范

### 代码风格
- 文件编码：`# -*- coding: utf-8 -*-`
- 使用 4 空格缩进
- 类名使用 PascalCase（如 `GalleryViewer`）
- 方法名使用 snake_case（如 `create_thumbnail`）
- 私有方法以 `_` 开头

### 注释规范
- 模块级文档字符串说明模块功能
- 类级文档字符串说明类职责
- 关键方法添加参数和返回值说明
- 复杂逻辑添加行内注释

### 数据库修改规范
- **所有数据库结构修改必须在 `init_database.py` 中完成**
- 使用 `table_exists()` 和 `get_table_info()` 检查现有结构
- 使用 `ALTER TABLE ADD COLUMN` 添加新字段
- 保持向后兼容，支持重复运行

### 配置管理规范
- 新配置项需要在 `ConfigManager.DEFAULT_CONFIG` 中设置默认值
- 设备相关配置使用 `devices` 和 `tags_v2` 结构
- 配置修改后必须调用 `save_config()` 写入文件
- **颜色配置**: 图库浏览器颜色统一在 `gallery_colors` 中管理，通过颜色设置对话框修改，不再硬编码

### 代码重构规范
- **功能单一职责**：每个模块/类只负责一类功能
- **避免代码重复**：提取通用组件到独立模块（如 `ui_components.py`）
- **配置与逻辑分离**：配置管理独立为模块（如 `config_manager.py`）
- **合理拆分大文件**：当文件超过800行时考虑拆分

## 关键功能说明

### 1. 视频-缩略图关联系统
- 支持将图片关联到视频（图片文件夹无视频时）
- 通过番号/编号匹配自动建议关联
- 关联关系存储在 `video_relations` 表
- 图库中显示"关联视频"标识

### 2. 番号/编号提取
- 从文件名提取视频番号（如 ABC-123）
- 支持多种格式：XXX-NNN、XXXNNN、XX-NNN 等
- 用于视频关联建议和在线信息爬取

### 3. 跨设备同步
- 设备ID基于计算机名（`platform.node()`）
- 每个设备独立存储书签和标签
- 配置文件中 `devices` 字段存储多设备数据

## 性能优化与死锁修复

### 数据库缓存加载优化

**问题描述**：
- 程序启动时，数据库缓存是异步加载的（后台线程）
- 如果主线程在缓存加载完成前查询数据库，会尝试获取 `_cache_lock`
- 如果此时后台线程持有锁，主线程会被阻塞，导致UI卡死

**修复方案**：

1. **缓存加载状态检查** (`main.py`):
   ```python
   def _on_db_ui_update(self):
       # 检查数据库缓存是否加载完成
       if self.db and hasattr(self.db, 'is_cache_ready') and not self.db.is_cache_ready:
           self.status_label.config(text="等待缓存加载...", foreground="blue")
           self.root.after(100, self._on_db_ui_update)  # 延迟重试
           return
   ```

2. **后台线程执行耗时操作** (`main.py`):
   - `_load_gallery_realtime_and_cache()` 现在创建后台线程执行实际扫描
   - UI更新通过 `root.after(0, ...)` 回到主线程执行

3. **GalleryLoader 异步化** (`gallery_loader.py`):
   - `load_gallery()` 创建后台线程执行加载
   - 避免阻塞UI主线程

### 关键优化点

| 优化项 | 文件 | 说明 |
|--------|------|------|
| 缓存状态检查 | `main.py` | `_on_db_ui_update` 检查 `is_cache_ready` |
| 异步文件夹扫描 | `main.py` | `_load_gallery_realtime_and_cache` 后台线程执行 |
| 异步图库加载 | `gallery_loader.py` | `load_gallery` 后台线程执行 |
| 锁超时处理 | `database_manager.py` | 使用 `RLock` 避免死锁 |

## 关键功能说明

### 4. 缩略图展示规则

#### 匹配优先级（由高到低）
1. **完全同名匹配**：`video.mp4` + `video.jpg` → 图片作为视频缩略图
2. **附加缩略图**：`video_1.jpg`, `video_2.jpg` → 同一视频的附加缩略图（可切换）
3. **通用封面**：cover.jpg / folder.jpg / poster.jpg / thumb.jpg 等 → 作为文件夹内所有视频的默认封面
4. **关联视频**：数据库 `video_relations` 表记录的跨文件夹关联

#### 展示状态标识
| 状态 | 背景色 | 标签文字 | 说明 |
|------|--------|----------|------|
| 专属视频 | 浅绿色 | 🎬 专属视频 | 图片与视频完全同名匹配 |
| 封面匹配 | 浅蓝色 | 📁 封面匹配 | 使用通用封面图片 |
| 关联视频 | 浅橙色 | 🔗 关联视频 | 图片关联到其他位置的视频 |
| 仅图片 | 白色 | 🖼️ 仅图片 | 无对应视频的独立图片 |
| 视频占位 | 深蓝灰 | 🎬 视频文件 | 无缩略图的视频 |

## 调试和测试

### 调试模式
- 在设置中开启 `debug_mode: true`
- 调试信息输出到控制台或调试面板

### 数据库检查
```bash
# 检查数据库结构
python init_database.py --check

# 强制重建数据库（会删除现有数据）
python init_database.py --force
```

### 启动程序
```bash
# 方式1：通过启动器（推荐）
python launcher.py

# 方式2：直接运行主程序（需确保数据库已存在）
python main.py

# 方式3：独立运行爬虫配置工具
python crawler_gui.py
```

## 注意事项

1. **数据库连接**: 使用 `DatabaseManager._get_connection()` 获取线程专属连接
2. **缓存一致性**: 修改数据后需要更新内存缓存和数据库
3. **路径处理**: 使用 `os.path.normpath()` 规范化路径，避免比较错误
4. **跨平台**: 程序主要面向 Windows，部分功能（如播放器调用）使用 Windows 路径格式
5. **线程安全**: 数据库缓存使用 `threading.RLock()` 保护
6. **模块导入顺序**: 避免循环导入，底层模块不应导入上层模块
7. **数据库缓存加载**: 数据库缓存是异步加载的，访问前需检查 `is_cache_ready` 状态，避免死锁


一、内部运行时的数据结构 (self._folder_cache)
python
self._folder_cache = {
    "D:/Videos/Bookmark1": {                    # 文件夹路径（归一化后）
        'id': 123,                               # 数据库 folder_id
        'name': "Bookmark1",                     # 文件夹名称
        'parent': None,                          # 父文件夹路径（根节点为None）
        'is_bookmark': True,                     # 是否为书签根节点
        'file_count': 15,                        # 文件数量
        'children': [                            # 子文件夹路径列表
            "D:/Videos/Bookmark1/SubFolder1",
            "D:/Videos/Bookmark1/SubFolder2"
        ]
    },
    "D:/Videos/Bookmark1/SubFolder1": {
        'id': 124,
        'name': "SubFolder1",
        'parent': "D:/Videos/Bookmark1",
        'is_bookmark': False,
        'file_count': 5,
        'children': []
    }
}

二、保存到JSON缓存文件的结构 (tree_{device_id}_{hash}.json)
文件位置: data/tree_{device_id}_{hash}.json

python
{
    # 元数据
    'device_id': "my_pc",                        # 设备ID
    'bookmark_id': "bm_001",                     # 书签ID
    'root_path': "D:/Videos/Bookmark1",          # 根路径
    'cache_time': 1734567890.123,                # 缓存时间戳（Unix时间）
    'created_at': "2025-12-19T10:30:00",         # 创建时间（ISO格式）
    
    # 节点数据（与_folder_cache类似但简化）
    'nodes': {
        "D:/Videos/Bookmark1": {
            'name': "Bookmark1",
            'parent': None,
            'is_bookmark': True,
            'file_count': 15,
            'children': [
                "D:/Videos/Bookmark1/SubFolder1",
                "D:/Videos/Bookmark1/SubFolder2"
            ]
            # 注意：不包含数据库的 'id' 字段
        },
        "D:/Videos/Bookmark1/SubFolder1": {
            'name': "SubFolder1",
            'parent': "D:/Videos/Bookmark1",
            'is_bookmark': False,
            'file_count': 5,
            'children': []
        }
    },
    
    # UI状态（保存时可选）
    'expanded_paths': [                          # 已展开的路径列表
        "D:/Videos/Bookmark1",
        "D:/Videos/Bookmark1/SubFolder1"
    ],
    'selected_path': "D:/Videos/Bookmark1/SubFolder1"  # 当前选中的路径
}
三、数据结构转换流程
1. 从数据库加载 → _folder_cache
python
# _load_folders_from_db() 方法中
folders[folder_path] = {
    'id': row['folder_id'],      # 从 folders 表读取
    'name': row['folder_name'],
    'parent': row['parent_path'],
    'is_bookmark': bool(row['is_bookmark']),
    'file_count': row['file_count'] or 0,
    'children': []               # 稍后填充
}
2. _folder_cache → JSON nodes（保存时）
python
# _folder_cache_to_nodes() 方法
nodes[path] = {
    'name': info['name'],
    'parent': info['parent'],
    'is_bookmark': info['is_bookmark'],
    'file_count': info['file_count'],
    'children': info.get('children', [])
}
# 注意：移除了数据库的 'id' 字段
3. JSON nodes → _folder_cache（加载时）
python
# _nodes_to_folder_cache() 方法
folder_cache[path] = {
    'id': node.get('id'),                    # 可能为 None
    'name': node.get('name', os.path.basename(path)),
    'parent': node.get('parent'),
    'is_bookmark': node.get('is_bookmark', False),
    'file_count': node.get('file_count', 0),
    'children': node.get('children', [])
}