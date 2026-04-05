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
    
    def _load_all_to_cache(self):
        """将所有数据加载到内存缓存（修复：使用[]代替.get()）"""
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
                'video_relations': {}
            }
            
            # 分批加载文件夹（优化大数据量情况）
            batch_size = 2000
            offset = 0
            while True:
                cursor.execute("""
                    SELECT * FROM folders 
                    WHERE is_deleted = 0 OR is_deleted IS NULL
                    LIMIT ? OFFSET ?
                """, (batch_size, offset))
                rows = cursor.fetchall()
                if not rows:
                    break
                
                for row in rows:
                    self._cache['folders'][row['folder_path']] = {
                        'id': row['folder_id'],
                        'path': row['folder_path'],
                        'name': row['folder_name'],
                        'parent': row['parent_path'] if row['parent_path'] else None,
                        'is_bookmark': bool(row['is_bookmark']) if row['is_bookmark'] else False,
                        'modified': convert_datetime(row['last_modified']),
                        'file_count': row['file_count'] or 0
                    }
                    if row['is_bookmark']:
                        self._cache['bookmarks'].append(row['folder_path'])
                
                offset += batch_size
                if len(rows) < batch_size:
                    break
            
            # 分批加载媒体文件
            offset = 0
            while True:
                cursor.execute("""
                    SELECT f.*, fo.folder_path as folder_dir 
                    FROM media_files f 
                    JOIN folders fo ON f.folder_id = fo.folder_id
                    WHERE f.is_deleted = 0 OR f.is_deleted IS NULL
                    LIMIT ? OFFSET ?
                """, (batch_size, offset))
                rows = cursor.fetchall()
                if not rows:
                    break
                
                for row in rows:
                    is_alias_val = row['is_alias'] if 'is_alias' in row.keys() else 0
                    
                    self._cache['files'][row['file_path']] = {
                        'id': row['file_id'],
                        'folder_id': row['folder_id'],
                        'folder_path': row['folder_dir'],
                        'name': row['file_name'],
                        'type': row['file_type'],
                        'size': row['file_size'] or 0,
                        'modified': convert_datetime(row['last_modified']),
                        'is_alias': is_alias_val,
                        'tag_summary': row['tag_summary'] if 'tag_summary' in row.keys() else None
                    }
                
                offset += batch_size
                if len(rows) < batch_size:
                    break
            
            # 加载演员信息（通常数据量小，一次性加载）
            cursor.execute("SELECT * FROM actors")
            for row in cursor.fetchall():
                self._cache['actors'][row['actor_id']] = {
                    'id': row['actor_id'],
                    'name': row['actor_name'],
                    'name_cn': row['actor_name_cn'] if row['actor_name_cn'] else None,
                    'sort_char': row['sort_char'] if row['sort_char'] else None,
                    'created': convert_datetime(row['created_date']),
                    'updated': convert_datetime(row['updated_date'])
                }
            
            # 加载演员关联
            cursor.execute("SELECT file_id, actor_id FROM file_actors")
            for row in cursor.fetchall():
                if row['file_id'] not in self._cache['file_actors']:
                    self._cache['file_actors'][row['file_id']] = []
                self._cache['file_actors'][row['file_id']].append(row['actor_id'])
            
            # 加载视频关联关系
            cursor.execute("SELECT primary_file_id, alias_file_id FROM video_relations")
            for row in cursor.fetchall():
                self._cache['video_relations'][row['alias_file_id']] = row['primary_file_id']
            
            cursor.close()
            
            if self.debug:
                self.debug.print(f"缓存加载完成: {len(self._cache['folders'])} 文件夹, "
                               f"{len(self._cache['files'])} 文件, "
                               f"{len(self._cache['actors'])} 演员, "
                               f"{len(self._cache['video_relations'])} 视频关联")
    
    # ==================== 视频关联管理接口 ====================
    
    def get_primary_video_path(self, file_id):
        """获取关联视频的主视频路径"""
        if not self._initialized:
            return None
        
        # 先查缓存（即使缓存未完全加载也可以查询）
        with self._cache_lock:
            if file_id in self._cache['video_relations']:
                primary_id = self._cache['video_relations'][file_id]
                for path, info in self._cache['files'].items():
                    if info['id'] == primary_id and info['type'] == 'video':
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
            alias_folder = os.path.dirname(alias_path)
            
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
        """根据路径获取文件信息"""
        with self._cache_lock:
            return self._cache['files'].get(file_path)
    
    # ==================== 文件夹同步检查（优化：增加强制刷新参数）====================
    
    def async_sync_check(self, root_folders, progress_callback=None, complete_callback=None, force_full=False):
        """
        异步检查文件夹更新并同步到数据库
        
        Args:
            root_folders: 根文件夹列表
            progress_callback: 进度回调
            complete_callback: 完成回调
            force_full: 是否强制全量扫描（False时只检查变更的文件夹）
        """
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
            
            with self._cache_lock:
                cached = self._cache['folders'].get(folder_path)
                if cached and cached.get('modified'):
                    return current_mtime_dt > cached['modified']
            return True  # 无缓存，认为已变更
        except:
            return True  # 出错时默认需要同步
    
    def _sync_folder(self, folder_path):
        """同步单个文件夹及其子文件夹"""
        from core import PathUtils
        
        changes = 0
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 标准化路径
        folder_path = os.path.normpath(folder_path)
        
        # 检查当前文件夹是否在数据库中
        cursor.execute("SELECT folder_id, last_modified FROM folders WHERE folder_path = ?", (folder_path,))
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
            # 检查是否需要更新（时间比较）
            db_mtime = existing['last_modified']
            db_mtime_dt = convert_datetime(db_mtime)
            
            # 优化：如果修改时间相同且非强制刷新，跳过详细扫描
            if db_mtime_dt and abs((current_mtime - db_mtime_dt).total_seconds()) < 1:
                # 时间几乎相同，假设无变更，但检查子文件夹是否存在
                pass  # 继续扫描子文件夹
            else:
                # 更新文件夹信息
                cursor.execute("""
                    UPDATE folders 
                    SET last_modified = ?, scan_time = datetime('now')
                    WHERE folder_id = ?
                """, (adapt_datetime(current_mtime), folder_id))
                changes += 1
        else:
            # 新增文件夹
            folder_name = os.path.basename(folder_path)
            parent_path = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
            
            cursor.execute("""
                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified, scan_time)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (folder_path, folder_name, parent_path, adapt_datetime(current_mtime)))
            
            folder_id = cursor.lastrowid
            changes += 1
        
        # 扫描文件夹内文件（仅在文件夹新增或修改时）
        if folder_id and os.path.exists(folder_path):
            # 获取数据库中该文件夹的文件列表（仅获取路径和修改时间，减少内存使用）
            cursor.execute("""
                SELECT file_path, last_modified FROM media_files 
                WHERE folder_id = ? AND is_deleted = 0
            """, (folder_id,))
            db_files = {row['file_path']: row['last_modified'] for row in cursor.fetchall()}
            
            # 扫描实际文件
            current_files = set()
            try:
                for entry in os.scandir(folder_path):
                    if entry.is_file():
                        ext = os.path.splitext(entry.name)[1].lower()
                        is_video = ext in PathUtils.VIDEO_EXTENSIONS
                        is_image = ext in PathUtils.IMAGE_EXTENSIONS
                        
                        if is_video or is_image:
                            file_path = entry.path
                            current_files.add(file_path)
                            file_type = 'video' if is_video else 'image'
                            file_mtime = datetime.fromtimestamp(entry.stat().st_mtime)
                            file_size = entry.stat().st_size
                            
                            # 检查是否需要更新
                            if file_path in db_files:
                                db_mtime = db_files[file_path]
                                db_mtime_dt = convert_datetime(db_mtime)
                                # 优化：时间差小于1秒认为相同
                                if db_mtime_dt is None or abs((file_mtime - db_mtime_dt).total_seconds()) >= 1:
                                    # 更新文件信息
                                    cursor.execute("""
                                        UPDATE media_files 
                                        SET last_modified = ?, file_size = ?
                                        WHERE file_path = ?
                                    """, (adapt_datetime(file_mtime), file_size, file_path))
                                    changes += 1
                            else:
                                # 新增文件
                                cursor.execute("""
                                    INSERT INTO media_files 
                                    (folder_id, file_path, file_name, file_type, file_size, last_modified)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (folder_id, file_path, entry.name, file_type, file_size, adapt_datetime(file_mtime)))
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
                    UPDATE media_files SET is_deleted = 1 WHERE file_path IN ({placeholders})
                """, batch)
                changes += len(batch)
            
            # 更新文件夹文件计数
            cursor.execute("""
                UPDATE folders SET file_count = ? WHERE folder_id = ?
            """, (len(current_files), folder_id))
        
        # 递归处理子文件夹（按需递归）
        try:
            for entry in os.scandir(folder_path):
                if entry.is_dir() and not entry.name.startswith('.'):
                    sub_changes = self._sync_folder(entry.path)
                    changes += sub_changes
        except PermissionError:
            pass
        
        conn.commit()
        cursor.close()
        return changes
    
    # ==================== 数据查询接口 ====================
    
    def get_folders_by_parent(self, parent_path=None, include_bookmarks=False):
        """获取子文件夹列表（从缓存）"""
        with self._cache_lock:
            if include_bookmarks and parent_path is None:
                # 返回所有书签
                return [self._cache['folders'][p] for p in self._cache['bookmarks'] 
                       if p in self._cache['folders']]
            
            result = []
            for path, info in self._cache['folders'].items():
                if info.get('parent') == parent_path:
                    result.append(info)
            return sorted(result, key=lambda x: x['name'])
    
    def get_files_by_folder(self, folder_path):
        """获取文件夹内文件（从缓存）"""
        with self._cache_lock:
            result = []
            for path, info in self._cache['files'].items():
                if info.get('folder_path') == folder_path:
                    result.append(info)
            return sorted(result, key=lambda x: x['name'])
    
    # ==================== 演员相关接口 ====================
    
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