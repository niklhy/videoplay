#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库管理模块 - 性能优化版
优化点：
1. 支持快速启动模式（fast_mode），跳过重复的结构检查
2. 异步加载缓存，不阻塞主线程
3. 优化SQLite连接参数（WAL模式、缓存设置）
4. 延迟同步策略，启动时不全量扫描
"""
import os
import sqlite3
import threading
import time
import json
from datetime import datetime
from pathlib import Path


def adapt_datetime(dt):
    """将datetime适配为SQLite存储格式"""
    if dt is None:
        return None
    return dt.isoformat()


def convert_datetime(s):
    """将SQLite存储格式转换为datetime"""
    if s is None:
        return None
    if isinstance(s, str):
        # 处理带T的ISO格式
        s = s.replace('T', ' ')
        if '.' in s:
            s = s.split('.')[0]
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except:
            try:
                return datetime.strptime(s, '%Y-%m-%d')
            except:
                return None
    return s


class DatabaseManager:
    """SQLite数据库管理器 - 性能优化版"""
    
    def __init__(self, db_path=None, debug_utils=None, fast_mode=True):
        """
        初始化数据库管理器
        
        Args:
            db_path: 数据库文件路径
            debug_utils: 调试工具
            fast_mode: 快速模式，True时跳过重复的结构检查，大幅提升启动速度
        """
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "media_library.db")
        self.db_path = db_path
        self.debug = debug_utils
        self._fast_mode = fast_mode
        
        # 内存缓存（移除 tags 和 file_tags）
        self._cache = {
            'folders': {},      # {path: folder_info}
            'files': {},        # {path: file_info}
            'actors': {},       # {id: actor_info}
            'file_actors': {},   # {file_id: [actor_ids]}
            'bookmarks': [],    # [folder_paths]
            'video_relations': {}  # {alias_file_id: primary_file_id}
        }
        
        self._cache_lock = threading.RLock()
        self._local = threading.local()  # 线程本地存储
        self._initialized = False
        self._cache_loaded = False  # 新增：标记缓存是否加载完成
        
        # 初始化数据库连接
        self._init_database(fast_mode=fast_mode)
    
    def _get_connection(self):
        """获取线程专属的数据库连接 - 优化版"""
        need_create = False
        
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            need_create = True
        else:
            # 检测连接是否仍然有效
            try:
                self._local.conn.execute("SELECT 1")
            except (sqlite3.ProgrammingError, sqlite3.Error):
                # 连接已关闭或失效，需要重新创建
                need_create = True
        
        if need_create:
            # 优化连接参数
            self._local.conn = sqlite3.connect(
                self.db_path, 
                check_same_thread=False,
                isolation_level=None,  # 自动提交模式，减少事务开销
                cached_statements=100  # 缓存预编译语句
            )
            self._local.conn.row_factory = sqlite3.Row
            
            # 性能优化：只需执行一次
            cursor = self._local.conn.cursor()
            # WAL模式提高并发性能
            cursor.execute("PRAGMA journal_mode = WAL")
            # 同步模式为NORMAL，平衡性能和安全性
            cursor.execute("PRAGMA synchronous = NORMAL")
            # 增加页面缓存（约40MB）
            cursor.execute("PRAGMA cache_size = 10000")
            # 临时表存储在内存
            cursor.execute("PRAGMA temp_store = MEMORY")
            # 内存映射I/O（如果文件不大）
            cursor.execute("PRAGMA mmap_size = 30000000000")
            cursor.close()
            
        return self._local.conn
    
    def _init_database(self, fast_mode=True):
        """
        初始化数据库连接和表结构
        
        Args:
            fast_mode: True时跳过详细的结构检查，假设数据库结构正常
        """
        try:
            # 检查数据库文件是否存在
            if not os.path.exists(self.db_path):
                print(f"数据库不存在: {self.db_path}")
                print("请先运行 init_database.py 创建数据库")
                self._initialized = False
                return
            
            # 快速模式：减少初始化检查，假设数据库结构正常
            if fast_mode:
                # 只测试连接，不做完整验证
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # 极简验证：检查核心表是否存在（只需查询sqlite_master）
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='media_files'"
                )
                if not cursor.fetchone():
                    print("数据库缺少核心表，需要初始化")
                    self._initialized = False
                    cursor.close()
                    return
                
                cursor.close()
                self._initialized = True
                
                # 关键优化：延迟加载缓存到后台，不阻塞启动
                if fast_mode:
                    threading.Thread(target=self._load_all_to_cache_async, daemon=True).start()
                else:
                    self._load_all_to_cache()
                
                if self.debug:
                    self.debug.print(f"数据库快速连接成功: {self.db_path}")
                return
            
            # 完整验证模式（首次创建或更新时使用）
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 验证核心表结构
            required_tables = ['folders', 'media_files', 'actors', 'file_actors', 'actor_relations', 'video_relations']
            missing_tables = []
            
            for table in required_tables:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)
                )
                if not cursor.fetchone():
                    missing_tables.append(table)
            
            if missing_tables:
                error_msg = f"缺少必需的表: {', '.join(missing_tables)}"
                if self.debug:
                    self.debug.print(error_msg)
                print(f"数据库初始化失败: {error_msg}")
                print("请运行 init_database.py 创建数据库")
                self._initialized = False
                cursor.close()
                return
            
            # 检查 media_files 字段（仅在非快速模式下）
            cursor.execute("PRAGMA table_info(media_files)")
            columns = {row[1]: row for row in cursor.fetchall()}
            
            if 'tag_summary' not in columns:
                if self.debug:
                    self.debug.print("警告: media_files 表缺少 tag_summary 字段，尝试添加...")
                try:
                    cursor.execute("ALTER TABLE media_files ADD COLUMN tag_summary TEXT")
                    conn.commit()
                    if self.debug:
                        self.debug.print("已添加 tag_summary 字段")
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"添加 tag_summary 字段失败: {e}")
            
            if 'is_alias' not in columns:
                if self.debug:
                    self.debug.print("警告: media_files 表缺少 is_alias 字段，尝试添加...")
                try:
                    cursor.execute("ALTER TABLE media_files ADD COLUMN is_alias INTEGER DEFAULT 0")
                    conn.commit()
                    if self.debug:
                        self.debug.print("已添加 is_alias 字段")
                except Exception as e:
                    if self.debug:
                        self.debug.print(f"添加 is_alias 字段失败: {e}")

            cursor.close()
            self._initialized = True
            
            # 加载缓存
            self._load_all_to_cache()
            
            if self.debug:
                self.debug.print(f"数据库连接成功: {self.db_path}")
                
        except Exception as e:
            print(f"数据库初始化失败: {e}")
            print("请确保已运行 init_database.py 创建数据库")
            self._initialized = False
    
    # ==================== 异步缓存加载（新增）====================
    
    def _load_all_to_cache_async(self):
        """异步加载所有数据到内存缓存，不阻塞主线程"""
        try:
            self._load_all_to_cache()
            self._cache_loaded = True
            if self.debug:
                self.debug.print(f"后台缓存加载完成: {len(self._cache['folders'])} 文件夹, "
                               f"{len(self._cache['files'])} 文件")
        except Exception as e:
            if self.debug:
                self.debug.print(f"后台缓存加载失败: {e}")
    
    def async_load_cache(self, callback=None):
        """异步加载数据库到缓存"""
        def load_thread():
            try:
                self._load_all_to_cache()
                self._cache_loaded = True
                if callback:
                    callback(True, "缓存加载完成")
            except Exception as e:
                if self.debug:
                    self.debug.print(f"缓存加载失败: {e}")
                if callback:
                    callback(False, str(e))
        
        thread = threading.Thread(target=load_thread, daemon=True)
        thread.start()
        return thread
    
    def _load_all_to_cache(self, device_id=None, bookmark_id=None):
        """将所有数据加载到内存缓存 - 兼容旧数据库结构"""
        with self._cache_lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 清空缓存
            self._cache = {
                'folders': {},
                'files': {},
                'actors': {},
                'file_actors': {},
                'bookmarks': [],
                'video_relations': {},
                'code_index': {}
            }

            # ==================== 1. 加载 folders 表 ====================
            # 先获取表结构，确定有哪些列
            cursor.execute("PRAGMA table_info(folders)")
            folder_columns = {col[1] for col in cursor.fetchall()}
            
            # 动态构建 SELECT 语句，只选择存在的列
            folder_select_cols = ['folder_id', 'folder_path', 'folder_name', 'parent_path', 
                                'is_bookmark', 'last_modified', 'file_count']
            if 'device_id' in folder_columns:
                folder_select_cols.append('device_id')
            if 'bookmark_id' in folder_columns:
                folder_select_cols.append('bookmark_id')
            
            cursor.execute(f"""
                SELECT {', '.join(folder_select_cols)}
                FROM folders 
                WHERE is_deleted = 0 OR is_deleted IS NULL
            """)
            
            for row in cursor.fetchall():
                folder_path = os.path.normpath(row['folder_path']) if row['folder_path'] else row['folder_path']
                parent_path = os.path.normpath(row['parent_path']) if row['parent_path'] else None
                
                # 安全获取字段值
                device_id_val = 'unknown'
                bookmark_id_val = None
                try:
                    if 'device_id' in folder_columns:
                        device_id_val = row['device_id'] or 'unknown'
                except (IndexError, KeyError):
                    pass
                try:
                    if 'bookmark_id' in folder_columns:
                        bookmark_id_val = row['bookmark_id']
                except (IndexError, KeyError):
                    pass
                
                self._cache['folders'][folder_path] = {
                    'id': row['folder_id'],
                    'path': folder_path,
                    'name': row['folder_name'],
                    'parent': parent_path,
                    'is_bookmark': bool(row['is_bookmark']) if row['is_bookmark'] else False,
                    'modified': convert_datetime(row['last_modified']),
                    'file_count': row['file_count'] or 0,
                    'device_id': device_id_val,
                    'bookmark_id': bookmark_id_val
                }
                if row['is_bookmark']:
                    self._cache['bookmarks'].append(folder_path)

            # ==================== 2. 加载 media_files 表 ====================
            # 获取表结构
            cursor.execute("PRAGMA table_info(media_files)")
            media_columns = {col[1] for col in cursor.fetchall()}
            
            # 动态构建 SELECT 语句
            media_select_cols = ['file_id', 'file_path', 'file_name', 'file_type', 'file_size',
                                'video_title', 'duration', 'release_date', 'video_code',
                                'last_modified', 'is_alias', 'tag_summary']
            if 'device_id' in media_columns:
                media_select_cols.append('device_id')
            if 'bookmark_id' in media_columns:
                media_select_cols.append('bookmark_id')
            
            cursor.execute(f"""
                SELECT f.{', f.'.join(media_select_cols)}, fo.folder_path as folder_dir
                FROM media_files f 
                JOIN folders fo ON f.folder_id = fo.folder_id
                WHERE f.is_deleted = 0 OR f.is_deleted IS NULL
            """)
            
            for row in cursor.fetchall():
                file_path = os.path.normpath(row['file_path']) if row['file_path'] else row['file_path']
                folder_dir = os.path.normpath(row['folder_dir']) if row['folder_dir'] else row['folder_dir']
                
                # 安全获取 is_alias
                is_alias_val = 0
                try:
                    if 'is_alias' in media_columns:
                        is_alias_val = row['is_alias'] or 0
                except (IndexError, KeyError):
                    pass
                
                # 安全获取 device_id
                device_id_val = 'unknown'
                try:
                    if 'device_id' in media_columns:
                        device_id_val = row['device_id'] or 'unknown'
                except (IndexError, KeyError):
                    pass
                
                # 安全获取 bookmark_id
                bookmark_id_val = None
                try:
                    if 'bookmark_id' in media_columns:
                        bookmark_id_val = row['bookmark_id']
                except (IndexError, KeyError):
                    pass
                
                self._cache['files'][file_path] = {
                    'id': row['file_id'],
                    'file_id': row['file_id'],
                    'folder_id': row['folder_id'],
                    'folder_path': folder_dir,
                    'name': row['file_name'],
                    'file_name': row['file_name'],
                    'type': row['file_type'],
                    'size': row['file_size'] or 0,
                    'file_size': row['file_size'] or 0,
                    'video_title': row['video_title'] if 'video_title' in media_columns and row['video_title'] else None,
                    'duration': row['duration'] if 'duration' in media_columns and row['duration'] else 0,
                    'release_date': row['release_date'] if 'release_date' in media_columns and row['release_date'] else None,
                    'video_code': row['video_code'] if 'video_code' in media_columns and row['video_code'] else None,
                    'modified': convert_datetime(row['last_modified']),
                    'is_alias': is_alias_val,
                    'tag_summary': row['tag_summary'] if 'tag_summary' in media_columns and row['tag_summary'] else None,
                    'device_id': device_id_val,
                    'bookmark_id': bookmark_id_val
                }

            # ==================== 3. 加载 actors 表 ====================
            cursor.execute("PRAGMA table_info(actors)")
            actor_columns = {col[1] for col in cursor.fetchall()}
            
            actor_select_cols = ['actor_id', 'actor_name', 'actor_name_cn', 'sort_char', 
                                'created_date', 'updated_date']
            if 'avatar_url' in actor_columns:
                actor_select_cols.append('avatar_url')
            if 'sort_order' in actor_columns:
                actor_select_cols.append('sort_order')
            
            cursor.execute(f"SELECT {', '.join(actor_select_cols)} FROM actors")
            
            for row in cursor.fetchall():
                avatar_url_val = None
                if 'avatar_url' in actor_columns:
                    try:
                        avatar_url_val = row['avatar_url']
                    except (IndexError, KeyError):
                        pass
                
                sort_order_val = 0
                if 'sort_order' in actor_columns:
                    try:
                        sort_order_val = row['sort_order'] or 0
                    except (IndexError, KeyError):
                        pass
                
                self._cache['actors'][row['actor_id']] = {
                    'id': row['actor_id'],
                    'name': row['actor_name'],
                    'name_cn': row['actor_name_cn'] if row['actor_name_cn'] else None,
                    'sort_char': row['sort_char'] if row['sort_char'] else None,
                    'avatar_url': avatar_url_val,
                    'sort_order': sort_order_val,
                    'created': convert_datetime(row['created_date']),
                    'updated': convert_datetime(row['updated_date'])
                }

            # ==================== 4. 加载 file_actors 表 ====================
            cursor.execute("SELECT file_id, actor_id FROM file_actors")
            for row in cursor.fetchall():
                file_id = row['file_id']
                actor_id = row['actor_id']
                if file_id not in self._cache['file_actors']:
                    self._cache['file_actors'][file_id] = []
                self._cache['file_actors'][file_id].append(actor_id)

            # ==================== 5. 加载 video_relations 表 ====================
            cursor.execute("SELECT alias_file_id, primary_file_id FROM video_relations")
            for row in cursor.fetchall():
                self._cache['video_relations'][row['alias_file_id']] = row['primary_file_id']

            # 构建番号索引
            self._build_code_index()

            cursor.close()

            if self.debug:
                self.debug.print(f"缓存加载完成: {len(self._cache['folders'])} 文件夹, "
                            f"{len(self._cache['files'])} 文件, "
                            f"{len(self._cache['actors'])} 演员, "
                            f"{len(self._cache['video_relations'])} 视频关联, "
                            f"{len(self._cache['code_index'])} 番号索引")
                
    def _build_code_index(self):
        """构建番号索引"""
        from core import extract_code_from_text
        
        code_index = {}
        
        for file_path, file_info in self._cache['files'].items():
            # 基本有效性检查
            if not file_path or not isinstance(file_path, str):
                continue
            
            if file_info.get('type') != 'video':
                continue
            
            codes_found = set()
            
            # 从视频文件名提取番号
            video_name = file_info.get('name', '')
            file_code = extract_code_from_text(video_name)
            if file_code:
                codes_found.add(file_code['canonical'])
                codes_found.add(file_code['full'])
            
            # 从文件夹名提取番号
            folder_path = file_info.get('folder_path', '')
            folder_name = os.path.basename(folder_path)
            folder_code = extract_code_from_text(folder_name)
            if folder_code:
                codes_found.add(folder_code['canonical'])
                codes_found.add(folder_code['full'])
            
            # 从数据库video_code字段提取
            db_video_code = file_info.get('video_code', '')
            if db_video_code:
                db_code = extract_code_from_text(db_video_code)
                if db_code:
                    codes_found.add(db_code['canonical'])
                    codes_found.add(db_code['full'])
                else:
                    codes_found.add(db_video_code.lower().replace('-', '').replace('_', ''))
            
            # 注册到索引
            for code in codes_found:
                if code:
                    if code not in code_index:
                        code_index[code] = []
                    # 避免重复
                    if not any(existing.get('file_id') == file_info.get('file_id') for existing in code_index[code]):
                        code_index[code].append(file_info)
        
        self._cache['code_index'] = code_index
        if self.debug:
            self.debug.print(f"[番号索引] 构建完成: {len(code_index)} 个唯一番号, "
                           f"共索引 {sum(len(v) for v in code_index.values())} 个视频")
            
    def search_by_code(self, search_text, device_id=None, bookmark_id=None):
        """通过番号快速搜索视频 - 增加设备/书签过滤
        
        Args:
            search_text: 搜索文本（番号/编号）
            device_id: 设备标识，过滤该设备的数据
            bookmark_id: 书签标识，过滤该书签的数据
        
        Returns:
            list: 匹配的视频file_info列表
        """
        from core import extract_code_from_text, get_pattern_matcher

        if not self.is_cache_ready:
            return []

        search_code = extract_code_from_text(search_text)
        if not search_code:
            return self._fallback_code_search(search_text, device_id, bookmark_id)

        matcher = get_pattern_matcher()
        results = []
        matched_codes = set()

        with self._cache_lock:
            code_index = self._cache.get('code_index', {})

            # 1. 精确匹配（canonical）
            canonical = search_code['canonical']
            if canonical in code_index:
                for file_info in code_index[canonical]:
                    # 增加设备过滤
                    if device_id and file_info.get('device_id') not in [device_id, 'unknown']:
                        continue
                    if bookmark_id and file_info.get('bookmark_id') != bookmark_id:
                        continue
                    if not any(r['file_id'] == file_info['file_id'] for r in results):
                        results.append(file_info)
                matched_codes.add(canonical)

            # 2. 全小写匹配（去掉分隔符）
            full = search_code['full']
            if full in code_index and full not in matched_codes:
                for file_info in code_index[full]:
                    if device_id and file_info.get('device_id') not in [device_id, 'unknown']:
                        continue
                    if bookmark_id and file_info.get('bookmark_id') != bookmark_id:
                        continue
                    if not any(r['file_id'] == file_info['file_id'] for r in results):
                        results.append(file_info)
                matched_codes.add(full)

            # 3. 模糊匹配（遍历索引key）
            for idx_code, file_list in code_index.items():
                if idx_code in matched_codes:
                    continue

                idx_code_parsed = extract_code_from_text(idx_code)
                if idx_code_parsed and matcher.is_strict_match(search_code, idx_code_parsed) >= 1:
                    for file_info in file_list:
                        # 增加设备过滤
                        if device_id and file_info.get('device_id') not in [device_id, 'unknown']:
                            continue
                        if bookmark_id and file_info.get('bookmark_id') != bookmark_id:
                            continue
                        if not any(r['file_id'] == file_info['file_id'] for r in results):
                            results.append(file_info)
                    matched_codes.add(idx_code)

        if self.debug:
            self.debug.print(f"[番号搜索] 索引查找 '{search_text}' -> 匹配 {len(results)} 个视频 "
                            f"(device_id={device_id}, bookmark_id={bookmark_id})")

        return results
    def _fallback_code_search(self, search_text, device_id=None, bookmark_id=None):
        """后备搜索 - 当无法解析为番号格式时使用简单包含匹配
        
        Args:
            search_text: 搜索文本
            device_id: 设备标识过滤
            bookmark_id: 书签标识过滤
        """
        search_lower = search_text.lower()
        results = []

        with self._cache_lock:
            for file_path, file_info in self._cache['files'].items():
                if file_info.get('type') != 'video':
                    continue

                # 设备过滤
                if device_id and file_info.get('device_id') not in [device_id, 'unknown']:
                    continue
                if bookmark_id and file_info.get('bookmark_id') != bookmark_id:
                    continue

                # 检查文件名、文件夹名、数据库video_code
                video_name = file_info.get('name', '').lower()
                folder_name = file_info.get('folder_path', '').lower()
                db_code = (file_info.get('video_code') or '').lower()

                if (search_lower in video_name or 
                    search_lower in folder_name or 
                    search_lower in db_code):
                    results.append(file_info)

        return results
    def get_folder_images_from_cache(self, folder_path):
        """从缓存获取文件夹下的所有图片路径"""
        images = []
        folder_path_norm = os.path.normpath(folder_path) if folder_path else folder_path
        
        with self._cache_lock:
            for fp, info in self._cache['files'].items():
                info_folder = os.path.normpath(info.get('folder_path', '')) if info.get('folder_path') else ''
                if (info_folder == folder_path_norm and 
                    info.get('type') == 'image' and
                    os.path.exists(fp)):
                    images.append(fp)
        
        return images

    # ==================== 视频关联管理接口 ====================
    
    def get_primary_video_path(self, file_id):
        """获取关联视频的主视频路径"""
        if not self._initialized:
            return None
        
        # 先查缓存
        with self._cache_lock:
            if file_id in self._cache['video_relations']:
                primary_id = self._cache['video_relations'][file_id]
                # 遍历缓存查找对应的路径
                for path, info in self._cache['files'].items():
                    if info.get('id') == primary_id and info.get('type') == 'video':
                        return path
        
        # 缓存未命中，查数据库
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mf.file_path 
                FROM video_relations vr
                JOIN media_files mf ON vr.primary_file_id = mf.file_id
                WHERE vr.alias_file_id = ?
            """, (file_id,))
            result = cursor.fetchone()
            cursor.close()
            if result:
                # 修复：使用索引访问，不是 .get()
                return result['file_path']
            return None
        except Exception as e:
            if self.debug:
                self.debug.print(f"获取主视频路径失败: {e}")
            return None
    
    def set_video_relation(self, alias_file_id, primary_file_id):
        """设置视频关联关系"""
        if not self._initialized:
            return False
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            # 获取关联文件的路径
            cursor.execute("SELECT file_path, folder_id FROM media_files WHERE file_id = ?", 
                          (alias_file_id,))
            alias_info = cursor.fetchone()
            
            if not alias_info:
                return False
            
            alias_path = alias_info['file_path']
            alias_folder = os.path.normpath(os.path.dirname(alias_path))
            
            # 插入或更新关联关系
            cursor.execute("""
                INSERT OR REPLACE INTO video_relations 
                (alias_file_id, primary_file_id, alias_folder_path, updated_date)
                VALUES (?, ?, ?, datetime('now'))
            """, (alias_file_id, primary_file_id, alias_folder))
            
            # 标记关联文件
            cursor.execute("""
                UPDATE media_files SET is_alias = 1 WHERE file_id = ?
            """, (alias_file_id,))
            
            conn.commit()
            
            # 更新缓存
            with self._cache_lock:
                self._cache['video_relations'][alias_file_id] = primary_file_id
                if alias_path in self._cache['files']:
                    self._cache['files'][alias_path]['is_alias'] = 1
            
            if self.debug:
                self.debug.print(f"建立视频关联: {alias_file_id} -> {primary_file_id}")
            
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"设置视频关联失败: {e}")
            return False
        finally:
            cursor.close()
    
    def remove_video_relation(self, alias_file_id):
        """解除视频关联关系"""
        if not self._initialized:
            return False
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM video_relations WHERE alias_file_id = ?", (alias_file_id,))
            cursor.execute("UPDATE media_files SET is_alias = 0 WHERE file_id = ?", (alias_file_id,))
            conn.commit()
            
            # 更新缓存
            with self._cache_lock:
                if alias_file_id in self._cache['video_relations']:
                    del self._cache['video_relations'][alias_file_id]
                # 查找并更新文件缓存中的is_alias
                for path, info in self._cache['files'].items():
                    if info['id'] == alias_file_id:
                        info['is_alias'] = 0
                        break
            
            if self.debug:
                self.debug.print(f"解除视频关联: {alias_file_id}")
            
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"解除视频关联失败: {e}")
            return False
        finally:
            cursor.close()
    
    def get_related_videos(self, primary_file_id):
        """获取主视频的所有关联视频"""
        if not self._initialized:
            return []
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT mf.* 
                FROM video_relations vr
                JOIN media_files mf ON vr.alias_file_id = mf.file_id
                WHERE vr.primary_file_id = ?
            """, (primary_file_id,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            if self.debug:
                self.debug.print(f"获取关联视频失败: {e}")
            return []
        finally:
            cursor.close()
    
    def check_video_relation_exists(self, alias_file_id):
        """检查指定文件是否已是关联视频"""
        with self._cache_lock:
            return alias_file_id in self._cache['video_relations']
    
    def get_file_by_path(self, file_path):
        """根据路径获取文件信息 - 增强版：支持规范化路径和大小写不敏感查找"""
        if not file_path:
            return None

        with self._cache_lock:
            # 1. 直接查找（最快路径）
            result = self._cache['files'].get(file_path)
            if result:
                return result

            # 2. 规范化路径后查找（处理混合斜杠、尾部斜杠等差异）
            norm_path = os.path.normpath(file_path)
            if norm_path != file_path:
                result = self._cache['files'].get(norm_path)
                if result:
                    return result

            # 3. Windows 下大小写不敏感查找（处理大小写差异和斜杠差异）
            if os.name == 'nt':
                file_path_lower = os.path.normpath(file_path).lower()
                for cached_path, info in self._cache['files'].items():
                    if os.path.normpath(cached_path).lower() == file_path_lower:
                        return info

            # 4. 尝试使用 os.path.samefile 比较（如果文件实际存在）
            try:
                if os.path.exists(file_path):
                    for cached_path, info in self._cache['files'].items():
                        if os.path.exists(cached_path) and os.path.samefile(file_path, cached_path):
                            return info
            except (OSError, ValueError):
                pass

            return None
    
    # ==================== 文件夹同步检查（优化：增加强制刷新参数）====================
    
    def async_sync_check(self, root_folders, progress_callback=None, complete_callback=None, 
                         force_full=False, device_id='unknown', bookmark_id=None):
        """异步检查文件夹更新并同步到数据库 - 增加设备/书签标识
        
        Args:
            root_folders: 根文件夹列表
            progress_callback: 进度回调
            complete_callback: 完成回调
            force_full: 是否强制全量扫描
            device_id: 设备标识
            bookmark_id: 书签标识
        """
        def sync_thread():
            try:
                total_changes = 0
                for idx, folder in enumerate(root_folders):
                    if progress_callback:
                        progress_callback(idx + 1, len(root_folders), f"正在检查: {os.path.basename(folder)}")

                    # 优化：如果不是强制刷新，先检查修改时间
                    if not force_full and not self._check_folder_changed(folder):
                        continue

                    changes = self._sync_folder(folder, device_id, bookmark_id)
                    total_changes += changes

                # 重新加载缓存（带设备过滤）
                self._load_all_to_cache(device_id, bookmark_id)

                if complete_callback:
                    complete_callback(True, f"同步完成，更新了 {total_changes} 个条目")

            except Exception as e:
                if self.debug:
                    self.debug.print(f"同步检查失败: {e}")
                if complete_callback:
                    complete_callback(False, str(e))

        thread = threading.Thread(target=sync_thread, daemon=True)
        thread.start()
        return thread
        def sync_thread():
            try:
                total_changes = 0
                for idx, folder in enumerate(root_folders):
                    if progress_callback:
                        progress_callback(idx + 1, len(root_folders), f"正在检查: {os.path.basename(folder)}")
                    
                    # 优化：如果不是强制刷新，先检查修改时间，无变更则跳过
                    if not force_full and not self._check_folder_changed(folder):
                        continue
                    
                    changes = self._sync_folder(folder)
                    total_changes += changes
                
                # 重新加载缓存
                self._load_all_to_cache()
                
                if complete_callback:
                    complete_callback(True, f"同步完成，更新了 {total_changes} 个条目")
                    
            except Exception as e:
                if self.debug:
                    self.debug.print(f"同步检查失败: {e}")
                if complete_callback:
                    complete_callback(False, str(e))
        
        thread = threading.Thread(target=sync_thread, daemon=True)
        thread.start()
        return thread
    
    def _check_folder_changed(self, folder_path):
        """快速检查文件夹是否变更（通过修改时间）"""
        try:
            current_mtime = os.path.getmtime(folder_path)
            current_mtime_dt = datetime.fromtimestamp(current_mtime)
            folder_path_norm = os.path.normpath(folder_path)
            
            with self._cache_lock:
                cached = self._cache['folders'].get(folder_path_norm)
                if cached and cached.get('modified'):
                    return current_mtime_dt > cached['modified']
            return True  # 无缓存，认为已变更
        except:
            return True  # 出错时默认需要同步
    
    def _sync_folder(self, folder_path, device_id='unknown', bookmark_id=None):
        """同步单个文件夹及其子文件夹 - 增加设备/书签标识
        
        Args:
            folder_path: 文件夹路径
            device_id: 设备标识，如 'roy-pc'
            bookmark_id: 书签标识，如 'e674fe576622'
        """
        from core import PathUtils

        changes = 0
        conn = self._get_connection()
        cursor = conn.cursor()

        # 标准化路径：统一反斜杠并转为小写
        folder_path = os.path.normpath(folder_path).lower()

        # 检查当前文件夹是否在数据库中（增加 device_id 匹配）
        cursor.execute("""
            SELECT folder_id, last_modified FROM folders 
            WHERE folder_path = ? AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
        """, (folder_path, device_id))
        existing = cursor.fetchone()

        # 获取文件夹实际修改时间
        try:
            current_mtime = os.path.getmtime(folder_path)
            current_mtime = datetime.fromtimestamp(current_mtime)
        except:
            current_mtime = datetime.now()

        folder_id = None
        if existing:
            folder_id = existing['folder_id']
            db_mtime = existing['last_modified']
            db_mtime_dt = convert_datetime(db_mtime)

            # 优化：如果修改时间相同且非强制刷新，跳过详细扫描
            if db_mtime_dt and abs((current_mtime - db_mtime_dt).total_seconds()) < 1:
                pass  # 继续扫描子文件夹
            else:
                # 更新文件夹信息（同时更新 device_id/bookmark_id）
                cursor.execute("""
                    UPDATE folders 
                    SET last_modified = ?, scan_time = datetime('now'),
                        device_id = ?, bookmark_id = ?
                    WHERE folder_id = ?
                """, (adapt_datetime(current_mtime), device_id, bookmark_id, folder_id))
                changes += 1
        else:
            # 新增文件夹（包含 device_id/bookmark_id）
            folder_name = os.path.basename(folder_path)
            parent_path = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None

            cursor.execute("""
                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified, scan_time, device_id, bookmark_id)
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?)
            """, (folder_path, folder_name, parent_path, adapt_datetime(current_mtime), device_id, bookmark_id))

            folder_id = cursor.lastrowid
            changes += 1

        # 扫描文件夹内文件（增加 device_id/bookmark_id）
        if folder_id and os.path.exists(folder_path):
            # 获取数据库中该文件夹的文件列表（增加 device_id 过滤）
            cursor.execute("""
                SELECT file_path, last_modified FROM media_files 
                WHERE folder_id = ? AND is_deleted = 0
                AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
            """, (folder_id, device_id))
            db_files = {os.path.normpath(row['file_path']): row['last_modified'] for row in cursor.fetchall()}

            # 扫描实际文件
            current_files = set()
            try:
                for entry in os.scandir(folder_path):
                    if entry.is_file():
                        ext = os.path.splitext(entry.name)[1].lower()
                        is_video = ext in PathUtils.VIDEO_EXTENSIONS
                        is_image = ext in PathUtils.IMAGE_EXTENSIONS

                        if is_video or is_image:
                            file_path = os.path.normpath(entry.path).lower()
                            current_files.add(file_path)
                            file_type = 'video' if is_video else 'image'
                            file_mtime = datetime.fromtimestamp(entry.stat().st_mtime)
                            file_size = entry.stat().st_size

                            # 检查是否需要更新（增加 device_id 匹配）
                            if file_path in db_files:
                                db_mtime = db_files[file_path]
                                db_mtime_dt = convert_datetime(db_mtime)
                                if db_mtime_dt is None or abs((file_mtime - db_mtime_dt).total_seconds()) >= 1:
                                    # 更新文件信息（包含 device_id/bookmark_id）
                                    cursor.execute("""
                                        UPDATE media_files 
                                        SET last_modified = ?, file_size = ?, device_id = ?, bookmark_id = ?
                                        WHERE file_path = ? AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
                                    """, (adapt_datetime(file_mtime), file_size, device_id, bookmark_id, file_path, device_id))
                                    changes += 1
                            else:
                                # 新增文件（包含 device_id/bookmark_id）
                                cursor.execute("""
                                    INSERT INTO media_files 
                                    (folder_id, file_path, file_name, file_type, file_size, last_modified, device_id, bookmark_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """, (folder_id, file_path, entry.name, file_type, file_size, adapt_datetime(file_mtime), device_id, bookmark_id))
                                changes += 1
            except PermissionError:
                pass

            # 标记已删除的文件（分批处理）
            deleted_files = [old_path for old_path in db_files if old_path not in current_files]
            batch_size = 500
            for i in range(0, len(deleted_files), batch_size):
                batch = deleted_files[i:i+batch_size]
                placeholders = ','.join('?' * len(batch))
                cursor.execute(f"""
                    UPDATE media_files SET is_deleted = 1 
                    WHERE file_path IN ({placeholders})
                    AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
                """, batch + [device_id])
                changes += len(batch)

            # 更新文件夹文件计数
            cursor.execute("""
                UPDATE folders SET file_count = ? WHERE folder_id = ?
            """, (len(current_files), folder_id))

        # 递归处理子文件夹（传递 device_id/bookmark_id）
        try:
            for entry in os.scandir(folder_path):
                if entry.is_dir() and not entry.name.startswith('.'):
                    sub_changes = self._sync_folder(entry.path, device_id, bookmark_id)
                    changes += sub_changes
        except PermissionError:
            pass

        conn.commit()
        cursor.close()
        return changes
    def get_folders_by_parent(self, parent_path=None, include_bookmarks=False, device_id=None, bookmark_id=None):
        """获取子文件夹列表 - 增加设备/书签过滤
        
        Args:
            parent_path: 父文件夹路径
            include_bookmarks: 是否包含书签
            device_id: 设备标识过滤
            bookmark_id: 书签标识过滤
        """
        parent_path_norm = os.path.normpath(parent_path) if parent_path else parent_path
        
        with self._cache_lock:
            if include_bookmarks and parent_path is None:
                # 返回指定设备的书签
                result = []
                for p in self._cache['bookmarks']:
                    if p in self._cache['folders']:
                        folder_info = self._cache['folders'][p]
                        # 过滤设备
                        if device_id and folder_info.get('device_id') not in [device_id, 'unknown']:
                            continue
                        if bookmark_id and folder_info.get('bookmark_id') != bookmark_id:
                            continue
                        result.append(folder_info)
                return sorted(result, key=lambda x: x['name'])

            result = []
            for path, info in self._cache['folders'].items():
                info_parent = info.get('parent')
                info_parent_norm = os.path.normpath(info_parent) if info_parent else info_parent
                if info_parent_norm == parent_path_norm:
                    # 过滤设备
                    if device_id and info.get('device_id') not in [device_id, 'unknown']:
                        continue
                    if bookmark_id and info.get('bookmark_id') != bookmark_id:
                        continue
                    result.append(info)
            return sorted(result, key=lambda x: x['name'])
    def get_files_by_folder(self, folder_path, device_id=None):
        """获取文件夹内文件（从缓存）- 增加设备过滤
        
        Args:
            folder_path: 文件夹路径
            device_id: 设备标识过滤
        """
        folder_path_norm = os.path.normpath(folder_path) if folder_path else folder_path
        
        with self._cache_lock:
            result = []
            for path, info in self._cache['files'].items():
                info_folder = os.path.normpath(info.get('folder_path', '')) if info.get('folder_path') else ''
                if info_folder == folder_path_norm:
                    # 设备过滤
                    if device_id and info.get('device_id') not in [device_id, 'unknown']:
                        continue
                    result.append(info)
            return sorted(result, key=lambda x: x['name'])
    def get_actors_by_file(self, file_id):
        """获取文件的演员列表"""
        with self._cache_lock:
            actor_ids = self._cache['file_actors'].get(file_id, [])
            result = []
            for aid in actor_ids:
                if aid in self._cache['actors']:
                    result.append(self._cache['actors'][aid])
            return result
    
    def get_actor_by_id(self, actor_id):
        """根据ID获取演员信息"""
        with self._cache_lock:
            return self._cache['actors'].get(actor_id)
    
    def search_actors(self, keyword):
        """搜索演员"""
        with self._cache_lock:
            result = []
            keyword_lower = keyword.lower()
            for actor_id, info in self._cache['actors'].items():
                name_match = info.get('name', '').lower().find(keyword_lower) >= 0
                name_cn_match = info.get('name_cn', '').lower().find(keyword_lower) >= 0 if info.get('name_cn') else False
                if name_match or name_cn_match:
                    result.append(info)
            return result
    
    # ==================== 标签标记接口（仅标记，非主存储）====================
    
    def set_file_tag_summary(self, file_path, tag_summary):
        """设置文件的标签摘要（仅作为标记，实际数据在config.json）"""
        if not self._initialized:
            return False
        
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE media_files SET tag_summary = ? WHERE file_path = ?",
                (tag_summary, file_path)
            )
            conn.commit()
            
            # 更新缓存
            with self._cache_lock:
                if file_path in self._cache['files']:
                    self._cache['files'][file_path]['tag_summary'] = tag_summary
            
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"设置标签摘要失败: {e}")
            return False
        finally:
            cursor.close()
    
    def get_file_tag_summary(self, file_path):
        """获取文件的标签摘要"""
        with self._cache_lock:
            if file_path in self._cache['files']:
                return self._cache['files'][file_path].get('tag_summary', None)
            return None
    
    # ==================== 关闭连接 ====================
    
    def close(self):
        """关闭数据库连接"""
        # 关闭线程本地连接
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
        self._initialized = False
    
    @property
    def is_ready(self):
        return self._initialized
    
    @property
    def is_cache_ready(self):
        """新增：缓存是否已加载完成"""
        return self._cache_loaded
    
    @property
    def _conn(self):
        """兼容属性，返回当前线程的连接"""
        return self._get_connection()

    def export_debug_data(self, data_dir, timestamp, status_callback=None):
        """
        导出数据库调试数据到 JSON 文件
        由主程序在后台线程中调用，减少 main.py 代码量。
        
        Args:
            data_dir: 保存目录
            timestamp: 时间戳字符串
            status_callback: 进度回调，签名 callback(message)
            
        Returns:
            tuple: (success: bool, file_path: str or None, error: str or None)
        """
        import json
        
        def report(msg):
            if status_callback:
                status_callback(msg)
        
        db_data = {
            "scan_time": timestamp,
            "db_ready": self.is_ready,
            "tables": {}
        }
        
        if not self.is_ready:
            db_data["note"] = "数据库未就绪"
            report("数据库未就绪，跳过导出")
        else:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                report("正在导出 folders 表...")
                cursor.execute(
                    "SELECT folder_id, folder_path, folder_name, parent_path, is_bookmark, file_count "
                    "FROM folders WHERE is_deleted = 0 OR is_deleted IS NULL"
                )
                db_data["tables"]["folders"] = [
                    {
                        "folder_id": row["folder_id"],
                        "folder_path": row["folder_path"],
                        "folder_name": row["folder_name"],
                        "parent_path": row["parent_path"],
                        "is_bookmark": bool(row["is_bookmark"]),
                        "file_count": row["file_count"]
                    }
                    for row in cursor.fetchall()
                ]
                
                report("正在导出 media_files 表...")
                cursor.execute("""
                    SELECT file_id, folder_id, file_path, file_name, file_type, file_size,
                           video_title, duration, release_date, video_code, is_alias
                    FROM media_files
                    WHERE is_deleted = 0 OR is_deleted IS NULL
                    LIMIT 1000
                """)
                db_data["tables"]["media_files"] = [
                    {
                        "file_id": row["file_id"],
                        "folder_id": row["folder_id"],
                        "file_path": row["file_path"],
                        "file_name": row["file_name"],
                        "file_type": row["file_type"],
                        "file_size": row["file_size"],
                        "video_title": row["video_title"],
                        "duration": row["duration"],
                        "release_date": row["release_date"],
                        "video_code": row["video_code"],
                        "is_alias": bool(row["is_alias"])
                    }
                    for row in cursor.fetchall()
                ]
                
                report("正在导出 video_relations 表...")
                cursor.execute(
                    "SELECT relation_id, alias_file_id, primary_file_id, alias_folder_path FROM video_relations"
                )
                db_data["tables"]["video_relations"] = [
                    {
                        "relation_id": row["relation_id"],
                        "alias_file_id": row["alias_file_id"],
                        "primary_file_id": row["primary_file_id"],
                        "alias_folder_path": row["alias_folder_path"]
                    }
                    for row in cursor.fetchall()
                ]
                
                report("正在导出 actors 表...")
                cursor.execute("SELECT actor_id, actor_name, actor_name_cn, sort_char FROM actors")
                db_data["tables"]["actors"] = [
                    {
                        "actor_id": row["actor_id"],
                        "actor_name": row["actor_name"],
                        "actor_name_cn": row["actor_name_cn"],
                        "sort_char": row["sort_char"]
                    }
                    for row in cursor.fetchall()
                ]
                
                report("正在导出 file_actors 表...")
                cursor.execute("SELECT file_id, actor_id FROM file_actors")
                db_data["tables"]["file_actors"] = [
                    {"file_id": row["file_id"], "actor_id": row["actor_id"]}
                    for row in cursor.fetchall()
                ]
                
                cursor.close()
            except Exception as e:
                db_data["error"] = str(e)
                report(f"数据库导出出错: {e}")
        
        # 写入文件
        db_save_path = os.path.join(data_dir, f"debug_database_{timestamp}.json")
        try:
            with open(db_save_path, 'w', encoding='utf-8') as f:
                json.dump(db_data, f, ensure_ascii=False, indent=2)
            report("数据库导出完成")
            return True, db_save_path, None
        except Exception as e:
            report(f"写入数据库 JSON 失败: {e}")
            return False, None, str(e)

    def _get_folder_tree_cache_dir(self):
        """获取文件夹树缓存目录（data子文件夹）"""
        cache_dir = os.path.join(os.path.dirname(self.db_path), "data")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def get_folder_tree_cache_path(self, device_id):
        """获取指定设备的文件夹树缓存文件路径"""
        cache_dir = self._get_folder_tree_cache_dir()
        return os.path.join(cache_dir, f"folder_tree_cache_{device_id}.json")

    def load_folder_tree_cache(self, device_id, bookmark_id=None):
        """加载指定设备的文件夹树缓存"""
        cache_path = self.get_folder_tree_cache_path(device_id)
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            # 验证设备ID
            if cache.get('device_id') != device_id:
                return None
            # 如果指定了书签ID，验证匹配（不匹配视为过期）
            if bookmark_id and cache.get('bookmark_id') != bookmark_id:
                return None
            return cache
        except Exception as e:
            if self.debug:
                self.debug.print(f"加载文件夹树缓存失败: {e}")
            return None

    def save_folder_tree_cache(self, device_id, bookmark_id, root_path, folder_entries):
        """保存文件夹树缓存到JSON（覆盖写入）"""
        try:
            cache_path = self.get_folder_tree_cache_path(device_id)
            cache = {
                'device_id': device_id,
                'bookmark_id': bookmark_id,
                'root_path': os.path.normpath(root_path),
                'folders': folder_entries,
                'timestamp': time.time()
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            if self.debug:
                self.debug.print(f"保存文件夹树缓存失败: {e}")
            return False

    def scan_folder_tree_fast(self, root_path):
        """快速扫描所有文件夹结构（不含文件），返回标准化条目列表"""
        folder_entries = []
        try:
            for dirpath, dirnames, _ in os.walk(root_path):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                norm_path = os.path.normpath(dirpath)
                parent = os.path.dirname(norm_path)
                if parent and parent != norm_path:
                    parent = os.path.normpath(parent)
                else:
                    parent = None
                folder_entries.append({
                    'path': norm_path,
                    'name': os.path.basename(norm_path),
                    'parent': parent,
                    'mtime': os.path.getmtime(dirpath)
                })
        except Exception as e:
            if self.debug:
                self.debug.print(f"扫描文件夹树失败: {e}")
        return folder_entries

    def async_refresh_folder_tree_cache(self, root_path, device_id, bookmark_id, on_complete=None, debug=None):
        """后台线程：扫描实际文件夹结构，保存缓存，完成后回调"""
        def worker():
            try:
                if debug:
                    debug.print(f"[后台扫描] 开始扫描文件夹: {root_path}")
                entries = self.scan_folder_tree_fast(root_path)
                self.save_folder_tree_cache(device_id, bookmark_id, root_path, entries)
                if debug:
                    debug.print(f"[后台扫描] 完成，共 {len(entries)} 个文件夹，已保存缓存")
                if on_complete:
                    on_complete(True, entries)
            except Exception as e:
                if debug:
                    debug.print(f"[后台扫描失败] {e}")
                if on_complete:
                    on_complete(False, str(e))
        threading.Thread(target=worker, daemon=True).start()

    # ==================== 树状图与文件夹同步支持（新增）====================

    def scan_folder_tree(self, root_path, debug=None):
        """递归扫描书签下的所有文件夹结构，只返回文件夹路径列表（不扫描文件）"""
        folder_paths = []
        try:
            for dirpath, dirnames, _ in os.walk(root_path):
                # 跳过隐藏文件夹
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                folder_paths.append(os.path.normpath(dirpath))
        except Exception as e:
            if debug:
                debug.print(f"扫描文件夹树失败: {e}")
        return folder_paths

    def sync_folder_files(self, folder_path, device_id='unknown', bookmark_id=None, debug=None):
        """
        同步单个文件夹中的文件到数据库。
        核对：路径、文件夹名、文件名、文件大小。
        以文件夹实际数据为准，缺失的补充，不一致的更新。
        """
        if not self._initialized or not os.path.exists(folder_path):
            return False, 0

        try:
            from core import PathUtils
            conn = self._get_connection()
            cursor = conn.cursor()

            # 1. 确保文件夹记录在数据库中存在
            folder_id = self._ensure_folder_record(cursor, folder_path, device_id, bookmark_id)

            # 2. 扫描文件夹实际文件（只关心视频/图片）
            current_files = {}
            try:
                for entry in os.scandir(folder_path):
                    if not entry.is_file():
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    is_video = ext in PathUtils.VIDEO_EXTENSIONS
                    is_image = ext in PathUtils.IMAGE_EXTENSIONS
                    if is_video or is_image:
                        file_path = os.path.normpath(entry.path)
                        stat = entry.stat()
                        current_files[file_path] = {
                            'name': entry.name,
                            'type': 'video' if is_video else 'image',
                            'size': stat.st_size,
                            'mtime': stat.st_mtime,
                            'folder_path': os.path.normpath(folder_path)
                        }
            except PermissionError:
                pass

            # 3. 获取数据库中该文件夹的现有文件记录
            cursor.execute("""
                SELECT file_id, file_path, file_name, file_type, file_size, last_modified
                FROM media_files
                WHERE folder_id = ? AND is_deleted = 0
            """, (folder_id,))
            db_files = {os.path.normpath(row['file_path']): dict(row) for row in cursor.fetchall()}

            changes = 0

            # 4. 处理新增和更新（以文件夹数据为准）
            for file_path, file_info in current_files.items():
                if file_path not in db_files:
                    # 数据库中没有，补充插入
                    cursor.execute("""
                        INSERT INTO media_files
                        (folder_id, file_path, file_name, file_type, file_size, last_modified, device_id, bookmark_id)
                        VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)
                    """, (folder_id, file_path, file_info['name'], file_info['type'],
                          file_info['size'], device_id, bookmark_id))
                    changes += 1
                else:
                    # 数据库中有，核对关键字段
                    db_file = db_files[file_path]
                    db_size = db_file.get('file_size') or 0
                    db_name = db_file.get('file_name', '')
                    if (db_name != file_info['name'] or
                        db_file.get('file_type') != file_info['type'] or
                        db_size != file_info['size']):
                        cursor.execute("""
                            UPDATE media_files
                            SET file_name = ?, file_type = ?, file_size = ?, last_modified = datetime('now')
                            WHERE file_id = ?
                        """, (file_info['name'], file_info['type'], file_info['size'], db_file['file_id']))
                        changes += 1

            # 5. 处理已删除的文件（数据库中有但文件夹中没有了）
            deleted_paths = set(db_files.keys()) - set(current_files.keys())
            if deleted_paths:
                placeholders = ','.join('?' * len(deleted_paths))
                cursor.execute(f"""
                    UPDATE media_files SET is_deleted = 1
                    WHERE file_path IN ({placeholders})
                """, tuple(deleted_paths))
                changes += len(deleted_paths)

            conn.commit()
            cursor.close()

            # 6. 有变更则刷新内存缓存
            if changes > 0:
                self._load_all_to_cache_async()

            if debug:
                debug.print(f"[DB同步] {os.path.basename(folder_path)}: 变更 {changes} 条记录")

            return True, changes

        except Exception as e:
            if debug:
                debug.print(f"[DB同步失败] {folder_path}: {e}")
                import traceback
                traceback.print_exc()
            return False, 0

    def _ensure_folder_record(self, cursor, folder_path, device_id='unknown', bookmark_id=None):
        """确保文件夹记录在数据库中存在，返回 folder_id"""
        folder_path = os.path.normpath(folder_path)
        cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder_path,))
        result = cursor.fetchone()
        if result:
            return result['folder_id']

        parent = os.path.dirname(folder_path)
        if parent and parent != folder_path:
            parent = os.path.normpath(parent)
        else:
            parent = None

        cursor.execute("""
            INSERT INTO folders (folder_path, folder_name, parent_path, last_modified, device_id, bookmark_id)
            VALUES (?, ?, ?, datetime('now'), ?, ?)
        """, (folder_path, os.path.basename(folder_path), parent, device_id, bookmark_id))
        return cursor.lastrowid

    # ==================== 从 main.py 迁移的数据库操作 ====================
    
    def merge_folder_scan_data(self, folder_path, folder_scanned_data, device_id='unknown', bookmark_id=None, debug=None):
        """合并文件夹扫描数据与数据库数据（从main.py _merge_and_update_database 迁移）"""
        try:
            if not folder_scanned_data:
                import time
                timeout = 10
                waited = 0
                while not folder_scanned_data and waited < timeout:
                    time.sleep(0.1)
                    waited += 0.1

            if not folder_scanned_data:
                if debug:
                    debug.print("  文件夹扫描数据不可用")
                return

            folder_files = folder_scanned_data.get('files', {})
            db_files = self._cache.get('files', {})

            files_to_update = []
            files_to_add = []

            for file_path, file_info in folder_files.items():
                db_info = None
                for db_path, info in db_files.items():
                    if db_path == file_path:
                        if (info.get('device_id') in [device_id, 'unknown'] and
                            (not bookmark_id or info.get('bookmark_id') == bookmark_id)):
                            db_info = info
                            break

                if db_info:
                    db_mtime = db_info.get('modified')
                    file_mtime = file_info.get('mtime')
                    mtime_changed = True

                    if db_mtime is not None and file_mtime is not None:
                        if isinstance(db_mtime, datetime):
                            db_ts = db_mtime.timestamp()
                        elif isinstance(db_mtime, (int, float)):
                            db_ts = float(db_mtime)
                        else:
                            db_ts = 0
                        mtime_changed = abs(db_ts - float(file_mtime)) > 1
                    elif db_mtime is None and file_mtime is None:
                        mtime_changed = False

                    if (db_info.get('name') != file_info.get('name') or
                        db_info.get('size') != file_info.get('size') or
                        mtime_changed):
                        file_info['device_id'] = device_id
                        file_info['bookmark_id'] = bookmark_id
                        files_to_update.append(file_info)
                else:
                    file_info['device_id'] = device_id
                    file_info['bookmark_id'] = bookmark_id
                    files_to_add.append(file_info)

            if debug:
                debug.print(f"  新增文件: {len(files_to_add)}, 需要更新: {len(files_to_update)}")

            if files_to_add or files_to_update:
                self.update_files_batch(folder_path, files_to_add, files_to_update, device_id, bookmark_id, debug)

        except Exception as e:
            if debug:
                debug.print(f"  合并数据失败: {e}")

    def update_files_batch(self, folder_path, files_to_add, files_to_update, device_id='unknown', bookmark_id=None, debug=None):
        """批量更新数据库文件信息（从main.py _update_database_files 迁移）"""
        try:
            changes = 0
            folder_path = os.path.normpath(folder_path)
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")

            cursor.execute("""
                SELECT folder_id FROM folders 
                WHERE folder_path = ? AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
            """, (folder_path, device_id))
            result = cursor.fetchone()

            if result:
                folder_id = result['folder_id']
                cursor.execute("""
                    UPDATE folders SET device_id = ?, bookmark_id = ?
                    WHERE folder_id = ?
                """, (device_id, bookmark_id, folder_id))
            else:
                parent = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
                if parent:
                    parent = os.path.normpath(parent)
                cursor.execute("""
                    INSERT INTO folders (folder_path, folder_name, parent_path, last_modified, device_id, bookmark_id)
                    VALUES (?, ?, ?, datetime('now'), ?, ?)
                """, (folder_path, os.path.basename(folder_path), parent, device_id, bookmark_id))
                folder_id = cursor.lastrowid
                changes += 1

            for file_info in files_to_add:
                cursor.execute("""
                    INSERT OR IGNORE INTO media_files 
                    (folder_id, file_path, file_name, file_type, file_size, last_modified, device_id, bookmark_id)
                    VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)
                """, (folder_id, file_info['path'], file_info['name'], 
                      file_info['type'], file_info['size'], device_id, bookmark_id))
                if cursor.rowcount > 0:
                    changes += 1

            for file_info in files_to_update:
                cursor.execute("""
                    UPDATE media_files 
                    SET file_name = ?, file_size = ?, last_modified = datetime('now'),
                        device_id = ?, bookmark_id = ?
                    WHERE file_path = ? AND (device_id = ? OR device_id IS NULL OR device_id = 'unknown')
                """, (file_info['name'], file_info['size'], device_id, bookmark_id, 
                      file_info['path'], device_id))
                if cursor.rowcount > 0:
                    changes += 1

            conn.commit()
            cursor.close()

            if changes == 0:
                if debug:
                    debug.print("  数据库无变更，跳过缓存刷新")
            elif changes <= 50:
                with self._cache_lock:
                    for file_info in files_to_add:
                        if file_info['path'] not in self._cache['files']:
                            self._cache['files'][file_info['path']] = {
                                'id': None, 'name': file_info['name'], 'type': file_info['type'],
                                'size': file_info['size'], 'folder_path': folder_path,
                                'modified': datetime.now(), 'device_id': device_id, 'bookmark_id': bookmark_id
                            }
                    for file_info in files_to_update:
                        if file_info['path'] in self._cache['files']:
                            self._cache['files'][file_info['path']]['name'] = file_info['name']
                            self._cache['files'][file_info['path']]['size'] = file_info['size']
                            self._cache['files'][file_info['path']]['modified'] = datetime.now()
                            self._cache['files'][file_info['path']]['device_id'] = device_id
                            self._cache['files'][file_info['path']]['bookmark_id'] = bookmark_id
                if debug:
                    debug.print(f"  数据库增量更新完成: {changes} 个变更（已同步到内存缓存）")
            else:
                self._load_all_to_cache_async(device_id, bookmark_id)
                if debug:
                    debug.print(f"  数据库批量更新完成: {changes} 个变更，已触发全量缓存刷新")

        except Exception as e:
            if debug:
                debug.print(f"  更新数据库失败: {e}")
                import traceback
                traceback.print_exc()
            try:
                conn.rollback()
            except:
                pass

    def sync_folder_lightweight(self, folder_path, debug=None):
        """轻量级同步单个文件夹（从main.py _sync_single_folder_lightweight 迁移）"""
        try:
            if self.is_ready:
                if hasattr(self, 'is_cache_ready') and not self.is_cache_ready:
                    return False, "缓存未就绪"
                changes = self._sync_folder(folder_path)
                return True, changes
        except Exception as e:
            if debug:
                debug.print(f"轻量级同步失败: {e}")
            return False, str(e)

    def background_sync(self, device_id, bookmarks, debug=None):
        """后台同步书签（从main.py _background_sync_safe 迁移）"""
        try:
            for bm in bookmarks:
                folder = bm['path']
                bm_id = bm.get('id', 'unknown')
                try:
                    self._sync_folder(folder, device_id, bm_id)
                    conn = self._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE folders SET is_bookmark = 1, device_id = ?, bookmark_id = ?
                        WHERE folder_path = ?
                    """, (device_id, bm_id, folder))
                    conn.commit()
                    cursor.close()
                except Exception as e:
                    if debug:
                        debug.print(f"同步失败 {folder}: {e}")
            self._load_all_to_cache(device_id)
        except Exception as e:
            if debug:
                debug.print(f"后台同步失败: {e}")

    def manual_full_sync(self, device_id, bookmarks, status_callback=None, debug=None):
        """手动完整同步（从main.py _manual_full_sync 迁移）"""
        try:
            total_changes = 0
            for idx, bm in enumerate(bookmarks):
                folder = bm['path']
                bm_id = bm.get('id', 'unknown')
                try:
                    if status_callback:
                        status_callback(f"同步: {os.path.basename(folder)}")
                    changes = self._sync_folder(folder, device_id, bm_id)
                    total_changes += changes
                    conn = self._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE folders SET is_bookmark = 1, device_id = ?, bookmark_id = ?
                        WHERE folder_path = ?
                    """, (device_id, bm_id, folder))
                    conn.commit()
                    cursor.close()
                except Exception as e:
                    if debug:
                        debug.print(f"同步失败 {folder}: {e}")
            self._load_all_to_cache_async(device_id)
            return True, total_changes
        except Exception as e:
            if debug:
                debug.print(f"完整同步失败: {e}")
            return False, 0

    @staticmethod
    def scan_folder(folder_path):
        """执行文件夹扫描，返回文件信息字典（从main.py _perform_folder_scan 迁移）"""
        files_info = {}
        folders_info = {}
        try:
            entries = list(os.scandir(folder_path))
        except Exception as e:
            return {'files': files_info, 'folders': folders_info}
        
        folders_to_scan = [(folder_path, os.path.basename(folder_path))]
        for entry in entries:
            if entry.is_dir():
                folders_to_scan.append((entry.path, entry.name))
        
        for scan_path, display_name in folders_to_scan:
            try:
                scan_entries = list(os.scandir(scan_path))
            except:
                continue
            
            folders_info[scan_path] = {
                'name': display_name,
                'path': scan_path,
                'file_count': 0
            }
            
            for entry in scan_entries:
                if not entry.is_file():
                    continue
                
                ext = os.path.splitext(entry.name)[1].lower()
                basename = os.path.splitext(entry.name)[0]
                stat = entry.stat()
                
                file_info = {
                    'path': os.path.normpath(entry.path),
                    'name': entry.name,
                    'basename': basename,
                    'ext': ext,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                    'folder_path': os.path.normpath(scan_path)
                }
                
                from core import PathUtils
                if PathUtils.is_video_file(entry.path):
                    file_info['type'] = 'video'
                    files_info[entry.path] = file_info
                    folders_info[scan_path]['file_count'] += 1
                elif PathUtils.is_image_file(entry.path):
                    file_info['type'] = 'image'
                    files_info[entry.path] = file_info
                    
        return {'files': files_info, 'folders': folders_info}

    def scan_folder_for_cache(self, folder_path, debug=None):
        """扫描文件夹并返回数据供后续合并使用（从main.py _scan_folder_for_cache 迁移）"""
        try:
            scanned_data = self.scan_folder(folder_path)
            if debug:
                debug.print(f"  文件夹扫描完成: {len(scanned_data.get('files', {}))} 个文件")
            return scanned_data
        except Exception as e:
            if debug:
                debug.print(f"扫描文件夹缓存失败: {e}")
            return None

    def init_and_merge_async(self, folder_path, current_bookmark_id, folder_scanned_data, device_id='unknown',
                             on_db_ready=None, on_sync_marked=None, on_merged=None, on_complete=None, debug=None):
        """异步初始化数据库并合并数据（从main.py _async_init_database_and_merge 迁移）"""
        def db_thread():
            try:
                from init_database import ensure_database_ready
                db_success, db_msg = ensure_database_ready()
                if not db_success:
                    if debug:
                        debug.print(f"[步骤5] 数据库初始化失败: {db_msg}")
                    return
                
                if not self.is_ready:
                    if debug:
                        debug.print("[步骤5] 数据库连接失败")
                    return
                
                if on_db_ready:
                    on_db_ready()
                
                if debug:
                    debug.print("[步骤5] 数据库连接成功，等待缓存加载...")
                
                import time
                timeout = 30
                waited = 0
                while not self.is_cache_ready and waited < timeout:
                    time.sleep(0.1)
                    waited += 0.1
                
                if not self.is_cache_ready:
                    if debug:
                        debug.print("[步骤5] 缓存加载超时")
                    return
                
                if debug:
                    debug.print(f"[步骤5] 数据库缓存加载完成: {len(self._cache.get('files', {}))} 文件")
                
                if on_sync_marked:
                    on_sync_marked()
                
                if debug:
                    debug.print("[步骤6] 标记已同步视频...")
                
                if on_merged:
                    on_merged()
                
                if debug:
                    debug.print("[步骤7] 合并文件夹数据并更新数据库...")
                
                self.merge_folder_scan_data(folder_path, folder_scanned_data, device_id, current_bookmark_id, debug)
                
                if on_complete:
                    on_complete(folder_path)
                    
            except Exception as e:
                if debug:
                    debug.print(f"[步骤5-7] 数据库处理失败: {e}")
                    import traceback
                    traceback.print_exc()
        
        threading.Thread(target=db_thread, daemon=True).start()

    def init_only_async(self, on_ready=None, debug=None):
        """仅初始化数据库（从main.py _async_init_database_only 迁移）"""
        def db_thread():
            try:
                from init_database import ensure_database_ready
                ensure_database_ready()
                if self.is_ready:
                    if on_ready:
                        on_ready()
            except Exception as e:
                if debug:
                    debug.print(f"数据库初始化失败: {e}")
        threading.Thread(target=db_thread, daemon=True).start()

    def dump_startup_debug_data(self, stage_name, folder_path, config, folder_cache, debug=None):
        """启动阶段调试数据保存（从main.py _dump_startup_debug_data 迁移）"""
        try:
            import tempfile
            dump_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_dumps")
            os.makedirs(dump_dir, exist_ok=True)
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            base_name = f"startup_debug_{timestamp}_{stage_name.replace(' ', '_')}"
            
            dump_data = {
                "stage": stage_name,
                "timestamp": timestamp,
                "target_folder": folder_path,
                "db_ready": self.is_ready if hasattr(self, 'is_ready') else False,
                "gallery_loader_exists": False,
            }
            
            try:
                config_path = config.config_file if hasattr(config, 'config_file') else None
                if config_path and os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        dump_data["config_json"] = json.load(f)
                else:
                    dump_data["config_json"] = {"_note": "config_file not found", "path": str(config_path)}
            except Exception as e:
                dump_data["config_json"] = {"_error": str(e)}
            
            folder_cache_info = {}
            if folder_cache:
                try:
                    cache_dir = folder_cache.cache_dir
                    folder_cache_info["cache_dir"] = cache_dir
                    folder_cache_info["cache_files"] = []
                    if os.path.exists(cache_dir):
                        for fname in sorted(os.listdir(cache_dir)):
                            if fname.endswith('.json'):
                                fpath = os.path.join(cache_dir, fname)
                                try:
                                    with open(fpath, 'r', encoding='utf-8') as f:
                                        cdata = json.load(f)
                                    folder_cache_info["cache_files"].append({
                                        "file": fname,
                                        "folder_path": cdata.get('folder_path'),
                                        "item_count": len(cdata.get('files_info', [])),
                                        "cache_time": cdata.get('cache_time')
                                    })
                                except Exception as e:
                                    folder_cache_info["cache_files"].append({"file": fname, "_error": str(e)})
                    
                    if folder_path:
                        try:
                            target_cache = folder_cache.load_folder_cache(folder_path)
                            if target_cache:
                                folder_cache_info["target_folder_cache"] = {
                                    "folder_path": target_cache.get('folder_path'),
                                    "item_count": len(target_cache.get('files_info', [])),
                                    "files_info": target_cache.get('files_info', [])
                                }
                            else:
                                folder_cache_info["target_folder_cache"] = {"_note": "no cache found"}
                        except Exception as e:
                            folder_cache_info["target_folder_cache"] = {"_error": str(e)}
                except Exception as e:
                    folder_cache_info["_error"] = str(e)
            else:
                folder_cache_info["_note"] = "folder_cache not available"
            dump_data["folder_cache"] = folder_cache_info
            
            db_cache_info = {}
            if self.is_ready:
                try:
                    db_cache_info["is_ready"] = self.is_ready
                    db_cache_info["is_cache_ready"] = getattr(self, 'is_cache_ready', False)
                    db_cache_info["files_count"] = len(self._cache.get('files', {}))
                    db_cache_info["folders_count"] = len(self._cache.get('folders', {}))
                    db_cache_info["actors_count"] = len(self._cache.get('actors', {}))
                    
                    if folder_path:
                        related_files = {}
                        for fp, info in self._cache.get('files', {}).items():
                            if info.get('folder_path') == folder_path:
                                related_files[fp] = info
                        db_cache_info["target_folder_files"] = related_files
                        db_cache_info["target_folder_files_count"] = len(related_files)
                except Exception as e:
                    db_cache_info["_error"] = str(e)
            else:
                db_cache_info["_note"] = "db not available"
            dump_data["db_cache"] = db_cache_info
            
            dump_path = os.path.join(dump_dir, f"{base_name}.json")
            with open(dump_path, 'w', encoding='utf-8') as f:
                json.dump(dump_data, f, ensure_ascii=False, indent=2)
            
            if debug:
                debug.print(f"[调试dump] 已保存启动调试数据: {dump_path}")
        except Exception as e:
            if debug:
                debug.print(f"[调试dump] 保存失败: {e}")


# ==================== 模块级函数：UI 缓存操作（从 main.py 迁移）====================

def load_ui_cache(debug=None):
    """加载UI缓存配置（从main.py _load_ui_cache 迁移）"""
    default = {
        'bookmarks': [], 'last_folder': None, 'last_root': None,
        'expanded_paths': [], 'tree_scroll_position': 0.0, 'last_scroll_y': 0, 'timestamp': 0
    }
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                full_config = json.load(f)
                cache = full_config.get('ui_cache', {})
                if time.time() - cache.get('timestamp', 0) < 86400:
                    return {**default, **cache}
    except Exception as e:
        if debug:
            debug.print(f"加载缓存失败: {e}")
    return default


def save_ui_cache(cache_data, debug=None):
    """保存UI缓存到config.json（从main.py _save_ui_cache 迁移）"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        full_config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                full_config = json.load(f)
        full_config['ui_cache'] = cache_data
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(full_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        if debug:
            debug.print(f"保存缓存失败: {e}")