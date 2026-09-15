#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图库加载器 - 处理文件夹扫描和图库内容加载
新逻辑：先子文件夹后当前文件夹，支持宫格显示
版本：2.0 - 支持字典结构（以番号为Key）
"""
import os
import threading
import re
import hashlib
from datetime import datetime
from core import PathUtils


class GalleryLoader:
    """图库加载器 - 处理文件夹扫描和图库内容加载"""
    
    # 数字后缀匹配模式：支持 -1, _1, 1, -01, _01, 01 等后缀
    ALTERNATE_SUFFIX_PATTERN = re.compile(r'^(.*?)(?:[_\-])?(\d+)$', re.IGNORECASE)
    # 通用封面文件名
    COVER_NAMES = ['cover', 'folder', 'poster', 'thumb', 'preview', 'default']
    
    def __init__(self, db_manager, folder_cache, debug_utils=None):
        self.db = db_manager
        self.folder_cache = folder_cache
        self.debug = debug_utils
        self._gallery_load_lock = threading.Lock()
        self._last_loaded_folder = None
        self._is_scanning_folder = None
    
    def load_gallery(self, folder_path, gallery_widget, status_callback, scroll_y=None, db_ready=False, force_reload=False):
        """加载图库 - 线程安全入口"""
        threading.Thread(
            target=self._load_gallery_thread,
            args=(folder_path, gallery_widget, status_callback, scroll_y, db_ready, force_reload),
            daemon=True
        ).start()
    
    def _load_gallery_thread(self, folder_path, gallery_widget, status_callback, scroll_y, db_ready, force_reload=False):
        """后台线程执行图库加载"""
        with self._gallery_load_lock:
            self._perform_load(folder_path, gallery_widget, status_callback, scroll_y, db_ready, force_reload)
    
    def _perform_load(self, folder_path, gallery_widget, status_callback, scroll_y, db_ready, force_reload=False):
        """实际执行加载"""
        if not force_reload and self._last_loaded_folder == folder_path and not str(folder_path).startswith('['):
            if self.debug:
                self.debug.print(f"文件夹 {folder_path} 已在UI中显示，跳过重复加载")
            return
        
        self._last_loaded_folder = folder_path
        gallery_widget._current_folder = folder_path
        
        if db_ready:
            self._load_realtime(folder_path, gallery_widget, status_callback, scroll_y)
            return
        
        try:
            cache_data = self.folder_cache.load_folder_cache(folder_path)
            
            if cache_data and cache_data.get('files_info'):
                files_info = cache_data.get('files_info', [])
                
                # 检查是否是字典格式缓存
                if isinstance(files_info, dict):
                    items_dict = files_info
                    processed_items = list(items_dict.values())
                elif isinstance(files_info, list) and files_info and 'item_key' in files_info[0]:
                    items_dict = self._load_from_cache_to_dict(folder_path, cache_data)
                    processed_items = list(items_dict.values())
                else:
                    filtered_files = self._filter_files(files_info)
                    processed_items = filtered_files
                    items_dict = {str(i): item for i, item in enumerate(processed_items)}
                
                if processed_items:
                    if self.debug:
                        self.debug.print(f"从缓存加载: {folder_path} ({len(processed_items)} 项)")
                    gallery_widget.clear()
                    gallery_widget.load_items_with_dict(processed_items, items_dict, status_callback, scroll_y=scroll_y)
                    status_callback(f"从缓存加载 ({len(processed_items)} 项)", 100)
                    return
            
            self._load_realtime(folder_path, gallery_widget, status_callback, scroll_y)
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"加载图库失败: {e}")
    
    def _load_realtime(self, folder_path, gallery_widget, status_callback, scroll_y):
        """实时扫描文件夹并加载"""
        self._is_scanning_folder = folder_path
        
        try:
            processed_dict = self._scan_folder_new(folder_path)
            processed_items = list(processed_dict.values())
            
            if processed_items:
                gallery_widget.load_items_with_dict(processed_items, processed_dict, status_callback, scroll_y=scroll_y)
                status_callback(f"就绪 ({len(processed_items)}项)", 100)
            else:
                gallery_widget.clear()
                status_callback("文件夹为空", 100)
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"实时扫描失败: {e}")
        finally:
            self._is_scanning_folder = None
    
    def _scan_folder_new(self, folder_path, tree_state=None, include_subfolders=True):
        """
        新逻辑扫描文件夹 - 返回字典结构（以番号为Key）
        
        Returns:
            dict: {video_code: item_dict} 或 {folder_path_hash: item_dict}
        """
        try:
            entries = list(os.scandir(folder_path))
        except Exception as e:
            if self.debug:
                self.debug.print(f"扫描文件夹失败: {e}")
            return {}
        
        # 分离子文件夹和文件
        sub_folders = []
        for entry in entries:
            if entry.is_dir():
                sub_folders.append(entry)
        
        all_processed_dict = {}
        
        # ========== 第一步：先处理所有子文件夹 ==========
        if include_subfolders:
            for sub_entry in sub_folders:
                sub_dict = self._process_sub_folder(sub_entry.path, sub_entry.name)
                for key, item in sub_dict.items():
                    if key not in all_processed_dict:
                        all_processed_dict[key] = item
                    else:
                        unique_key = f"{key}_{item.get('folder_path', '')}"
                        all_processed_dict[unique_key] = item
        
        # ========== 第二步：处理当前文件夹 ==========
        current_dict = self._process_current_folder(folder_path)
        for key, item in current_dict.items():
            if key not in all_processed_dict:
                all_processed_dict[key] = item
        
        # ========== 第三步：注入数据库信息 ==========
        if self.db:
            self._inject_db_info_to_dict(all_processed_dict)
        
        # 打印结果统计
        if all_processed_dict and self.debug:
            video_items = [it for it in all_processed_dict.values() if it.get('video_path')]
            grid_items = [it for it in all_processed_dict.values() if it.get('display_mode') in ['grid2', 'grid4']]
            standalone_img_items = [it for it in all_processed_dict.values() 
                                   if not it.get('video_path') and it.get('file_type') == 'image']
            self.debug.print(f"[图库加载] 共 {len(all_processed_dict)} 项: "
                           f"{len(video_items)} 个视频, {len(grid_items)} 个宫格, "
                           f"{len(standalone_img_items)} 个独立图片")
        
        # ========== 第四步：保存到缓存 ==========
        self._save_dict_to_cache(folder_path, sub_folders, all_processed_dict, tree_state)
        
        # 路径规范化
        for key, item in all_processed_dict.items():
            if item.get('video_path'):
                item['video_path'] = os.path.normpath(item['video_path']).lower()
            if item.get('primary_video_path'):
                item['primary_video_path'] = os.path.normpath(item['primary_video_path']).lower()
            if item.get('folder_path'):
                item['folder_path'] = os.path.normpath(item['folder_path']).lower()
            if item.get('videos'):
                item['videos'] = [os.path.normpath(v).lower() for v in item['videos']]
        
        return all_processed_dict
    
    def _inject_db_info_to_dict(self, items_dict):
        """为字典中的所有项注入数据库信息"""
        from core import extract_code_from_text
        
        # 先为所有项生成 video_code（如果还没有）
        for key, item in items_dict.items():
            video_path = item.get('video_path')
            if video_path and not item.get('video_code'):
                video_name = os.path.basename(video_path)
                folder_name = os.path.basename(item.get('folder_path', ''))
                
                video_code_info = extract_code_from_text(video_name)
                folder_code_info = extract_code_from_text(folder_name)
                
                if video_code_info:
                    item['video_code'] = video_code_info['canonical']
                elif folder_code_info:
                    item['video_code'] = folder_code_info['canonical']
                else:
                    item['video_code'] = hashlib.md5(video_path.encode()).hexdigest()[:12]
        
        # 查询视频关联数量
        for key, item in items_dict.items():
            video_path = item.get('video_path')
            primary_video_path = item.get('primary_video_path')
            
            if video_path and not primary_video_path:
                try:
                    db_file = self.db.get_file_by_path(video_path)
                    if db_file and not db_file.get('is_alias'):
                        aliases = self.db.get_related_videos(db_file.get('id'))
                        if aliases:
                            item['alias_count'] = len(aliases)
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"查询关联数量失败 {video_path}: {e}")
        
        # 查询视频采集信息
        for key, item in items_dict.items():
            video_path = item.get('video_path')
            primary_video_path = item.get('primary_video_path')
            
            if video_path and not primary_video_path:
                try:
                    db_file = self.db.get_file_by_path(video_path)
                    if db_file:
                        item['video_title'] = db_file.get('video_title', '')
                        item['video_code'] = item.get('video_code') or db_file.get('video_code', '')
                        item['release_date'] = db_file.get('release_date', '')
                        item['duration'] = db_file.get('duration', 0)
                        item['file_size'] = db_file.get('file_size', 0) or db_file.get('size', 0)
                        
                        file_id = db_file.get('id')
                        if file_id:
                            try:
                                actors = self.db.get_actors_by_file(file_id)
                                if actors:
                                    actor_names = []
                                    for actor in actors:
                                        name = actor.get('actor_name', '')
                                        name_cn = actor.get('actor_name_cn', '')
                                        if name and name_cn:
                                            actor_names.append(f"{name}（{name_cn}）")
                                        elif name:
                                            actor_names.append(name)
                                        elif name_cn:
                                            actor_names.append(name_cn)
                                    item['actors'] = '，'.join(actor_names)
                            except Exception as e:
                                if self.debug:
                                    self.debug.print(f"查询演员信息失败 {video_path}: {e}")
                        
                        # 更新字典key
                        new_key = item.get('video_code')
                        if new_key and new_key != key and new_key not in items_dict:
                            items_dict[new_key] = item
                            
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"查询采集信息失败 {video_path}: {e}")
            
            # 处理宫格项
            videos = item.get('videos', [])
            if videos:
                self._inject_grid_db_info(item, videos)
    
    def _inject_grid_db_info(self, item, videos):
        """为宫格项注入数据库信息"""
        try:
            synced_count = 0
            video_infos = []
            for v_path in videos:
                db_file = self.db.get_file_by_path(v_path)
                if db_file:
                    v_info = {
                        'path': v_path,
                        'video_title': db_file.get('video_title', ''),
                        'video_code': db_file.get('video_code', ''),
                        'release_date': db_file.get('release_date', ''),
                        'duration': db_file.get('duration', 0),
                        'file_size': db_file.get('file_size', 0) or db_file.get('size', 0),
                    }
                    file_id = db_file.get('id')
                    if file_id:
                        try:
                            actors = self.db.get_actors_by_file(file_id)
                            if actors:
                                actor_names = []
                                for actor in actors:
                                    name = actor.get('actor_name', '')
                                    name_cn = actor.get('actor_name_cn', '')
                                    if name and name_cn:
                                        actor_names.append(f"{name}（{name_cn}）")
                                    elif name:
                                        actor_names.append(name)
                                    elif name_cn:
                                        actor_names.append(name_cn)
                                v_info['actors'] = '，'.join(actor_names)
                        except Exception:
                            pass
                    
                    if v_info.get('video_title') or v_info.get('actors'):
                        synced_count += 1
                    
                    video_infos.append(v_info)
            
            item['video_infos'] = video_infos
            item['has_synced_video'] = synced_count > 0
            item['synced_count'] = synced_count
        except Exception as e:
            if self.debug:
                self.debug.print(f"查询宫格采集信息失败: {e}")
    
    def _generate_item_key(self, item, default_path=None):
        """
        为item生成唯一键
        
        优先级：
        1. 如果有 video_code，使用番号
        2. 如果有 video_path，使用路径hash
        3. 如果有 folder_path，使用路径hash
        4. 使用默认路径 + 类型
        """
        from core import extract_code_from_text
        
        video_code = item.get('video_code')
        if video_code:
            return video_code
        
        video_path = item.get('video_path')
        if video_path:
            video_name = os.path.basename(video_path)
            code_info = extract_code_from_text(video_name)
            if code_info:
                code = code_info['canonical']
                item['video_code'] = code
                return code
            return hashlib.md5(video_path.encode()).hexdigest()[:12]
        
        folder_path = item.get('folder_path', default_path)
        if folder_path:
            return f"folder_{hashlib.md5(folder_path.encode()).hexdigest()[:10]}"
        
        return f"mode_{item.get('display_mode', 'single')}_{item.get('match_type', 'unknown')}"
    
    def _process_sub_folder(self, folder_path, folder_name):
        """
        处理子文件夹 - 返回字典结构
        """
        items = []
        
        try:
            entries = list(os.scandir(folder_path))
        except:
            return {}
        
        videos = []
        images = []
        
        for entry in entries:
            if not entry.is_file():
                continue
            if PathUtils.is_video_file(entry.path):
                videos.append({
                    'path': os.path.normpath(entry.path).lower(),
                    'name': entry.name,
                    'basename': os.path.splitext(entry.name)[0].lower(),
                    'ext': os.path.splitext(entry.name)[1].lower()
                })
            elif PathUtils.is_image_file(entry.path):
                images.append({
                    'path': os.path.normpath(entry.path).lower(),
                    'name': entry.name,
                    'basename': os.path.splitext(entry.name)[0].lower(),
                    'ext': os.path.splitext(entry.name)[1].lower()
                })
        
        images.sort(key=lambda x: x['name'].lower())
        items_dict = {}
        
        # 单图单视频
        if len(videos) == 1 and len(images) == 1:
            video = videos[0]
            img = images[0]
            item = {
                'folder_name': folder_name,
                'folder_path': folder_path,
                'main_image': img['path'],
                'video_path': video['path'],
                'alternates': [],
                'match_type': 'sub_dedicated',
                'file_type': 'image',
                'is_alias': False,
                'primary_video_path': None,
                'is_sub_folder': True,
                'sub_folder_path': folder_path,
                'display_mode': 'single'
            }
            key = self._generate_item_key(item, folder_path)
            items_dict[key] = item
        
        # 多图单视频
        elif len(videos) == 1 and len(images) > 1:
            video = videos[0]
            main_img = None
            for cover_name in self.COVER_NAMES:
                for img in images:
                    if img['basename'].lower() == cover_name:
                        main_img = img
                        break
                if main_img:
                    break
            
            if not main_img:
                main_img = images[0]
            
            alternates = [img['path'] for img in images if img['path'] != main_img['path']]
            
            item = {
                'folder_name': folder_name,
                'folder_path': folder_path,
                'main_image': main_img['path'],
                'video_path': video['path'],
                'alternates': alternates,
                'match_type': 'sub_cover',
                'file_type': 'image',
                'is_alias': False,
                'primary_video_path': None,
                'is_sub_folder': True,
                'sub_folder_path': folder_path,
                'display_mode': 'single'
            }
            key = self._generate_item_key(item, folder_path)
            items_dict[key] = item
        
        # 多图多视频
        elif len(videos) > 1 and len(images) >= 1:
            display_mode = 'grid2' if len(images) == 2 else 'grid4'
            item = {
                'folder_name': folder_name,
                'folder_path': folder_path,
                'main_image': images[0]['path'] if images else None,
                'grid_images': [img['path'] for img in images[:4]],
                'video_path': None,
                'videos': [v['path'] for v in videos],
                'match_type': 'sub_multi_video_grid',
                'file_type': 'grid',
                'is_alias': False,
                'primary_video_path': None,
                'is_sub_folder': True,
                'sub_folder_path': folder_path,
                'display_mode': display_mode,
                'image_count': len(images),
                'video_count': len(videos)
            }
            key = self._generate_item_key(item, folder_path)
            items_dict[key] = item
        
        # 纯图片
        elif len(videos) == 0 and len(images) >= 1:
            if len(images) == 1:
                img = images[0]
                item = self._create_image_only_item(img, folder_path, folder_name=folder_name)
                item['is_sub_folder'] = True
                item['sub_folder_path'] = folder_path
                key = self._generate_item_key(item, folder_path)
                items_dict[key] = item
            else:
                standalone_images = []
                for img in images:
                    item = self._create_image_only_item(img, folder_path, folder_name=folder_name)
                    standalone_images.append(item)
                
                if standalone_images:
                    display_mode = 'grid2' if len(standalone_images) == 2 else 'grid4'
                    item = {
                        'folder_name': folder_name,
                        'folder_path': folder_path,
                        'main_image': standalone_images[0]['main_image'] if standalone_images else None,
                        'grid_images': [it['main_image'] for it in standalone_images[:4]],
                        'all_images': [it['main_image'] for it in standalone_images],
                        'video_path': None,
                        'match_type': 'sub_image_only_grid',
                        'file_type': 'grid',
                        'is_alias': False,
                        'primary_video_path': None,
                        'is_sub_folder': True,
                        'sub_folder_path': folder_path,
                        'display_mode': display_mode,
                        'image_count': len(standalone_images)
                    }
                    key = self._generate_item_key(item, folder_path)
                    items_dict[key] = item
        
        # 只有视频没有图片
        elif len(videos) >= 1 and len(images) == 0:
            for video in videos:
                item = {
                    'folder_name': folder_name,
                    'folder_path': folder_path,
                    'main_image': "__VIDEO_PLACEHOLDER__",
                    'video_path': video['path'],
                    'file_name': video['name'],
                    'match_type': 'sub_placeholder',
                    'file_type': 'video_placeholder',
                    'is_alias': False,
                    'primary_video_path': None,
                    'is_sub_folder': True,
                    'sub_folder_path': folder_path,
                    'display_mode': 'single'
                }
                key = self._generate_item_key(item, folder_path)
                items_dict[key] = item
        
        return items_dict
    
    def _process_current_folder(self, folder_path):
        """
        处理当前文件夹 - 返回字典结构
        """
        items = []
        
        try:
            entries = list(os.scandir(folder_path))
        except:
            return {}
        
        videos = []
        images = []
        
        for entry in entries:
            if not entry.is_file():
                continue
            if PathUtils.is_video_file(entry.path):
                videos.append({
                    'path': os.path.normpath(entry.path).lower(),
                    'name': entry.name,
                    'basename': os.path.splitext(entry.name)[0].lower(),
                    'ext': os.path.splitext(entry.name)[1].lower()
                })
            elif PathUtils.is_image_file(entry.path):
                images.append({
                    'path': os.path.normpath(entry.path).lower(),
                    'name': entry.name,
                    'basename': os.path.splitext(entry.name)[0].lower(),
                    'ext': os.path.splitext(entry.name)[1].lower()
                })
        
        images.sort(key=lambda x: x['name'].lower())
        items_dict = {}
        
        # 单视频
        if len(videos) == 1:
            video = videos[0]
            
            if len(images) >= 1:
                main_img = None
                for cover_name in self.COVER_NAMES:
                    for img in images:
                        if img['basename'] == cover_name:
                            main_img = img
                            break
                    if main_img:
                        break
                
                if not main_img:
                    main_img = images[0]
                
                alternates = [img['path'] for img in images if img['path'] != main_img['path']]
                
                item = {
                    'folder_name': ".",
                    'folder_path': folder_path,
                    'main_image': main_img['path'],
                    'video_path': video['path'],
                    'alternates': alternates,
                    'match_type': 'cover_priority' if any(img['basename'] in self.COVER_NAMES for img in images) else 'cover',
                    'file_type': 'image',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
                key = self._generate_item_key(item, folder_path)
                items_dict[key] = item
            else:
                item = {
                    'folder_name': ".",
                    'folder_path': folder_path,
                    'main_image': "__VIDEO_PLACEHOLDER__",
                    'video_path': video['path'],
                    'file_name': video['name'],
                    'match_type': 'placeholder',
                    'file_type': 'video_placeholder',
                    'is_alias': False,
                    'primary_video_path': None,
                    'display_mode': 'single'
                }
                key = self._generate_item_key(item, folder_path)
                items_dict[key] = item
        
        # 多视频
        elif len(videos) > 1:
            image_basenames = {img['basename']: img for img in images}
            matched_images = set()
            
            for video in videos:
                v_basename = video['basename']
                main_img = image_basenames.get(v_basename)
                alternates = []
                
                if main_img:
                    matched_images.add(v_basename)
                    alternates = self._find_alternate_images(v_basename, images, main_img['path'])
                    match_type = 'dedicated'
                else:
                    for cover_name in self.COVER_NAMES:
                        if cover_name in image_basenames:
                            main_img = image_basenames[cover_name]
                            matched_images.add(cover_name)
                            alternates = self._find_alternate_images(v_basename, images, main_img['path'])
                            match_type = 'cover'
                            break
                
                if main_img:
                    item = {
                        'folder_name': ".",
                        'folder_path': folder_path,
                        'main_image': main_img['path'],
                        'video_path': video['path'],
                        'alternates': alternates,
                        'match_type': match_type,
                        'file_type': 'image',
                        'is_alias': False,
                        'primary_video_path': None,
                        'display_mode': 'single'
                    }
                    key = self._generate_item_key(item, folder_path)
                    items_dict[key] = item
                else:
                    item = {
                        'folder_name': ".",
                        'folder_path': folder_path,
                        'main_image': "__VIDEO_PLACEHOLDER__",
                        'video_path': video['path'],
                        'file_name': video['name'],
                        'match_type': 'placeholder',
                        'file_type': 'video_placeholder',
                        'is_alias': False,
                        'primary_video_path': None,
                        'display_mode': 'single'
                    }
                    key = self._generate_item_key(item, folder_path)
                    items_dict[key] = item
            
            # 处理未匹配的图片
            unmatched_images = [img for img in images if img['basename'] not in matched_images]
            for img in unmatched_images:
                item = self._create_image_only_item(img, folder_path)
                key = self._generate_item_key(item, folder_path)
                if key not in items_dict:
                    items_dict[key] = item
        
        # 纯图片
        elif len(videos) == 0 and len(images) >= 1:
            for img in images:
                item = self._create_image_only_item(img, folder_path)
                key = self._generate_item_key(item, folder_path)
                items_dict[key] = item
        
        return items_dict
    
    def _find_alternate_images(self, video_basename, images, main_img_path):
        """查找视频的数字后缀图片作为副缩略图"""
        alternates = []
        for img in images:
            if img['path'] == main_img_path:
                continue
            
            img_basename = img['basename']
            match = self.ALTERNATE_SUFFIX_PATTERN.match(img_basename)
            if match:
                base_part = match.group(1)
                if base_part == video_basename:
                    alternates.append(img['path'])
        
        return alternates
    
    def _create_image_only_item(self, img, folder_path, folder_name="."):
        """创建纯图片项目"""
        is_alias = False
        primary_video_path = None
        
        if self.db and getattr(self.db, 'is_ready', False):
            db_file = self.db.get_file_by_path(img['path'])
            if db_file and db_file.get('is_alias'):
                is_alias = True
                primary_video_path = self.db.get_primary_video_path(db_file.get('id'))
        
        return {
            'folder_name': folder_name,
            'folder_path': folder_path,
            'main_image': img['path'],
            'video_path': primary_video_path if is_alias else None,
            'alternates': [],
            'match_type': 'linked' if is_alias else 'standalone',
            'file_type': 'image',
            'is_alias': is_alias,
            'primary_video_path': primary_video_path,
            'display_mode': 'single'
        }
    
    def _save_dict_to_cache(self, folder_path, sub_folders, items_dict, tree_state=None):
        """保存字典结构到缓存"""
        cache_files_info = {}
        
        for key, item in items_dict.items():
            main_image = item.get('main_image')
            
            if main_image == "__VIDEO_PLACEHOLDER__":
                file_path = item.get('video_path', main_image)
                file_name = item.get('file_name', 'video')
            elif main_image:
                file_path = main_image
                file_name = os.path.basename(main_image)
            else:
                file_path = item.get('sub_folder_path') or item.get('folder_path')
                file_name = item.get('folder_name', 'grid')
            
            cache_files_info[key] = {
                'item_key': key,
                'file_path': file_path,
                'file_name': file_name,
                'file_type': item['file_type'],
                'folder_path': item['folder_path'],
                'folder_name': item['folder_name'],
                'video_path': os.path.normpath(item.get('video_path')).lower() if item.get('video_path') else None,
                'video_code': item.get('video_code'),
                'primary_video_path': os.path.normpath(item.get('primary_video_path')).lower() if item.get('primary_video_path') else None,
                'is_alias': 1 if item.get('is_alias') else 0,
                'alternates': item.get('alternates', []),
                'match_type': item['match_type'],
                'display_mode': item.get('display_mode', 'single'),
                'is_sub_folder': item.get('is_sub_folder', False),
                'sub_folder_path': item.get('sub_folder_path'),
                'grid_images': item.get('grid_images', []),
                'all_images': item.get('all_images', []),
                'alias_count': item.get('alias_count', 0),
                'video_title': item.get('video_title', ''),
                'release_date': item.get('release_date', ''),
                'duration': item.get('duration', 0),
                'actors': item.get('actors', ''),
            }
        
        self.folder_cache.save_folder_cache(
            folder_path,
            sub_folders=sub_folders,
            files_info=cache_files_info,
            tree_state=tree_state
        )
    
    def _load_from_cache_to_dict(self, folder_path, cache_data):
        """从缓存恢复为字典结构"""
        items_dict = {}
        files_info = cache_data.get('files_info', {})
        
        if isinstance(files_info, dict):
            for key, cache_info in files_info.items():
                items_dict[key] = {
                    'folder_name': cache_info.get('folder_name', ''),
                    'folder_path': cache_info.get('folder_path', folder_path),
                    'main_image': cache_info.get('file_path', ''),
                    'video_path': cache_info.get('video_path'),
                    'video_code': cache_info.get('video_code'),
                    'primary_video_path': cache_info.get('primary_video_path'),
                    'alternates': cache_info.get('alternates', []),
                    'match_type': cache_info.get('match_type', 'standalone'),
                    'file_type': cache_info.get('file_type', 'image'),
                    'is_alias': bool(cache_info.get('is_alias', 0)),
                    'display_mode': cache_info.get('display_mode', 'single'),
                    'is_sub_folder': cache_info.get('is_sub_folder', False),
                    'sub_folder_path': cache_info.get('sub_folder_path'),
                    'grid_images': cache_info.get('grid_images', []),
                    'all_images': cache_info.get('all_images', []),
                    'alias_count': cache_info.get('alias_count', 0),
                    'video_title': cache_info.get('video_title', ''),
                    'release_date': cache_info.get('release_date', ''),
                    'duration': cache_info.get('duration', 0),
                    'actors': cache_info.get('actors', ''),
                }
        elif isinstance(files_info, list):
            for cache_info in files_info:
                key = cache_info.get('item_key')
                if not key:
                    key = cache_info.get('video_code')
                    if not key:
                        key = hashlib.md5(cache_info.get('file_path', '').encode()).hexdigest()[:12]
                
                items_dict[key] = {
                    'folder_name': cache_info.get('folder_name', ''),
                    'folder_path': cache_info.get('folder_path', folder_path),
                    'main_image': cache_info.get('file_path', ''),
                    'video_path': cache_info.get('video_path'),
                    'video_code': cache_info.get('video_code'),
                    'primary_video_path': cache_info.get('primary_video_path'),
                    'alternates': cache_info.get('alternates', []),
                    'match_type': cache_info.get('match_type', 'standalone'),
                    'file_type': cache_info.get('file_type', 'image'),
                    'is_alias': bool(cache_info.get('is_alias', 0)),
                    'display_mode': cache_info.get('display_mode', 'single'),
                    'is_sub_folder': cache_info.get('is_sub_folder', False),
                    'sub_folder_path': cache_info.get('sub_folder_path'),
                    'grid_images': cache_info.get('grid_images', []),
                    'all_images': cache_info.get('all_images', []),
                    'alias_count': cache_info.get('alias_count', 0),
                    'video_title': cache_info.get('video_title', ''),
                    'release_date': cache_info.get('release_date', ''),
                    'duration': cache_info.get('duration', 0),
                    'actors': cache_info.get('actors', ''),
                }
        
        return items_dict
    
    def _filter_files(self, files_info):
        """过滤文件列表，去重并过滤视频文件"""
        seen = set()
        result = []
        for f in files_info:
            fp = f.get('file_path')
            if fp:
                normalized = os.path.normpath(fp).lower()
                if normalized not in seen:
                    seen.add(normalized)
                    result.append(f)
        
        filtered = [f for f in result if f.get('file_type') != 'video']
        return filtered

    # ==================== 统一加载入口 ====================
    
    def load_folder_unified(self, folder_path, gallery_widget, status_callback, scroll_y=None, 
                            db_ready=False, force_reload=False, source="", on_loaded=None, 
                            tree_state=None, include_subfolders=True):
        """统一的文件夹加载入口"""
        if not folder_path:
            if self.debug:
                self.debug.print(f"[统一加载] 路径无效: {folder_path}")
            return
        
        # 【关键修复】对网络路径跳过 os.path.exists 检查，避免超时导致图库不加载
        is_network = folder_path.startswith('\\\\')
        if not is_network:
            try:
                if not os.path.exists(folder_path):
                    if self.debug:
                        self.debug.print(f"[统一加载] 路径不存在: {folder_path}")
                    return
            except Exception as e:
                if self.debug:
                    self.debug.print(f"[统一加载] 路径检查异常: {folder_path}, {e}")
                return
        
        if not force_reload and self._last_loaded_folder == folder_path and not str(folder_path).startswith('['):
            if self.debug:
                self.debug.print(f"[统一加载] 文件夹已在UI中显示，跳过: {os.path.basename(folder_path)}")
            return
        
        self._last_loaded_folder = folder_path
        gallery_widget._current_folder = folder_path
        self._is_scanning_folder = folder_path
        
        if status_callback:
            status_callback("正在扫描文件夹...", 0)
        
        def _do_scan():
            try:
                processed_dict = self._scan_folder_new(folder_path, tree_state=tree_state, 
                                                        include_subfolders=include_subfolders)
                processed_items = list(processed_dict.values())
                
                def _update_ui():
                    try:
                        if processed_items:
                            gallery_widget.load_items_with_dict(
                                processed_items, processed_dict, status_callback, scroll_y=scroll_y
                            )
                            
                            if status_callback:
                                status_callback(f"就绪 ({len(processed_items)}项)", 100)
                            
                            if on_loaded:
                                on_loaded(folder_path, len(processed_items))
                            
                            if self.debug:
                                self.debug.print(f"[统一加载] 来源[{source}] -> 已加载 {len(processed_items)} 项: {os.path.basename(folder_path)}")
                        else:
                            gallery_widget.clear()
                            if status_callback:
                                status_callback("文件夹为空", 100)
                            
                            if on_loaded:
                                on_loaded(folder_path, 0)
                    except Exception as e:
                        if self.debug:
                            self.debug.print(f"[统一加载] UI更新失败: {e}")
                            import traceback
                            traceback.print_exc()
                        if status_callback:
                            status_callback("加载失败", 100)
                    finally:
                        self._is_scanning_folder = None
                
                gallery_widget.parent.after(0, _update_ui)
                
            except Exception as e:
                if self.debug:
                    self.debug.print(f"[统一加载] 来源[{source}] 扫描失败: {e}")
                    import traceback
                    traceback.print_exc()
                
                def _on_error():
                    if status_callback:
                        status_callback("加载失败", 100)
                    self._is_scanning_folder = None
                
                gallery_widget.parent.after(0, _on_error)
        
        threading.Thread(target=_do_scan, daemon=True).start()
    
    # ==================== 公开更新方法 ====================
    
    def update_item_by_code(self, gallery_widget, video_code, updates):
        """
        更新指定番号的视频项
        
        Args:
            gallery_widget: GalleryViewer实例
            video_code: 视频番号
            updates: 要更新的字段字典
            
        Returns:
            bool: 是否更新成功
        """
        if hasattr(gallery_widget, 'update_item_by_code'):
            return gallery_widget.update_item_by_code(video_code, updates)
        return False
    
    def update_synced_status(self, gallery_widget, video_code, is_synced=True):
        """更新视频的同步状态"""
        return self.update_item_by_code(gallery_widget, video_code, {
            'has_synced_video': is_synced
        })
    
    def update_video_info(self, gallery_widget, video_code, video_info):
        """
        更新视频采集信息
        
        Args:
            gallery_widget: GalleryViewer实例
            video_code: 视频番号
            video_info: 采集到的视频信息
        """
        updates = {
            'video_title': video_info.get('title', ''),
            'video_code': video_info.get('code', video_code),
            'release_date': video_info.get('date', ''),
            'duration': video_info.get('duration', 0),
            'actors': video_info.get('actors_str', ''),
            'has_synced_video': True,
        }
        return self.update_item_by_code(gallery_widget, video_code, updates)