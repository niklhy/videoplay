#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库初始化脚本 - 增强版（支持设备/书签隔离）
支持重复运行自动检查和更新表结构
用法: python init_database.py [--force] [--check]
"""
import os
import sqlite3
import argparse
from datetime import datetime


def get_db_path():
    """获取数据库文件路径"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_library.db")


def get_connection(db_path):
    """获取数据库连接"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # 性能优化设置
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = 10000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def table_exists(cursor, table_name):
    """检查表是否存在"""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def index_exists(cursor, index_name):
    """检查索引是否存在"""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,)
    )
    return cursor.fetchone() is not None


def get_table_info(cursor, table_name):
    """获取表结构信息"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row['name']: row for row in cursor.fetchall()}


def drop_legacy_tag_tables(cursor):
    """清理旧版标签相关表（如果存在）"""
    legacy_tables = ['file_tags', 'tags']
    dropped = []
    
    for table in legacy_tables:
        if table_exists(cursor, table):
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
                dropped.append(table)
            except Exception as e:
                print(f"  警告: 删除旧表 {table} 失败: {e}")
    
    # 清理相关索引
    legacy_indexes = ['idx_file_tag', 'idx_tag_count']
    for idx in legacy_indexes:
        if index_exists(cursor, idx):
            try:
                cursor.execute(f"DROP INDEX IF EXISTS {idx}")
            except:
                pass
    
    return dropped


def check_and_create_database(db_path=None, force=False, fast_mode=False):
    """
    检查并创建/更新数据库结构
    自动清理残留的旧版标签表（不做数据迁移）
    
    Args:
        db_path: 数据库文件路径，None则使用默认路径
        force: 是否强制重建数据库
        fast_mode: 快速模式，减少输出信息，适用于程序内部调用
    
    Returns:
        tuple: (success: bool, message: str, is_new_db: bool)
    """
    if db_path is None:
        db_path = get_db_path()

    db_path = os.path.abspath(db_path)
    is_new_db = not os.path.exists(db_path)

    # 确保目录存在
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except Exception as e:
        return False, f"无法创建数据库目录: {e}", False

    if not fast_mode:
        print(f"数据库路径: {db_path}")
        print(f"数据库状态: {'新建' if is_new_db else '已存在'}")
        print("-" * 60)

    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        changes = []

        # ==================== 1. 清理残留的旧版标签表 ====================
        dropped_legacy = drop_legacy_tag_tables(cursor)
        if dropped_legacy and not fast_mode:
            changes.append(f"✓ 清理旧版标签表: {', '.join(dropped_legacy)}")

        # ==================== 2. 创建/更新 folders 表（增加设备/书签字段）====================
        if not table_exists(cursor, 'folders'):
            cursor.execute("""
                CREATE TABLE folders (
                    folder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_path TEXT NOT NULL,
                    folder_name TEXT NOT NULL,
                    parent_path TEXT,
                    is_bookmark INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0,
                    last_modified TIMESTAMP,
                    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_count INTEGER DEFAULT 0,
                    device_id TEXT DEFAULT 'unknown',
                    bookmark_id TEXT,
                    UNIQUE(folder_path, device_id, bookmark_id)
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: folders (含 device_id, bookmark_id)")
        else:
            # 检查并添加缺失字段
            existing_cols = get_table_info(cursor, 'folders')
            required_cols = {
                'folder_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'folder_path': 'TEXT NOT NULL',
                'folder_name': 'TEXT NOT NULL',
                'parent_path': 'TEXT',
                'is_bookmark': 'INTEGER DEFAULT 0',
                'is_deleted': 'INTEGER DEFAULT 0',
                'last_modified': 'TIMESTAMP',
                'scan_time': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
                'file_count': 'INTEGER DEFAULT 0',
                'device_id': 'TEXT DEFAULT "unknown"',
                'bookmark_id': 'TEXT'
            }

            for col_name, col_type in required_cols.items():
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE folders ADD COLUMN {col_name} {col_type}")
                        if not fast_mode:
                            changes.append(f"✓ 添加字段: folders.{col_name}")
                    except sqlite3.OperationalError as e:
                        if not fast_mode:
                            changes.append(f"⚠ 跳过字段: folders.{col_name} ({e})")
            
            # 创建复合唯一索引
            try:
                cursor.execute("DROP INDEX IF EXISTS idx_folder_path")
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_folder 
                    ON folders(folder_path, device_id, bookmark_id)
                """)
                if not fast_mode:
                    changes.append("✓ 创建复合唯一索引: idx_unique_folder (folder_path, device_id, bookmark_id)")
            except Exception as e:
                if not fast_mode:
                    changes.append(f"⚠ 创建复合索引失败: {e}")

        # 创建 folders 表其他索引
        if not index_exists(cursor, 'idx_folder_path'):
            cursor.execute("CREATE INDEX idx_folder_path ON folders(folder_path)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_folder_path")

        if not index_exists(cursor, 'idx_parent'):
            cursor.execute("CREATE INDEX idx_parent ON folders(parent_path)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_parent")

        if not index_exists(cursor, 'idx_device_bookmark'):
            cursor.execute("CREATE INDEX idx_device_bookmark ON folders(device_id, bookmark_id)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_device_bookmark")

        # ==================== 3. 创建/更新 actors 表 ====================
        if not table_exists(cursor, 'actors'):
            cursor.execute("""
                CREATE TABLE actors (
                    actor_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_name TEXT NOT NULL,
                    actor_name_cn TEXT,
                    sort_char TEXT,
                    avatar_url TEXT,
                    sort_order INTEGER DEFAULT 0,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: actors (含 avatar_url, sort_order)")
        else:
            # 检查并添加缺失字段
            existing_cols = get_table_info(cursor, 'actors')
            actor_required_cols = {
                'avatar_url': 'TEXT',
                'sort_order': 'INTEGER DEFAULT 0'
            }
            for col_name, col_type in actor_required_cols.items():
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE actors ADD COLUMN {col_name} {col_type}")
                        if not fast_mode:
                            changes.append(f"✓ 添加字段: actors.{col_name}")
                    except sqlite3.OperationalError as e:
                        if not fast_mode:
                            changes.append(f"⚠ 跳过字段: actors.{col_name} ({e})")

        # 创建 actors 表索引
        if not index_exists(cursor, 'idx_actor_name'):
            cursor.execute("CREATE INDEX idx_actor_name ON actors(actor_name)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_actor_name")

        if not index_exists(cursor, 'idx_actor_name_cn'):
            cursor.execute("CREATE INDEX idx_actor_name_cn ON actors(actor_name_cn)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_actor_name_cn")

        # ==================== 4. 创建 actor_relations 表 ====================
        if not table_exists(cursor, 'actor_relations'):
            cursor.execute("""
                CREATE TABLE actor_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER NOT NULL,
                    related_actor_id INTEGER NOT NULL,
                    relation_type TEXT DEFAULT 'alias',
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(actor_id, related_actor_id),
                    FOREIGN KEY (actor_id) REFERENCES actors(actor_id) ON DELETE CASCADE,
                    FOREIGN KEY (related_actor_id) REFERENCES actors(actor_id) ON DELETE CASCADE
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: actor_relations")

        if not index_exists(cursor, 'idx_actor_relation'):
            cursor.execute("CREATE INDEX idx_actor_relation ON actor_relations(actor_id)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_actor_relation")

        if not index_exists(cursor, 'idx_related_actor'):
            cursor.execute("CREATE INDEX idx_related_actor ON actor_relations(related_actor_id)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_related_actor")

        # ==================== 5. 创建/更新 actor_folders 表（增加设备/书签字段）====================
        if not table_exists(cursor, 'actor_folders'):
            cursor.execute("""
                CREATE TABLE actor_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_path TEXT NOT NULL,
                    folder_name TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_scan TIMESTAMP,
                    device_id TEXT DEFAULT 'unknown',
                    bookmark_id TEXT,
                    UNIQUE(folder_path, device_id, bookmark_id)
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: actor_folders (含 device_id, bookmark_id)")
        else:
            existing_cols = get_table_info(cursor, 'actor_folders')
            if 'device_id' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE actor_folders ADD COLUMN device_id TEXT DEFAULT 'unknown'")
                    if not fast_mode:
                        changes.append("✓ 添加字段: actor_folders.device_id")
                except Exception:
                    pass
            if 'bookmark_id' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE actor_folders ADD COLUMN bookmark_id TEXT")
                    if not fast_mode:
                        changes.append("✓ 添加字段: actor_folders.bookmark_id")
                except Exception:
                    pass

        # ==================== 6. 创建/更新 media_files 表（增加设备/书签字段）====================
        if not table_exists(cursor, 'media_files'):
            cursor.execute("""
                CREATE TABLE media_files (
                    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_id INTEGER,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_type TEXT CHECK(file_type IN ('video', 'image')),
                    file_size INTEGER DEFAULT 0,
                    release_date DATE,
                    video_title TEXT,
                    duration INTEGER DEFAULT 0,
                    last_modified TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0,
                    is_alias INTEGER DEFAULT 0,
                    tag_summary TEXT,
                    video_code TEXT,
                    device_id TEXT DEFAULT 'unknown',
                    bookmark_id TEXT,
                    UNIQUE(file_path, device_id, bookmark_id),
                    FOREIGN KEY (folder_id) REFERENCES folders(folder_id) ON DELETE CASCADE
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: media_files (含 device_id, bookmark_id, video_code)")
        else:
            # 检查并添加缺失字段
            existing_cols = get_table_info(cursor, 'media_files')
            required_cols = {
                'file_id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
                'folder_id': 'INTEGER',
                'file_path': 'TEXT NOT NULL',
                'file_name': 'TEXT NOT NULL',
                'file_type': "TEXT CHECK(file_type IN ('video', 'image'))",
                'file_size': 'INTEGER DEFAULT 0',
                'release_date': 'DATE',
                'video_title': 'TEXT',
                'duration': 'INTEGER DEFAULT 0',
                'last_modified': 'TIMESTAMP',
                'is_deleted': 'INTEGER DEFAULT 0',
                'is_alias': 'INTEGER DEFAULT 0',
                'tag_summary': 'TEXT',
                'video_code': 'TEXT',
                'device_id': 'TEXT DEFAULT "unknown"',
                'bookmark_id': 'TEXT'
            }

            for col_name, col_type in required_cols.items():
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE media_files ADD COLUMN {col_name} {col_type}")
                        if not fast_mode:
                            changes.append(f"✓ 添加字段: media_files.{col_name}")
                    except sqlite3.OperationalError as e:
                        if not fast_mode:
                            changes.append(f"⚠ 跳过字段: media_files.{col_name} ({e})")
            
            # 创建复合唯一索引
            try:
                cursor.execute("DROP INDEX IF EXISTS idx_file_path")
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file 
                    ON media_files(file_path, device_id, bookmark_id)
                """)
                if not fast_mode:
                    changes.append("✓ 创建复合唯一索引: idx_unique_file (file_path, device_id, bookmark_id)")
            except Exception as e:
                if not fast_mode:
                    changes.append(f"⚠ 创建复合索引失败: {e}")

        # 创建 media_files 表其他索引
        indexes_to_create = [
            ('idx_file_path', 'CREATE INDEX idx_file_path ON media_files(file_path)'),
            ('idx_folder', 'CREATE INDEX idx_folder ON media_files(folder_id)'),
            ('idx_type', 'CREATE INDEX idx_type ON media_files(file_type)'),
            ('idx_release_date', 'CREATE INDEX idx_release_date ON media_files(release_date)'),
            ('idx_is_alias', 'CREATE INDEX idx_is_alias ON media_files(is_alias)'),
            ('idx_media_device', 'CREATE INDEX idx_media_device ON media_files(device_id, bookmark_id)')
        ]
        
        for idx_name, idx_sql in indexes_to_create:
            if not index_exists(cursor, idx_name):
                cursor.execute(idx_sql)
                if not fast_mode:
                    changes.append(f"✓ 创建索引: {idx_name}")

        # ==================== 7. 创建 file_actors 表 ====================
        if not table_exists(cursor, 'file_actors'):
            cursor.execute("""
                CREATE TABLE file_actors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    bind_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_id, actor_id),
                    FOREIGN KEY (file_id) REFERENCES media_files(file_id) ON DELETE CASCADE,
                    FOREIGN KEY (actor_id) REFERENCES actors(actor_id) ON DELETE CASCADE
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: file_actors")

        if not index_exists(cursor, 'idx_file_actor'):
            cursor.execute("CREATE INDEX idx_file_actor ON file_actors(file_id, actor_id)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_file_actor")

        if not index_exists(cursor, 'idx_actor_file'):
            cursor.execute("CREATE INDEX idx_actor_file ON file_actors(actor_id)")
            if not fast_mode:
                changes.append("✓ 创建索引: idx_actor_file")

        # ==================== 8. 创建/更新 video_relations 表（增加设备/书签字段）====================
        if not table_exists(cursor, 'video_relations'):
            cursor.execute("""
                CREATE TABLE video_relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    primary_file_id INTEGER NOT NULL,
                    alias_file_id INTEGER NOT NULL UNIQUE,
                    alias_folder_path TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    device_id TEXT DEFAULT 'unknown',
                    bookmark_id TEXT,
                    FOREIGN KEY (primary_file_id) REFERENCES media_files(file_id) ON DELETE CASCADE,
                    FOREIGN KEY (alias_file_id) REFERENCES media_files(file_id) ON DELETE CASCADE
                )
            """)
            if not fast_mode:
                changes.append("✓ 创建表: video_relations (含 device_id, bookmark_id)")
        else:
            existing_cols = get_table_info(cursor, 'video_relations')
            if 'alias_folder_path' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE video_relations ADD COLUMN alias_folder_path TEXT")
                    if not fast_mode:
                        changes.append("✓ 添加字段: video_relations.alias_folder_path")
                except Exception:
                    pass
            if 'device_id' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE video_relations ADD COLUMN device_id TEXT DEFAULT 'unknown'")
                    if not fast_mode:
                        changes.append("✓ 添加字段: video_relations.device_id")
                except Exception:
                    pass
            if 'bookmark_id' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE video_relations ADD COLUMN bookmark_id TEXT")
                    if not fast_mode:
                        changes.append("✓ 添加字段: video_relations.bookmark_id")
                except Exception:
                    pass

        # 确保索引存在
        vr_indexes = [
            ('idx_video_relation_primary', 'CREATE INDEX idx_video_relation_primary ON video_relations(primary_file_id)'),
            ('idx_video_relation_alias', 'CREATE INDEX idx_video_relation_alias ON video_relations(alias_file_id)'),
            ('idx_video_relation_folder', 'CREATE INDEX idx_video_relation_folder ON video_relations(alias_folder_path)'),
            ('idx_video_device', 'CREATE INDEX idx_video_device ON video_relations(device_id, bookmark_id)')
        ]
        
        for idx_name, idx_sql in vr_indexes:
            if not index_exists(cursor, idx_name):
                cursor.execute(idx_sql)
                if not fast_mode:
                    changes.append(f"✓ 创建索引: {idx_name}")

        # 提交结构更改
        conn.commit()
        cursor.close()

        # ========== 路径规范化迁移：统一使用反斜杠并转为小写 ==========
        normalize_database_paths(conn, fast_mode=fast_mode)

        conn.close()

        # 输出结果
        if not fast_mode:
            print()
            if changes:
                print("执行的操作:")
                for change in changes:
                    print(f"  {change}")
            else:
                print("数据库结构已是最新，无需修改")

            print()
            print("=" * 60)
            print("数据库检查/初始化完成")
            print("=" * 60)
            print(f"数据库文件: {db_path}")
            if os.path.exists(db_path):
                print(f"文件大小: {os.path.getsize(db_path) / 1024:.1f} KB")
            print("=" * 60)

        return True, "数据库就绪", is_new_db

    except Exception as e:
        error_msg = f"数据库操作失败: {e}"
        if not fast_mode:
            print(f"\n错误: {error_msg}")
            import traceback
            traceback.print_exc()
        return False, error_msg, False


def normalize_database_paths(conn, fast_mode=False):
    """
    规范化数据库中的所有路径：统一使用反斜杠并转为小写。
    
    这是为了确保 Windows 下路径比较的一致性：
    - 将正斜杠 '/' 替换为反斜杠 '\\'
    - 通过 os.path.normpath 消除多余反斜杠和相对路径
    - 全部转为小写（Windows 文件系统不区分大小写）
    
    Args:
        conn: 数据库连接
        fast_mode: 是否静默模式（减少输出）
    """
    try:
        cursor = conn.cursor()
        total_changes = 0

        # 1. 规范化 folders 表
        cursor.execute("SELECT folder_id, folder_path, parent_path FROM folders")
        folder_updates = []
        for row in cursor.fetchall():
            folder_id = row['folder_id']
            old_folder = row['folder_path']
            old_parent = row['parent_path']
            new_folder = os.path.normpath(old_folder).lower() if old_folder else old_folder
            new_parent = os.path.normpath(old_parent).lower() if old_parent else old_parent
            if new_folder != old_folder or new_parent != old_parent:
                folder_updates.append((new_folder, new_parent, folder_id))
        
        if folder_updates:
            cursor.executemany(
                "UPDATE folders SET folder_path = ?, parent_path = ? WHERE folder_id = ?",
                folder_updates
            )
            total_changes += len(folder_updates)

        # 2. 规范化 media_files 表
        cursor.execute("SELECT file_id, file_path FROM media_files")
        file_updates = []
        for row in cursor.fetchall():
            file_id = row['file_id']
            old_path = row['file_path']
            new_path = os.path.normpath(old_path).lower() if old_path else old_path
            if new_path != old_path:
                file_updates.append((new_path, file_id))
        
        if file_updates:
            cursor.executemany(
                "UPDATE media_files SET file_path = ? WHERE file_id = ?",
                file_updates
            )
            total_changes += len(file_updates)

        # 3. 规范化 video_relations 表
        cursor.execute("SELECT relation_id, alias_folder_path FROM video_relations")
        relation_updates = []
        for row in cursor.fetchall():
            relation_id = row['relation_id']
            old_alias = row['alias_folder_path']
            new_alias = os.path.normpath(old_alias).lower() if old_alias else old_alias
            if new_alias != old_alias:
                relation_updates.append((new_alias, relation_id))
        
        if relation_updates:
            cursor.executemany(
                "UPDATE video_relations SET alias_folder_path = ? WHERE relation_id = ?",
                relation_updates
            )
            total_changes += len(relation_updates)

        conn.commit()
        cursor.close()

        if total_changes > 0 and not fast_mode:
            print(f"  路径规范化: {total_changes} 条记录已更新（统一反斜杠+小写）")

    except Exception as e:
        # 路径规范化失败不应阻止程序启动，记录错误即可
        if not fast_mode:
            print(f"  路径规范化跳过: {e}")


def ensure_database_ready(db_path=None):
    """
    供主程序调用的便捷接口，确保数据库就绪（快速模式）
    在程序启动时调用，自动检查并更新数据库结构
    
    Returns:
        tuple: (success: bool, message: str)
    """
    success, msg, is_new = check_and_create_database(db_path, force=False, fast_mode=True)
    return success, msg


def check_database_status(db_path=None):
    """检查数据库状态并输出详细信息"""
    if db_path is None:
        db_path = get_db_path()

    if not os.path.exists(db_path):
        print(f"数据库不存在: {db_path}")
        return False

    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        print("=" * 60)
        print("数据库状态检查")
        print("=" * 60)
        print(f"数据库路径: {db_path}")
        print(f"文件大小: {os.path.getsize(db_path) / 1024:.1f} KB")
        print()

        # 检查表
        print("【表结构】")
        tables = ['folders', 'actors', 'actor_relations', 'actor_folders', 
                  'media_files', 'file_actors', 'video_relations']
        for table in tables:
            exists = table_exists(cursor, table)
            status = "✓ 存在" if exists else "✗ 不存在"
            print(f"  {table:<20} {status}")

            if exists:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = cursor.fetchall()
                print(f"    字段: {', '.join([c['name'] for c in columns])}")
        print()

        # 检查旧版标签表（应已被删除）
        print("【旧版标签表状态（应不存在）】")
        legacy_tables = ['tags', 'file_tags']
        for table in legacy_tables:
            exists = table_exists(cursor, table)
            status = "⚠ 仍存在" if exists else "✓ 已清理"
            print(f"  {table:<20} {status}")
        print()

        # 统计信息
        print("【统计信息】")
        try:
            cursor.execute("SELECT COUNT(*) FROM folders")
            folder_count = cursor.fetchone()[0]
            print(f"  文件夹数量: {folder_count}")
            
            cursor.execute("SELECT COUNT(DISTINCT folder_path) FROM folders")
            unique_folders = cursor.fetchone()[0]
            print(f"  唯一文件夹路径: {unique_folders}")

            cursor.execute("SELECT COUNT(*) FROM media_files WHERE is_deleted = 0")
            file_count = cursor.fetchone()[0]
            print(f"  媒体文件数量: {file_count}")
            
            cursor.execute("SELECT COUNT(DISTINCT file_path) FROM media_files WHERE is_deleted = 0")
            unique_files = cursor.fetchone()[0]
            print(f"  唯一文件路径: {unique_files}")
            
            cursor.execute("""
                SELECT COUNT(*) FROM media_files 
                WHERE is_deleted = 0 AND device_id != 'unknown'
            """)
            device_files = cursor.fetchone()[0]
            print(f"  带设备标识的文件: {device_files}")
            
            cursor.execute("SELECT COUNT(*) FROM video_relations")
            relation_count = cursor.fetchone()[0]
            print(f"  视频关联关系: {relation_count}")
            
            cursor.execute("SELECT COUNT(*) FROM media_files WHERE tag_summary IS NOT NULL AND tag_summary != ''")
            tagged_count = cursor.fetchone()[0]
            print(f"  已标记标签的文件: {tagged_count}")
            
            cursor.execute("SELECT COUNT(*) FROM actors")
            actor_count = cursor.fetchone()[0]
            print(f"  演员数量: {actor_count}")

        except Exception as e:
            print(f"  统计信息获取失败: {e}")

        cursor.close()
        conn.close()

        print("=" * 60)
        return True

    except Exception as e:
        print(f"检查失败: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite数据库初始化脚本（增强版 - 支持设备/书签隔离）")
    parser.add_argument("--force", action="store_true", help="强制重置数据库（删除并重建）")
    parser.add_argument("--check", action="store_true", help="仅检查数据库状态")
    parser.add_argument("--fast", action="store_true", help="快速模式（减少输出）")

    args = parser.parse_args()

    if args.check:
        success = check_database_status()
        exit(0 if success else 1)

    elif args.force:
        # 强制重置
        print("警告: 这将删除现有数据库！")
        confirm = input("确认删除并重新创建? (输入 'yes' 确认): ")
        if confirm.lower() == 'yes':
            db_path = get_db_path()
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                    print(f"已删除旧数据库: {db_path}")
                except Exception as e:
                    print(f"删除失败: {e}")
                    exit(1)
            success, msg, _ = check_and_create_database(db_path, fast_mode=args.fast)
            exit(0 if success else 1)
        else:
            print("已取消")
            exit(0)

    else:
        # 默认：检查并增量更新
        success, msg, _ = check_and_create_database(fast_mode=args.fast)
        exit(0 if success else 1)