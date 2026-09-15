#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演员管理模块 - 配置持久化修复版
关键修复：
1. 启动时优先从 config.json 加载（避免数据库为空时覆盖配置）
2. 合并数据库和配置的数据（取并集）
3. 确保每次增删都立即写入 config.json
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import re
import threading
import sqlite3
import json

# 拼音库（中文）
try:
    from pypinyin import lazy_pinyin
    PYPINYIN_AVAILABLE = True
except ImportError:
    PYPINYIN_AVAILABLE = False

# 日文罗马音库（可选）
try:
    import pykakasi
    kakasi_converter = pykakasi.kakasi()
    PYKAKASI_AVAILABLE = True
except ImportError:
    PYKAKASI_AVAILABLE = False


class ActorManager:
    """演员管理器 - 配置持久化修复版"""

    def __init__(self, parent_frame, db_manager, config_manager=None, debug_utils=None):
        self.parent = parent_frame
        self.db = db_manager
        self.config = config_manager  # 配置管理器引用（关键）
        self.debug = debug_utils
        self.db_path = None
        self.actor_folders = []  # 内存中的文件夹列表
        self._ui_created = False
        
        # 排序状态
        self._sort_column = None
        self._sort_reverse = False
        
        try:
            if self.db and hasattr(self.db, 'db_path'):
                self.db_path = self.db.db_path
            
            self._create_ui()
            self._ui_created = True
            
            # 关键修复：启动时立即从配置加载（不依赖数据库）
            self._load_actor_folders_safe()
            
            if not self.db or not getattr(self.db, 'is_ready', False):
                if self.actor_folders:
                    self.status_label.config(
                        text=f"已从配置加载 {len(self.actor_folders)} 个文件夹（数据库未连接）", 
                        foreground="orange"
                    )
                else:
                    self.status_label.config(text="请添加演员文件夹（将自动保存）", foreground="gray")
                
        except Exception as e:
            error_msg = f"演员管理初始化错误: {str(e)}"
            if self.debug:
                self.debug.print(error_msg)
            if not self._ui_created and self.parent:
                for widget in self.parent.winfo_children():
                    widget.destroy()
                tk.Label(self.parent, text=error_msg, fg="red", wraplength=400).pack(padx=20, pady=20)
            raise

    # ==================== 配置持久化核心修复 ====================
    
    def _save_actor_folders_to_config(self):
        """保存演员文件夹到配置文件 - 确保立即写入磁盘"""
        if not self.config:
            if self.debug:
                self.debug.print("警告: config_manager 未设置，无法保存配置")
            return False
        
        try:
            # 方法1：直接设置属性（适用于 ExtendedConfigManager）
            if hasattr(self.config, 'actor_folders'):
                self.config.actor_folders = list(self.actor_folders)
            
            # 方法2：更新内部字典（备用）
            if hasattr(self.config, 'config') and isinstance(self.config.config, dict):
                self.config.config['actor_folders'] = list(self.actor_folders)
            
            # 方法3：调用保存方法（关键：必须写入文件）
            saved = False
            if hasattr(self.config, 'save_config'):
                saved = self.config.save_config()
                if self.debug:
                    self.debug.print(f"演员文件夹已保存到 config.json: {self.actor_folders} (结果: {saved})")
            else:
                # 如果没有save_config方法，尝试直接写文件
                self._force_save_config_file()
                
            return True
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"保存演员文件夹配置失败: {e}")
            return False

    def _force_save_config_file(self):
        """强制直接写入配置文件（备用方案）"""
        try:
            # 尝试找到配置文件路径
            config_path = None
            if hasattr(self.config, 'config_path'):
                config_path = self.config.config_path
            else:
                # 默认路径：当前脚本目录下的 config.json
                config_path = os.path.join(os.path.dirname(__file__), "config.json")
            
            if config_path and os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                data['actor_folders'] = list(self.actor_folders)
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                if self.debug:
                    self.debug.print(f"已强制写入 config.json: {config_path}")
        except Exception as e:
            if self.debug:
                self.debug.print(f"强制保存配置失败: {e}")

    def _load_actor_folders_from_config(self):
        """从配置文件加载 - 尝试多种方式"""
        if not self.config:
            if self.debug:
                self.debug.print("警告: config_manager 未设置，无法加载配置")
            return []
        
        folders = []
        try:
            # 方式1：直接属性访问（适用于 ExtendedConfigManager）
            if hasattr(self.config, 'actor_folders') and isinstance(self.config.actor_folders, list):
                folders = list(self.config.actor_folders)
                if self.debug:
                    self.debug.print(f"从 config.actor_folders 加载: {folders}")
                return folders
            
            # 方式2：通过config字典访问
            if hasattr(self.config, 'config') and isinstance(self.config.config, dict):
                cfg = self.config.config
                if 'actor_folders' in cfg and isinstance(cfg['actor_folders'], list):
                    folders = list(cfg['actor_folders'])
                    if self.debug:
                        self.debug.print(f"从 config.config['actor_folders'] 加载: {folders}")
                    return folders
            
            # 方式3：尝试从文件直接读取（如果以上都失败）
            folders = self._load_from_config_file_directly()
            
        except Exception as e:
            if self.debug:
                self.debug.print(f"从配置加载演员文件夹失败: {e}")
        
        return folders

    def _load_from_config_file_directly(self):
        """直接从文件读取（备用）"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                folders = data.get('actor_folders', [])
                if self.debug:
                    self.debug.print(f"直接从文件加载 actor_folders: {folders}")
                return folders
        except Exception as e:
            if self.debug:
                self.debug.print(f"直接读取配置文件失败: {e}")
        return []

    # ==================== 关键修复：合并加载策略 ====================

    def _load_actor_folders_safe(self):
        """
        安全加载策略（修复版）：
        1. 始终先从配置文件加载（保底）
        2. 如果数据库就绪，合并数据库中的数据（不覆盖配置）
        3. 如果配置中有而数据库中没有的数据，同步到数据库
        """
        self.actor_folders = []
        
        # 第一步：优先从配置文件加载（关键修复：无论数据库状态如何，先读配置）
        config_folders = self._load_actor_folders_from_config()
        if config_folders:
            # 过滤掉不存在的路径（可选，保留的话可以看到[×]标记）
            self.actor_folders = [f for f in config_folders if isinstance(f, str)]
            if self.debug:
                self.debug.print(f"从配置加载了 {len(self.actor_folders)} 个文件夹")
        
        # 刷新显示（先显示配置的，即使数据库还没好）
        self._refresh_folder_listbox()
        
        # 第二步：如果数据库就绪，合并数据（数据库作为补充，不覆盖配置）
        if self.db and getattr(self.db, 'is_ready', False):
            try:
                db_folders = self._load_actor_folders_from_db()
                if db_folders:
                    # 合并策略：配置优先，数据库补充
                    added_count = 0
                    for folder in db_folders:
                        if folder not in self.actor_folders:
                            self.actor_folders.append(folder)
                            added_count += 1
                    
                    if added_count > 0:
                        self._refresh_folder_listbox()
                        if self.debug:
                            self.debug.print(f"从数据库合并了 {added_count} 个新文件夹")
                    
                    # 同步回配置（确保配置包含所有数据库中的文件夹）
                    if added_count > 0:
                        self._save_actor_folders_to_config()
                        
            except Exception as e:
                if self.debug:
                    self.debug.print(f"从数据库加载失败（继续使用配置数据）: {e}")
        
        # 第三步：如果数据库就绪且配置中有额外数据，同步到数据库
        if self.db and getattr(self.db, 'is_ready', False) and self.actor_folders:
            try:
                self._sync_folders_to_database()
            except Exception as e:
                if self.debug:
                    self.debug.print(f"同步配置到数据库失败: {e}")
        
        # 更新状态显示
        self._update_folder_status()

    def _load_actor_folders_from_db(self):
        """从数据库加载，返回列表（不直接修改self.actor_folders）"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT folder_path FROM actor_folders WHERE is_active = 1 ORDER BY folder_path")
            rows = cursor.fetchall()
            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()
            
            folders = [row['folder_path'] for row in rows]
            if self.debug:
                self.debug.print(f"从数据库读取到 {len(folders)} 个文件夹")
            return folders
        except Exception as e:
            if self.debug:
                self.debug.print(f"数据库查询失败: {e}")
            return []

    def _sync_folders_to_database(self):
        """将当前self.actor_folders中的文件夹同步到数据库（确保不重复）"""
        if not self.db or not getattr(self.db, 'is_ready', False):
            return
        
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            added = 0
            
            for folder in self.actor_folders:
                cursor.execute("SELECT folder_id FROM actor_folders WHERE folder_path = ?", (folder,))
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO actor_folders (folder_path, folder_name, is_active) VALUES (?, ?, 1)",
                        (folder, os.path.basename(folder))
                    )
                    added += 1
            
            if added > 0:
                conn.commit()
                if self.debug:
                    self.debug.print(f"已将 {added} 个配置中的文件夹同步到数据库")
            
            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()
                
        except Exception as e:
            if self.debug:
                self.debug.print(f"同步到数据库失败: {e}")

    def _update_folder_status(self):
        """更新文件夹区域的状态标签"""
        if self.actor_folders:
            if self.db and getattr(self.db, 'is_ready', False):
                self.status_label.config(
                    text=f"共 {len(self.actor_folders)} 个演员文件夹（已保存）", 
                    foreground="green"
                )
            else:
                self.status_label.config(
                    text=f"已加载 {len(self.actor_folders)} 个文件夹（来自配置，数据库未连接）", 
                    foreground="orange"
                )
        else:
            self.status_label.config(text="请添加演员文件夹（将自动保存到配置）", foreground="gray")

    def _refresh_folder_listbox(self):
        """刷新文件夹列表框"""
        if hasattr(self, 'folder_listbox') and self.folder_listbox:
            self.folder_listbox.delete(0, tk.END)
            for path in self.actor_folders:
                if os.path.exists(path):
                    display = os.path.basename(path)
                else:
                    display = "[×] " + os.path.basename(path)  # 标记不存在的文件夹
                self.folder_listbox.insert(tk.END, display)

    # ==================== 文件夹操作（确保保存） ====================

    def _add_folder(self):
        """添加文件夹（保存到配置+数据库）"""
        folder = filedialog.askdirectory(title="选择演员文件夹（包含演员子文件夹的目录）")
        if not folder:
            return

        folder = os.path.normpath(folder)

        if folder in self.actor_folders:
            messagebox.showwarning("提示", "该文件夹已存在")
            return

        # 1. 添加到内存
        self.actor_folders.append(folder)
        self._refresh_folder_listbox()
        
        # 2. 关键：立即保存到配置文件（无论数据库状态）
        success = self._save_actor_folders_to_config()
        if not success:
            messagebox.showwarning("警告", "文件夹已添加但保存到配置失败，重启后可能丢失")
        
        # 3. 尝试保存到数据库（如果可用）
        if self.db and getattr(self.db, 'is_ready', False):
            try:
                conn = self._get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO actor_folders (folder_path, folder_name, is_active) VALUES (?, ?, 1)",
                    (folder, os.path.basename(folder))
                )
                conn.commit()
                cursor.close()
                if not (self.db and hasattr(self.db, '_get_connection')):
                    conn.close()
            except Exception as e:
                if self.debug:
                    self.debug.print(f"添加到数据库失败（已保存到配置）: {e}")
        
        self._update_folder_status()
        if self.debug:
            self.debug.print(f"添加文件夹: {folder}")

    def _remove_folder(self):
        """删除文件夹（从配置+数据库移除）"""
        selection = self.folder_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的文件夹")
            return

        idx = selection[0]
        if idx >= len(self.actor_folders):
            return

        folder = self.actor_folders[idx]

        msg = f"确定要删除文件夹吗？\n{folder}\n\n注意：这不会删除演员数据，只是移除扫描路径。"
        if not messagebox.askyesno("确认", msg):
            return

        # 1. 从内存移除
        self.actor_folders.pop(idx)
        self._refresh_folder_listbox()
        
        # 2. 关键：立即保存到配置（覆盖原有列表）
        self._save_actor_folders_to_config()
        
        # 3. 从数据库删除（如果可用）
        if self.db and getattr(self.db, 'is_ready', False):
            try:
                conn = self._get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM actor_folders WHERE folder_path = ?", (folder,))
                conn.commit()
                cursor.close()
                if not (self.db and hasattr(self.db, '_get_connection')):
                    conn.close()
            except Exception as e:
                if self.debug:
                    self.debug.print(f"从数据库删除失败（已从配置移除）: {e}")
        
        self._update_folder_status()

    # ==================== 其余方法（排序、扫描等） ====================

    def _get_db_connection(self):
        """获取数据库连接"""
        if self.db and hasattr(self.db, '_get_connection'):
            try:
                conn = self.db._get_connection()
                conn.execute("SELECT 1")
                return conn
            except:
                pass
        
        if self.db_path and os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                return conn
            except Exception as e:
                if self.debug:
                    self.debug.print(f"连接数据库失败: {e}")
                raise Exception(f"无法连接数据库: {self.db_path}")
        
        raise Exception("数据库未初始化")

    def _create_ui(self):
        """创建界面"""
        for widget in self.parent.winfo_children():
            widget.destroy()
            
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 文件夹管理区
        top_frame = ttk.LabelFrame(main_frame, text="演员文件夹管理（自动保存至 config.json）")
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        folder_frame = ttk.Frame(top_frame)
        folder_frame.pack(fill=tk.X, padx=5, pady=5)

        self.folder_listbox = tk.Listbox(folder_frame, height=4, font=("微软雅黑", 9))
        self.folder_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        folder_scroll = ttk.Scrollbar(folder_frame, command=self.folder_listbox.yview)
        folder_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.folder_listbox.config(yscrollcommand=folder_scroll.set)

        btn_frame = ttk.Frame(top_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="添加文件夹", command=self._add_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除选中", command=self._remove_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="更新演员", command=self._scan_actors).pack(side=tk.LEFT, padx=10)

        self.progress = ttk.Progressbar(top_frame, mode='determinate')
        self.progress.pack(fill=tk.X, padx=5, pady=5)
        self.status_label = ttk.Label(top_frame, text="初始化...", foreground="gray")
        self.status_label.pack(padx=5, pady=2)

        # 演员列表区
        bottom_frame = ttk.LabelFrame(main_frame, 
            text="演员列表（点击表头排序：英文/拼音/罗马音首字母统一排序 A-Z）")
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        toolbar = ttk.Frame(bottom_frame)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(toolbar, text="保存修改", command=self._save_changes).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="删除选中演员", command=self._delete_selected_actor).pack(side=tk.LEFT, padx=10)
        ttk.Label(toolbar, text="搜索:").pack(side=tk.LEFT, padx=(20, 2))
        self.search_entry = ttk.Entry(toolbar, width=20)
        self.search_entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="搜索", command=self._search_actors).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="清除", command=self._clear_search).pack(side=tk.LEFT, padx=2)

        # 表格
        table_frame = ttk.Frame(bottom_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        columns = ('id', 'actor_name', 'actor_name_cn', 'related_names', 'created_date')
        self.actor_tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=20)
        
        self._update_column_headings()
        
        self.actor_tree.column('id', width=50, anchor='center')
        self.actor_tree.column('actor_name', width=200)
        self.actor_tree.column('actor_name_cn', width=150)
        self.actor_tree.column('related_names', width=250)
        self.actor_tree.column('created_date', width=120)

        tree_scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.actor_tree.yview)
        tree_scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.actor_tree.xview)
        self.actor_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        self.actor_tree.grid(row=0, column=0, sticky='nsew')
        tree_scroll_y.grid(row=0, column=1, sticky='ns')
        tree_scroll_x.grid(row=1, column=0, sticky='ew')
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.actor_tree.bind('<Double-1>', self._on_double_click)

        # 编辑区
        self.edit_frame = ttk.LabelFrame(bottom_frame, text="编辑演员信息")
        self.edit_frame.pack(fill=tk.X, padx=5, pady=5)
        self.edit_frame.pack_forget()

        ttk.Label(self.edit_frame, text="日文名:").grid(row=0, column=0, padx=5, pady=5, sticky='e')
        self.edit_name = ttk.Entry(self.edit_frame, width=30)
        self.edit_name.grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(self.edit_frame, text="中文名:").grid(row=0, column=2, padx=5, pady=5, sticky='e')
        self.edit_name_cn = ttk.Entry(self.edit_frame, width=20)
        self.edit_name_cn.grid(row=0, column=3, padx=5, pady=5)
        ttk.Button(self.edit_frame, text="保存", command=self._save_edit).grid(row=0, column=4, padx=5, pady=5)
        ttk.Button(self.edit_frame, text="取消", command=self._cancel_edit).grid(row=0, column=5, padx=5, pady=5)

        self.current_edit_id = None

    def _update_column_headings(self):
        """更新表头"""
        headings = {
            'id': 'ID',
            'actor_name': '日文名（读音A-Z）',
            'actor_name_cn': '中文名（拼音A-Z）',
            'related_names': '关联名称',
            'created_date': '创建时间'
        }
        sortable = ['id', 'actor_name', 'actor_name_cn']
        
        for col, text in headings.items():
            if col in sortable and self._sort_column == col:
                marker = " ▼" if self._sort_reverse else " ▲"
                self.actor_tree.heading(col, text=text + marker,
                                       command=lambda c=col: self._sort_by_column(c))
            else:
                self.actor_tree.heading(col, text=text,
                                       command=lambda c=col: self._sort_by_column(c) if c in sortable else None)

    def _get_unified_sort_key(self, text):
        """统一读音排序键（首字母A-Z）"""
        if not text:
            return "ZZZ"
        
        first_char = text[0]
        
        # 英文字母
        if first_char.isascii() and first_char.isalpha():
            return first_char.upper()
        
        # 中文汉字
        if '\u4e00' <= first_char <= '\u9fff':
            if PYPINYIN_AVAILABLE:
                try:
                    pinyin = lazy_pinyin(first_char)[0]
                    return pinyin[0].upper() if pinyin else "ZZ"
                except:
                    return "ZZ"
            return "ZZ"
        
        # 日文假名
        if ('\u3040' <= first_char <= '\u309f') or ('\u30a0' <= first_char <= '\u30ff'):
            if PYKAKASI_AVAILABLE:
                try:
                    result = kakasi_converter.convert(first_char)
                    if result and len(result) > 0:
                        romaji = result[0].get('hepburn', '')
                        if romaji:
                            return romaji[0].upper()
                except:
                    pass
            return self._kana_to_initial(first_char)
        
        # 日文汉字
        if PYKAKASI_AVAILABLE:
            try:
                result = kakasi_converter.convert(first_char)
                if result and len(result) > 0:
                    romaji = result[0].get('hepburn', '')
                    if romaji and romaji[0].isalpha():
                        return romaji[0].upper()
            except:
                pass
        
        return "Z" + first_char.upper()

    def _kana_to_initial(self, char):
        """假名到首字母映射"""
        kana_map = {
            'あ': 'A', 'い': 'I', 'う': 'U', 'え': 'E', 'お': 'O',
            'か': 'K', 'き': 'K', 'く': 'K', 'け': 'K', 'こ': 'K',
            'さ': 'S', 'し': 'S', 'す': 'S', 'せ': 'S', 'そ': 'S',
            'た': 'T', 'ち': 'C', 'つ': 'T', 'て': 'T', 'と': 'T',
            'な': 'N', 'に': 'N', 'ぬ': 'N', 'ね': 'N', 'の': 'N',
            'は': 'H', 'ひ': 'H', 'ふ': 'F', 'へ': 'H', 'ほ': 'H',
            'ま': 'M', 'み': 'M', 'む': 'M', 'め': 'M', 'も': 'M',
            'や': 'Y', 'ゆ': 'Y', 'よ': 'Y',
            'ら': 'R', 'り': 'R', 'る': 'R', 'れ': 'R', 'ろ': 'R',
            'わ': 'W', 'を': 'W', 'ん': 'N',
            'が': 'G', 'ぎ': 'G', 'ぐ': 'G', 'げ': 'G', 'ご': 'G',
            'ざ': 'Z', 'じ': 'J', 'ず': 'Z', 'ぜ': 'Z', 'ぞ': 'Z',
            'だ': 'D', 'ぢ': 'J', 'づ': 'Z', 'で': 'D', 'ど': 'D',
            'ば': 'B', 'び': 'B', 'ぶ': 'B', 'べ': 'B', 'ぼ': 'B',
            'ぱ': 'P', 'ぴ': 'P', 'ぷ': 'P', 'ぺ': 'P', 'ぽ': 'P',
            'ぁ': 'A', 'ぃ': 'I', 'ぅ': 'U', 'ぇ': 'E', 'ぉ': 'O',
            'ゃ': 'Y', 'ゅ': 'Y', 'ょ': 'Y', 'っ': 'T',
            'ゐ': 'I', 'ゑ': 'E', 'ゎ': 'W',
        }
        
        if '\u30a0' <= char <= '\u30ff':  # 片假名转平假名
            hiragana_code = ord(char) - 0x60
            if 0x3040 <= hiragana_code <= 0x309f:
                char = chr(hiragana_code)
        
        return kana_map.get(char, "Z")

    def _sort_by_column(self, col):
        """排序"""
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        
        self._update_column_headings()
        self._refresh_actors_with_sort()

    def _refresh_actors_with_sort(self):
        """刷新并排序"""
        for item in self.actor_tree.get_children():
            self.actor_tree.delete(item)

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT 
                    a.actor_id,
                    a.actor_name,
                    a.actor_name_cn,
                    a.sort_char,
                    a.created_date,
                    GROUP_CONCAT(ar.related_actor_id) as related_ids
                FROM actors a
                LEFT JOIN actor_relations ar ON a.actor_id = ar.actor_id
                GROUP BY a.actor_id
            """)

            rows = cursor.fetchall()
            items = []
            
            for row in rows:
                actor_id = row['actor_id']
                name = row['actor_name'] or ''
                name_cn = row['actor_name_cn'] or ''
                sort_char = row['sort_char']
                created = row['created_date'][:10] if row['created_date'] else ''

                related_names = ''
                if row['related_ids']:
                    related_ids = [int(x) for x in row['related_ids'].split(',')]
                    names = []
                    for rid in related_ids[:3]:
                        cursor.execute("SELECT actor_name FROM actors WHERE actor_id = ?", (rid,))
                        rrow = cursor.fetchone()
                        if rrow:
                            names.append(rrow['actor_name'])
                    related_names = ', '.join(names)
                    if len(related_ids) > 3:
                        related_names += f" 等{len(related_ids)}人"

                items.append({
                    'id': actor_id,
                    'actor_name': name,
                    'actor_name_cn': name_cn,
                    'sort_char': sort_char,
                    'related_names': related_names,
                    'created_date': created
                })

            # 排序
            if self._sort_column:
                if self._sort_column == 'id':
                    items.sort(key=lambda x: int(x['id']) if str(x['id']).isdigit() else 999999, 
                              reverse=self._sort_reverse)
                elif self._sort_column == 'actor_name_cn':
                    items.sort(key=lambda x: self._get_unified_sort_key(x['actor_name_cn']),
                              reverse=self._sort_reverse)
                elif self._sort_column == 'actor_name':
                    def get_jp_key(item):
                        if item.get('sort_char'):
                            return item['sort_char'].upper()
                        return self._get_unified_sort_key(item['actor_name'])
                    
                    items.sort(key=get_jp_key, reverse=self._sort_reverse)

            for item in items:
                self.actor_tree.insert('', 'end', values=(
                    item['id'], item['actor_name'], item['actor_name_cn'],
                    item['related_names'], item['created_date']
                ))

            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()

            status_text = f"共 {len(items)} 个演员"
            if self._sort_column:
                direction = "降序" if self._sort_reverse else "升序"
                col_map = {'id': 'ID', 'actor_name': '日文名', 'actor_name_cn': '中文名'}
                status_text += f" (按{col_map.get(self._sort_column)} {direction})"
            self.status_label.config(text=status_text, foreground="gray")

        except Exception as e:
            if self.debug:
                self.debug.print(f"刷新演员列表失败: {e}")

    def _scan_actors(self):
        """扫描演员"""
        if not self.actor_folders:
            messagebox.showwarning("提示", "请先添加演员文件夹")
            return

        self.status_label.config(text="正在扫描演员文件夹...", foreground="blue")
        self.progress['value'] = 0
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        """后台扫描"""
        try:
            all_subfolders = []
            for folder_path in self.actor_folders:
                if not os.path.exists(folder_path):
                    continue
                try:
                    subfolders = [f for f in os.scandir(folder_path) if f.is_dir()]
                    all_subfolders.extend(subfolders)
                except:
                    continue

            total = len(all_subfolders)
            if total == 0:
                self.parent.after(0, lambda: self.status_label.config(text="未找到子文件夹", foreground="orange"))
                return

            processed = new_actors = updated_actors = new_relations = 0
            conn = self._get_db_connection()
            cursor = conn.cursor()

            for subfolder in all_subfolders:
                processed += 1
                progress = int(processed / total * 100)
                status = f"正在扫描 ({processed}/{total}): {subfolder.name}"
                self.parent.after(0, lambda p=progress, s=status: (
                    self.progress.config(value=p),
                    self.status_label.config(text=s, foreground="blue")
                ))

                folder_name = subfolder.name
                sort_char = folder_name[0].upper() if folder_name and folder_name[0].isalpha() else None
                actors = self._parse_actor_name(folder_name)

                if actors:
                    result = self._save_or_update_actors(cursor, actors, sort_char)
                    new_actors += result['new']
                    updated_actors += result['updated']
                    new_relations += result['new_relations']

            cursor.execute("UPDATE actor_folders SET last_scan = datetime('now') WHERE is_active = 1")
            conn.commit()
            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()

            final = f"扫描完成: 新增 {new_actors} 个, 更新 {updated_actors} 个, 关联 {new_relations} 对"
            self.parent.after(0, lambda: (
                self._refresh_actors_with_sort(),
                self.status_label.config(text=final, foreground="green"),
                self.progress.config(value=100)
            ))

        except Exception as e:
            self.parent.after(0, lambda: self.status_label.config(text=f"扫描失败: {e}", foreground="red"))

    def _parse_actor_name(self, folder_name):
        """解析文件夹名"""
        actors = []
        name_part = folder_name
        
        if folder_name and folder_name[0].isalpha() and len(folder_name) > 1:
            if not folder_name[1].isascii() or folder_name[1].isalpha():
                name_part = folder_name[1:]

        parts = name_part.split('+')
        for part in parts:
            part = part.strip()
            if not part:
                continue
            match = re.match(r'^([^()]+)(?:\(([^)]+)\))?$', part)
            if match:
                jp_name = match.group(1).strip()
                cn_name = match.group(2).strip() if match.group(2) else None
                if jp_name:
                    actors.append({'name': jp_name, 'name_cn': cn_name})
        return actors

    def _save_or_update_actors(self, cursor, actors, sort_char=None):
        """保存演员"""
        if not actors:
            return {'new': 0, 'updated': 0, 'new_relations': 0}

        new_count = updated_count = new_relations = 0
        actor_ids = []

        for actor in actors:
            name = actor['name']
            name_cn = actor['name_cn']

            cursor.execute("SELECT actor_id, actor_name_cn FROM actors WHERE actor_name = ?", (name,))
            row = cursor.fetchone()

            if row:
                actor_id = row['actor_id']
                if name_cn and (not row['actor_name_cn'] or row['actor_name_cn'] != name_cn):
                    cursor.execute(
                        "UPDATE actors SET actor_name_cn = ?, updated_date = datetime('now') WHERE actor_id = ?",
                        (name_cn, actor_id)
                    )
                    updated_count += 1
            else:
                cursor.execute(
                    "INSERT INTO actors (actor_name, actor_name_cn, sort_char) VALUES (?, ?, ?)",
                    (name, name_cn, sort_char)
                )
                actor_id = cursor.lastrowid
                new_count += 1

            actor_ids.append(actor_id)

        if len(actor_ids) > 1:
            for i, aid in enumerate(actor_ids):
                for rid in actor_ids[i+1:]:
                    if aid == rid:
                        continue
                    cursor.execute("SELECT id FROM actor_relations WHERE actor_id = ? AND related_actor_id = ?", (aid, rid))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO actor_relations (actor_id, related_actor_id) VALUES (?, ?)", (aid, rid))
                        new_relations += 1
                    cursor.execute("SELECT id FROM actor_relations WHERE actor_id = ? AND related_actor_id = ?", (rid, aid))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO actor_relations (actor_id, related_actor_id) VALUES (?, ?)", (rid, aid))
                        new_relations += 1

        return {'new': new_count, 'updated': updated_count, 'new_relations': new_relations}

    def _on_double_click(self, event):
        """双击编辑"""
        selection = self.actor_tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.actor_tree.item(item, 'values')
        self.current_edit_id = values[0]
        self.edit_name.delete(0, tk.END)
        self.edit_name.insert(0, values[1])
        self.edit_name_cn.delete(0, tk.END)
        self.edit_name_cn.insert(0, values[2])
        self.edit_frame.pack(fill=tk.X, padx=5, pady=5)
        self.edit_name.focus_set()

    def _save_edit(self):
        """保存编辑"""
        if not self.current_edit_id:
            return
        name = self.edit_name.get().strip()
        name_cn = self.edit_name_cn.get().strip()
        if not name:
            messagebox.showwarning("提示", "日文名不能为空")
            return

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE actors SET actor_name = ?, actor_name_cn = ?, updated_date = datetime('now') WHERE actor_id = ?",
                (name, name_cn if name_cn else None, self.current_edit_id)
            )
            conn.commit()
            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()
            self._cancel_edit()
            self._refresh_actors_with_sort()
            messagebox.showinfo("成功", "演员信息已更新")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def _cancel_edit(self):
        self.edit_frame.pack_forget()
        self.current_edit_id = None

    def _save_changes(self):
        self._refresh_actors_with_sort()

    def _delete_selected_actor(self):
        """删除演员"""
        selection = self.actor_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的演员")
            return
        item = selection[0]
        values = self.actor_tree.item(item, 'values')
        actor_id, name = values[0], values[1]
        
        if not messagebox.askyesno("确认", f"确定要删除演员 '{name}' 吗？\n\n注意：这将同时删除关联关系。"):
            return

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM actor_relations WHERE actor_id = ? OR related_actor_id = ?", (actor_id, actor_id))
            cursor.execute("DELETE FROM actors WHERE actor_id = ?", (actor_id,))
            conn.commit()
            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()
            self.actor_tree.delete(item)
            messagebox.showinfo("成功", f"演员 '{name}' 已删除")
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {e}")

    def _search_actors(self):
        """搜索演员"""
        keyword = self.search_entry.get().strip()
        if not keyword:
            self._refresh_actors_with_sort()
            return

        for item in self.actor_tree.get_children():
            self.actor_tree.delete(item)

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.actor_id, a.actor_name, a.actor_name_cn, a.created_date
                FROM actors a
                LEFT JOIN actor_relations ar ON a.actor_id = ar.actor_id
                WHERE a.actor_name LIKE ? OR a.actor_name_cn LIKE ?
                GROUP BY a.actor_id
            """, ('%' + keyword + '%', '%' + keyword + '%'))

            for row in cursor.fetchall():
                self.actor_tree.insert('', 'end', values=(
                    row['actor_id'], row['actor_name'], row['actor_name_cn'] or '', '',
                    row['created_date'][:10] if row['created_date'] else ''
                ))

            cursor.close()
            if not (self.db and hasattr(self.db, '_get_connection')):
                conn.close()
                
            self.status_label.config(text=f"搜索到 {len(self.actor_tree.get_children())} 个演员", foreground="blue")
        except Exception as e:
            messagebox.showerror("错误", f"搜索失败: {e}")

    def _clear_search(self):
        self.search_entry.delete(0, tk.END)
        self._refresh_actors_with_sort()

    def refresh(self):
        """刷新页面"""
        if self.db and hasattr(self.db, 'db_path') and not self.db_path:
            self.db_path = self.db.db_path
        
        # 重新加载文件夹（从配置）
        self._load_actor_folders_safe()
        self._refresh_actors_with_sort()