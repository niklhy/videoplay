# -*- coding: utf-8 -*-
"""配置管理模块（设计文档第 3 章）。

职责：config.json 的读写、设备/根目录管理、上次运行状态记忆、
路径相对/绝对转换，以及按设备存储的缩略图尺寸配置（28 号模块 34.5 节）。

config.json 结构（设计文档 3.3 节）::

    {
      "version": "2.0",
      "current_device": "A",
      "settings": {...},              # 设备级共享设置
      "devices": {
        "A": {
          "name": "家电脑",
          "device_label": "本机",
          "roots": [RootConfig...],
          "bookmarks": [...]
        }
      },
      "last_state": {"A": LastState...},
      "thumbnail_size": {"A": "medium"}   # 28 号视图控制模块，按设备存储
    }

路径约定：配置文件中的相对路径以根目录 ID 作为首段
（如 ``"D1/D11/D112"``，见 3.3 节 last_state 示例）。
绝对路径支持 Windows 本地盘符路径（``D:/视频``）与
UNC 网络路径（``\\\\server\\share``）两种形式；
所有路径比较统一使用 ``os.path.normcase`` + ``os.path.normpath`` 规范化。

仅使用标准库；数据类（RootConfig / LastState）与常量一律从契约文件导入。
"""

import json
import os
import re
import shutil

from constants import CONFIG_FILE, THUMBNAIL_DEFAULT_SIZE, THUMBNAIL_SIZES
from models import LastState, RootConfig

# 配置文件格式版本（设计文档 3.3 节）
CONFIG_VERSION = "2.0"

# 损坏配置文件的备份后缀
BACKUP_SUFFIX = ".bak"

# Windows 本地盘符路径（如 D:\xxx 或 D:/xxx）
_DRIVE_PATH_RE = re.compile(r'^[A-Za-z]:[\\/]')
# Windows UNC 网络路径（如 \\server\share）
_UNC_PATH_RE = re.compile(r'^[\\/]{2}[^\\/]+[\\/]+[^\\/]+')


def _is_absolute_path(path):
    """判断路径是否为绝对路径（兼容本地盘符路径与 UNC 网络路径）。

    除 ``os.path.isabs`` 外，额外用正则兜底，避免在非 Windows 平台
    上运行/测试时无法识别 ``D:/视频``、``\\\\server\\share`` 形式。

    :param path: 待判断的路径字符串
    :return: 是绝对路径返回 True，否则返回 False
    """
    if not path:
        return False
    if os.path.isabs(path):
        return True
    return bool(_DRIVE_PATH_RE.match(path) or _UNC_PATH_RE.match(path))


def _normalize_path(path):
    """规范化路径用于比较：normcase + normpath（统一大小写与分隔符）。

    :param path: 原始路径
    :return: 规范化后的路径（仅用于比较，不用于展示/存储）
    """
    return os.path.normcase(os.path.normpath(path))


def _default_config():
    """生成默认配置结构（设计文档 3.3 节）。

    :return: 默认配置字典
    """
    return {
        "version": CONFIG_VERSION,
        "current_device": "",
        "settings": {
            "remember_position": True,
            "debug_mode": False,
            "player": {
                "default_player": "potplayer",
                "potplayer_path": "C:/Program Files/PotPlayer/PotPlayerMini64.exe",
                "vlc_path": "C:/Program Files/VideoLAN/VLC/vlc.exe",
                "use_embedded_vlc": False,
            },
            "gallery": {
                "color_scheme": "default",
            },
        },
        "devices": {},
        "last_state": {},
    }


class ConfigManager:
    """配置管理器：配置读写、设备/根目录管理、上次状态、路径转换。

    :ivar config_path: 配置文件路径
    :ivar data: 配置数据字典（与 config.json 结构一致）
    """

    def __init__(self, config_path=CONFIG_FILE):
        """初始化配置管理器并立即加载配置。

        :param config_path: 配置文件路径，默认取契约常量 CONFIG_FILE
        """
        self.config_path = config_path
        self.data = _default_config()
        self.load()

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def load(self):
        """加载配置文件到 self.data。

        行为约定：
        - 文件不存在：返回默认结构并自动创建配置文件；
        - JSON 损坏：先备份为 ``config.json.bak``，再重建默认结构并保存；
        - 顶层字段缺失：与默认结构合并补齐，兼容旧版本配置。

        :return: 加载（或重建）后的配置数据字典
        """
        if not os.path.isfile(self.config_path):
            self.data = _default_config()
            self.save()
            return self.data

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("配置文件顶层结构必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError, OSError):
            # 备份损坏文件后重建默认配置
            try:
                shutil.copyfile(self.config_path, self.config_path + BACKUP_SUFFIX)
            except OSError:
                pass
            self.data = _default_config()
            self.save()
            return self.data

        # 与默认结构合并，补齐缺失的顶层字段
        defaults = _default_config()
        merged = defaults
        for key, value in loaded.items():
            if key in ("settings",) and isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        self.data = merged
        return self.data

    def save(self):
        """将 self.data 写入配置文件。

        JSON 格式强制：ensure_ascii=False、缩进 2、UTF-8 编码。
        先写入临时文件再替换，避免写入中断造成配置文件截断。
        """
        directory = os.path.dirname(os.path.abspath(self.config_path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)

        temp_path = self.config_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_path, self.config_path)

    # ------------------------------------------------------------------
    # 当前设备
    # ------------------------------------------------------------------
    def get_current_device_id(self):
        """获取当前设备标识。

        :return: 当前设备 ID 字符串（未设置时返回空字符串）
        """
        return str(self.data.get("current_device", "") or "")

    def set_current_device_id(self, device_id):
        """设置当前设备标识并立即保存。

        :param device_id: 设备 ID（如 "A"）
        """
        self.data["current_device"] = str(device_id or "")
        self.save()

    def _resolve_device_id(self, device_id):
        """将 None 的设备 ID 解析为当前设备 ID。

        :param device_id: 设备 ID 或 None
        :return: 有效的设备 ID 字符串
        """
        if device_id is None:
            return self.get_current_device_id()
        return str(device_id)

    def _get_device_entry(self, device_id, create=False):
        """获取设备配置节点（devices 下的子字典）。

        :param device_id: 设备 ID
        :param create: 节点不存在时是否自动创建（含默认子结构）
        :return: 设备节点字典；不存在且 create=False 时返回 None
        """
        devices = self.data.setdefault("devices", {})
        entry = devices.get(device_id)
        if entry is None and create:
            entry = {"name": "", "device_label": "", "roots": [], "bookmarks": []}
            devices[device_id] = entry
        if isinstance(entry, dict):
            entry.setdefault("name", "")
            entry.setdefault("device_label", "")
            entry.setdefault("roots", [])
            entry.setdefault("bookmarks", [])
        return entry if isinstance(entry, dict) else None

    # ------------------------------------------------------------------
    # 设备根目录管理
    # ------------------------------------------------------------------
    def get_device_roots(self, device_id=None):
        """获取指定设备的根目录列表。

        :param device_id: 设备 ID，None 表示当前设备
        :return: RootConfig 对象列表（无配置时返回空列表）
        """
        device_id = self._resolve_device_id(device_id)
        entry = self._get_device_entry(device_id)
        if not entry:
            return []
        roots = []
        for item in entry.get("roots", []):
            if not isinstance(item, dict):
                continue
            roots.append(RootConfig(
                root_id=str(item.get("root_id", "")),
                name=str(item.get("name", "")),
                path=str(item.get("path", "")),
                root_type=str(item.get("root_type", "local")),
                enabled=bool(item.get("enabled", True)),
            ))
        return roots

    def add_device_root(self, device_id, root_config):
        """添加（或按 root_id 覆盖）设备根目录并保存。

        :param device_id: 设备 ID
        :param root_config: RootConfig 对象
        """
        entry = self._get_device_entry(device_id, create=True)
        new_item = {
            "root_id": root_config.root_id,
            "name": root_config.name,
            "path": root_config.path,
            "root_type": root_config.root_type,
            "enabled": bool(root_config.enabled),
        }
        roots = entry["roots"]
        for index, item in enumerate(roots):
            if isinstance(item, dict) and item.get("root_id") == root_config.root_id:
                roots[index] = new_item
                self.save()
                return
        roots.append(new_item)
        self.save()

    def remove_device_root(self, device_id, root_id):
        """移除设备根目录并保存。

        :param device_id: 设备 ID
        :param root_id: 根目录 ID
        :return: 移除成功返回 True，未找到返回 False
        """
        entry = self._get_device_entry(device_id)
        if not entry:
            return False
        roots = entry.get("roots", [])
        remaining = [it for it in roots
                     if not (isinstance(it, dict) and it.get("root_id") == root_id)]
        if len(remaining) == len(roots):
            return False
        entry["roots"] = remaining
        self.save()
        return True

    def get_root_by_id(self, root_id, device_id=None):
        """按根目录 ID 查找 RootConfig。

        :param root_id: 根目录 ID（如 "D1"）
        :param device_id: 设备 ID，None 时先查当前设备，未找到再遍历全部设备
        :return: RootConfig 对象，未找到返回 None
        """
        if device_id is not None:
            for root in self.get_device_roots(device_id):
                if root.root_id == root_id:
                    return root
            return None

        # 先查当前设备
        root = self.get_root_by_id(root_id, self.get_current_device_id())
        if root is not None:
            return root
        # 再遍历其他设备
        for dev_id in self.data.get("devices", {}):
            if dev_id == self.get_current_device_id():
                continue
            for root in self.get_device_roots(dev_id):
                if root.root_id == root_id:
                    return root
        return None

    # ------------------------------------------------------------------
    # 上次运行状态
    # ------------------------------------------------------------------
    def get_last_state(self, device_id=None):
        """获取指定设备的上次运行状态。

        :param device_id: 设备 ID，None 表示当前设备
        :return: LastState 对象（无记录时返回默认空状态）
        """
        device_id = self._resolve_device_id(device_id)
        raw = self.data.get("last_state", {}).get(device_id, {})
        if not isinstance(raw, dict):
            raw = {}
        expanded = raw.get("tree2_expanded", [])
        if not isinstance(expanded, list):
            expanded = []
        return LastState(
            selected_root=str(raw.get("selected_root", "") or ""),
            selected_folder=str(raw.get("selected_folder", "") or ""),
            tree2_expanded=[str(p) for p in expanded],
        )

    def set_last_state(self, device_id, state):
        """保存指定设备的上次运行状态。

        :param device_id: 设备 ID
        :param state: LastState 对象
        """
        last_state = self.data.setdefault("last_state", {})
        last_state[device_id] = {
            "selected_root": state.selected_root,
            "selected_folder": state.selected_folder,
            "tree2_expanded": list(state.tree2_expanded or []),
        }
        self.save()

    # ------------------------------------------------------------------
    # 路径相对/绝对转换
    # ------------------------------------------------------------------
    def resolve_absolute_path(self, relative_path, root_id=None):
        """将相对路径解析为绝对路径。

        规则：
        - 已是绝对路径（含本地盘符、UNC 网络路径）时原样返回；
        - 相对路径首段若是根目录 ID（如 ``D1/D11``），优先使用该根目录并剥除首段；
        - root_id 为 None 时依次尝试：显式首段根 ID → 参数 root_id →
          该设备上次状态中的 selected_root；
        - 找不到根目录配置时原样返回输入。

        :param relative_path: 相对路径（可含根 ID 首段）或绝对路径
        :param root_id: 根目录 ID，None 表示自动推断
        :return: 绝对路径字符串
        """
        if not relative_path:
            return ""
        relative_path = str(relative_path)

        # 已是绝对路径（本地盘符或 UNC），直接返回
        if _is_absolute_path(relative_path):
            return relative_path

        # 统一使用正斜杠分段，兼容两种分隔符
        segments = relative_path.replace("\\", "/").split("/")
        segments = [s for s in segments if s]

        device_id = self.get_current_device_id()

        # 首段匹配根目录 ID 时，剥除首段并作为根 ID
        if segments and self.get_root_by_id(segments[0], device_id) is not None:
            if root_id is None:
                root_id = segments[0]
            segments = segments[1:]

        # root_id 仍为空时，回退到上次状态选中的根目录
        if root_id is None:
            root_id = self.get_last_state(device_id).selected_root or None

        if root_id is None:
            return relative_path

        root = self.get_root_by_id(root_id, device_id)
        if root is None or not root.path:
            return relative_path

        if not segments:
            return root.path
        return os.path.join(root.path, *segments)

    def get_relative_path(self, absolute_path, root_id):
        """将绝对路径转换为以根目录 ID 为首段的相对路径。

        例如根 ``D1`` 路径为 ``D:/视频`` 时，
        ``D:/视频/电影/a.mp4`` → ``D1/电影/a.mp4``。
        路径不在该根目录之下（含跨盘符情况）时原样返回输入。
        比较统一走 normcase + normpath，兼容分隔符与大小写差异。

        :param absolute_path: 绝对路径（本地盘符或 UNC 均可）
        :param root_id: 根目录 ID
        :return: 相对路径（首段为根 ID），无法转换时返回原路径
        """
        if not absolute_path:
            return ""
        root = self.get_root_by_id(root_id)
        if root is None or not root.path:
            return absolute_path

        try:
            rel = os.path.relpath(absolute_path, root.path)
        except (ValueError, OSError):
            # 跨盘符等无法计算相对路径的情况
            return absolute_path

        # relpath 结果以 .. 开头说明不在根目录之下
        if rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../") or rel.startswith("..\\"):
            return absolute_path

        return root_id + "/" + rel.replace("\\", "/")

    # ------------------------------------------------------------------
    # 缩略图尺寸配置（28 号视图控制模块 34.5 节，按设备存储）
    # ------------------------------------------------------------------
    def get_thumbnail_size(self):
        """获取当前设备保存的缩略图尺寸键名。

        :return: 尺寸键名（small/medium/large/xlarge），
                 未设置或取值非法时返回 THUMBNAIL_DEFAULT_SIZE
        """
        device_id = self.get_current_device_id()
        size_map = self.data.get("thumbnail_size", {})
        if not isinstance(size_map, dict):
            return THUMBNAIL_DEFAULT_SIZE
        size_key = size_map.get(device_id, THUMBNAIL_DEFAULT_SIZE)
        if size_key not in THUMBNAIL_SIZES:
            return THUMBNAIL_DEFAULT_SIZE
        return size_key

    def set_thumbnail_size(self, size_key):
        """保存缩略图尺寸到当前设备配置。

        非法键名（不在 THUMBNAIL_SIZES 中）直接忽略，不写入配置。

        :param size_key: 尺寸键名（small/medium/large/xlarge）
        """
        if size_key not in THUMBNAIL_SIZES:
            return
        device_id = self.get_current_device_id()
        if "thumbnail_size" not in self.data or not isinstance(self.data.get("thumbnail_size"), dict):
            self.data["thumbnail_size"] = {}
        self.data["thumbnail_size"][device_id] = size_key
        self.save()
