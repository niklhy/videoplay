# -*- coding: utf-8 -*-
"""全局常量定义（契约文件，对应设计文档第2章）。

所有模块从这里导入常量，禁止在各模块中重复定义或修改取值。
"""
import os

# 软件版本
APP_VERSION = "2.0.0"
APP_NAME = "媒体图库浏览器"

# 配置文件与路径
CONFIG_FILE = "config.json"
CACHE_DIR = "cache"
DB_FILE = "database.sqlite"
THUMBNAIL_CACHE_DIR = os.path.join(CACHE_DIR, "thumbnails")

# 支持的媒体文件扩展名
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.wmv', '.mov', '.flv', '.m4v', '.ts'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif'}

# 通用封面文件名（小写比较）
COVER_KEYWORDS = ('cover', 'folder', 'poster', 'thumb', 'preview', 'default')

# 缩略图尺寸配置
THUMBNAIL_SIZES = {
    "small":  {"label": "小",  "width": 150, "min_height": 200},
    "medium": {"label": "中",  "width": 200, "min_height": 260},
    "large":  {"label": "大",  "width": 250, "min_height": 330},
    "xlarge": {"label": "最大", "width": 300, "min_height": 400},
}
THUMBNAIL_DEFAULT_SIZE = "medium"  # 默认中(200像素)

# 缩略图交互样式（暗色主题）
THUMBNAIL_BG_NORMAL = "#2b2b2b"       # 默认背景色
THUMBNAIL_BG_HOVER = "#3d3d3d"        # 悬停背景色
THUMBNAIL_BG_SELECTED = "#1a5276"     # 选中背景色
THUMBNAIL_BORDER_NORMAL = "#404040"   # 默认边框色
THUMBNAIL_BORDER_HOVER = "#5dade2"    # 悬停边框色
THUMBNAIL_BORDER_SELECTED = "#3498db" # 选中边框色
THUMBNAIL_BORDER_WIDTH = 1            # 边框宽度（像素）
THUMBNAIL_PADDING = 8                 # 内边距（像素）
THUMBNAIL_GAP = 10                    # 缩略图间距（像素）

# 数据库表名
TABLE_FILES = "files"
TABLE_DEVICES = "devices"
TABLE_ACTORS = "actors"
TABLE_VIDEO_ACTORS = "video_actors"
TABLE_TAGS = "tags"
TABLE_FILE_TAGS = "file_tags"
TABLE_ACTOR_TAGS = "actor_tags"
TABLE_VIDEO_LINKS = "video_links"
TABLE_CUSTOM_CODES = "custom_codes"
TABLE_ACTOR_FOLDERS = "actor_folders"

# 状态机状态
STATE_IDLE = "idle"
STATE_DEVICE_LOADING = "device_loading"
STATE_FOLDER_LOADING = "folder_loading"
STATE_READY = "ready"

# 缩略图匹配模式
MODE_COVER_MATCH = "cover_match"        # 封面匹配：单视频+单图片(cover)
MODE_IMAGE_ONLY = "image_only"          # 仅图片
MODE_VIDEO_LINKED = "video_linked"      # 数据库关联视频
MODE_EXCLUSIVE_VIDEO = "exclusive_video"  # 专属视频：单视频+同名单图
MODE_COVER_PRIORITY = "cover_priority"  # 封面优先：单视频+多图片

# 日志级别
LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVEL_INFO = "INFO"
LOG_LEVEL_WARNING = "WARNING"
LOG_LEVEL_ERROR = "ERROR"
LOG_LEVEL_CRITICAL = "CRITICAL"

LOG_COLORS = {
    LOG_LEVEL_DEBUG:    '#808080',
    LOG_LEVEL_INFO:     '#00FF00',
    LOG_LEVEL_WARNING:  '#FFA500',
    LOG_LEVEL_ERROR:    '#FF0000',
    LOG_LEVEL_CRITICAL: '#FF00FF',
}
