# -*- coding: utf-8 -*-
"""事件类型常量（契约文件，对应设计文档5.2节）。

所有模块通过 event_bus 发布/订阅事件时使用这里的常量，禁止散写字符串。
"""

# 设备树事件
EVENT_DEVICE_SELECTED = "device_selected"
EVENT_DEVICE_EXPANDED = "device_expanded"

# 文件夹树事件
EVENT_ROOT_SELECTED = "root_selected"
EVENT_FOLDER_EXPANDED = "folder_expanded"
EVENT_FOLDER_SELECTED = "folder_selected"
EVENT_FOLDER_SCAN_COMPLETE = "folder_scan_complete"

# 图库事件
EVENT_GALLERY_LOAD_REQUEST = "gallery_load_request"
EVENT_GALLERY_DATA_READY = "gallery_data_ready"
EVENT_THUMBNAIL_READY = "thumbnail_ready"

# 元数据事件
EVENT_METADATA_LOADED = "metadata_loaded"
EVENT_METADATA_UPDATED = "metadata_updated"

# 状态机事件
EVENT_STATE_CHANGED = "state_changed"

# 扫描事件
EVENT_SCAN_STARTED = "scan_started"
EVENT_SCAN_PROGRESS = "scan_progress"
EVENT_SCAN_COMPLETED = "scan_completed"

# 演员事件
EVENT_ACTOR_SELECTED = "actor_selected"

# 视图事件（设计文档34章）
EVENT_THUMBNAIL_SIZE_CHANGED = "thumbnail_size_changed"

# 标签事件
EVENT_TAG_CHANGED = "tag_changed"           # 标签增删改
EVENT_FILE_TAGS_CHANGED = "file_tags_changed"  # 文件标签变化

# 视频关联事件
EVENT_VIDEO_LINK_CHANGED = "video_link_changed"

# 日志事件
EVENT_LOG_APPENDED = "log_appended"
