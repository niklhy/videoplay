# -*- coding: utf-8 -*-
"""播放器设置模块（对应设计文档第27章）。

职责：外部视频播放器（PotPlayer/VLC）路径配置、自动检测、调用播放、内嵌 VLC。

配置存储结构（config.json）：
    settings.player = {
        "default_player": "potplayer",      # 默认播放器：potplayer | vlc
        "potplayer_path": "...",            # PotPlayer 可执行文件路径
        "vlc_path": "...",                  # VLC 可执行文件路径
        "use_embedded_vlc": false           # 是否使用内嵌 VLC 播放
    }

本模块通过注入的 config_manager 对象读写配置，
修改后调用其 save() 持久化。
"""

import logging
import os
import subprocess

# 支持的播放器类型常量
PLAYER_POTPLAYER = "potplayer"
PLAYER_VLC = "vlc"
SUPPORTED_PLAYERS = (PLAYER_POTPLAYER, PLAYER_VLC)

# 各播放器常见安装路径（自动检测时依次检查）
POTPLAYER_CANDIDATE_PATHS = (
    r"C:\Program Files\PotPlayer\PotPlayerMini64.exe",
    r"C:\Program Files\PotPlayer\PotPlayerMini.exe",
    r"C:\Program Files (x86)\PotPlayer\PotPlayerMini64.exe",
    r"C:\Program Files (x86)\PotPlayer\PotPlayerMini.exe",
    r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
    r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe",
)
VLC_CANDIDATE_PATHS = (
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files\VideoLAN\VLC (x86)\vlc.exe",
)

logger = logging.getLogger(__name__)


class PlayerSettings:
    """播放器设置管理器。

    管理 PotPlayer/VLC 两种外部播放器的路径配置、自动检测与调用播放。

    属性：
        config_manager: ConfigManager 配置管理器实例（注入）
    """

    def __init__(self, config_manager):
        """初始化播放器设置管理器。

        参数：
            config_manager: ConfigManager 实例，
                            需具备 data: dict 属性与 save() 方法。
        """
        self.config_manager = config_manager
        # 内嵌 VLC 实例缓存（惰性创建）
        self._vlc_instance = None

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------
    def _get_player_config(self) -> dict:
        """获取 settings.player 配置字典（不存在时自动创建结构）。

        返回：
            dict，即 config_manager.data["settings"]["player"]。
        """
        data = self.config_manager.data
        settings = data.setdefault("settings", {})
        return settings.setdefault("player", {})

    def _normalize_player_type(self, player_type):
        """规范化播放器类型字符串。

        参数：
            player_type: 播放器类型，None 时取默认播放器。

        返回：
            str，规范化后的播放器类型（potplayer / vlc）。

        引发：
            ValueError: 播放器类型不受支持。
        """
        if player_type is None:
            player_type = self.get_default_player()
        player_type = str(player_type).strip().lower()
        if player_type not in SUPPORTED_PLAYERS:
            raise ValueError(
                f"不支持的播放器类型: {player_type!r}，"
                f"支持: {', '.join(SUPPORTED_PLAYERS)}")
        return player_type

    @staticmethod
    def _path_config_key(player_type: str) -> str:
        """获取播放器路径在配置中的键名。"""
        return f"{player_type}_path"

    # ------------------------------------------------------------------
    # 默认播放器
    # ------------------------------------------------------------------
    def get_default_player(self) -> str:
        """获取默认播放器类型。

        返回：
            str，"potplayer" 或 "vlc"，默认为 "potplayer"。
        """
        player_cfg = self._get_player_config()
        default = player_cfg.get("default_player", PLAYER_POTPLAYER)
        default = str(default).strip().lower()
        return default if default in SUPPORTED_PLAYERS else PLAYER_POTPLAYER

    def set_default_player(self, player_type: str) -> None:
        """设置默认播放器。

        参数：
            player_type: "potplayer" 或 "vlc"。

        引发：
            ValueError: 播放器类型不受支持。
        """
        player_type = self._normalize_player_type(player_type)
        player_cfg = self._get_player_config()
        player_cfg["default_player"] = player_type
        self.config_manager.save()

    # ------------------------------------------------------------------
    # 播放器路径读写
    # ------------------------------------------------------------------
    def get_player_path(self, player_type: str = None) -> str:
        """获取指定播放器的可执行文件路径。

        参数：
            player_type: "potplayer" / "vlc"，None 时取默认播放器。

        返回：
            str，已配置的路径（可能为空字符串或不存在）。

        引发：
            ValueError: 播放器类型不受支持。
        """
        player_type = self._normalize_player_type(player_type)
        player_cfg = self._get_player_config()
        path = player_cfg.get(self._path_config_key(player_type), "")
        return str(path) if path else ""

    def set_player_path(self, player_type: str, path: str) -> None:
        """设置指定播放器的可执行文件路径。

        参数：
            player_type: "potplayer" 或 "vlc"。
            path: 可执行文件绝对路径。

        引发：
            ValueError: 播放器类型不受支持。
        """
        player_type = self._normalize_player_type(player_type)
        player_cfg = self._get_player_config()
        player_cfg[self._path_config_key(player_type)] = str(path)
        self.config_manager.save()

    # ------------------------------------------------------------------
    # 路径检查与自动检测
    # ------------------------------------------------------------------
    def check_player_exists(self, player_type: str = None) -> bool:
        """检查指定播放器的可执行文件是否存在。

        参数：
            player_type: "potplayer" / "vlc"，None 时取默认播放器。

        返回：
            bool，路径已配置且文件存在返回 True。
        """
        try:
            player_type = self._normalize_player_type(player_type)
        except ValueError:
            return False
        path = self.get_player_path(player_type)
        return bool(path) and os.path.exists(path)

    def auto_detect_player(self, player_type: str) -> str:
        """自动检测指定播放器的安装路径。

        依次检查常见安装路径，找到第一个存在的可执行文件。

        参数：
            player_type: "potplayer" 或 "vlc"。

        返回：
            str，检测到的可执行文件路径；未检测到返回空字符串。

        引发：
            ValueError: 播放器类型不受支持。
        """
        player_type = self._normalize_player_type(player_type)
        if player_type == PLAYER_POTPLAYER:
            candidates = POTPLAYER_CANDIDATE_PATHS
        else:
            candidates = VLC_CANDIDATE_PATHS
        for candidate in candidates:
            if os.path.exists(candidate):
                logger.info("自动检测到播放器 %s: %s", player_type, candidate)
                return candidate
        logger.info("未检测到播放器 %s 的常用安装路径", player_type)
        return ""

    # ------------------------------------------------------------------
    # 播放与内嵌 VLC
    # ------------------------------------------------------------------
    def play_video(self, video_path: str, player_type: str = None) -> bool:
        """调用外部播放器播放视频。

        参数：
            video_path: 视频文件的绝对路径。
            player_type: "potplayer" / "vlc"，None 时取默认播放器。

        返回：
            bool，成功启动播放器返回 True；路径无效或启动失败返回 False。
        """
        try:
            player_type = self._normalize_player_type(player_type)
        except ValueError as exc:
            logger.error("播放失败: %s", exc)
            return False

        player_path = self.get_player_path(player_type)
        if not player_path or not os.path.exists(player_path):
            logger.error("播放失败: 播放器 %s 路径无效 (%r)",
                         player_type, player_path)
            return False
        if not os.path.exists(video_path):
            logger.error("播放失败: 视频文件不存在: %s", video_path)
            return False

        try:
            # 以分离进程启动外部播放器，不阻塞本程序
            subprocess.Popen([player_path, video_path])
            logger.info("已启动 %s 播放: %s", player_type, video_path)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("播放失败: 启动 %s 出错: %s", player_path, exc)
            return False

    def get_embedded_vlc(self):
        """获取内嵌 VLC 实例（python-vlc）。

        返回：
            vlc.Instance 实例；python-vlc 未安装或创建失败时返回 None。

        说明：
            依赖 python-vlc 绑定（import vlc），未安装时优雅降级，
            调用方应判断返回值是否为 None。
        """
        if self._vlc_instance is not None:
            return self._vlc_instance
        try:
            import vlc  # python-vlc 绑定，未安装时抛 ImportError
        except ImportError:
            logger.warning("python-vlc 未安装，内嵌 VLC 播放不可用")
            return None
        try:
            self._vlc_instance = vlc.Instance()
            return self._vlc_instance
        except Exception as exc:  # noqa: BLE001 - 第三方库异常类型不确定
            logger.error("创建内嵌 VLC 实例失败: %s", exc)
            return None
