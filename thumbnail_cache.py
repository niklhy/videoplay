# -*- coding: utf-8 -*-
"""缩略图缓存模块（对应设计文档第12章）。

提供缩略图的内存 LRU 缓存与磁盘缓存能力：
- 内存缓存：dict[path+size] -> PIL.Image，超出容量时淘汰最旧条目
- 磁盘缓存：缓存键 = md5(f"{path}|{w}x{h}")，JPEG 质量 85 保存
- 原图尺寸查询：带缓存，失败返回 (0, 0)
- 过期缓存清理
"""
import hashlib
import logging
import os
import time
from collections import OrderedDict

from PIL import Image

import constants

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """缩略图缓存管理器（内存 LRU + 磁盘持久化）。

    属性:
        cache_dir: 磁盘缓存目录
        max_cache_size: 内存缓存最大条目数
        _memory_cache: 内存缓存（OrderedDict，LRU 淘汰）
        _original_size_cache: 原图尺寸缓存 {path: (w, h)}
    """

    def __init__(self, cache_dir: str = constants.THUMBNAIL_CACHE_DIR,
                 max_size: int = 500):
        """初始化缓存管理器。

        参数:
            cache_dir: 磁盘缓存目录，默认取 constants.THUMBNAIL_CACHE_DIR
            max_size: 内存缓存最大条目数，超出后淘汰最旧条目
        """
        self.cache_dir = cache_dir
        self.max_cache_size = max_size
        # 内存缓存：OrderedDict 实现 LRU（末尾为最新）
        self._memory_cache = OrderedDict()
        # 原图尺寸缓存
        self._original_size_cache = {}
        # 确保缓存目录存在
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError as e:
            logger.error("创建缩略图缓存目录失败: %s (%s)", self.cache_dir, e)

    # ------------------------------------------------------------------
    # 缓存键
    # ------------------------------------------------------------------
    @staticmethod
    def _make_key(source_path: str, size: tuple) -> str:
        """生成内存缓存键。"""
        w, h = size
        return f"{source_path}|{w}x{h}"

    @staticmethod
    def _make_disk_name(source_path: str, size: tuple) -> str:
        """生成磁盘缓存文件名（源文件路径hash+尺寸）。"""
        w, h = size
        digest = hashlib.md5(f"{source_path}|{w}x{h}".encode("utf-8")).hexdigest()
        return digest + ".jpg"

    def _get_disk_path(self, source_path: str, size: tuple) -> str:
        """返回磁盘缓存文件的完整路径。"""
        return os.path.join(self.cache_dir,
                            self._make_disk_name(source_path, size))

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def get_thumbnail(self, source_path: str, size: tuple) -> str:
        """获取缩略图路径。

        先查内存缓存（dict[path+size] -> Image），未命中再查磁盘缓存；
        磁盘命中则加载回内存并返回缓存文件路径，未命中则生成新缩略图。

        参数:
            source_path: 源图片文件路径
            size: 目标尺寸 (宽, 高)

        返回:
            缓存文件路径；生成失败时返回源文件路径
        """
        if not source_path or not os.path.isfile(source_path):
            logger.warning("缩略图源文件不存在: %s", source_path)
            return source_path

        key = self._make_key(source_path, size)

        # 1. 内存缓存命中
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)  # 标记为最新
            disk_path = self._get_disk_path(source_path, size)
            if os.path.isfile(disk_path):
                return disk_path
            # 磁盘文件被外部删除，需重新生成
            self._memory_cache.pop(key, None)

        # 2. 磁盘缓存命中
        disk_path = self._get_disk_path(source_path, size)
        if os.path.isfile(disk_path):
            try:
                img = Image.open(disk_path)
                img.load()
                self._put_memory(key, img)
                return disk_path
            except Exception as e:
                logger.warning("读取磁盘缩略图失败，将重新生成: %s (%s)", disk_path, e)
                try:
                    os.remove(disk_path)
                except OSError:
                    pass

        # 3. 未命中，生成缩略图
        return self.generate_thumbnail(source_path, size)

    def generate_thumbnail(self, source_path: str, size: tuple) -> str:
        """生成缩略图并写入磁盘与内存缓存。

        使用 PIL 打开源图，按 size 缩放后保存为 JPEG（质量 85）。
        打开失败时记录日志并返回源文件路径。

        参数:
            source_path: 源图片文件路径
            size: 目标尺寸 (宽, 高)

        返回:
            缓存文件路径；失败时返回源文件路径
        """
        key = self._make_key(source_path, size)
        try:
            img = Image.open(source_path)
            img.load()
            # 统一转 RGB，避免 PNG 透明通道导致 JPEG 保存失败
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail(size)
            disk_path = self._get_disk_path(source_path, size)
            img.save(disk_path, "JPEG", quality=85)
            self._put_memory(key, img)
            return disk_path
        except Exception as e:
            logger.error("生成缩略图失败: %s (%s)", source_path, e)
            return source_path

    def get_original_size(self, image_path: str) -> tuple:
        """获取原图尺寸（带缓存）。

        参数:
            image_path: 图片文件路径

        返回:
            (宽, 高)；失败返回 (0, 0)
        """
        if image_path in self._original_size_cache:
            return self._original_size_cache[image_path]

        try:
            with Image.open(image_path) as img:
                size = img.size
        except Exception as e:
            logger.warning("获取原图尺寸失败: %s (%s)", image_path, e)
            size = (0, 0)

        self._original_size_cache[image_path] = size
        return size

    def clear_cache(self) -> None:
        """清空内存缓存、原图尺寸缓存及磁盘缓存文件。"""
        self._memory_cache.clear()
        self._original_size_cache.clear()
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, name)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError as e:
                logger.warning("删除缓存文件失败: %s (%s)", file_path, e)

    def get_cache_size(self) -> int:
        """获取磁盘缓存中的文件数量。

        返回:
            缓存目录下的文件数
        """
        if not os.path.isdir(self.cache_dir):
            return 0
        return sum(1 for name in os.listdir(self.cache_dir)
                   if os.path.isfile(os.path.join(self.cache_dir, name)))

    def cleanup_old_cache(self, max_age_days: int = 30) -> None:
        """清理过期缓存文件。

        删除最后修改时间超过 max_age_days 天的缓存文件。

        参数:
            max_age_days: 最大保留天数，默认 30 天
        """
        if not os.path.isdir(self.cache_dir):
            return
        now = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60
        for name in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, name)
            try:
                if not os.path.isfile(file_path):
                    continue
                if now - os.path.getmtime(file_path) > max_age_seconds:
                    os.remove(file_path)
            except OSError as e:
                logger.warning("清理过期缓存失败: %s (%s)", file_path, e)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _put_memory(self, key: str, img: Image) -> None:
        """写入内存缓存并执行 LRU 淘汰。"""
        self._memory_cache[key] = img
        self._memory_cache.move_to_end(key)
        while len(self._memory_cache) > self.max_cache_size:
            self._memory_cache.popitem(last=False)  # 删除最旧条目
