# -*- coding: utf-8 -*-
"""异步扫描模块（async_scanner.py）。

对应设计文档第八章：基于线程池的异步文件夹扫描与媒体/图片文件识别。

- 扫描结果通过 folder_index.py 的 FolderIndex 写入内存索引。
- scan_folder 的 on_complete 回调在工作线程中调用，调用方如需更新
  UI 请自行通过 tkinter 的 after 机制调度到主线程。
- 文件类型判断统一依据契约文件 constants.py 中的扩展名集合，大小写不敏感。
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Dict, List, Optional

from constants import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from folder_index import FolderIndex


class AsyncScanner:
    """线程池异步扫描器。

    属性:
        folder_index: FolderIndex         内存索引，扫描结果写入其中
        executor: ThreadPoolExecutor      扫描线程池
        _active_scans: dict[str, Future]  规范化路径 -> 进行中的扫描 Future
        _callbacks: dict[str, list]       规范化路径 -> on_complete 回调列表
        _lock: threading.RLock            保护 _active_scans / _callbacks
    """

    def __init__(self, folder_index: FolderIndex, max_workers: int = 3):
        """初始化扫描器。

        参数:
            folder_index: FolderIndex 实例
            max_workers: 线程池最大工作线程数，默认 3
        """
        self.folder_index = folder_index
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="async_scanner")
        self._active_scans = {}
        self._callbacks = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 文件类型判断（静态方法）
    # ------------------------------------------------------------------
    @staticmethod
    def is_media_file(filename: str) -> bool:
        """判断文件名是否为媒体（视频）文件，大小写不敏感。"""
        ext = os.path.splitext(filename)[1].lower()
        return ext in VIDEO_EXTENSIONS

    @staticmethod
    def is_image_file(filename: str) -> bool:
        """判断文件名是否为图片文件，大小写不敏感。"""
        ext = os.path.splitext(filename)[1].lower()
        return ext in IMAGE_EXTENSIONS

    # ------------------------------------------------------------------
    # 异步扫描
    # ------------------------------------------------------------------
    def scan_folder(self, folder_path: str, recursive: bool = False,
                    on_complete: Optional[Callable] = None) -> Future:
        """异步扫描指定文件夹，返回 Future。

        扫描完成后结果写入内存索引，并回调 on_complete(result_dict)。
        注意：回调在工作线程中执行，UI 更新需调用方自行 after 调度。

        参数:
            folder_path: 待扫描文件夹路径
            recursive: 是否递归扫描全部子文件夹
            on_complete: 完成回调，签名 on_complete(result: dict)

        返回:
            concurrent.futures.Future，result() 为扫描结果字典
        """
        norm = os.path.normpath(folder_path)
        with self._lock:
            if on_complete is not None:
                self._callbacks.setdefault(norm, []).append(on_complete)
            future = self.executor.submit(self._scan_task, norm, recursive)
            self._active_scans[norm] = future
        return future

    def _scan_task(self, folder_path: str, recursive: bool) -> dict:
        """线程池任务包装：执行扫描、取出并触发回调、清理登记。"""
        try:
            result = self._do_scan(folder_path, recursive)
        except Exception as exc:  # 扫描异常兜底，保证回调一定触发
            result = {
                "folder_path": folder_path,
                "subfolders": [],
                "media_files": [],
                "image_files": [],
                "error": str(exc),
            }
        with self._lock:
            callbacks = self._callbacks.pop(folder_path, [])
            self._active_scans.pop(folder_path, None)
        for callback in callbacks:
            try:
                callback(result)
            except Exception:
                pass  # 回调异常不影响其他回调与 Future 结果
        return result

    def scan_folder_sync(self, folder_path: str, recursive: bool = False) -> dict:
        """同步扫描指定文件夹（在当前线程执行），结果同样写入内存索引。

        返回字典格式::
            {"folder_path": str, "subfolders": [...],
             "media_files": [...], "image_files": [...]}

        扫描失败时额外包含 "error" 字段。
        """
        return self._do_scan(os.path.normpath(folder_path), recursive)

    def scan_subfolders_batch(self, folder_paths: List[str]) -> List[dict]:
        """批量异步扫描多个文件夹（每个文件夹一层），按入参顺序返回结果列表。"""
        norms = [os.path.normpath(p) for p in folder_paths]
        futures = [self.executor.submit(self._do_scan, p, False) for p in norms]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # 单文件夹失败不影响其他结果
                results.append({
                    "folder_path": "",
                    "subfolders": [],
                    "media_files": [],
                    "image_files": [],
                    "error": str(exc),
                })
        return results

    # ------------------------------------------------------------------
    # 带索引缓存的查询
    # ------------------------------------------------------------------
    def get_media_in_folder(self, folder_path: str) -> List[str]:
        """获取指定文件夹中的媒体文件，优先读内存索引缓存。

        索引未命中（节点不存在或从未扫描）时同步扫描一层并写入索引。
        """
        norm = os.path.normpath(folder_path)
        node = self.folder_index.get_folder(norm)
        if node is not None and node.last_scan is not None:
            return list(node.media_files)
        result = self._do_scan(norm, False)
        return list(result["media_files"])

    def get_media_recursive(self, folder_path: str) -> List[str]:
        """递归获取指定文件夹及其后代中的全部媒体文件，优先读内存索引缓存。

        索引未命中时同步递归扫描并写入索引。
        """
        norm = os.path.normpath(folder_path)
        node = self.folder_index.get_folder(norm)
        if node is not None and node.last_scan is not None:
            return self.folder_index.get_all_media_recursive(norm)
        result = self._do_scan(norm, True)
        return list(result["media_files"])

    # ------------------------------------------------------------------
    # 取消与状态查询
    # ------------------------------------------------------------------
    def cancel_scan(self, folder_path: str) -> bool:
        """取消指定文件夹的进行中扫描。

        返回:
            True 表示成功取消（或任务尚未开始即被取消）；
            False 表示无此任务或任务已运行完毕无法取消。
        """
        norm = os.path.normpath(folder_path)
        with self._lock:
            future = self._active_scans.pop(norm, None)
            self._callbacks.pop(norm, None)
        if future is None:
            return False
        return future.cancel()

    def is_scanning(self, folder_path: str) -> bool:
        """判断指定文件夹是否正在扫描。"""
        with self._lock:
            future = self._active_scans.get(os.path.normpath(folder_path))
        return future is not None and not future.done()

    # ------------------------------------------------------------------
    # 核心扫描实现
    # ------------------------------------------------------------------
    def _do_scan(self, folder_path: str, recursive: bool) -> dict:
        """扫描文件夹并返回结果字典，同时将逐目录结果写入内存索引。

        使用 os.scandir 高效枚举；recursive=False 只收集一层，
        recursive=True 递归收集全部后代目录。

        返回字典格式::
            {"folder_path": str, "subfolders": [...],
             "media_files": [...], "image_files": [...]}

        recursive=True 时 subfolders / media_files / image_files 为全部
        后代目录与文件的汇总；目录不存在或无权限时额外包含 "error" 字段。
        """
        norm = os.path.normpath(folder_path)
        if not os.path.isdir(norm):
            return {
                "folder_path": norm,
                "subfolders": [],
                "media_files": [],
                "image_files": [],
                "error": "目录不存在: %s" % norm,
            }

        # 逐目录收集：path -> (直接子目录, 视频文件, 图片文件)
        infos = {}
        stack = [norm]
        while stack:
            current = stack.pop()
            if current in infos:
                continue
            subfolders, media_files, image_files = [], [], []
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda e: e.name.lower())
            except OSError:
                entries = []
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subfolders.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        if self.is_media_file(entry.name):
                            media_files.append(entry.path)
                        elif self.is_image_file(entry.name):
                            image_files.append(entry.path)
                except OSError:
                    continue  # 单个条目异常（如权限不足）不影响整体扫描
            infos[current] = (subfolders, media_files, image_files)
            if recursive:
                stack.extend(subfolders)

        # 将逐目录结果写入内存索引（节点返回后补充 image_files 字段）
        for path, (subs, media, images) in infos.items():
            node = self.folder_index.add_folder(path, subs, media)
            node.image_files = images

        root_subs, root_media, root_images = infos.get(norm, ([], [], []))
        if not recursive:
            return {
                "folder_path": norm,
                "subfolders": sorted(root_subs),
                "media_files": sorted(root_media),
                "image_files": sorted(root_images),
            }

        # 递归模式：汇总全部后代目录与媒体文件
        all_subs, all_media, all_images = [], [], []
        for subs, media, images in infos.values():
            all_subs.extend(subs)
            all_media.extend(media)
            all_images.extend(images)
        return {
            "folder_path": norm,
            "subfolders": sorted(all_subs),
            "media_files": sorted(all_media),
            "image_files": sorted(all_images),
        }
