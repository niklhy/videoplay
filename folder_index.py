# -*- coding: utf-8 -*-
"""内存索引模块（folder_index.py）。

对应设计文档第七章：文件夹树的内存索引，支持脏标记与增量更新。

- 数据结构 FolderNode 从契约文件 models.py 导入，禁止重复定义。
- 线程安全：所有公共方法均通过 threading.RLock 保护，可嵌套调用。
- 所有路径统一使用 os.path.normpath 规范化后作为索引键。
"""
import os
import threading
from datetime import datetime
from typing import List, Optional

from models import FolderNode, FileMetadata


class FolderIndex:
    """文件夹内存索引。

    以规范化后的文件夹绝对路径为键，维护 path -> FolderNode 的字典；
    附带脏路径集合，用于标记需要重新扫描的文件夹。

    属性:
        _folders: dict[str, FolderNode]  规范化路径 -> 节点
        _lock: threading.RLock           读写锁（可重入）
        _dirty_paths: set[str]           待重新扫描的规范化路径集合
    """

    def __init__(self):
        """初始化空索引。"""
        self._folders = {}
        self._lock = threading.RLock()
        self._dirty_paths = set()

    @staticmethod
    def _norm(path: str) -> str:
        """规范化路径作为索引键。"""
        return os.path.normpath(path)

    def add_folder(self, path: str, subfolders: List[str] = None,
                   media_files: List[str] = None) -> FolderNode:
        """添加或更新文件夹节点。

        已存在的节点会合并更新 subfolders / media_files（传入 None 表示不更新），
        并刷新 last_scan 时间戳；不存在的节点会创建后插入索引。

        参数:
            path: 文件夹绝对路径
            subfolders: 子文件夹路径列表（可选）
            media_files: 媒体（视频）文件路径列表（可选）

        返回:
            对应的 FolderNode 实例（调用方可继续补充 image_files 等字段）
        """
        with self._lock:
            norm = self._norm(path)
            node = self._folders.get(norm)
            if node is None:
                name = os.path.basename(norm) or norm
                node = FolderNode(path=norm, name=name)
                self._folders[norm] = node
            if subfolders is not None:
                node.subfolders = [self._norm(p) for p in subfolders]
            if media_files is not None:
                node.media_files = list(media_files)
            node.last_scan = datetime.now()
            return node

    def get_folder(self, path: str) -> Optional[FolderNode]:
        """按路径获取文件夹节点，不存在返回 None。"""
        with self._lock:
            return self._folders.get(self._norm(path))

    def remove_folder(self, path: str) -> None:
        """移除指定文件夹节点及其全部后代节点。"""
        with self._lock:
            norm = self._norm(path)
            prefix = norm + os.sep
            for key in [k for k in self._folders
                        if k == norm or k.startswith(prefix)]:
                del self._folders[key]
            self._dirty_paths.discard(norm)

    def get_subfolders(self, path: str) -> List[str]:
        """获取指定文件夹的直接子文件夹路径列表，不存在返回空列表。"""
        with self._lock:
            node = self._folders.get(self._norm(path))
            return list(node.subfolders) if node else []

    def get_media_files(self, path: str) -> List[str]:
        """获取指定文件夹的直接媒体文件路径列表，不存在返回空列表。"""
        with self._lock:
            node = self._folders.get(self._norm(path))
            return list(node.media_files) if node else []

    def get_all_media_recursive(self, path: str) -> List[str]:
        """递归收集指定文件夹及其全部后代中的媒体文件路径。

        使用显式栈做深度遍历，避免递归深度限制；结果按遍历顺序收集。

        参数:
            path: 起始文件夹路径

        返回:
            全部媒体文件路径列表（含子文件夹中的媒体文件）
        """
        with self._lock:
            norm = self._norm(path)
            result = []
            stack = [norm]
            visited = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                node = self._folders.get(current)
                if node is None:
                    continue
                result.extend(node.media_files)
                stack.extend(node.subfolders)
            return result

    def update_metadata(self, path: str, metadata: FileMetadata) -> None:
        """更新指定文件夹中某个文件的元数据。

        以 metadata.relative_path 为键写入节点的 metadata_map；
        若文件夹节点不存在则先创建。

        参数:
            path: 文件所在文件夹路径
            metadata: FileMetadata 实例
        """
        with self._lock:
            norm = self._norm(path)
            node = self._folders.get(norm)
            if node is None:
                node = self.add_folder(norm)
            node.metadata_map[metadata.relative_path] = metadata

    def mark_dirty(self, path: str) -> None:
        """将指定文件夹标记为脏（需要重新扫描）。"""
        with self._lock:
            self._dirty_paths.add(self._norm(path))

    def get_dirty_paths(self) -> List[str]:
        """获取全部脏路径列表（排序后返回，便于稳定遍历）。"""
        with self._lock:
            return sorted(self._dirty_paths)

    def mark_clean(self, path: str) -> None:
        """将指定文件夹的脏标记清除。"""
        with self._lock:
            self._dirty_paths.discard(self._norm(path))

    def clear(self) -> None:
        """清空全部索引数据与脏标记。"""
        with self._lock:
            self._folders.clear()
            self._dirty_paths.clear()
