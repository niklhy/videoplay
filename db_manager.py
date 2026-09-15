# -*- coding: utf-8 -*-
"""数据库管理模块（对应设计文档第 四 章）。

本模块是全局契约中心：负责 SQLite 连接管理、文件元数据 CRUD，
以及演员 / 标签 / 视频关联 / 手动番号 / 演员文件夹等全部表的操作。
其他所有模块一律通过 DatabaseManager 访问数据库，禁止各自建表或直连。

表结构以设计文档 02 号《数据库管理模块》4.3 节为唯一权威定义。
"""
import os
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from models import Actor, ActorFolderConfig, FileMetadata, Tag, VideoLink


class DatabaseManager:
    """SQLite 数据库管理器（线程安全）。

    连接配置：check_same_thread=False（供后台扫描线程复用）、WAL 模式、
    外键约束开启、行工厂 sqlite3.Row；所有操作经内部 RLock 保护。
    """

    def __init__(self, db_path: str, device_id: str):
        """初始化数据库连接并建表。

        :param db_path: SQLite 数据库文件路径
        :param device_id: 当前设备标识（所有按设备隔离的查询默认使用）
        """
        self.db_path = db_path
        self.current_device_id = device_id
        self._lock = threading.RLock()

        db_dir = os.path.dirname(os.path.abspath(db_path))
        if db_dir and not os.path.isdir(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA synchronous=NORMAL")
        self.init_tables()

    # ------------------------------------------------------------------
    # 通用线程安全辅助
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行写 SQL（自动提交），返回 cursor。供其他模块执行未覆盖的 SQL。

        :param sql: SQL 语句（支持 ? 占位符）
        :param params: 参数元组
        :return: sqlite3.Cursor（写操作后已提交）
        """
        with self._lock:
            cursor = self.connection.execute(sql, params)
            self.connection.commit()
            return cursor

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        """执行查询 SQL，返回 list[sqlite3.Row]。供其他模块执行未覆盖的 SQL。

        :param sql: SQL 语句（支持 ? 占位符）
        :param params: 参数元组
        :return: 查询结果行列表
        """
        with self._lock:
            cursor = self.connection.execute(sql, params)
            return cursor.fetchall()

    # ------------------------------------------------------------------
    # datetime <-> SQLite 文本双向转换
    # ------------------------------------------------------------------
    @staticmethod
    def _dt_to_str(value) -> Optional[str]:
        """datetime 转 SQLite 存储文本（ISO 格式，秒精度）。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    @staticmethod
    def _str_to_dt(value) -> Optional[datetime]:
        """SQLite 文本转 datetime，支持多种常见格式；非法值返回 None。"""
        if value is None or isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------
    # init_tables：创建 4.3 节全部 9 张表与全部索引（幂等）
    # ------------------------------------------------------------------
    def init_tables(self) -> None:
        """创建设计文档 4.3 节的全部 9 张表和全部索引，IF NOT EXISTS 幂等，可重复运行。"""
        ddl_statements = [
            # 1. 文件元数据表
            """CREATE TABLE IF NOT EXISTS files (
                file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, relative_path TEXT NOT NULL, file_name TEXT NOT NULL,
                title TEXT DEFAULT '', duration TEXT DEFAULT '', release_date TEXT DEFAULT '',
                cover_path TEXT DEFAULT '', sync_status TEXT DEFAULT 'pending',
                video_link TEXT DEFAULT '', last_scan TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, relative_path)
            )""",
            # 2. 演员主表
            """CREATE TABLE IF NOT EXISTS actors (
                actor_id INTEGER PRIMARY KEY AUTOINCREMENT,
                japanese_name TEXT NOT NULL, chinese_name TEXT, initial_letter TEXT,
                alt_japanese_name TEXT, alt_chinese_name TEXT,
                profile_image TEXT, notes TEXT, video_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(japanese_name)
            )""",
            # 3. 视频-演员关联表
            """CREATE TABLE IF NOT EXISTS video_actors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, relative_path TEXT NOT NULL, actor_id INTEGER NOT NULL,
                actor_order INTEGER DEFAULT 0, role_type TEXT DEFAULT 'cast',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, relative_path, actor_id),
                FOREIGN KEY (actor_id) REFERENCES actors(actor_id) ON DELETE CASCADE
            )""",
            # 4. 标签定义表
            """CREATE TABLE IF NOT EXISTS tags (
                tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, tag_name TEXT NOT NULL,
                tag_color TEXT DEFAULT '#2196F3',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, tag_name)
            )""",
            # 5. 文件标签关联表
            """CREATE TABLE IF NOT EXISTS file_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, relative_path TEXT NOT NULL, tag_id INTEGER NOT NULL,
                tagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, relative_path, tag_id),
                FOREIGN KEY (tag_id) REFERENCES tags(tag_id)
            )""",
            # 6. 演员标签关联表
            """CREATE TABLE IF NOT EXISTS actor_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(actor_id, tag_id),
                FOREIGN KEY (actor_id) REFERENCES actors(actor_id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
            )""",
            # 7. 视频关联表
            """CREATE TABLE IF NOT EXISTS video_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, primary_video_path TEXT NOT NULL,
                linked_folder_path TEXT NOT NULL, match_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, primary_video_path, linked_folder_path)
            )""",
            # 8. 手动番号映射表
            """CREATE TABLE IF NOT EXISTS custom_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, relative_path TEXT NOT NULL, custom_code TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, relative_path)
            )""",
            # 9. 演员文件夹配置表
            """CREATE TABLE IF NOT EXISTS actor_folders (
                folder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL, folder_path TEXT NOT NULL, folder_name TEXT,
                is_enabled BOOLEAN DEFAULT 1, last_scan TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, folder_path)
            )""",
            # 索引
            "CREATE INDEX IF NOT EXISTS idx_files_device ON files(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_files_path ON files(device_id, relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_files_status ON files(device_id, sync_status)",
            "CREATE INDEX IF NOT EXISTS idx_actors_name ON actors(japanese_name)",
            "CREATE INDEX IF NOT EXISTS idx_actors_cn_name ON actors(chinese_name)",
            "CREATE INDEX IF NOT EXISTS idx_actors_initial ON actors(initial_letter)",
            "CREATE INDEX IF NOT EXISTS idx_actors_alt_jp ON actors(alt_japanese_name)",
            "CREATE INDEX IF NOT EXISTS idx_actors_alt_cn ON actors(alt_chinese_name)",
            "CREATE INDEX IF NOT EXISTS idx_video_actors_device ON video_actors(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_video_actors_path ON video_actors(device_id, relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_video_actors_actor ON video_actors(actor_id)",
            "CREATE INDEX IF NOT EXISTS idx_tags_device ON tags(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_file_tags ON file_tags(device_id, tag_id)",
            "CREATE INDEX IF NOT EXISTS idx_file_tags_path ON file_tags(device_id, relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_video_links_device ON video_links(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_custom_codes_device ON custom_codes(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_custom_codes_path ON custom_codes(device_id, relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_actor_folders_device ON actor_folders(device_id)",
        ]
        with self._lock:
            for sql in ddl_statements:
                self.connection.execute(sql)
            self.connection.commit()

    # ------------------------------------------------------------------
    # 行 -> 数据类转换
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_metadata(row: sqlite3.Row,
                         actor_names: Optional[List[str]] = None,
                         actor_ids: Optional[List[int]] = None,
                         video_code: str = "") -> FileMetadata:
        """files 表行转 FileMetadata 契约对象。"""
        return FileMetadata(
            file_id=row["file_id"],
            device_id=row["device_id"],
            relative_path=row["relative_path"],
            file_name=row["file_name"],
            title=row["title"] or "",
            duration=row["duration"] or "",
            release_date=row["release_date"] or "",
            cover_path=row["cover_path"] or "",
            sync_status=row["sync_status"] or "pending",
            video_link=row["video_link"] or "",
            video_code=video_code or "",
            last_scan=DatabaseManager._str_to_dt(row["last_scan"]),
            created_at=DatabaseManager._str_to_dt(row["created_at"]),
            actor_names=list(actor_names) if actor_names else [],
            actor_ids=list(actor_ids) if actor_ids else [],
        )

    @staticmethod
    def _row_to_actor(row: sqlite3.Row) -> Actor:
        """actors 表行转 Actor 契约对象。"""
        return Actor(
            actor_id=row["actor_id"],
            japanese_name=row["japanese_name"] or "",
            chinese_name=row["chinese_name"] or "",
            initial_letter=row["initial_letter"] or "",
            alt_japanese_name=row["alt_japanese_name"] or "",
            alt_chinese_name=row["alt_chinese_name"] or "",
            profile_image=row["profile_image"] or "",
            notes=row["notes"] or "",
            video_count=row["video_count"] or 0,
            created_at=DatabaseManager._str_to_dt(row["created_at"]),
            updated_at=DatabaseManager._str_to_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_tag(row: sqlite3.Row) -> Tag:
        """tags 表行（含文件计数）转 Tag 契约对象。"""
        return Tag(
            tag_id=row["tag_id"],
            device_id=row["device_id"],
            tag_name=row["tag_name"],
            tag_color=row["tag_color"] or "#2196F3",
            file_count=row["file_count"] if "file_count" in row.keys() else 0,
            created_at=DatabaseManager._str_to_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_link(row: sqlite3.Row) -> VideoLink:
        """video_links 表行转 VideoLink 契约对象。"""
        return VideoLink(
            link_id=row["link_id"],
            device_id=row["device_id"],
            primary_video_path=row["primary_video_path"],
            linked_folder_path=row["linked_folder_path"],
            match_code=row["match_code"] or "",
            created_at=DatabaseManager._str_to_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # 文件元数据 CRUD（设计 4.1 节）
    # ------------------------------------------------------------------
    def get_file_metadata(self, relative_path: str) -> Optional[FileMetadata]:
        """按相对路径查询当前设备的单个文件元数据。

        :param relative_path: 相对于根目录的路径
        :return: FileMetadata 或 None
        """
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM files WHERE device_id=? AND relative_path=?",
                (self.current_device_id, relative_path),
            ).fetchone()
            if row is None:
                return None
            names, ids = self._get_video_actors(self.current_device_id, relative_path)
            code = self.get_custom_code(relative_path)
            return self._row_to_metadata(row, names, ids, code or "")

    def get_files_metadata_batch(self, relative_paths: List[str]) -> Dict[str, FileMetadata]:
        """批量查询文件元数据。

        :param relative_paths: 相对路径列表
        :return: dict，relative_path -> FileMetadata（仅包含存在的记录）
        """
        if not relative_paths:
            return {}
        placeholders = ",".join("?" for _ in relative_paths)
        params = [self.current_device_id, *relative_paths]
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM files WHERE device_id=? AND relative_path IN ({placeholders})",
                params,
            ).fetchall()
            result: Dict[str, FileMetadata] = {}
            for row in rows:
                rel = row["relative_path"]
                names, ids = self._get_video_actors(self.current_device_id, rel)
                code = self.get_custom_code(rel)
                result[rel] = self._row_to_metadata(row, names, ids, code or "")
            return result

    def save_file_metadata(self, relative_path: str, metadata: FileMetadata) -> None:
        """保存文件元数据，按 (device_id, relative_path) UPSERT（当前设备）。

        非空的 video_code 会同步写入 custom_codes 表（手动番号映射）。

        :param relative_path: 相对于根目录的路径
        :param metadata: FileMetadata 契约对象
        """
        device_id = metadata.device_id or self.current_device_id
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.connection.execute(
                """INSERT INTO files
                   (device_id, relative_path, file_name, title, duration, release_date,
                    cover_path, sync_status, video_link, last_scan, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_id, relative_path) DO UPDATE SET
                       file_name=excluded.file_name,
                       title=excluded.title,
                       duration=excluded.duration,
                       release_date=excluded.release_date,
                       cover_path=excluded.cover_path,
                       sync_status=excluded.sync_status,
                       video_link=excluded.video_link,
                       last_scan=excluded.last_scan""",
                (
                    device_id, relative_path, metadata.file_name, metadata.title,
                    metadata.duration, metadata.release_date, metadata.cover_path,
                    metadata.sync_status, metadata.video_link,
                    self._dt_to_str(metadata.last_scan) or now_str,
                    self._dt_to_str(metadata.created_at) or now_str,
                ),
            )
            if metadata.video_code:
                self.connection.execute(
                    """INSERT INTO custom_codes (device_id, relative_path, custom_code,
                                                 created_at, updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(device_id, relative_path) DO UPDATE SET
                           custom_code=excluded.custom_code,
                           updated_at=excluded.updated_at""",
                    (device_id, relative_path, metadata.video_code, now_str, now_str),
                )
            self.connection.commit()

    def update_sync_status(self, relative_path: str, status: str) -> None:
        """更新单个文件的同步状态（当前设备）。

        :param relative_path: 相对于根目录的路径
        :param status: pending | synced | failed
        """
        with self._lock:
            self.connection.execute(
                "UPDATE files SET sync_status=? WHERE device_id=? AND relative_path=?",
                (status, self.current_device_id, relative_path),
            )
            self.connection.commit()

    def get_files_by_folder(self, folder_relative_path: str) -> List[FileMetadata]:
        """查询指定文件夹（含子文件夹）下的全部文件元数据（当前设备）。

        :param folder_relative_path: 文件夹相对路径
        :return: FileMetadata 列表
        """
        folder = folder_relative_path.strip("/\\")
        with self._lock:
            if not folder:
                rows = self.connection.execute(
                    "SELECT * FROM files WHERE device_id=?", (self.current_device_id,)
                ).fetchall()
            else:
                pattern = folder.replace("\\", "/") + "/%"
                rows = self.connection.execute(
                    "SELECT * FROM files WHERE device_id=? "
                    "AND (relative_path=? OR relative_path LIKE ?)",
                    (self.current_device_id, folder, pattern),
                ).fetchall()
            result = []
            for row in rows:
                rel = row["relative_path"]
                names, ids = self._get_video_actors(self.current_device_id, rel)
                code = self.get_custom_code(rel)
                result.append(self._row_to_metadata(row, names, ids, code or ""))
            return result

    def get_unsynced_files(self, limit: int = 100) -> List[FileMetadata]:
        """查询当前设备未同步（sync_status != 'synced'）的文件。

        :param limit: 最大返回条数（默认 100）
        :return: FileMetadata 列表
        """
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM files WHERE device_id=? AND sync_status<>'synced' "
                "ORDER BY file_id LIMIT ?",
                (self.current_device_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                rel = row["relative_path"]
                names, ids = self._get_video_actors(self.current_device_id, rel)
                code = self.get_custom_code(rel)
                result.append(self._row_to_metadata(row, names, ids, code or ""))
            return result

    def search_files(self, keyword: str) -> List[FileMetadata]:
        """跨字段模糊搜索当前设备文件：标题 / 文件名 / 番号 / 演员名。

        番号匹配走 custom_codes 表（手动番号映射）；演员名匹配走
        video_actors + actors 关联。

        :param keyword: 搜索关键词
        :return: FileMetadata 列表（去重）
        """
        pattern = f"%{keyword}%"
        sql = """
            SELECT DISTINCT f.* FROM files f
            LEFT JOIN custom_codes cc
                ON cc.device_id=f.device_id AND cc.relative_path=f.relative_path
            LEFT JOIN video_actors va
                ON va.device_id=f.device_id AND va.relative_path=f.relative_path
            LEFT JOIN actors a ON a.actor_id=va.actor_id
            WHERE f.device_id=? AND (
                f.title LIKE ? OR f.file_name LIKE ? OR cc.custom_code LIKE ?
                OR a.japanese_name LIKE ? OR a.chinese_name LIKE ?
                OR a.alt_japanese_name LIKE ? OR a.alt_chinese_name LIKE ?
            )
            ORDER BY f.file_id
        """
        with self._lock:
            rows = self.connection.execute(
                sql, (self.current_device_id, pattern, pattern, pattern,
                      pattern, pattern, pattern, pattern),
            ).fetchall()
            result = []
            for row in rows:
                rel = row["relative_path"]
                names, ids = self._get_video_actors(self.current_device_id, rel)
                code = self.get_custom_code(rel)
                result.append(self._row_to_metadata(row, names, ids, code or ""))
            return result

    def delete_file_record(self, relative_path: str) -> None:
        """删除当前设备指定文件的元数据记录（含演员关联与标签关联）。

        :param relative_path: 相对于根目录的路径
        """
        with self._lock:
            self.connection.execute(
                "DELETE FROM files WHERE device_id=? AND relative_path=?",
                (self.current_device_id, relative_path),
            )
            self.connection.execute(
                "DELETE FROM video_actors WHERE device_id=? AND relative_path=?",
                (self.current_device_id, relative_path),
            )
            self.connection.execute(
                "DELETE FROM file_tags WHERE device_id=? AND relative_path=?",
                (self.current_device_id, relative_path),
            )
            self.connection.commit()

    def get_device_stats(self) -> dict:
        """获取当前设备的数据库统计信息。

        :return: dict，包含 total_files / synced / pending / failed /
                 total_actors / total_tags / total_links / total_actor_folders
        """
        with self._lock:
            def _scalar(sql: str, params: tuple = ()):
                return self.connection.execute(sql, params).fetchone()[0]

            stats = {
                "device_id": self.current_device_id,
                "total_files": _scalar(
                    "SELECT COUNT(*) FROM files WHERE device_id=?",
                    (self.current_device_id,)),
                "synced": _scalar(
                    "SELECT COUNT(*) FROM files WHERE device_id=? AND sync_status='synced'",
                    (self.current_device_id,)),
                "pending": _scalar(
                    "SELECT COUNT(*) FROM files WHERE device_id=? AND sync_status='pending'",
                    (self.current_device_id,)),
                "failed": _scalar(
                    "SELECT COUNT(*) FROM files WHERE device_id=? AND sync_status='failed'",
                    (self.current_device_id,)),
                "total_actors": _scalar("SELECT COUNT(*) FROM actors"),
                "total_tags": _scalar(
                    "SELECT COUNT(*) FROM tags WHERE device_id=?",
                    (self.current_device_id,)),
                "total_links": _scalar(
                    "SELECT COUNT(*) FROM video_links WHERE device_id=?",
                    (self.current_device_id,)),
                "total_actor_folders": _scalar(
                    "SELECT COUNT(*) FROM actor_folders WHERE device_id=?",
                    (self.current_device_id,)),
            }
            return stats

    def vacuum(self) -> None:
        """压缩数据库文件（VACUUM），回收已删除记录占用的空间。"""
        with self._lock:
            self.connection.commit()
            self.connection.execute("VACUUM")

    def close(self) -> None:
        """关闭数据库连接（应用退出时调用）。"""
        with self._lock:
            self.connection.commit()
            self.connection.close()

    # ------------------------------------------------------------------
    # 演员相关方法（设计 4.1 节）
    # ------------------------------------------------------------------
    def _get_or_create_actor(self, actor_name: str) -> int:
        """按日文名查询演员，不存在则创建，返回 actor_id。

        :param actor_name: 演员日文名（唯一键）
        :return: actor_id
        """
        with self._lock:
            row = self.connection.execute(
                "SELECT actor_id FROM actors WHERE japanese_name=?",
                (actor_name,),
            ).fetchone()
            if row is not None:
                return row["actor_id"]
            cursor = self.connection.execute(
                "INSERT INTO actors (japanese_name) VALUES (?)", (actor_name,)
            )
            self.connection.commit()
            return cursor.lastrowid

    def _get_video_actors(self, device_id: str, relative_path: str) -> Tuple[List[str], List[int]]:
        """查询指定视频的演员（按 actor_order 排序）。

        :param device_id: 设备标识
        :param relative_path: 视频相对路径
        :return: (演员显示名列表, 演员ID列表)；显示名为「日文名(中文名)」或纯日文名
        """
        with self._lock:
            rows = self.connection.execute(
                """SELECT a.actor_id, a.japanese_name, a.chinese_name
                   FROM video_actors va
                   JOIN actors a ON a.actor_id=va.actor_id
                   WHERE va.device_id=? AND va.relative_path=?
                   ORDER BY va.actor_order, va.id""",
                (device_id, relative_path),
            ).fetchall()
            ids = [row["actor_id"] for row in rows]
            names = []
            for row in rows:
                if row["chinese_name"]:
                    names.append(f"{row['japanese_name']}({row['chinese_name']})")
                else:
                    names.append(row["japanese_name"])
            return names, ids

    def get_files_by_actor(self, actor_id: int) -> List[str]:
        """查询指定演员关联的全部视频相对路径（跨设备去重）。

        :param actor_id: 演员ID
        :return: relative_path 列表
        """
        with self._lock:
            rows = self.connection.execute(
                "SELECT DISTINCT relative_path FROM video_actors WHERE actor_id=? "
                "ORDER BY relative_path",
                (actor_id,),
            ).fetchall()
            return [row["relative_path"] for row in rows]

    def search_actors(self, keyword: str) -> List[Actor]:
        """按关键词模糊搜索演员（日文名/中文名/关联名）。

        :param keyword: 搜索关键词
        :return: Actor 列表
        """
        pattern = f"%{keyword}%"
        with self._lock:
            rows = self.connection.execute(
                """SELECT * FROM actors
                   WHERE japanese_name LIKE ? OR chinese_name LIKE ?
                      OR alt_japanese_name LIKE ? OR alt_chinese_name LIKE ?
                   ORDER BY video_count DESC, actor_id""",
                (pattern, pattern, pattern, pattern),
            ).fetchall()
            return [self._row_to_actor(row) for row in rows]

    def get_actor_by_id(self, actor_id: int) -> Optional[Actor]:
        """按 ID 查询演员详情。

        :param actor_id: 演员ID
        :return: Actor 或 None
        """
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM actors WHERE actor_id=?", (actor_id,)
            ).fetchone()
            return self._row_to_actor(row) if row is not None else None

    def _update_actor_video_counts(self) -> None:
        """重算全部演员的关联视频数（actors.video_count），全表刷新。"""
        with self._lock:
            self.connection.execute(
                """UPDATE actors SET video_count=COALESCE((
                       SELECT COUNT(DISTINCT va.device_id || '/' || va.relative_path)
                       FROM video_actors va WHERE va.actor_id=actors.actor_id
                   ), 0)"""
            )
            self.connection.commit()

    # ------------------------------------------------------------------
    # 标签表 CRUD（设计 21 章标签管理模块隐含需要）
    # ------------------------------------------------------------------
    def create_tag(self, tag_name: str, tag_color: str = "#2196F3",
                   device_id: Optional[str] = None) -> int:
        """创建标签（同名幂等，已存在则返回现有 tag_id）。

        :param tag_name: 标签名
        :param tag_color: 标签颜色（默认 #2196F3）
        :param device_id: 设备标识（默认当前设备）
        :return: tag_id
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            row = self.connection.execute(
                "SELECT tag_id FROM tags WHERE device_id=? AND tag_name=?",
                (device_id, tag_name),
            ).fetchone()
            if row is not None:
                return row["tag_id"]
            cursor = self.connection.execute(
                "INSERT INTO tags (device_id, tag_name, tag_color) VALUES (?,?,?)",
                (device_id, tag_name, tag_color),
            )
            self.connection.commit()
            return cursor.lastrowid

    def delete_tag(self, tag_id: int) -> bool:
        """删除标签及其全部文件/演员关联。

        :param tag_id: 标签ID
        :return: 是否删除了记录
        """
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM tags WHERE tag_id=?", (tag_id,))
            self.connection.execute(
                "DELETE FROM file_tags WHERE tag_id=?", (tag_id,))
            self.connection.execute(
                "DELETE FROM actor_tags WHERE tag_id=?", (tag_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def rename_tag(self, tag_id: int, new_name: str) -> bool:
        """重命名标签。

        :param tag_id: 标签ID
        :param new_name: 新标签名
        :return: 是否更新成功
        """
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE tags SET tag_name=? WHERE tag_id=?", (new_name, tag_id))
            self.connection.commit()
            return cursor.rowcount > 0

    def get_all_tags(self, device_id: Optional[str] = None) -> List[Tag]:
        """查询设备全部标签（含每个标签的文件计数，按名称排序）。

        :param device_id: 设备标识（默认当前设备）
        :return: Tag 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                """SELECT t.*, COUNT(ft.id) AS file_count
                   FROM tags t
                   LEFT JOIN file_tags ft ON ft.tag_id=t.tag_id
                   WHERE t.device_id=?
                   GROUP BY t.tag_id
                   ORDER BY t.tag_name""",
                (device_id,),
            ).fetchall()
            return [self._row_to_tag(row) for row in rows]

    def get_tag_count(self, device_id: Optional[str] = None) -> int:
        """查询设备标签总数。

        :param device_id: 设备标识（默认当前设备）
        :return: 标签数
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS c FROM tags WHERE device_id=?", (device_id,)
            ).fetchone()
            return row["c"]

    # ------------------------------------------------------------------
    # file_tags 关联（设计 21 章标签管理模块隐含需要）
    # ------------------------------------------------------------------
    def add_tag_to_file(self, relative_path: str, tag_id: int,
                        device_id: Optional[str] = None) -> None:
        """给文件打标签（幂等）。

        :param relative_path: 文件相对路径
        :param tag_id: 标签ID
        :param device_id: 设备标识（默认当前设备）
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO file_tags (device_id, relative_path, tag_id) "
                "VALUES (?,?,?)",
                (device_id, relative_path, tag_id),
            )
            self.connection.commit()

    def remove_tag_from_file(self, relative_path: str, tag_id: int,
                             device_id: Optional[str] = None) -> bool:
        """移除文件上的标签。

        :param relative_path: 文件相对路径
        :param tag_id: 标签ID
        :param device_id: 设备标识（默认当前设备）
        :return: 是否移除了记录
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM file_tags WHERE device_id=? AND relative_path=? AND tag_id=?",
                (device_id, relative_path, tag_id),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def get_file_tags(self, relative_path: str,
                      device_id: Optional[str] = None) -> List[Tag]:
        """查询文件的全部标签。

        :param relative_path: 文件相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: Tag 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                """SELECT t.*, COUNT(ft2.id) AS file_count
                   FROM file_tags ft
                   JOIN tags t ON t.tag_id=ft.tag_id
                   LEFT JOIN file_tags ft2 ON ft2.tag_id=t.tag_id
                   WHERE ft.device_id=? AND ft.relative_path=?
                   GROUP BY t.tag_id
                   ORDER BY t.tag_name""",
                (device_id, relative_path),
            ).fetchall()
            return [self._row_to_tag(row) for row in rows]

    def get_files_by_tag(self, tag_id: int,
                         device_id: Optional[str] = None) -> List[str]:
        """查询打过指定标签的全部文件相对路径。

        :param tag_id: 标签ID
        :param device_id: 设备标识（默认当前设备）
        :return: relative_path 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                "SELECT relative_path FROM file_tags WHERE device_id=? AND tag_id=? "
                "ORDER BY relative_path",
                (device_id, tag_id),
            ).fetchall()
            return [row["relative_path"] for row in rows]

    # ------------------------------------------------------------------
    # video_links 视频关联（设计 24 章视频关联模块隐含需要）
    # ------------------------------------------------------------------
    def create_link(self, primary_video_path: str, linked_folder_path: str,
                    match_code: str = "", device_id: Optional[str] = None) -> int:
        """创建视频关联记录（同主视频+同文件夹幂等，返回现有 link_id）。

        :param primary_video_path: 主视频相对路径
        :param linked_folder_path: 被关联的图片文件夹相对路径
        :param match_code: 匹配番号
        :param device_id: 设备标识（默认当前设备）
        :return: link_id
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            row = self.connection.execute(
                "SELECT link_id FROM video_links WHERE device_id=? AND "
                "primary_video_path=? AND linked_folder_path=?",
                (device_id, primary_video_path, linked_folder_path),
            ).fetchone()
            if row is not None:
                return row["link_id"]
            cursor = self.connection.execute(
                "INSERT INTO video_links (device_id, primary_video_path, "
                "linked_folder_path, match_code) VALUES (?,?,?,?)",
                (device_id, primary_video_path, linked_folder_path, match_code),
            )
            self.connection.commit()
            return cursor.lastrowid

    def remove_link(self, link_id: int) -> bool:
        """删除单条视频关联。

        :param link_id: 关联ID
        :return: 是否删除了记录
        """
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM video_links WHERE link_id=?", (link_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def remove_links_by_folder(self, linked_folder_path: str,
                               device_id: Optional[str] = None) -> int:
        """删除指定图片文件夹的全部视频关联。

        :param linked_folder_path: 图片文件夹相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: 删除的条数
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM video_links WHERE device_id=? AND linked_folder_path=?",
                (device_id, linked_folder_path),
            )
            self.connection.commit()
            return cursor.rowcount

    def get_linked_videos(self, linked_folder_path: str,
                          device_id: Optional[str] = None) -> List[VideoLink]:
        """查询指定图片文件夹关联到的全部视频。

        :param linked_folder_path: 图片文件夹相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: VideoLink 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM video_links WHERE device_id=? AND linked_folder_path=? "
                "ORDER BY primary_video_path",
                (device_id, linked_folder_path),
            ).fetchall()
            return [self._row_to_link(row) for row in rows]

    def get_primary_videos(self, device_id: Optional[str] = None) -> List[str]:
        """查询当前设备全部被关联的主视频相对路径（去重）。

        :param device_id: 设备标识（默认当前设备）
        :return: primary_video_path 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                "SELECT DISTINCT primary_video_path FROM video_links "
                "WHERE device_id=? ORDER BY primary_video_path",
                (device_id,),
            ).fetchall()
            return [row["primary_video_path"] for row in rows]

    def get_primary_path(self, relative_path: str,
                         device_id: Optional[str] = None) -> Optional[str]:
        """查询指定文件（图片）通过所在文件夹的关联得到的主视频路径。

        :param relative_path: 图片文件相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: 主视频相对路径，未关联时返回 None
        """
        device_id = device_id or self.current_device_id
        posix_path = relative_path.replace("\\", "/")
        folder = posix_path.rsplit("/", 1)[0] if "/" in posix_path else ""
        with self._lock:
            row = self.connection.execute(
                "SELECT primary_video_path FROM video_links WHERE device_id=? AND "
                "linked_folder_path=? ORDER BY link_id LIMIT 1",
                (device_id, folder),
            ).fetchone()
            return row["primary_video_path"] if row is not None else None

    def get_all_links(self, device_id: Optional[str] = None) -> List[VideoLink]:
        """查询设备全部视频关联记录。

        :param device_id: 设备标识（默认当前设备）
        :return: VideoLink 列表
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM video_links WHERE device_id=? ORDER BY link_id",
                (device_id,),
            ).fetchall()
            return [self._row_to_link(row) for row in rows]

    def batch_update_paths(self, old_prefix: str, new_prefix: str,
                           device_id: Optional[str] = None) -> int:
        """批量替换路径前缀（设备根目录迁移/盘符变更时使用）。

        同时更新 video_links 的 primary_video_path 与 linked_folder_path。

        :param old_prefix: 旧路径前缀
        :param new_prefix: 新路径前缀
        :param device_id: 设备标识（默认当前设备；传 None 且显式给 None 时仍取当前设备）
        :return: 更新的关联条数（主视频路径命中的条数）
        """
        device_id = device_id or self.current_device_id
        old_norm = old_prefix.replace("\\", "/").rstrip("/")
        new_norm = new_prefix.replace("\\", "/").rstrip("/")
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE video_links SET primary_video_path=? || "
                "SUBSTR(primary_video_path, ?) WHERE device_id=? AND "
                "primary_video_path LIKE ?",
                (new_norm, len(old_norm) + 1, device_id, old_norm + "%"),
            )
            self.connection.execute(
                "UPDATE video_links SET linked_folder_path=? || "
                "SUBSTR(linked_folder_path, ?) WHERE device_id=? AND "
                "linked_folder_path LIKE ?",
                (new_norm, len(old_norm) + 1, device_id, old_norm + "%"),
            )
            self.connection.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # custom_codes 手动番号映射（设计 25 章爬虫配置模块隐含需要）
    # ------------------------------------------------------------------
    def get_custom_code(self, relative_path: str,
                        device_id: Optional[str] = None) -> Optional[str]:
        """查询文件的手动番号。

        :param relative_path: 文件相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: 番号字符串，未设置时返回 None
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            row = self.connection.execute(
                "SELECT custom_code FROM custom_codes WHERE device_id=? AND "
                "relative_path=?",
                (device_id, relative_path),
            ).fetchone()
            return row["custom_code"] if row is not None else None

    def set_custom_code(self, relative_path: str, custom_code: str,
                        device_id: Optional[str] = None) -> None:
        """设置文件的手动番号（UPSERT）。

        :param relative_path: 文件相对路径
        :param custom_code: 番号
        :param device_id: 设备标识（默认当前设备）
        """
        device_id = device_id or self.current_device_id
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.connection.execute(
                """INSERT INTO custom_codes (device_id, relative_path, custom_code,
                                             created_at, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(device_id, relative_path) DO UPDATE SET
                       custom_code=excluded.custom_code,
                       updated_at=excluded.updated_at""",
                (device_id, relative_path, custom_code, now_str, now_str),
            )
            self.connection.commit()

    def remove_custom_code(self, relative_path: str,
                           device_id: Optional[str] = None) -> bool:
        """移除文件的手动番号。

        :param relative_path: 文件相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: 是否移除了记录
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM custom_codes WHERE device_id=? AND relative_path=?",
                (device_id, relative_path),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def has_custom_code(self, relative_path: str,
                        device_id: Optional[str] = None) -> bool:
        """判断文件是否设置了手动番号。

        :param relative_path: 文件相对路径
        :param device_id: 设备标识（默认当前设备）
        :return: True/False
        """
        device_id = device_id or self.current_device_id
        with self._lock:
            row = self.connection.execute(
                "SELECT 1 FROM custom_codes WHERE device_id=? AND relative_path=?",
                (device_id, relative_path),
            ).fetchone()
            return row is not None

    # ------------------------------------------------------------------
    # actor_folders 演员文件夹配置（设计 26 章演员管理模块隐含需要）
    # ------------------------------------------------------------------
    def add_actor_folder(self, folder_path: str, folder_name: str = "",
                         device_id: Optional[str] = None,
                         is_enabled: bool = True) -> int:
        """添加演员文件夹配置（同设备同路径幂等，返回现有 folder_id）。

        :param folder_path: 文件夹路径（相对或绝对，按设备约定）
        :param folder_name: 文件夹名称（为空时取路径末段）
        :param device_id: 设备标识（默认当前设备）
        :param is_enabled: 是否启用
        :return: folder_id
        """
        device_id = device_id or self.current_device_id
        if not folder_name:
            folder_name = folder_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        with self._lock:
            row = self.connection.execute(
                "SELECT folder_id FROM actor_folders WHERE device_id=? AND folder_path=?",
                (device_id, folder_path),
            ).fetchone()
            if row is not None:
                return row["folder_id"]
            cursor = self.connection.execute(
                "INSERT INTO actor_folders (device_id, folder_path, folder_name, "
                "is_enabled) VALUES (?,?,?,?)",
                (device_id, folder_path, folder_name, 1 if is_enabled else 0),
            )
            self.connection.commit()
            return cursor.lastrowid

    def get_actor_folders(self, device_id: Optional[str] = None,
                          only_enabled: bool = False) -> List[ActorFolderConfig]:
        """查询设备的演员文件夹配置列表。

        :param device_id: 设备标识（默认当前设备）
        :param only_enabled: 是否只返回启用的文件夹
        :return: ActorFolderConfig 列表
        """
        device_id = device_id or self.current_device_id
        sql = ("SELECT * FROM actor_folders WHERE device_id=? "
               + ("AND is_enabled=1 " if only_enabled else "")
               + "ORDER BY folder_path")
        with self._lock:
            rows = self.connection.execute(sql, (device_id,)).fetchall()
            result = []
            for row in rows:
                result.append(ActorFolderConfig(
                    folder_id=row["folder_id"],
                    device_id=row["device_id"],
                    folder_path=row["folder_path"],
                    folder_name=row["folder_name"] or "",
                    is_enabled=bool(row["is_enabled"]),
                    last_scan=self._str_to_dt(row["last_scan"]),
                    created_at=self._str_to_dt(row["created_at"]),
                ))
            return result

    def remove_actor_folder(self, folder_id: int) -> bool:
        """删除演员文件夹配置。

        :param folder_id: 配置ID
        :return: 是否删除了记录
        """
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM actor_folders WHERE folder_id=?", (folder_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def update_actor_folder_scan(self, folder_id: int,
                                 scan_time: Optional[datetime] = None) -> None:
        """更新演员文件夹的最后扫描时间。

        :param folder_id: 配置ID
        :param scan_time: 扫描时间（默认当前时间）
        """
        with self._lock:
            self.connection.execute(
                "UPDATE actor_folders SET last_scan=? WHERE folder_id=?",
                (self._dt_to_str(scan_time or datetime.now()), folder_id),
            )
            self.connection.commit()
