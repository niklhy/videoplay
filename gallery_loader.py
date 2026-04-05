#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图库加载器 - 处理文件夹扫描和图库内容加载
"""
import os
import threading
import re
from datetime import datetime
from core import PathUtils


class GalleryLoader:
    """图库加载器 - 处理文件夹扫描和图库内容加载"""
    
    def __init__(self, db_manager, folder_cache, debug_utils=None):
        self.db = db_manager
        self.folder_cache = folder_cache
        self.debug = debug_utils
        self._gallery_load_lock = threading.Lock()
        self._last_loaded_folder = None
        self._is_scanning_folder = None
    
    def load_gallery(self, folder_path, gallery_widget, status_callback, scroll_y=None, db_ready=False):
        """加载图库 - 线程安全入口"""
        with self._gallery_load_lock:
            self._perform_load(folder_path, gallery_widget, status_callback, scroll_y, db_ready)
    
    def _perform_load(self, folder_path, gallery_widget, status_callback, scroll_y, db_ready):
        """实际执行加载"""
        if self._last_loaded_folder == folder_path and not str(folder_path).startswith('['):
            if self.debug:
                self.debug.print(f"文件夹 {folder_path} 已在UI中显示，跳过重复加载")
            return
        
        self._last_loaded_folder = folder_path
        
        # 关键修复：更新gallery的当前文件夹，确保refresh()使用正确的路径
        gallery_widget._current_folder = folder_path
        
        # 数据库就绪时直接实时扫描
        if db_ready:
            self._load_realtime(folder_path, gallery_widget, status_callback, scroll_y)
            return
        
        # 尝试从缓存加载
        try:
            cache_data = self.folder_cache.load_folder_cache(folder_path)
            
            if cache_data and cache_data.get('files_info'):
                files_info = cache_data.get('files_info', [])
                filtered_files = self._filter_files(files_info)
                
                if filtered_files:
                    if self.debug:
                        self.debug.print(f"从缓存加载: {folder_path} ({len(filtered_files)} 项)")
                    gallery_widget.clear()
                    gallery_widget.load_from_db(filtered_files, status_callback, scroll_y=scroll_y)
                    status_callback(f"从缓存加载 ({len(filtered_files)} 项)", 100)
                    return
            
            # 缓存未命中，实时扫描
            self._load_realtime(folder_path, gallery_widget, status_callback, scroll_y)
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"加载图库失败: {e}")
    
    def _load_realtime(self, folder_path, gallery_widget, status_callback, scroll_y):
        """实时扫描文件夹并加载"""
        self._is_scanning_folder = folder_path
        
        try:
            processed_items = self._scan_folder(folder_path)
            
            if processed_items:
                gallery_widget.load_items(processed_items, status_callback, scroll_y=scroll_y)
                status_callback(f"就绪 ({len(processed_items)}项)", 100)
            else:
                gallery_widget.clear()
                status_callback("文件夹为空", 100)
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"实时扫描失败: {e}")
        finally:
            self._is_scanning_folder = None
    
    def _scan_folder(self, folder_path):
        """扫描文件夹，返回处理后的项目列表"""
        try:
            entries = list(os.scandir(folder_path))
        except Exception as e:
            if self.debug:
                self.debug.print(f"扫描文件夹失败: {e}")
            return []
        
        sub_folders = []
        folders_to_scan = [(folder_path, ".")]
        
        # 收集子文件夹
        for entry in entries:
            if entry.is_dir():
                sub_folders.append(entry.path)
                folders_to_scan.append((entry.path, entry.name))
        
        folder_data_map = {}
        
        # 扫描每个文件夹
        for scan_path, display_name in folders_to_scan:
            try:
                scan_entries = list(os.scandir(scan_path))
            except:
                continue
            
            videos = {}
            images = {}
            image_list = []
            
            for entry in scan_entries:
                if not entry.is_file():
                    continue
                
                ext = os.path.splitext(entry.name)[1].lower()
                basename = os.path.splitext(entry.name)[0].lower()
                stat = entry.stat()
                size_mb = round(stat.st_size / (1024 * 1024), 1)
                mtime = stat.st_mtime
                
                file_info = {
                    'path': entry.path,
                    'name': entry.name,
                    'basename': basename,
                    'ext': ext,
                    'size_mb': size_mb,
                    'mtime': mtime
                }
                
                if PathUtils.is_video_file(entry.path):
                    videos[basename] = file_info
                elif PathUtils.is_image_file(entry.path):
                    images[basename] = file_info
                    image_list.append(file_info)
            
            folder_data_map[scan_path] = {
                'display_name': display_name,
                'videos': videos,
                'images': images,
                'image_list': image_list
            }
        
        # 处理匹配逻辑
        processed_items = []
        
        for scan_path, data in folder_data_map.items():
            videos = data['videos']
            images = data['images']
            image_list = data['image_list']
            display_name = data['display_name']
            
            video_to_images = {}
            matched_images = set()
            
            # 1. 处理完全同名匹配
            for v_basename, v_info in videos.items():
                if v_basename in images:
                    video_to_images[v_basename] = {
                        'main': images[v_basename],
                        'alternates': [],
                        'video': v_info
                    }
                    matched_images.add(v_basename)
            
            # 2. 处理附加缩略图识别
            alternate_pattern = re.compile(r'^(.*?)[_\s\-]?(\d+)$')
            
            for img_basename, img_info in images.items():
                if img_basename in matched_images:
                    continue
                
                for v_basename in videos.keys():
                    if img_basename == v_basename:
                        continue
                    
                    match = alternate_pattern.match(img_basename)
                    if match:
                        base_part = match.group(1)
                        if base_part == v_basename:
                            if v_basename in video_to_images:
                                video_to_images[v_basename]['alternates'].append(img_info)
                            else:
                                video_to_images[v_basename] = {
                                    'main': img_info,
                                    'alternates': [],
                                    'video': videos[v_basename],
                                    'is_alternate_mode': True
                                }
                            matched_images.add(img_basename)
                            break
            
            # 3. 处理通用封面
            cover_names = ['cover', 'folder', 'poster', 'thumb', 'preview', 'default']
            for cover_name in cover_names:
                if cover_name in images:
                    for v_basename, v_info in videos.items():
                        if v_basename not in video_to_images:
                            video_to_images[v_basename] = {
                                'main': images[cover_name],
                                'alternates': [],
                                'video': v_info,
                                'is_cover': True
                            }
                            matched_images.add(cover_name)
                            break
            
            # 4. 生成有视频的项目
            for v_basename, mapping in video_to_images.items():
                item = {
                    'folder_name': display_name,
                    'folder_path': scan_path,
                    'video_path': mapping['video']['path'],
                    'video_size': mapping['video']['size_mb'],
                    'video_mtime': mapping['video']['mtime'],
                    'main_image': mapping['main']['path'],
                    'alternates': [img['path'] for img in mapping.get('alternates', [])],
                    'match_type': 'dedicated' if not mapping.get('is_cover') else 'cover',
                    'file_type': 'image',
                    'is_alias': False,
                    'primary_video_path': None
                }
                processed_items.append(item)
            
            # 5. 处理没有图片匹配的占位符视频
            for v_basename, v_info in videos.items():
                if v_basename not in video_to_images:
                    item = {
                        'folder_name': display_name,
                        'folder_path': scan_path,
                        'video_path': v_info['path'],
                        'video_size': v_info['size_mb'],
                        'video_mtime': v_info['mtime'],
                        'main_image': "__VIDEO_PLACEHOLDER__",
                        'alternates': [],
                        'match_type': 'placeholder',
                        'file_type': 'video_placeholder',
                        'file_name': v_info['name'],
                        'is_alias': False,
                        'primary_video_path': None
                    }
                    processed_items.append(item)
            
            # 6. 处理仅图片（无视频匹配）
            standalone_images = [img for img in image_list if img['basename'] not in matched_images]
            for img in standalone_images:
                # 检查数据库中是否是关联图片
                is_alias = False
                primary_video_path = None
                
                if self.db and getattr(self.db, 'is_ready', False):
                    db_file = self.db.get_file_by_path(img['path'])
                    if db_file and db_file.get('is_alias'):
                        is_alias = True
                        primary_video_path = self.db.get_primary_video_path(db_file.get('id'))
                
                item = {
                    'folder_name': display_name,
                    'folder_path': scan_path,
                    'video_path': primary_video_path if is_alias else None,
                    'main_image': img['path'],
                    'alternates': [],
                    'match_type': 'linked' if is_alias else 'standalone',
                    'file_type': 'image',
                    'is_alias': is_alias,
                    'primary_video_path': primary_video_path
                }
                processed_items.append(item)
        
        # 保存到缓存
        if processed_items:
            cache_files_info = []
            for item in processed_items:
                cache_info = {
                    'file_path': item['main_image'],
                    'file_name': os.path.basename(item['main_image']) if item['main_image'] != "__VIDEO_PLACEHOLDER__" else item.get('file_name', 'video'),
                    'file_type': item['file_type'],
                    'folder_path': item['folder_path'],
                    'folder_name': item['folder_name'],
                    'video_path': item.get('video_path'),
                    'primary_video_path': item.get('primary_video_path'),
                    'is_alias': 1 if item.get('is_alias') else 0,
                    'alternates': item.get('alternates', []),
                    'match_type': item['match_type']
                }
                cache_files_info.append(cache_info)
            
            self.folder_cache.save_folder_cache(
                folder_path,
                sub_folders=sub_folders,
                files_info=cache_files_info
            )
        
        return processed_items
    
    def _filter_files(self, files_info):
        """过滤文件列表，去重并过滤视频文件"""
        # 去重
        seen = set()
        result = []
        for f in files_info:
            fp = f.get('file_path')
            if fp:
                normalized = os.path.normpath(fp).lower()
                if normalized not in seen:
                    seen.add(normalized)
                    result.append(f)
        
        # 只保留图片文件
        filtered = [f for f in result if f.get('file_type') != 'video']
        return filtered
