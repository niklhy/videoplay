#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扩展配置管理器 - 支持设备分组和相对路径标签
"""
import os
import hashlib
import platform
import time
from core import ConfigManager


class ExtendedConfigManager(ConfigManager):
    """扩展配置管理器 - 支持设备分组和相对路径标签"""
    
    def __init__(self):
        super().__init__()
        self.device_id = platform.node()  # 自动获取计算机名
        
        # 关键修复：从 _config 字典加载或初始化设备相关配置
        # 确保新配置结构存在（从配置文件加载或创建）
        if 'devices' not in self._config:
            self._config['devices'] = {}
        self.devices = self._config['devices']  # 直接引用，确保修改同步到 _config
        
        if 'tags_v2' not in self._config:
            self._config['tags_v2'] = {}
        self.tags_v2 = self._config['tags_v2']  # 直接引用
        
        if 'current_device_name' not in self._config:
            self._config['current_device_name'] = self.device_id
        self.current_device_name = self._config['current_device_name']

        # 新增：书签状态存储（记录每个书签最后浏览的子文件夹）
        if 'bookmark_states' not in self._config:
            self._config['bookmark_states'] = {}
        self.bookmark_states = self._config['bookmark_states']

        # 确保当前设备存在（不检查网络路径有效性，避免阻塞）
        if self.device_id not in self.devices:
            self.devices[self.device_id] = {
                "display_name": self.current_device_name,
                "bookmarks": []
            }
            self.save_config()
        
        # 确保标签列表存在（兼容旧属性）
        if not hasattr(self, 'tags'):
            self.tags = self._config.get('tags', [])
    
    def check_network_paths_async(self, callback=None):
        """
        异步检查网络路径有效性（避免启动时阻塞）
        
        Args:
            callback: 检查完成后的回调函数，接收(unavailable_paths)参数
        """
        import threading
        
        def check_paths():
            unavailable = []
            for dev_id, device in self.devices.items():
                for bm in device.get('bookmarks', []):
                    path = bm.get('path', '')
                    # 只检查网络路径
                    if path.startswith('\\\\'):
                        try:
                            # 使用快速超时检查（如果可能）
                            import subprocess
                            result = subprocess.run(
                                ['ping', '-n', '1', '-w', '1000', path.split('\\')[2]],
                                capture_output=True,
                                timeout=2
                            )
                            if result.returncode != 0:
                                unavailable.append(path)
                        except:
                            # 如果快速检查失败，使用os.path.exists（有超时风险）
                            # 或者标记为未知状态
                            pass
            
            if callback:
                callback(unavailable)
        
        threading.Thread(target=check_paths, daemon=True).start()
        
    def _generate_bookmark_id(self, path):
        """生成书签唯一ID（基于路径哈希）"""
        return hashlib.md5(path.encode('utf-8')).hexdigest()[:12]
    
    def _get_bookmark_for_path(self, folder_path):
        """查找路径所属的书签信息"""
        folder_path = os.path.normpath(folder_path).lower()
        device = self.devices.get(self.device_id, {})
        for bm in device.get('bookmarks', []):
            bm_path = os.path.normpath(bm['path']).lower()
            if folder_path == bm_path or folder_path.startswith(bm_path + os.sep):
                return bm
        return None
    
    def _get_relative_path(self, file_path):
        """获取文件相对于书签的路径"""
        file_path = os.path.normpath(file_path).lower()
        bm = self._get_bookmark_for_path(os.path.dirname(file_path))
        if bm:
            try:
                return os.path.relpath(file_path, os.path.normpath(bm['path']).lower())
            except:
                return None
        return None

    def save_bookmark_state(self, bm_id, folder_path, tree_state=None, gallery_scroll_y=0):
        """保存书签浏览状态"""
        if not bm_id:
            return
        self.bookmark_states[bm_id] = {
            'last_folder': folder_path,
            'tree_state': tree_state or {},
            'gallery_scroll_y': gallery_scroll_y,
            'timestamp': time.time()
        }
        self.save_config()

    # 新增：书签状态管理方法
    def get_bookmark_state(self, bm_id):
        """获取指定书签的最后状态"""
        if not bm_id:
            return {}
        return self.bookmark_states.get(bm_id, {}).copy()
        
    # ==================== 设备管理接口 ====================
    
    def get_current_device_id(self):
        return self.device_id
    
    def set_device_display_name(self, name):
        """设置当前设备的显示名称"""
        if self.device_id in self.devices:
            self.devices[self.device_id]['display_name'] = name
            self.current_device_name = name
            self.save_config()
    
    def get_all_devices(self):
        """获取所有设备列表"""
        return self.devices
    
    def add_bookmark(self, folder_path, display_name=None):
        """添加新书签（新格式）"""
        folder_path = os.path.normpath(folder_path.strip()).lower()
        if not os.path.exists(folder_path):
            return False
            
        device = self.devices.setdefault(self.device_id, {
            "display_name": self.current_device_name,
            "bookmarks": []
        })
        
        # 检查是否已存在
        for bm in device['bookmarks']:
            if os.path.normpath(bm['path']).lower() == folder_path:
                return False
        
        bm_id = self._generate_bookmark_id(folder_path)
        device['bookmarks'].append({
            "path": folder_path,
            "display_name": display_name or os.path.basename(folder_path),
            "id": bm_id
        })
        self.save_config()
        return True
    
    def remove_bookmark(self, index_or_id):
        """移除书签（支持索引或ID）"""
        device = self.devices.get(self.device_id, {})
        bookmarks = device.get('bookmarks', [])
        
        if isinstance(index_or_id, int) and 0 <= index_or_id < len(bookmarks):
            removed_bm = bookmarks.pop(index_or_id)
            # 清理相关标签
            if self.device_id in self.tags_v2 and removed_bm['id'] in self.tags_v2[self.device_id]:
                del self.tags_v2[self.device_id][removed_bm['id']]
            self.save_config()
            return True
        return False
    
    def update_bookmark_display_name(self, bookmark_id, new_name):
        """更新书签的显示名称"""
        device_id = self.device_id
        if device_id not in self.devices:
            return False
        
        bookmarks = self.devices[device_id].get('bookmarks', [])
        for bm in bookmarks:
            if bm.get('id') == bookmark_id:
                bm['display_name'] = new_name
                self.save_config()
                return True
        return False
    
    def get_bookmarks_for_current_device(self):
        """获取当前设备的书签列表"""
        return self.devices.get(self.device_id, {}).get('bookmarks', [])
    
    # ==================== 标签管理接口（相对路径版）====================
    
    def get_video_tags(self, video_path):
        """获取视频标签（自动处理路径转换）"""
        video_path = os.path.normpath(video_path).lower()
        rel_path = self._get_relative_path(video_path)
        
        if not rel_path:
            # 回退到检查是否在其他设备中有匹配（同步过来的数据）
            for dev_id, dev_tags in self.tags_v2.items():
                for bm_id, files in dev_tags.items():
                    for stored_rel_path, tags in files.items():
                        # 尝试匹配绝对路径（用于其他设备同步过来的数据）
                        if dev_id in self.devices:
                            for bm in self.devices[dev_id].get('bookmarks', []):
                                if bm['id'] == bm_id:
                                    check_path = os.path.normpath(os.path.join(bm['path'], stored_rel_path)).lower()
                                    if check_path == video_path:
                                        return tags
            return []
        
        bm = self._get_bookmark_for_path(os.path.dirname(video_path))
        if not bm:
            return []
            
        bm_id = bm['id']
        device_tags = self.tags_v2.get(self.device_id, {})
        bm_tags = device_tags.get(bm_id, {})
        
        return bm_tags.get(rel_path, [])
    
    def set_video_tags(self, video_path, tags_list):
        """设置视频标签（使用相对路径存储）"""
        video_path = os.path.normpath(video_path).lower()
        rel_path = self._get_relative_path(video_path)
        
        if not rel_path:
            # 不在任何书签内，无法打标签
            return False
        
        bm = self._get_bookmark_for_path(os.path.dirname(video_path))
        bm_id = bm['id']
        
        # 确保结构存在
        if self.device_id not in self.tags_v2:
            self.tags_v2[self.device_id] = {}
        if bm_id not in self.tags_v2[self.device_id]:
            self.tags_v2[self.device_id][bm_id] = {}
        
        if tags_list:
            self.tags_v2[self.device_id][bm_id][rel_path] = list(tags_list)
        else:
            # 移除标签
            if rel_path in self.tags_v2[self.device_id][bm_id]:
                del self.tags_v2[self.device_id][bm_id][rel_path]
        
        return True
    
    def get_all_video_tags(self):
        """获取所有标签数据（格式：{绝对路径: [标签]}，向后兼容）"""
        result = {}
        
        for dev_id, dev_data in self.tags_v2.items():
            # 获取该设备的书签映射
            device_bookmarks = {}
            if dev_id in self.devices:
                for bm in self.devices[dev_id].get('bookmarks', []):
                    device_bookmarks[bm['id']] = bm['path']
            
            for bm_id, files in dev_data.items():
                bm_path = device_bookmarks.get(bm_id)
                if not bm_path or not os.path.exists(bm_path):
                    continue
                
                for rel_path, tags in files.items():
                    abs_path = os.path.normpath(os.path.join(bm_path, rel_path)).lower()
                    if os.path.exists(abs_path):
                        result[abs_path] = tags
        
        return result
    
    def get_all_tags(self):
        """获取所有标签定义列表，按自定义顺序（tags列表定义的顺序）
        修复：确保显示所有定义的标签，包括计数为0的"""
        # 从配置直接获取标签定义（确保最新）
        defined_tags = self._config.get('tags', [])

        # 收集所有实际使用的标签
        used_tags_set = set()
        for dev_data in self.tags_v2.values():
            for bm_data in dev_data.values():
                for tags in bm_data.values():
                    used_tags_set.update(tags)

        # 合并所有标签：先按defined_tags顺序，再补充剩余的按字母顺序
        result = []

        # 1. 先添加所有已定义的标签（保持用户定义的顺序）
        for tag in defined_tags:
            if tag:  # 确保不是空字符串
                result.append(tag)

        # 2. 补充只在实际使用中但不在定义列表中的标签（按字母顺序排在后面）
        remaining = used_tags_set - set(defined_tags)
        if remaining:
            result.extend(sorted(remaining))

        return result


    def add_tag(self, tag_name):
        """添加全局标签定义"""
        if tag_name not in self.tags:
            self.tags.append(tag_name)
            self.save_config()
            return True
        return False
    
    def remove_tag(self, tag_name):
        """移除全局标签，同时清理所有文件上的该标签"""
        if tag_name in self.tags:
            self.tags.remove(tag_name)
        
        # 清理所有设备上的该标签
        for dev_id in list(self.tags_v2.keys()):
            for bm_id in list(self.tags_v2[dev_id].keys()):
                for rel_path in list(self.tags_v2[dev_id][bm_id].keys()):
                    if tag_name in self.tags_v2[dev_id][bm_id][rel_path]:
                        self.tags_v2[dev_id][bm_id][rel_path].remove(tag_name)
                        if not self.tags_v2[dev_id][bm_id][rel_path]:
                            del self.tags_v2[dev_id][bm_id][rel_path]
        
        self.save_config()
        return True
    
    def rename_tag(self, old_name, new_name):
        """重命名标签"""
        # 更新全局标签
        if old_name in self.tags:
            idx = self.tags.index(old_name)
            self.tags[idx] = new_name
        
        # 更新所有文件上的标签
        for dev_id in self.tags_v2:
            for bm_id in self.tags_v2[dev_id]:
                for rel_path in list(self.tags_v2[dev_id][bm_id].keys()):
                    if old_name in self.tags_v2[dev_id][bm_id][rel_path]:
                        self.tags_v2[dev_id][bm_id][rel_path].remove(old_name)
                        if new_name not in self.tags_v2[dev_id][bm_id][rel_path]:
                            self.tags_v2[dev_id][bm_id][rel_path].append(new_name)
        
        self.save_config()
        return True
