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

## 项目结构

```
项目根目录/
├── launcher.py           # 程序启动器，自动检查并初始化数据库
├── main.py              # 主程序（约3300行），包含主窗口和各类对话框
├── core.py              # 核心模块：配置管理、图片查看器、视频播放器、工具函数
├── config_manager.py    # 扩展配置管理器（设备分组、跨设备标签）
├── database_manager.py  # 数据库管理器（性能优化版，支持异步缓存）
├── init_database.py     # 数据库初始化和升级脚本
├── gallery_viewer.py    # 图库浏览模块（缩略图展示、鼠标滚轮、右键菜单）
├── bookmark_manager.py  # 书签管理器（支持设备分组树形结构）
├── tree_explorer.py     # 资源管理器（文件夹树形浏览）
├── tag_manager.py       # 标签管理模块（跨设备标签存储）
├── actor_manager.py     # 演员管理模块（支持拼音排序、文件夹关联）
├── video_relation_manager.py  # 视频关联管理器（单图关联+批量扫描）
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

### 6. gallery_viewer.py
- `GalleryViewer`: 图库查看器类
- 缩略图网格展示
- 鼠标滚轮滚动支持
- 右键菜单（标签设置、视频关联等）
- 支持多种标识：视频、仅图片、关联视频

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

### 12. ui_components.py
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
  }
}
```

## 数据库结构

### 核心表

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `folders` | 文件夹信息 | folder_path, folder_name, is_bookmark |
| `media_files` | 媒体文件 | file_path, file_name, file_type(video/image), is_alias, file_size, release_date |
| `actors` | 演员信息 | actor_name, actor_name_cn, sort_char |
| `file_actors` | 文件-演员关联 | file_id, actor_id |
| `video_relations` | 视频主从关联 | primary_file_id, alias_file_id, alias_folder_path |
| `actor_relations` | 演员别名关系 | actor_id, related_actor_id, relation_type |
| `actor_folders` | 演员文件夹 | folder_path, folder_name |

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
├── gallery_viewer.py (GalleryViewer)
├── tag_manager.py (TagManager)
├── actor_manager.py (ActorManager)
└── video_relation_manager.py (RelationManager, VideoRelationDialog)
```

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

### 4. 缩略图展示规则
- 文件夹中所有图片作为缩略图显示
- 单图片+单视频：图片为缩略图，视频为对应视频
- 单图片+多视频：检查同名匹配，剩余视频显示占位符
- 多图片+多视频：按文件名匹配，支持主缩略图+附加缩略图切换

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
```

## 注意事项

1. **数据库连接**: 使用 `DatabaseManager._get_connection()` 获取线程专属连接
2. **缓存一致性**: 修改数据后需要更新内存缓存和数据库
3. **路径处理**: 使用 `os.path.normpath()` 规范化路径，避免比较错误
4. **跨平台**: 程序主要面向 Windows，部分功能（如播放器调用）使用 Windows 路径格式
5. **线程安全**: 数据库缓存使用 `threading.RLock()` 保护
6. **模块导入顺序**: 避免循环导入，底层模块不应导入上层模块
